"""Private retained-output orchestration at the adapter's execution boundary."""
from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import json
import uuid
from threading import Lock

from _application.consent import ConsentError
from _application.delivery_failure import DeliveryFailure
from _application.projection import canonical_result_envelope, unavailable_delivery_envelope as unavailable
from _application.result_navigation import build_view, resolve_page, select_page
from _application.results import Ok, Error, ErrorCode
from _application.types import InitialAuthorisationClass
from _bootstrap.result_capacity import capacity_recovery, representation_recovery
from .result_store import FileResultStore, ResultStoreError


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:32]


def store_delivery_failure(error: ResultStoreError, limits) -> DeliveryFailure:
    """Translate known storage codes without exposing backend messages or paths."""
    if error.code == 'capacity_exceeded':
        values, message = capacity_recovery({'result_bytes': limits.max_result_bytes,
            'owner_bytes': limits.max_owner_bytes, 'brain_bytes': limits.max_brain_bytes})
        return DeliveryFailure('capacity_exceeded', message, capacity_limits=values)
    if error.code == 'representation_too_large':
        return DeliveryFailure('representation_too_large', representation_recovery())
    return DeliveryFailure('result_unavailable', 'Private result storage is unavailable.')


class ResultDeliverySession:
    """One verified namespace; neither its credential nor result IDs grant execution."""

    def __init__(self, store: FileResultStore, credential: str, *, issued=False):
        """Authenticate a namespace before any source work; keep issuance out of stored bodies."""
        self.store = store
        self.owner = store.authenticate(credential)
        self.credential = credential if issued else None
        self._before = None
        self._authority = None
        self._source_completed = False
        self._source_lock = Lock()
        self._active_source = None

    def begin(self, context, command_id, request=None):
        """Reserve admission order and authority observations before source execution."""
        with self._source_lock:
            if self._active_source is not None:
                raise DeliveryFailure('invocation_in_progress',
                    'This delivery port already has an active source invocation. Use a separate composed context for concurrent calls.')
            active = {'invocation_id': context.invocation_id, 'command_id': command_id,
                      'ticket': None, 'finishing': False}
            self._active_source = active
        try:
            self._authority = None
            self._before = None
            self._source_completed = False
            self._list_before = (self._revision(context, request.location.value)
                if command_id == "artefact.list" and request is not None and request.cursor is None else None)
            self._before = context.authorisation.service.result_delivery_stamp(command_id)
            try:
                ticket = self.store.admit(self.owner, command_id, context.invocation_id)
            except ResultStoreError as exc:
                raise store_delivery_failure(exc, self.store.limits) from None
            with self._source_lock:
                active['ticket'] = dict(ticket)
            return ticket
        except BaseException:
            with self._source_lock:
                self._active_source = None
            raise

    def source_finished(self, context, entry, admission, result):
        """Issue continuation only for eligible admitted output under unchanged authority."""
        with self._source_lock:
            active = self._active_source
            if (active is None or active['finishing'] or active['ticket'] is None
                    or active['invocation_id'] != context.invocation_id
                    or active['command_id'] != entry.command_id):
                return
            # Nested refreshes and concurrent callers cannot replace this proof.
            self._authority = None
            self._source_completed = True
            if not entry.retain_output:
                return
            service = context.authorisation.service
            try:
                current = service.result_delivery_stamp(entry.command_id)
                if current != self._before:
                    return
                control = entry.initial_class is InitialAuthorisationClass.CONTROL
                ordinary = (not entry.output_owner_bound
                            and (control or entry.command_id in service.policy.initial)
                            and service.state(entry.command_id) == 'authorised')
                if not ordinary and not control and (admission is None or not admission.entered):
                    return
                self._authority = {'command_id':entry.command_id,
                                   'exceptional':not ordinary,
                                   'stamp':None if ordinary else self._before}
            except ConsentError:
                return

    def _verify_source_disclosure(self, context, result):
        """Check opening proof again, including exits where storage or layout failed."""
        try:
            current = context.authorisation.service.result_delivery_stamp(result.command_id)
            if self._before is None or current != self._before:
                raise DeliveryFailure('disclosure_ended', 'Source disclosure authority could not be verified.')
            if self._authority is None:
                safe_refusal = (not self._source_completed and isinstance(result, Error)
                    and result.effects == 'none' and result.error.code in {
                        ErrorCode.AUTHORITY_DENIED, ErrorCode.AUTHORISATION_REQUIRED,
                        ErrorCode.CAPABILITY_UNAVAILABLE})
                if safe_refusal:
                    return False
                raise DeliveryFailure('disclosure_ended', 'Source disclosure authority could not be verified.')
            self._check(context, self._authority)
        except DeliveryFailure:
            raise
        except Exception:
            raise DeliveryFailure('disclosure_ended', 'Source disclosure authority could not be verified.') from None
        return True

    def finish(self, context, request, result, ticket):
        """Bind publication and disclosure proof to the one active source admission."""
        with self._source_lock:
            active = self._active_source
            if (active is None or active['finishing'] or active['ticket'] is None
                    or active['invocation_id'] != context.invocation_id
                    or active['command_id'] != result.command_id or active['ticket'] != ticket):
                raise DeliveryFailure('disclosure_ended', 'Source delivery admission could not be verified.')
            active['finishing'] = True
        try:
            return self._finish(context, request, result, ticket)
        finally:
            with self._source_lock:
                self._active_source = None
                self._before = self._authority = None
                self._source_completed = False

    def _finish(self, context, request, result, ticket):
        """Publish immutable output and exact page maps after source outcome finalisation."""
        eligible = self._verify_source_disclosure(context, result)
        try:
            frame = canonical_result_envelope(result)
            command = result.command_id
            if not eligible:
                view = build_view(frame)
                if len(view.pages)>1 or view.details:
                    return unavailable(frame,'Output is not eligible for retained continuation.')
                return view.page()
            query = None
            opening_list = isinstance(result, Ok) and command == 'artefact.list' and request.cursor is None
            if opening_list:
                revision = self._revision(context, request.location.value)
                # A retained list is still a query selection, with the same
                # ten-page default; None prevents freshness checks from refreshing it.
                query = {'command_id': command, 'payload': asdict(request), 'revision': revision,
                         'backing': 'query' if revision is not None else 'retained'}
                if revision is None:
                    frame['warnings'].append({'code': 'degraded_capability', 'message':
                        f'This {request.location.value} filtered artefact list is a retained opening snapshot. '
                        'Archive freshness has no maintained revision and is not checked after opening; '
                        'pages do not refresh. Invoke artefact.list again for current data.'})
            if query and query['revision'] != self._list_before:
                return unavailable(frame,'Source changed while opening this query; rerun the read.')
            view = build_view(frame, page_size=request.page_size if opening_list else None)
            key = self._view_key(view.unit,view.page_size)
            self._authority['default_view'] = key
            try:
                result_id = self.store.publish(self.owner,ticket,frame,self._authority,query)
                self._save(result_id,key,view,query['revision'] if query else None)
                output, _ = self._select(result_id,key,page=1,start=1,maximum=10 if query else None)
            except ResultStoreError as exc:
                raise store_delivery_failure(exc, self.store.limits) from None
            return self._stamp(output,result_id,key,query['revision'] if query else None,False,
                               list_query=query is not None)
        finally:
            self._verify_source_disclosure(context, result)

    @staticmethod
    def _view_key(unit,size):
        return _digest([unit,size])

    def _save(self,result_id,key,view,revision):
        """Persist bounded independent pages and detail views, not a whole-body memory cache."""
        metadata = {**view.metadata(), 'view_id':key,
                    'source_revision':revision,'empty':view.page(1)}
        # Empty views can still carry bounded context (for example last_pass).
        # Nonempty views need only outcome metadata for out-of-range reads.
        if view.pages:
            metadata['empty']['result'] = None
        self.store.save_view(self.owner,result_id,key,list(view.pages),metadata)
        for detail_id,frame in view.details.items():
            self.store.save_view(self.owner,result_id,'detail:'+detail_id,[frame],{'detail_id':detail_id})

    def _cached_view(self, result_id, key):
        """Treat a missing view as a layout miss, preserving other store failures."""
        try:
            return self.store.view_metadata(self.owner, result_id, key)
        except ResultStoreError as exc:
            if exc.code != 'unavailable':
                raise
            return None

    def _select(self,result_id,key,*,page,start,maximum,query_revision=None,anchor=None,
                absolute_selection=False):
        """Read at most one stored page and apply the common numeric selection contract."""
        number=start if page==0 else page
        within_selection = anchor is not None or (number >= start and (maximum is None or number < start + maximum))
        meta,frame,observation=self.store.read_view_selection(self.owner,result_id,key,number,
            observe=query_revision is not None and (within_selection or absolute_selection),
            **({'anchor': anchor} if anchor is not None else {}))
        if anchor is not None:
            page=start=frame['delivery']['page']
        elif absolute_selection and not within_selection and 1 <= number <= meta['total_pages']:
            start=number
        frame=select_page(frame,meta,page,start,maximum)
        descriptor=frame['delivery']
        descriptor['source_revision']=meta.get('source_revision')
        descriptor['requested_anchor']=anchor
        if anchor is not None and descriptor['first_item'] is not None and descriptor['first_item'] <= anchor:
            descriptor['overlap_notice']='This anchored page includes preceding units; its first_item and last_item show the overlap.'
        previous = observation.previous_revision if observation else None
        if (query_revision is not None and descriptor['total_pages']
                and descriptor['status'] != 'out_of_range'
                and (previous or query_revision) != descriptor['source_revision']):
            descriptor['change_notice']='Source projection changed; this page reflects current data. Records may have moved between pages.'
        else:
            observation = None
        for field in ('continuation','outside_continuation'):
            if descriptor[field] is not None:
                descriptor[field].update(result_id=result_id,page_size=meta['page_size'])
        if descriptor['status']=='truncated':
            descriptor['message']='Call result.read with result_id and continuation, keeping the same result session.'
        return frame, observation

    @staticmethod
    def _stamp(frame,result_id,key,revision,changed,*,list_query=False):
        """Expose backing/freshness without changing the originating semantic status."""
        revision=frame['delivery'].get('source_revision',revision)
        frame['delivery'].update(result_id=result_id,view_id=key,
            backing='query' if revision else 'retained',
            consistency='observed-projection' if revision else 'immutable-output',
            count_scope='filtered-artefacts' if revision or list_query else frame['delivery'].get('count_scope', 'produced-output'),
            source_revision=revision,
            change_notice='Source projection changed; this page reflects current data. Records may have moved between pages.' if changed else None)
        payload=frame.get('result')
        if isinstance(payload,dict) and isinstance(payload.get('items'),list):
            if 'returned' in payload:payload['returned']=len(payload['items'])
            if 'truncated' in payload:payload['truncated']=bool(payload['truncated'] or frame['delivery']['has_more'] or frame['delivery']['has_more_outside_selection'])
        return frame

    def _check(self,context,authority):
        """Revalidate current disclosure and the independent exceptional continuation stamp."""
        context.authorisation.service.check_result_delivery(authority['command_id'],authority.get('stamp'))

    @staticmethod
    def _revision(context,location):
        """Observe query inputs through the existing derived-snapshot owner."""
        if location in {'archived', 'all'}:
            return None
        snapshots=context.derived_snapshots
        if snapshots is None or not hasattr(snapshots,'list_revision'):
            raise ValueError('Dynamic list freshness is unavailable in this context.')
        return snapshots.list_revision(location)

    def read(self,context,request):
        """Retrieve under current authority without exposing backend paths or replaying effects."""
        try:
            return self._read(context,request)
        except ResultStoreError as exc:
            raise store_delivery_failure(exc, self.store.limits) from None

    def _read(self,context,request):
        """Resolve a view, refresh authorised live queries, then serve one bounded page."""
        meta=self.store.metadata(self.owner,request.result_id)
        self._check(context,meta['authority'])
        detail_cursor = request.continuation or {}
        detail = request.detail
        inherited_detail = detail_cursor.get('detail')
        if inherited_detail is not None:
            if (detail_cursor.get('result_id') != request.result_id
                    or not isinstance(inherited_detail, str) or not 1 <= len(inherited_detail) <= 128):
                raise ValueError('Detail continuation belongs to another result or is invalid.')
            if detail is not None and detail != inherited_detail:
                raise ValueError('Explicit detail conflicts with its continuation.')
            detail = inherited_detail
        if detail is not None:
            allowed = {'result_id', 'detail', 'view_id', 'selection_id', 'page', 'origin_page',
                       'start_page', 'max_pages', 'page_size'}
            if (not isinstance(detail, str) or not 1 <= len(detail) <= 128
                    or set(detail_cursor) - allowed
                    or (detail_cursor and detail_cursor.get('result_id') != request.result_id)):
                raise ValueError('Invalid detail or continuation.')
        if detail == '@outcome':
            for field in ('page', 'origin_page', 'start_page', 'max_pages', 'page_size'):
                value = detail_cursor.get(field)
                if value is not None and (type(value) is not int or not 1 <= value <= 2**63 - 1):
                    raise ValueError('Outcome continuation counts must be bounded positive integers.')
            size = request.page_size or detail_cursor.get('page_size', 25)
            if type(size) is not int or not 1 <= size <= 100:
                raise ValueError('Outcome page_size must be between 1 and 100.')
            key = 'outcome:' + str(size)
            old_key = detail_cursor.get('view_id')
            if old_key is not None and old_key != 'outcome:' + str(detail_cursor.get('page_size', size)):
                raise ValueError('Outcome continuation belongs to another view.')
            if isinstance(request.page, str):
                origin = detail_cursor.get('origin_page')
                if origin is None and detail_cursor.get('page') is not None:
                    origin = detail_cursor['page'] - 1
                page = resolve_page(request.page, origin)
            else:
                page = detail_cursor.get('page', 1) if request.page is None else request.page
            start = detail_cursor.get('start_page', 1)
            maximum = request.max_pages if request.max_pages is not None else detail_cursor.get('max_pages')
            if maximum == 'all':
                maximum = None
            anchor = request.anchor
            if old_key is not None and old_key != key:
                if anchor is None and request.page != 0:
                    raise ValueError('Changing outcome page size requires an explicit source anchor.')
                if anchor is None:
                    anchor = 0
            cached = self._cached_view(request.result_id, key)
            if cached is None:
                stored = self.store.load(self.owner, request.result_id)['envelope']
                outcome = {k: stored[k] for k in ('error', 'committed_effects', 'warnings') if k in stored}
                if stored.get('status') == 'partial' and isinstance(stored.get('result'), dict):
                    outcome.update(stored['result'])
                header = {k: stored[k] for k in ('schema', 'command', 'command_version', 'status')}
                # Full outcome values belong to the traversable body. Carry only
                # bounded execution truth in the header inherited by every child.
                if 'committed_effects' in outcome:
                    effects = outcome['committed_effects']
                    header['committed_effect_summary'] = {
                        'count': len(effects) if isinstance(effects, (list, dict)) else None,
                        'sha256': hashlib.sha256(json.dumps(effects, sort_keys=True,
                            ensure_ascii=False).encode('utf-8')).hexdigest(),
                    }
                if isinstance(stored.get('error'), dict):
                    header['error'] = {k: stored['error'][k] for k in ('code', 'effects')
                                       if k in stored['error']}
                view = build_view({**header, 'warnings': [], 'result': outcome}, page_size=size)
                self._save(request.result_id, key, view, None)
            frame, _ = self._select(request.result_id, key, page=page, start=start, maximum=maximum, anchor=anchor,
                absolute_selection=type(request.page) is int and request.page > 0)
            for field in ('continuation', 'outside_continuation'):
                if frame['delivery'].get(field) is not None:
                    frame['delivery'][field]['detail'] = '@outcome'
            self._check(context, meta['authority'])
            return self._stamp(frame, request.result_id, key, None, False)
        if detail is not None:
            frame = self.store.read_view_page(self.owner, request.result_id, 'detail:' + detail, 1)
            self._check(context, meta['authority'])
            frame = self._stamp(frame, request.result_id, 'detail:' + detail, None, False)
            next_detail = frame['delivery'].get('next_detail')
            frame['delivery']['continuation'] = ({'result_id': request.result_id, 'detail': next_detail}
                                                 if next_detail is not None else None)
            frame['delivery']['has_more'] = next_detail is not None
            frame['delivery']['outside_continuation'] = None
            frame['delivery']['has_more_outside_selection'] = False
            return frame
        cursor=request.continuation or {}
        allowed={'result_id','view_id','selection_id','page','origin_page','start_page','max_pages','page_size'}
        if set(cursor)-allowed or (cursor and cursor.get('result_id')!=request.result_id):
            raise ValueError('Continuation belongs to another result or has invalid fields.')
        for field in ('page', 'origin_page', 'start_page', 'max_pages', 'page_size'):
            value = cursor.get(field)
            if value is not None and (type(value) is not int or not 1 <= value <= 2**63 - 1):
                raise ValueError('Continuation counts must be bounded positive integers.')
        for field in ('view_id', 'selection_id'):
            value = cursor.get(field)
            if value is not None and (not isinstance(value, str) or not 1 <= len(value) <= 128):
                raise ValueError('Continuation identity is invalid.')
        if cursor and ('page' not in cursor or 'view_id' not in cursor):
            raise ValueError('Continuation requires an addressed page and view.')
        if isinstance(request.page,str):
            origin = cursor.get('origin_page')
            if origin is None and cursor:
                origin = cursor['page'] - 1
            page=resolve_page(request.page, origin)
        else:
            page=cursor.get('page',1) if request.page is None else request.page
        start=cursor.get('start_page',1)
        if type(start) is not int or start<1:raise ValueError('Invalid selection start.')
        maximum=request.max_pages if request.max_pages is not None else cursor.get('max_pages',10 if meta.get('query') else None)
        if maximum == 'all': maximum = None
        if maximum is not None and (type(maximum) is not int or maximum<1):raise ValueError('Invalid selection limit.')
        size=request.page_size or cursor.get('page_size')
        query=meta.get('query')
        if query:
            revision=self._revision(context,query['payload'].get('location','active'))
        else:revision=None
        origin_key=cursor.get('view_id') or meta['authority'].get('default_view')
        origin=self._cached_view(request.result_id,origin_key) if origin_key else None
        key=self._view_key(origin['unit'],size) if origin and size is not None else origin_key
        cached=origin if key==origin_key else self._cached_view(request.result_id,key)
        anchor = request.anchor
        resizing = key != origin_key
        if resizing and anchor is None and page == 0:
            anchor = 0
        if resizing and (cursor or cached is None) and anchor is None:
            raise ValueError('Changing page size requires an explicit source anchor.')
        if not cached or (size is not None and size!=cached['page_size']) or (query and cached.get('source_revision')!=revision):
            if query and query.get('backing', 'query') != 'retained':
                frame=self._refresh(context,query,revision)
            else:
                frame=self.store.load(self.owner,request.result_id)['envelope']
            view=build_view(frame,page_size=size)
            new_key=self._view_key(view.unit,view.page_size)
            anchor = 0 if page == 0 and anchor is None and key!=new_key else anchor
            if key and key!=new_key and anchor is None:
                raise ValueError('Changing page size requires an explicit source anchor.')
            key=new_key
            self._save(request.result_id,key,view,revision)
        frame,observation=self._select(request.result_id,key,page=page,start=start,maximum=maximum,
            query_revision=query['revision'] if query else None,anchor=anchor,
            absolute_selection=type(request.page) is int and request.page > 0)
        changed=bool(frame['delivery']['change_notice'])
        self._check(context,meta['authority'])
        if observation is not None:
            self.store.commit_page_observation(observation)
        return self._stamp(frame,request.result_id,key,frame['delivery']['source_revision'],changed,
                           list_query=query is not None)

    def _refresh(self,context,query,revision):
        """Run a fresh read-only query and reject a mixed projection observation."""
        from _application.registry import current_request_resolver
        from _application.application import CommandApplication
        request=current_request_resolver().resolve(query['command_id'],query['payload'])
        # Refresh is a fresh read with current source authority, never mutation replay.
        fresh=replace(context,invocation_id='result-refresh-'+uuid.uuid4().hex,
                      operation_id=None,result_delivery=self)
        result=CommandApplication(fresh).invoke(request)
        if not isinstance(result, Ok):
            command = request.COMMAND_ID
            if isinstance(result, Error) and result.error.code is ErrorCode.AUTHORISATION_REQUIRED:
                raise ConsentError('authorisation_required',
                    f'Fresh query authorisation for {command} is required. Inspect access.status or use '
                    f'access.request for {command}, then retry result.read. Do not replay the originating command.')
            if isinstance(result, Error) and result.error.code is ErrorCode.AUTHORITY_DENIED:
                raise ConsentError('denied',
                    f'Fresh query authority for {command} is denied. Inspect access.status for {command}, '
                    'then retry result.read only after that authority is restored. Do not replay the originating command.')
            raise ValueError(f'query_refresh_failed: The current {command} source query could not be refreshed. '
                'Inspect vault.check or runtime.status, repair the source, then retry result.read. '
                'Do not replay a mutation to recover output.')
        if self._revision(context,request.location.value)!=revision:
            raise ValueError('Source changed during refresh; retry this page.')
        return canonical_result_envelope(result)

    def clear(self,context):
        """Forget this verified namespace only, preserving its resume credential."""
        context.authorisation.service.check_permission("result.clear")
        try:
            self.store.clear(self.owner)
        except ResultStoreError as exc:
            raise ValueError(str(exc)) from None

    def search(self, context, request):
        """Search retained body units, excluding headers and line/record extras.

        Unit offsets never advance past an unreturned match. Text overlap finds
        literals split across fragments; resume look-behind reads at most 256
        pages in addition to scan_pages forward reads. Details are not traversed:
        immutable view metadata determines coverage on every call, so scan_complete
        may be true while complete remains false. No full-envelope load occurs here.
        """
        from _application.result_navigation import body_fits

        def units_for(frame):
            payload = frame.get('result')
            first = frame.get('delivery', {}).get('first_item') or 1
            if isinstance(payload, dict) and isinstance(payload.get('fragments'), list):
                return [(fragment['text'], {
                    'line': fragment['line'], 'character_start': fragment['start'],
                }) for fragment in payload['fragments']]
            if isinstance(payload, dict) and isinstance(payload.get('content'), str):
                payload = payload['content']
            if isinstance(payload, str):
                return [(line, {'line': first + index, 'character_start': 0})
                        for index, line in enumerate(payload.splitlines())]
            if isinstance(payload, dict) and record_field is not None:
                records = payload[record_field]
            elif isinstance(payload, dict) and isinstance(payload.get('fields'), list):
                records = payload['fields']
            elif isinstance(payload, list):
                records = payload
            else:
                records = [] if payload is None else [payload]
            return [(json.dumps(record, ensure_ascii=False, separators=(',', ':')),
                     {'record': first + index}) for index, record in enumerate(records)]

        try:
            meta = self.store.metadata(self.owner, request.result_id)
            self._check(context, meta['authority'])
            if meta.get('query') and meta['query'].get('backing', 'query') != 'retained':
                raise ValueError('Literal result search is for retained output; use source search for a live query.')
            key = meta['authority'].get('default_view')
            if not key:
                raise ValueError('The retained search view is unavailable.')
            view = self.store.view_metadata(self.owner, request.result_id, key)
            record_field = view['record_field']
            total = view['page_count']
            number, offset = request.start_page, request.start_offset
            matches = []
            scanned = overlap_pages = 0
            details_skipped = view['has_details']
            stopped = False
            tail, tail_line = '', None
            overlap = len(request.literal) - 1
            while number <= total and scanned < request.scan_pages and not stopped:
                self._check(context, meta['authority'])
                frame = self.store.read_view_page(self.owner, request.result_id, key, number)
                scanned += 1
                units = units_for(frame)
                if offset > len(units):
                    raise ValueError('start_offset is outside the retained page; restart at offset 0.')
                while offset < len(units):
                    text, location = units[offset]
                    line = location.get('line')
                    if line != tail_line or line is None:
                        tail = ''
                        tail_line = line
                        expected = location.get('character_start', 0)
                        prior_page, prior_offset, prior_units = number, offset - 1, units
                        while line is not None and expected > 0 and len(tail) < overlap:
                            if prior_offset < 0:
                                if prior_page <= 1 or overlap_pages >= 256:
                                    raise ValueError('Text overlap is unavailable within the search work budget.')
                                prior_page -= 1
                                self._check(context, meta['authority'])
                                prior = self.store.read_view_page(self.owner, request.result_id, key, prior_page)
                                overlap_pages += 1
                                prior_units = units_for(prior)
                                prior_offset = len(prior_units) - 1
                                if prior_offset < 0:
                                    raise ValueError('The retained text fragment sequence is incomplete.')
                            previous_text, previous_location = prior_units[prior_offset]
                            if (previous_location.get('line') != line
                                    or previous_location.get('character_start', 0) + len(previous_text) != expected
                                    or not previous_text):
                                raise ValueError('The retained text fragment sequence is incomplete.')
                            tail = (previous_text + tail)[-overlap:]
                            expected = previous_location.get('character_start', 0)
                            prior_offset -= 1
                    combined = tail + text
                    column = combined.find(request.literal, max(0, len(tail) - len(request.literal) + 1))
                    if column >= 0:
                        match = {'page': number, 'offset': offset,
                                 'column': location.get('character_start', 0) - len(tail) + column,
                                 **location,
                                 'snippet': combined[max(0, column - 40):column + len(request.literal) + 80]}
                        candidate = {'matches': [*matches, match], 'scanned_pages': scanned,
                                     'next_page': number, 'next_offset': offset + 1,
                                     'complete': False, 'scan_complete': False,
                                     'details_skipped': False, 'coverage': 'retained-body-units',
                                     'overlap_pages': 256}
                        if not body_fits(candidate):
                            if not matches:
                                raise ValueError('A matching snippet exceeds the bounded search response.')
                            stopped = True
                            break
                        matches.append(match)
                    tail = combined[-overlap:] if overlap and line is not None else ''
                    offset += 1
                    if len(matches) >= request.limit:
                        stopped = True
                        break
                if offset == len(units):
                    number += 1
                    offset = 0
            exhausted = number > total
            self._check(context, meta['authority'])
            return {'matches': tuple(matches), 'scanned_pages': scanned,
                    'next_page': None if exhausted else number,
                    'next_offset': None if exhausted else offset,
                    'scan_complete': exhausted, 'details_skipped': details_skipped,
                    'coverage': 'retained-body-units',
                    'overlap_pages': overlap_pages, 'complete': exhausted and not details_skipped}
        except ResultStoreError as exc:
            raise store_delivery_failure(exc, self.store.limits) from None
