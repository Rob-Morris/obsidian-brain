"""Source execution, private retained output and disclosure at the real adapter boundary."""
from dataclasses import replace
import json
import uuid

import pytest

from _application.adapter import ApplicationAdapter
from _application.registry import current_application_catalogue, current_request_resolver
from _application.consent import ConsentScope
from _application.authorisation import PreparationCoordinator
from _command_interface.result_delivery import ResultDeliverySession
from _command_interface.result_store import FileResultStore
from _command_interface.derived_snapshots import FileDerivedSnapshotStore
from command_application import context_for

TARGET='Designs/project~command-fixture/Command Fixture Design.md'


@pytest.mark.parametrize('location', ['archived', 'all'])
def test_archive_list_snapshot_preserves_selection_without_freshness_work(system, monkeypatch, location):
    root, context, store, credential, invoke = system
    archive = root / '_Archive' / 'Notes'
    archive.mkdir(parents=True, exist_ok=True)
    for number in range(24):
        (archive / f'Archived {number:02}.md').write_text(
            '---\ntype: living/note\ncreated: 2026-10-20\nstatus: initial\n---\nOpening content\n')
    index_path = root / '.brain/local/retrieval-index.json'
    index = json.loads(index_path.read_text())
    index['documents'] = [dict(path=f'Notes/{i:04}.md', title=f'{i:04}',
        created='2026-10-20', type='living/note', status='initial') for i in range(3)]
    index_path.write_text(json.dumps(index))

    def forbidden(*args, **kwargs):
        pytest.fail('retained archive navigation attempted revision or source work')

    # Classification at both admission and publication must avoid archive revision work.
    monkeypatch.setattr(context.derived_snapshots, 'list_revision', forbidden)
    first = invoke('artefact.list', {'location': location, 'page_size': 2, 'sort': 'title'}).structured_content
    assert first['status'] == 'ok', first
    assert first['delivery'].get('backing', 'retained') == 'retained'
    assert first['delivery'].get('consistency', 'immutable-output') == 'immutable-output'
    assert first['delivery']['count_scope'] == 'filtered-artefacts'
    result_id = first['delivery']['result_id']
    owner = store.authenticate(credential)
    load = store.load
    expected = load(owner, result_id)['envelope']['result']['items']
    descriptor = store.metadata(owner, result_id)['query']
    assert descriptor['backing'] == 'retained' and descriptor['revision'] is None
    total = 24 if location == 'archived' else 27
    assert len(expected) == total
    assert first['delivery']['total_items'] == total
    assert first['delivery']['total_pages'] == (total + 1) // 2
    assert first['delivery']['page_size'] == 2
    assert first['delivery']['selected_pages'] == first['delivery']['max_pages'] == 10
    assert first['delivery']['pages_after_selection'] == (total + 1) // 2 - 10
    assert first['delivery']['has_more_outside_selection']

    # Edits and new files after opening cannot change the retained union.
    (archive / 'Archived 00.md').unlink()
    (archive / 'New.md').write_text('---\ntype: living/note\ncreated: 2026-10-21\n---\nNew\n')
    monkeypatch.setattr(ResultDeliverySession, '_refresh', forbidden)
    from _portable import artefact_listing, vault_files
    monkeypatch.setattr(artefact_listing, 'list_combined_from_vault', forbidden)
    monkeypatch.setattr(vault_files, 'list_archived_artefacts', forbidden)
    monkeypatch.setattr(store, 'load', forbidden)

    second = invoke('result.read', {'result_id': result_id,
        'continuation': first['delivery']['continuation']}).structured_content
    assert second['delivery']['max_pages'] == second['delivery']['selected_pages'] == 10
    assert second['delivery']['page_size'] == 2
    assert second['result']['items'] == expected[2:4]
    selected_last = invoke('result.read', {'result_id': result_id, 'page': 10}).structured_content
    outside = invoke('result.read', {'result_id': result_id,
        'continuation': selected_last['delivery']['outside_continuation']}).structured_content
    assert outside['delivery']['start_page'] == outside['delivery']['page'] == 11

    page = invoke('result.read', {'result_id': result_id, 'page': 0, 'max_pages': 'all'}).structured_content
    items = []
    for _ in range((total + 1) // 2):
        delivery = page['delivery']
        assert page['status'] == 'ok'
        assert delivery.get('backing', 'retained') == 'retained'
        assert delivery.get('consistency', 'immutable-output') == 'immutable-output'
        assert delivery['count_scope'] == 'filtered-artefacts'
        assert delivery.get('source_revision') is delivery.get('change_notice') is None
        assert delivery['total_items'] == total
        source = page
        assert source['status'] == 'ok'
        assert any(warning['code'] == 'degraded_capability'
            and f'This {location} filtered artefact list' in warning['message']
            and 'not checked after opening' in warning['message'] for warning in source['warnings'])
        items.extend(source['result']['items'])
        if delivery['continuation'] is None:
            break
        page = invoke('result.read', {'result_id': result_id,
            'continuation': delivery['continuation']}).structured_content
    else:
        pytest.fail('retained list navigation did not finish within its exact page count')
    assert items == expected

    # A new layout reads the opening snapshot, never the changed source query.
    with monkeypatch.context() as resize:
        resize.setattr(store, 'load', load)
        resized = invoke('result.read', {'result_id': result_id,
            'page_size': 3, 'anchor': 0}).structured_content
    assert resized['delivery']['page_size'] == 3
    assert resized['delivery']['max_pages'] == 10
    assert resized['delivery']['total_pages'] == (total + 2) // 3
    assert resized['delivery']['count_scope'] == 'filtered-artefacts'
    assert resized['result']['items'] == expected[:3]
    cached = invoke('result.read', {'result_id': result_id,
        'page_size': 3, 'anchor': 3}).structured_content
    assert cached['result']['items'] == expected[3:6]
    search = invoke('result.search', {'result_id': result_id,
        'literal': 'Archived 00', 'scan_pages': 50}).structured_content
    assert search['status'] == 'ok', search
    assert search['result']['complete']
    assert len(search['result']['matches']) == 1
    assert 'Archived 00' in search['result']['matches'][0]['snippet']


def test_explicit_list_cursor_keeps_legacy_source_window(system, monkeypatch):
    root, context, store, credential, invoke = system
    index_path = root / '.brain/local/retrieval-index.json'
    index = json.loads(index_path.read_text())
    index['documents'] = [dict(path=f'Notes/{i:04}.md', title=f'{i:04}',
        created='2026-10-20', type='living/note', status='initial') for i in range(6)]
    index_path.write_text(json.dumps(index))
    from _portable.artefact_listing import list_combined_from_vault
    first_window = list_combined_from_vault(root, top_k=2, sort='title')
    assert first_window['next_cursor'] is not None

    def forbidden(*args, **kwargs):
        pytest.fail('legacy source cursor entered dynamic query classification')

    monkeypatch.setattr(context.derived_snapshots, 'list_revision', forbidden)
    output = invoke('artefact.list', {'cursor': first_window['next_cursor'],
        'page_size': 2, 'sort': 'title'}).structured_content
    assert output['status'] == 'ok', output
    owner = store.authenticate(credential)
    retained = store.load(owner, output['delivery']['result_id'])
    assert retained['query'] is None
    assert [item['path'] for item in retained['envelope']['result']['items']] == ['Notes/0002.md', 'Notes/0003.md']
    assert retained['envelope']['result']['next_cursor'] is not None
    assert retained['envelope']['result']['truncated']
    assert output['delivery']['status'] == 'complete'
    assert output['result']['truncated']
    assert output['result']['next_cursor'] == retained['envelope']['result']['next_cursor']

@pytest.fixture
def system(command_vault_clone):
    root=command_vault_clone.vault_root
    context=context_for(root,profile='operator',derived_snapshots=FileDerivedSnapshotStore(root))
    store=FileResultStore(root,context.selected_brain.brain_id,context.authorisation.service.identity.principal)
    credential=store.issue()
    adapter=ApplicationAdapter(current_application_catalogue(),current_request_resolver())
    def invoke(command,payload,*,token=credential,operation=None):
        delivery=ResultDeliverySession(store,token)
        invocation=replace(context,invocation_id='test-'+uuid.uuid4().hex,result_delivery=delivery,operation_id=operation)
        return adapter.invoke(invocation,command,payload)
    return root,context,store,credential,invoke


def content(frame):
    value=frame['result']
    assert 'fragments' not in value
    return value['content']


def test_real_read_pages_do_not_reexecute_source_and_cached_read_is_bounded(system,monkeypatch):
    root,context,store,token,invoke=system
    path=root/TARGET
    path.write_text(path.read_text()+'\n'+''.join(f'Line {i}: 雪 and escaped " text\n' for i in range(700)))
    first=invoke('artefact.read',{'reference':TARGET}).structured_content
    assert first['status']=='ok' and first['delivery']['total_pages']>1
    result_id=first['delivery']['result_id']
    expected=store.load(store.authenticate(token),result_id)['envelope']['result']['content']
    def forbidden(*args,**kwargs):pytest.fail('paging reexecuted or reread the full source')
    monkeypatch.setattr(store,'load',forbidden)
    from _portable import artefact_read
    monkeypatch.setattr(artefact_read,'read_from_vault',forbidden)
    parts=[content(first)];cursor=first['delivery']['continuation']
    while cursor:
        page=invoke('result.read',{'result_id':result_id,'continuation':cursor}).structured_content
        assert page['status']=='ok'
        parts.append(content(page))
        cursor=page['delivery']['continuation']
    assert ''.join(parts)==expected


def test_distinct_agents_and_unrelated_commands_preserve_slots(system):
    _,_,store,token,invoke=system
    first=invoke('artefact.read',{'reference':TARGET}).structured_content
    result_id=first['delivery']['result_id']
    invoke('command.describe',{'target_command_id':'artefact.read'})
    assert invoke('result.read',{'result_id':result_id}).structured_content['status']=='ok'
    other=store.issue()
    foreign=invoke('result.read',{'result_id':result_id},token=other).structured_content
    assert foreign['status']=='error' and foreign['result'] is None
    invoke('artefact.read',{'reference':TARGET})
    assert invoke('result.read',{'result_id':result_id}).structured_content['status']=='error'


def test_dynamic_pages_warn_independently_even_when_count_is_unchanged(system):
    root,_,_,_,invoke=system
    index_path=root/'.brain/local/retrieval-index.json'
    index=json.loads(index_path.read_text())
    index['documents']=[{'path':f'Notes/{i:04}.md','title':f'{i:04}',
        'created':'2026-10-20','type':'living/note','status':'initial'} for i in range(220)]
    index_path.write_text(json.dumps(index))
    first=invoke('artefact.list',{'sort':'title'}).structured_content
    result_id=first['delivery']['result_id']
    assert result_id and first['delivery']['total_items']==220
    index['documents'][0]['status']='updated'
    index_path.write_text(json.dumps(index))
    second=invoke('result.read',{'result_id':result_id,'page':2}).structured_content
    one=invoke('result.read',{'result_id':result_id,'page':1}).structured_content
    assert second['delivery']['change_notice'] and one['delivery']['change_notice']
    assert not invoke('result.read',{'result_id':result_id,'page':1}).structured_content['delivery'].get('change_notice')
    assert one['result']['items'][0]['status']=='updated'


def test_current_read_reduction_blocks_body_counts_and_search(system):
    _,context,_,_,invoke=system
    first=invoke('artefact.read',{'reference':TARGET}).structured_content
    result_id=first['delivery']['result_id']
    context.authorisation.service.reduce(commands=('artefact.read',))
    blocked=invoke('result.read',{'result_id':result_id}).structured_content
    assert blocked['status']=='error' and blocked['result'] is None
    assert invoke('result.search',{'result_id':result_id,'literal':'fixture'}).structured_content['status']=='error'


def test_specific_execution_consent_is_spent_but_result_delivery_remains(system):
    root,context,store,token,_=system
    service=context.authorisation.service
    # Narrow initial execution authority, preserving the retrieval controls.
    service.reduce(commands=('document.write-body',))
    from _common import document_revision_at
    request=current_request_resolver().resolve('document.write-body',{
        'document':{'resource':'artefact','reference':TARGET},'expected_revision':document_revision_at(root/TARGET),
        'operation':'append','content':{'source':'inline','content':'\nA once-only addition.\n'}})
    coordinator=PreparationCoordinator(service,current_application_catalogue(),context.authorisation.content_for)
    descriptor=coordinator.prepare(context,request,request_id='prepare-output-test')
    grant=service.request(request_id='approve-output-test',scope=ConsentScope.OPERATION,
        operation_id=descriptor['operation_id'],digest=descriptor['digest'],review=descriptor['review'])
    adapter=ApplicationAdapter(current_application_catalogue(),current_request_resolver())
    execution=replace(context,invocation_id='write-output-once',operation_id=descriptor['operation_id'],
                      result_delivery=ResultDeliverySession(store,token))
    from _application.projection import canonical_wire_value
    produced=adapter.invoke(execution,'document.write-body',canonical_wire_value(request)).structured_content
    assert produced['status']=='ok',produced
    assert service.inspect(descriptor['operation_id'])['state']=='spent'
    result_id=produced['delivery']['result_id']
    def read_result():
        reader=replace(context,invocation_id=uuid.uuid4().hex,operation_id=None,result_delivery=ResultDeliverySession(store,token))
        return adapter.invoke(reader,'result.read',{'result_id':result_id}).structured_content
    assert read_result()['status']=='ok'
    assert (root/TARGET).read_text().count('A once-only addition.')==1
    service.reduce(grant_ids=(grant.grant_id,))
    blocked=read_result()
    assert blocked['status']=='error' and blocked['result'] is None


def test_get_all_resize_and_relative_navigation_preserve_units(system):
    root, _, _, _, invoke = system
    path = root / TARGET
    path.write_text(path.read_text() + '\n' + ''.join(f'Record {i}\n' for i in range(600)))
    first = invoke('artefact.read', {'reference': TARGET}).structured_content
    result_id = first['delivery']['result_id']
    cursor = first['delivery']['continuation']
    next_page = invoke('result.read', {'result_id': result_id, 'continuation': cursor,
                                     'page': '+1'}).structured_content
    assert next_page['delivery']['page'] == 2
    resized = invoke('result.read', {'result_id': result_id, 'page': 0,
                                   'page_size': 50}).structured_content
    assert resized['status'] == 'ok'
    assert resized['delivery']['page'] == 1
    assert resized['delivery']['page_size'] == 50
    assert resized['delivery']['has_more']


def test_query_page_size_and_uncapped_selection_are_explicit(system):
    root, _, _, _, invoke = system
    index_path = root / '.brain/local/retrieval-index.json'
    index = json.loads(index_path.read_text())
    index['documents'] = [dict(path=f'Notes/{i:04}.md', title=f'{i:04}',
        created='2026-10-20', type='living/note', status='initial') for i in range(100)]
    index_path.write_text(json.dumps(index))
    first = invoke('artefact.list', {'page_size': 2}).structured_content
    assert first['delivery']['page_size'] == 2
    assert first['delivery']['selected_pages'] == 10
    assert first['delivery']['pages_after_selection'] == 40
    outside = invoke('result.read', {'result_id': first['delivery']['result_id'],
                                    'page': 11}).structured_content
    info = outside['delivery']
    assert outside['status'] == 'ok' and info['status'] == 'truncated'
    assert (info['page'], info['start_page'], info['end_page'], info['max_pages']) == (11, 11, 20, 10)
    assert (info['pages_before_selection'], info['pages_after_selection'], info['remaining_pages']) == (10, 30, 9)
    assert info['selection_id'] != first['delivery']['selection_id']
    assert isinstance(info['continuation'], str) and info['continuation'].startswith('t1_')
    following = invoke('result.read', {'result_id': first['delivery']['result_id'],
                                     'continuation': info['continuation']}).structured_content
    assert following['delivery']['page'] == 12
    assert following['delivery']['selection_id'] == info['selection_id']
    full = invoke('result.read', {'result_id': first['delivery']['result_id'],
                                 'page': 0, 'max_pages': 'all'}).structured_content
    assert full['delivery']['total_pages'] == 50
    assert 'selected_pages' not in full['delivery']
    assert not full['delivery'].get('has_more_outside_selection', False)
    assert len(full['result']['items']) == 2


def test_control_output_is_bound_to_its_original_owner(system):
    _, context, store, token, invoke = system
    produced = invoke('access.status', {}).structured_content
    assert produced['status'] == 'ok'
    result_id = produced['delivery']['result_id']
    assert result_id
    assert invoke('result.read', {'result_id': result_id}).structured_content['status'] == 'ok'


def test_dynamic_out_of_range_preserves_current_counts(system):
    _, _, _, _, invoke = system
    first = invoke('artefact.list', {}).structured_content
    beyond = invoke('result.read', {'result_id': first['delivery']['result_id'],
                                   'page': first['delivery']['total_pages'] + 1}).structured_content
    assert beyond['status'] == 'ok'
    assert beyond['delivery']['status'] == 'out_of_range'
    assert beyond['delivery']['total_pages'] == first['delivery']['total_pages']


@pytest.mark.parametrize('bad', ['two', 't2_unknown-version', 't1_malformed'])
def test_malformed_continuation_is_a_request_conflict(system, bad):
    _, _, _, _, invoke = system
    first = invoke('artefact.read', {'reference': TARGET}).structured_content
    result_id = first['delivery']['result_id']
    response = invoke('result.read', {'result_id': result_id,
        'continuation': bad}).structured_content
    assert response['status'] == 'error'
    assert response['error']['code'] == 'conflict'


def test_reused_port_does_not_retain_an_early_preflight_refusal(system):
    _, context, store, token, _ = system
    from _application.types import DependencyTier
    delivery = ResultDeliverySession(store, token)
    adapter = ApplicationAdapter(current_application_catalogue(), current_request_resolver())
    first = replace(context, invocation_id=uuid.uuid4().hex, result_delivery=delivery)
    assert adapter.invoke(first, 'artefact.read', {'reference': TARGET}).structured_content['delivery']['result_id']
    second = replace(first, invocation_id=uuid.uuid4().hex, dependency_tier=DependencyTier.BOOTSTRAP)
    refused = adapter.invoke(second, 'artefact.read', {'reference': TARGET}).structured_content
    assert refused['status'] == 'error'
    assert refused['error']['code'] == 'capability_unavailable'
    assert refused['delivery'].get('result_id') is None


def test_revocation_does_not_depend_on_result_retention_capacity(system, monkeypatch):
    _, context, _, _, invoke = system
    def forbidden(*args, **kwargs):
        pytest.fail('non-retained revocation attempted cache admission')
    monkeypatch.setattr(ResultDeliverySession, 'begin', forbidden)
    reduced = invoke('access.reduce', {'reduction': {'kind': 'commands',
                       'commands': ['artefact.read']}}).structured_content
    assert reduced['status'] == 'ok'
    assert reduced['delivery'].get('result_id') is None
    assert context.authorisation.service.state('artefact.read') != 'authorised'


def test_malformed_changed_query_is_not_misclassified_as_missing_authorisation(system):
    root, _, _, _, invoke = system
    first = invoke('artefact.list', {'sort': 'title'}).structured_content
    assert first['status'] == 'ok'
    result_id = first['delivery']['result_id']
    secret = 'private-malformed-source-value'
    (root / '.brain/local/retrieval-index.json').write_text('{"documents": invalid-' + secret)
    failed = invoke('result.read', {'result_id': result_id}).structured_content
    assert failed['status'] == 'error'
    assert failed['error']['code'] == 'conflict'
    assert failed['result'] is None
    message = failed['error']['message']
    assert 'query_refresh_failed' in message
    assert 'vault.check' in message and 'runtime.status' in message
    assert 'access.request' not in message
    assert secret not in str(failed)


def test_spent_query_grant_delivers_cached_output_but_changed_query_needs_source_authority(system, monkeypatch):
    root, context, store, token, invoke = system
    service = context.authorisation.service
    service.reduce(commands=('artefact.list',))
    request = current_request_resolver().resolve('artefact.list', {'sort': 'title'})
    coordinator = PreparationCoordinator(service, current_application_catalogue(), context.authorisation.content_for)
    descriptor = coordinator.prepare(context, request, request_id='prepare-specific-list')
    service.request(request_id='approve-specific-list', scope=ConsentScope.OPERATION,
        operation_id=descriptor['operation_id'], digest=descriptor['digest'], review=descriptor['review'])
    from _application.projection import canonical_wire_value
    adapter = ApplicationAdapter(current_application_catalogue(), current_request_resolver())
    execution = replace(context, invocation_id='specific-list-output', operation_id=descriptor['operation_id'],
                        result_delivery=ResultDeliverySession(store, token))
    first = adapter.invoke(execution, 'artefact.list', canonical_wire_value(request)).structured_content
    assert first['status'] == 'ok', first
    assert service.inspect(descriptor['operation_id'])['state'] == 'spent'
    result_id = first['delivery']['result_id']
    from _portable import artefact_listing
    def forbidden(*args, **kwargs):
        pytest.fail('cached output or unauthorised fresh query reran source enumeration')
    monkeypatch.setattr(artefact_listing, 'list_combined_from_vault', forbidden)
    cached = invoke('result.read', {'result_id': result_id}).structured_content
    assert cached['status'] == 'ok'
    assert cached['result']['items'] == first['result']['items']
    path = root / '.brain/local/retrieval-index.json'
    index = json.loads(path.read_text())
    index['documents'][0]['title'] = 'Changed current query title'
    path.write_text(json.dumps(index))
    denied = invoke('result.read', {'result_id': result_id}).structured_content
    assert denied['status'] == 'error'
    assert denied['error']['code'] == 'authorisation_required'
    assert denied['result'] is None
    message = denied['error']['message']
    assert 'artefact.list' in message and 'Fresh query' in message
    assert 'access.status' in message and 'access.request' in message and 'retry result.read' in message
    assert service.inspect(descriptor['operation_id'])['state'] == 'spent'


@pytest.mark.parametrize('text', ['', '雪 😀 "quoted" \\ tail\n', ('雪😀"\\' * 6000) + '\nlast\n'],
                         ids=['empty', 'short-unicode', 'oversized-unicode-line'])
def test_full_text_has_one_content_value_contiguous_bounds_and_exact_counts(system, text):
    root, _, _, _, invoke = system
    path = root / 'Notes' / 'Delivery Unicode.md'
    path.parent.mkdir(exist_ok=True)
    path.write_text(text, encoding='utf-8')
    first = invoke('vault.read-file', {'path': 'Notes/Delivery Unicode.md'}).structured_content
    assert first['status'] == 'ok', first.get('error')
    result_id = first['delivery']['result_id']
    total_pages = first['delivery']['total_pages']
    assert total_pages == 0 if not text else total_pages >= 1
    page = first
    pieces = []
    offset = 0
    for number in range(1, max(1, total_pages) + 1):
        payload = page['result']
        assert page['command'] == 'vault.read-file'
        assert page['command_version'] == first['command_version']
        assert 'envelope' not in payload and 'fragments' not in payload
        assert 'range' not in payload and 'next_cursor' not in payload
        chunk = payload['content']
        assert payload['content_range'] == {'start': offset, 'end': offset + len(chunk), 'total': len(text)}
        assert payload['revision'] == first['result']['revision']
        info = page['delivery']
        assert info['unit'] == 'bytes'
        assert info['total_items'] == len(text.encode('utf-8'))
        assert info['total_pages'] == total_pages
        assert info['page'] == number
        assert info['remaining_pages'] == max(0, total_pages - number)
        assert info['has_more'] == (number < total_pages)
        assert len(json.dumps(page, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) <= 8000
        pieces.append(chunk)
        offset += len(chunk)
        if info['continuation'] is None:
            assert number == max(1, total_pages)
            break
        page = invoke('result.read', {'result_id': result_id, 'continuation': info['continuation']}).structured_content
        assert page['status'] == 'ok', page.get('error')
    assert ''.join(pieces) == text
    assert ''.join(pieces).encode('utf-8') == path.read_bytes()
