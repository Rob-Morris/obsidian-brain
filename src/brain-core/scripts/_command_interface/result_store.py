"""Private, durable result namespaces; disclosure authority remains with the caller.

Owner IDs are public selectors. A store instance must issue or authenticate the
holder credential before using an owner. Reconstructing a store does not restore
that proof. ``clear`` removes output; ``revoke`` also invalidates the credential.
No clock, receipt expiry, source execution or consent policy belongs here.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
from threading import RLock

from _common._file_lock import MutationLockError, exclusive_file_lock
from _bootstrap.private_files import protect_dir
from .result_accounting import COUNTER, COUNTER_TEMP, FileResultAccounting, ResultAccountingError

RESULT_DIRECTORY = Path('.brain/local/results')
_ID = re.compile(r'[0-9a-f]{64}\Z')
_ENCODER = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(',', ':'))


class ResultStoreError(ResultAccountingError):
    """Content-safe failure with optional primitive capacity/representation metadata.

    capacity_scope is brain/owner/result only for retention capacity failures.
    representation_scope is page/metadata for format bounds. actual_limit is a
    bounded positive byte ceiling; neither paths nor source values are included.
    """


@dataclass(frozen=True)
class ResultStoreLimits:
    """Positive byte ceilings for retained bodies, staged writes and view state.

    Owner and Brain budgets include metadata and unfinished files. Page and
    metadata limits bound individual reads; none of these limits imply expiry.
    """

    max_result_bytes: int = 256 * 1024 * 1024
    max_owner_bytes: int = 1024 * 1024 * 1024
    max_brain_bytes: int = 4 * 1024 * 1024 * 1024
    max_page_bytes: int = 32768
    max_metadata_bytes: int = 65536

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in vars(self).values()):
            raise ResultStoreError('invalid_input', 'Storage limits must be positive integers.')


def _digest(value: str) -> str:
    try:
        return hashlib.sha256(value.encode('utf-8')).hexdigest()
    except UnicodeError:
        raise ResultStoreError('invalid_input', 'Identifiers must contain valid Unicode.') from None


def _windows_extended_path(absolute: str) -> str:
    """Keep all cache operations independent of the Windows long-path opt-in."""
    if absolute.startswith('\\\\?\\'):
        return absolute
    return '\\\\?\\UNC\\' + absolute[2:] if absolute.startswith('\\\\') else '\\\\?\\' + absolute


def _bounded_text(value, limit=1024):
    try:
        return isinstance(value, str) and 0 < len(value.encode('utf-8')) <= limit
    except UnicodeError:
        return False


def _identifier(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ResultStoreError('unavailable', 'Retained output is unavailable.')
    return value


@dataclass(frozen=True)
class ResultPageObservation:
    """Internal staged observation of a validated physical page, never authority."""

    owner_id: str
    result_id: str
    view_id: str
    generation: str
    page: int
    revision: str
    previous_revision: str | None


class FileResultStore:
    """Retain eligible output independently of receipts and consent lifetimes.

    Callers authenticate holder credentials and check current disclosure authority
    before delivering stored data. Supply only sanitised, retention-eligible
    envelopes and authority/query descriptors; this store cannot infer eligibility.
    """

    def __init__(self, vault_root, brain_id: str, principal: str,
                 limits: ResultStoreLimits | dict | None = None):
        """Open private local storage bound to this Brain, root and principal.

        Each binding has an independent child namespace. Moving/replacing the
        root or changing Brain identity permits fresh sessions without adopting
        old credentials or deleting old bytes. All children share the parent
        lock and aggregate quota. Reopening still requires holder proof.
        """
        try:
            self.limits = (ResultStoreLimits(**limits) if isinstance(limits, dict)
                           else ResultStoreLimits() if limits is None else limits)
        except TypeError:
            raise ResultStoreError('invalid_input', 'Invalid storage limits.') from None
        if not isinstance(self.limits, ResultStoreLimits):
            raise ResultStoreError('invalid_input', 'Invalid storage limits.')
        if not all(isinstance(v, str) and v for v in (brain_id, principal)):
            raise ResultStoreError('invalid_input', 'Brain and principal are required.')
        absolute_root = os.path.abspath(vault_root)
        self._root = Path(_windows_extended_path(absolute_root) if os.name == 'nt' else absolute_root)
        self._results_directory = self._root / RESULT_DIRECTORY
        self._accounting = FileResultAccounting(self._results_directory,
            brain_bytes=self.limits.max_brain_bytes, owner_bytes=self.limits.max_owner_bytes,
            result_bytes=self.limits.max_result_bytes)
        self._principal = _digest(principal)
        self._brain = _digest(brain_id)
        self._authenticated: set[str] = set()
        self._lock = RLock()
        self._initialising = True
        try:
            self._safe(self._root, directory=True)
            root_stat = self._root.stat()
            self._binding = {'root': str(self._root), 'device': root_stat.st_dev,
                             'inode': root_stat.st_ino, 'brain': self._brain}
            binding_key = _digest(_ENCODER.encode(self._binding))
            self._directory = self._results_directory / binding_key
            for path in (self._root / '.brain', self._root / '.brain/local', self._results_directory):
                self._mkdir(path)
            with self._locked():
                created = not self._directory.exists()
                self._mkdir(self._directory)
                path = self._directory / 'binding.json'
                if path.exists():
                    if self._read(path, self.limits.max_metadata_bytes) != self._binding:
                        raise ResultStoreError('scope_mismatch', 'Result storage belongs to a different Brain root.')
                else:
                    # A missing descriptor never authorises adoption of existing bytes.
                    if not created:
                        raise ResultStoreError('scope_mismatch', 'Result storage binding is unavailable.')
                    try:
                        self._atomic(path, self._binding, self.limits.max_metadata_bytes)
                    except BaseException:
                        if created:
                            self._remove(self._directory)
                        raise
            self._initialising = False
        except (OSError, ValueError):
            raise ResultStoreError('storage_error', 'Result storage could not be opened safely.') from None

    def _safe(self, path: Path, *, directory=False):
        for parent in reversed((path, *path.parents)):
            info = parent.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise ResultStoreError('unsafe_path', 'Result storage contains an unsafe path.')
        info = path.lstat()
        if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
            raise ResultStoreError('unsafe_path', 'Result storage has an invalid file type.')
        if not directory and info.st_nlink != 1:
            raise ResultStoreError('unsafe_path', 'Result storage contains a linked file.')

    def _mkdir(self, path):
        if path.exists() or path.is_symlink():
            self._safe(path, directory=True)
        if self._accounting._active and path.is_relative_to(self._results_directory):
            self._accounting.mkdir(path)
        protect_dir(path)
        self._safe(path, directory=True)

    @contextmanager
    def _locked(self):
        try:
            with self._lock:
                self._safe(self._results_directory, directory=True)
                current = self._root.stat()
                if (current.st_dev, current.st_ino) != (self._binding['device'], self._binding['inode']):
                    raise ResultStoreError('scope_mismatch', 'The Brain root has changed.')
                lock = self._results_directory / '.lock'
                if lock.exists() or lock.is_symlink():
                    self._safe(lock)
                with exclusive_file_lock(lock, timeout=30, create_parent=False, follow_symlinks=False), self._accounting.transaction():
                    if self._directory.exists() or self._directory.is_symlink():
                        self._safe(self._directory, directory=True)
                    binding = self._directory / 'binding.json'
                    if not binding.exists() and not self._initialising:
                        raise ResultStoreError('scope_mismatch', 'The Brain root binding is unavailable.')
                    if binding.exists() and self._read(binding, self.limits.max_metadata_bytes) != self._binding:
                        raise ResultStoreError('scope_mismatch', 'The Brain root binding has changed.')
                    yield
        except ResultStoreError:
            raise
        except ResultAccountingError as exc:
            raise ResultStoreError(exc.code, exc.message, capacity_scope=exc.capacity_scope,
                actual_limit=exc.actual_limit, representation_scope=exc.representation_scope) from None
        except MutationLockError:
            raise ResultStoreError('storage_busy', 'Result storage is busy; retry delivery.') from None
        except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
            raise ResultStoreError('storage_error', 'Retained output could not be stored or read safely.') from None

    def _read(self, path, limit):
        self._safe(path)
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ResultStoreError('unavailable', 'Stored result metadata or page exceeds its limit.')
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ResultStoreError('unavailable', 'Stored result data is invalid.')
        return value

    def _write(self, path, value, limit, *, enforce=True, representation_scope='metadata'):
        chunks = (chunk.encode('utf-8') for chunk in _ENCODER.iterencode(value))
        return self._accounting.write(path, chunks, limit, enforce=enforce,
                                      representation_scope=representation_scope)

    def _sync(self, directory):
        if os.name != 'nt':
            fd = os.open(directory, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def _atomic(self, path, value, limit, *, enforce_quota=True):
        temporary = path.parent / ('.tmp-' + secrets.token_hex(16))
        try:
            self._write(temporary, value, limit, enforce=enforce_quota)
            if path.exists() or path.is_symlink():
                self._safe(path)
            self._accounting.replace(temporary, path, enforce=enforce_quota)
        finally:
            self._accounting.remove(temporary)

    def _size(self, directory):
        if self._accounting._active:
            return self._accounting.total(directory)
        with self._locked():
            return self._accounting.total(directory)

    def _quota(self, owner=None, result=None):
        for scope, path, limit in (
            ('result', result, self.limits.max_result_bytes),
            ('owner', owner, self.limits.max_owner_bytes),
            ('brain', self._results_directory, self.limits.max_brain_bytes),
        ):
            if path is not None and self._accounting.total(path) > limit:
                raise ResultStoreError('capacity_exceeded',
                    'Retention capacity exceeded; explicitly clear output or raise storage limits.',
                    capacity_scope=scope, actual_limit=limit)

    def _owner_path(self, owner_id):
        return self._directory / _identifier(owner_id)

    def _owner(self, owner_id):
        path = self._owner_path(owner_id)
        if owner_id not in self._authenticated:
            raise ResultStoreError('invalid_credential', 'A valid result-session credential is required.')
        try:
            record = self._read(path / 'owner.json', self.limits.max_metadata_bytes)
        except FileNotFoundError:
            raise ResultStoreError('invalid_credential', 'The result session is unavailable or revoked.') from None
        if record.get('principal') != self._principal or record.get('binding') != self._binding:
            raise ResultStoreError('invalid_credential', 'The result session is unavailable or revoked.')
        return path

    def issue(self) -> str:
        """Create a distinct owner and return its one-time holder credential.

        The digest of the random 32-byte credential is the public owner ID and
        directory key; raw credentials are never persisted. Issuance establishes
        proof on this instance. Callers keep the one-time returned value private
        for resumption; presenting the public digest cannot authenticate it.
        """
        with self._locked():
            credential = secrets.token_hex(32)
            digest = _digest(credential)
            owner_id = digest
            owner = self._owner_path(owner_id)
            self._mkdir(owner)
            try:
                self._atomic(owner / 'owner.json', {'credential_hash': digest,
                             'principal': self._principal, 'binding': self._binding},
                             self.limits.max_metadata_bytes)
                self._atomic(owner / 'slots.json', {'slots': {}}, self.limits.max_metadata_bytes)
            except BaseException:
                self._remove(owner)
                raise
            self._authenticated.add(owner_id)
            return credential

    def authenticate(self, credential: str) -> str:
        """Check one digest-keyed owner record without scanning other sessions.

        The supplied credential must hash to the owner-directory key and match
        that record's digest, principal and Brain/root binding. A public owner ID
        is not holder proof. Success enables the owner only on this instance;
        neither source authority nor consent is renewed. Explicit revocation
        removes the lookup endpoint, including for already-authenticated stores.
        """
        if not isinstance(credential, str) or not _ID.fullmatch(credential):
            raise ResultStoreError('invalid_credential', 'A valid result-session credential is required.')
        digest = _digest(credential)
        with self._locked():
            path = self._owner_path(digest)
            try:
                record = self._read(path / 'owner.json', self.limits.max_metadata_bytes)
            except FileNotFoundError:
                raise ResultStoreError('invalid_credential', 'A valid result-session credential is required.') from None
            if (secrets.compare_digest(record.get('credential_hash', ''), digest)
                    and record.get('principal') == self._principal
                    and record.get('binding') == self._binding):
                self._authenticated.add(digest)
                return digest
        raise ResultStoreError('invalid_credential', 'A valid result-session credential is required.')

    def _slots(self, owner):
        return self._read(owner / 'slots.json', self.limits.max_metadata_bytes)

    def admit(self, owner_id: str, command_id: str, invocation_id: str) -> dict:
        """Reserve the owner's latest command slot before source execution.

        Admission atomically assigns a monotonic sequence and immediately makes
        the previous result unavailable, even while the new outcome is pending.
        Return the ticket unchanged to publish; late completions cannot replace
        a newer admission, and unrelated command slots remain independent.
        """
        if not all(_bounded_text(v) for v in (command_id, invocation_id)):
            raise ResultStoreError('invalid_input', 'Bounded command and invocation IDs are required.')
        with self._locked():
            owner = self._owner(owner_id)
            index = self._slots(owner)
            key = _digest(command_id)
            previous = index['slots'].get(key)
            ticket = {'result_id': secrets.token_hex(32),
                      'sequence': previous['sequence'] + 1 if previous else 1,
                      'command_id': command_id, 'invocation_id': invocation_id,
                      'owner_id': owner_id}
            index['slots'][key] = {**ticket, 'state': 'pending'}
            self._atomic(owner / 'slots.json', index, self.limits.max_metadata_bytes)
            if previous:
                self._remove(owner / previous['result_id'])
            return ticket

    def _remove(self, path):
        self._accounting.remove(path)

    def _latest(self, owner, result_id):
        _identifier(result_id)
        for slot in self._slots(owner)['slots'].values():
            if slot['result_id'] == result_id:
                return slot
        raise ResultStoreError('unavailable', 'The result is superseded or unavailable.')

    def _result(self, owner_id, result_id):
        owner = self._owner(owner_id)
        slot = self._latest(owner, result_id)
        if slot['state'] != 'published':
            raise ResultStoreError('unavailable', 'Retained output is pending or unavailable.')
        path = owner / _identifier(result_id)
        self._safe(path, directory=True)
        return owner, path

    def publish(self, owner_id: str, ticket: dict, envelope: dict, authority: dict,
                query: dict | None = None) -> str:
        """Publish eligible output once against the exact latest pending ticket.

        Bodies and descriptors are staged privately within all byte budgets;
        the slot becomes readable only after complete files are installed. A
        stale or used ticket fails closed. Callers own sanitisation, retention
        eligibility and source-outcome finalisation: a storage error must never
        trigger source execution again or alter its committed outcome.
        """
        if (not isinstance(ticket, dict) or not isinstance(envelope, dict)
                or not isinstance(authority, dict) or (query is not None and not isinstance(query, dict))):
            raise ResultStoreError('invalid_input', 'Result publication requires mappings.')
        with self._locked():
            owner = self._owner(owner_id)
            slot = self._latest(owner, ticket.get('result_id'))
            expected = {key: value for key, value in slot.items() if key != 'state'}
            if ticket != expected or slot['state'] != 'pending':
                raise ResultStoreError('invalid_ticket', 'The publication ticket is invalid or already used.')
            result_id = ticket['result_id']
            path = owner / result_id
            staging = owner / ('.tmp-' + secrets.token_hex(16))
            self._mkdir(staging)
            try:
                body_bytes = self._write(staging / 'body.json',
                                         {'envelope': envelope, 'authority': authority, 'query': query},
                                         None, representation_scope=None)
                self._write(staging / 'metadata.json', {**expected, 'state': 'published',
                            'body_bytes': body_bytes, 'authority': authority, 'query': query},
                            self.limits.max_metadata_bytes)
                self._quota(owner, staging)
                self._sync(staging)
                self._accounting.replace(staging, path)
                self._sync(owner)
                index = self._slots(owner)
                index['slots'][_digest(slot['command_id'])]['state'] = 'published'
                self._atomic(owner / 'slots.json', index, self.limits.max_metadata_bytes)
            except BaseException:
                self._remove(staging)
                # A failed index update must leave a pending slot, never readable bytes.
                if self._latest(owner, result_id)['state'] == 'pending':
                    self._remove(path)
                raise
            return result_id

    def metadata(self, owner_id: str, result_id: str) -> dict:
        """Read bounded descriptors after checking owner proof and latest-slot identity.

        The full envelope is never opened. Returned authority and query data are
        descriptors for caller-owned disclosure checks, not a grant of access;
        superseded, pending and cleared results cannot alias newer output.
        """
        with self._locked():
            _, path = self._result(owner_id, result_id)
            return self._read(path / 'metadata.json', self.limits.max_metadata_bytes)

    def load(self, owner_id: str, result_id: str) -> dict:
        """Decode the immutable envelope, authority and query for a published result.

        Owner proof and latest-slot identity are checked under the storage lock.
        This reads the full retained body within its result limit; callers must
        enforce current disclosure policy before delivering any of its contents.
        """
        with self._locked():
            _, path = self._result(owner_id, result_id)
            return self._read(path / 'body.json', self.limits.max_result_bytes)

    def save_view(self, owner_id: str, result_id: str, view_id: str,
                  pages: list[dict], metadata: dict) -> None:
        """Install a complete view generation without changing the original body.

        Each page has its own bounded file. Atomic pointer replacement selects
        the new generation after every page is written; failed staging preserves
        the prior map. Quotas include overlapping generations until cleanup.
        Metadata stays small and gains an exact page_count; per-page revision
        observations survive replacement because they live outside generations.
        """
        if (not _bounded_text(view_id)
                or not isinstance(pages, list) or not all(isinstance(p, dict) for p in pages)
                or not isinstance(metadata, dict)):
            raise ResultStoreError('invalid_input', 'A view requires an ID, page mappings and metadata.')
        with self._locked():
            owner, result = self._result(owner_id, result_id)
            views = result / 'views'
            self._mkdir(views)
            pointer = views / (_digest(view_id) + '.json')
            previous = self._read(pointer, self.limits.max_metadata_bytes) if pointer.exists() else None
            generation = secrets.token_hex(32)
            target = views / generation
            staging = views / ('.tmp-' + secrets.token_hex(16))
            self._mkdir(staging)
            try:
                self._write(staging / 'metadata.json', {**metadata, 'page_count': len(pages)},
                            self.limits.max_metadata_bytes)
                for number, page in enumerate(pages, 1):
                    self._write(staging / f'{number}.json', page, self.limits.max_page_bytes,
                                representation_scope='page')
                self._quota(owner, result)
                self._sync(staging)
                self._accounting.replace(staging, target)
                self._sync(views)
                self._atomic(pointer, {'generation': generation}, self.limits.max_metadata_bytes)
            except BaseException:
                # An fsync error can follow a successful pointer replacement.
                current = self._read(pointer, self.limits.max_metadata_bytes) if pointer.exists() else None
                if current != {'generation': generation}:
                    self._remove(target)
                raise
            finally:
                self._remove(staging)
            if previous:
                self._remove(views / _identifier(previous['generation']))

    def _view(self, owner_id, result_id, view_id):
        if not _bounded_text(view_id):
            raise ResultStoreError('invalid_input', 'A bounded view ID is required.')
        _, result = self._result(owner_id, result_id)
        pointer = result / 'views' / (_digest(view_id) + '.json')
        if not pointer.exists():
            raise ResultStoreError('unavailable', 'The retained view is unavailable.')
        current = self._read(pointer, self.limits.max_metadata_bytes)
        path = pointer.parent / _identifier(current['generation'])
        self._safe(path, directory=True)
        return path

    def view_metadata(self, owner_id: str, result_id: str, view_id: str) -> dict:
        """Read bounded metadata from the selected generation without opening pages.

        The same owner and latest-result checks as body reads apply. Metadata
        and page_count describe one selected generation; separate subsequent
        calls may observe a later refresh of that view.
        """
        with self._locked():
            path = self._view(owner_id, result_id, view_id)
            return self._read(path / 'metadata.json', self.limits.max_metadata_bytes)

    def read_view_selection(self, owner_id: str, result_id: str, view_id: str,
                            page: int, *, observe: bool = False,
                            anchor: int | None = None) -> tuple[dict, dict, ResultPageObservation | None]:
        """Read metadata and one addressed frame from the same locked generation.

        Empty and out-of-range selections return the generation's outcome-only
        frame so callers can report its current counts without a second read.
        Optional observation stages this generation's revision and previous
        page observation without writing state. Commit it only after the final
        disclosure check. Empty and out-of-range frames stage no observation.
        A zero-based character (text) or unit (records/fields) anchor selects
        its containing page by binary search
        under this same lock; no full body or separate-generation map is read.
        Anchor zero selects page one of an empty view.
        """
        if type(page) is not int or page < 1:
            raise ResultStoreError('page_out_of_range', 'A positive stored page number is required.')
        if anchor is not None and (type(anchor) is not int or anchor < 0):
            raise ResultStoreError('invalid_input', 'An anchor must be a non-negative unit position.')
        with self._locked():
            path = self._view(owner_id, result_id, view_id)
            metadata = self._read(path / 'metadata.json', self.limits.max_metadata_bytes)
            if anchor is None:
                frame = (self._read(path / f'{page}.json', self.limits.max_page_bytes)
                         if page <= metadata['page_count'] else dict(metadata['empty']))
            elif metadata['page_count'] == 0 and anchor == 0:
                page, frame = 1, dict(metadata['empty'])
            else:
                low, high = 1, metadata['page_count']
                frame = None
                while low <= high:
                    middle = (low + high) // 2
                    candidate = self._read(path / f'{middle}.json', self.limits.max_page_bytes)
                    payload = candidate.get('result')
                    content_range = (payload.get('content_range')
                                     if isinstance(payload, dict) and isinstance(payload.get('content'), str) else None)
                    if content_range is not None:
                        first, last = content_range['start'] + 1, content_range['end']
                    else:
                        bounds = candidate['delivery']
                        first, last = bounds['first_item'], bounds['last_item']
                    if anchor + 1 < first:
                        high = middle - 1
                    elif anchor + 1 > last:
                        low = middle + 1
                    else:
                        page, frame = middle, candidate
                        break
                if frame is None:
                    raise ResultStoreError('anchor_out_of_range',
                        'Anchor is outside the current view; supply an explicit restart position.')
            if anchor is not None:
                frame['delivery'] = {**frame['delivery'], 'page': page}
            observation = None
            if observe and page <= metadata['page_count']:
                revision = metadata.get('source_revision')
                if not _bounded_text(revision):
                    raise ResultStoreError('invalid_input', 'A bounded, non-empty revision ID is required.')
                _, result = self._result(owner_id, result_id)
                previous = self._page_observation(result, view_id, page)
                observation = ResultPageObservation(owner_id, result_id, view_id,
                    path.name, page, revision, previous)
            return metadata, frame, observation

    def commit_page_observation(self, observation: ResultPageObservation) -> None:
        """Commit an authorised snapshot even if its view has since refreshed.

        Owner proof and latest-result identity still apply; physical bounds were
        checked when staging. A competing accepted observation wins rather than
        being overwritten by a delayed commit based on an earlier observation.
        This internal value is not a credential or a public transport token.
        """
        with self._locked():
            owner, result = self._result(observation.owner_id, observation.result_id)
            current = self._page_observation(result, observation.view_id, observation.page)
            if current == observation.previous_revision:
                self._observe_page(owner, result, observation.view_id,
                                   observation.page, observation.revision)

    def read_view_page(self, owner_id: str, result_id: str, view_id: str, page: int) -> dict:
        """Read one positive, absolute page from a coherent selected generation.

        Selection, range validation and the bounded page read share the lock
        with refresh and cleanup. Neither the full envelope nor other pages are
        opened, and unavailable pages never silently select another page.
        """
        if type(page) is not int or page < 1:
            raise ResultStoreError('page_out_of_range', 'A positive stored page number is required.')
        with self._locked():
            path = self._view(owner_id, result_id, view_id)
            metadata = self._read(path / 'metadata.json', self.limits.max_metadata_bytes)
            if page > metadata['page_count']:
                raise ResultStoreError('page_out_of_range', 'The requested page is outside the stored view.')
            return self._read(path / f'{page}.json', self.limits.max_page_bytes)

    def observe_page(self, owner_id: str, result_id: str, view_id: str,
                     page: int, revision: str) -> str | None:
        """Atomically advance one page's observation, independent of view refreshes.

        Return the previous revision, or None for this page's first observation.
        Only the addressed page advances; unchanged observations avoid a write.
        Call only after accepting delivery. Non-secret revision IDs are limited
        to 1,024 UTF-8 bytes, and the separate state file counts towards quotas.
        Owner proof, current page bounds and latest-result identity are checked
        under the lock; observing a page grants no disclosure authority.
        """
        if type(page) is not int or page < 1:
            raise ResultStoreError('page_out_of_range', 'A positive stored page number is required.')
        if not _bounded_text(revision):
            raise ResultStoreError('invalid_input', 'A bounded, non-empty revision ID is required.')
        with self._locked():
            owner, result = self._result(owner_id, result_id)
            view = self._view(owner_id, result_id, view_id)
            metadata = self._read(view / 'metadata.json', self.limits.max_metadata_bytes)
            if page > metadata['page_count']:
                raise ResultStoreError('page_out_of_range', 'The requested page is outside the stored view.')
            return self._observe_page(owner, result, view_id, page, revision)

    def _observe_page(self, owner, result, view_id, page, revision):
        """Advance validated page state while the caller holds the store lock."""
        observations = result / 'observations'
        self._mkdir(observations)
        pages = observations / _digest(view_id)
        self._mkdir(pages)
        state = pages / f'{page}.json'
        limit = min(8192, self.limits.max_metadata_bytes)
        previous = self._page_observation(result, view_id, page)
        if previous != revision:
            self._atomic(state, {'revision': revision}, limit)
        return previous

    def _page_observation(self, result, view_id, page):
        """Read page history without creating state; caller holds the store lock."""
        state = result / 'observations' / _digest(view_id) / f'{page}.json'
        limit = min(8192, self.limits.max_metadata_bytes)
        return self._read(state, limit)['revision'] if state.exists() else None

    def clear(self, owner_id: str) -> None:
        """Invalidate all owner results and pending tickets, then remove retained files.

        Credentials and command sequences remain valid for future admissions.
        Invalidation precedes deletion under the shared lock, so readers cannot
        observe partial cleanup. Cleanup can proceed after quotas are lowered;
        it neither revokes source consent nor removes source-outcome receipts.
        """
        with self._locked():
            owner = self._owner(owner_id)
            # Keep monotonic sequences but invalidate every result, including pending tickets.
            index = self._slots(owner)
            for slot in index['slots'].values():
                slot['state'] = 'cleared'
            self._atomic(owner / 'slots.json', index, self.limits.max_metadata_bytes,
                         enforce_quota=False)
            for path in owner.iterdir():
                if path.name not in ('owner.json', 'slots.json', COUNTER, COUNTER_TEMP):
                    self._remove(path)

    def revoke(self, owner_id: str) -> None:
        """Explicitly end a result namespace and invalidate its holder credential.

        The owner directory is atomically removed from the live namespace before
        its files are deleted. Other authenticated instances recheck ownership
        on every operation and therefore cannot reuse their earlier proof.
        Source permissions, consent and outcome receipts are outside this store.
        """
        with self._locked():
            owner = self._owner(owner_id)
            tombstone = self._directory / ('.revoked-' + secrets.token_hex(16))
            self._accounting.replace(owner, tombstone, enforce=False)
            self._sync(self._directory)
            self._authenticated.discard(owner_id)
            self._remove(tombstone)
