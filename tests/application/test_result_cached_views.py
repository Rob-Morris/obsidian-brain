"""Known layouts identify absolute reads without decoding retained source bodies."""
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from math import ceil, log2
import json

import pytest

from _application.result.read import ResultReadRequest
from _application.result_navigation import build_view
from _command_interface import result_delivery
from _command_interface.result_delivery import ResultDeliverySession
from _command_interface.result_store import FileResultStore, ResultStoreError


@pytest.fixture
def retained(tmp_path):
    root = tmp_path / 'Brain'
    root.mkdir()
    store = FileResultStore(root, 'brain', 'reader')
    session = ResultDeliverySession(store, store.issue(), issued=True)
    context = SimpleNamespace(authorisation=SimpleNamespace(service=SimpleNamespace(
        check_result_delivery=lambda *args: None)))

    def create(payload):
        envelope = {'schema': 'brain.command-result/1', 'command': 'test.read',
                    'command_version': 1, 'status': 'ok', 'warnings': [], 'result': payload}
        view = build_view(envelope)
        key = session._view_key(view.unit, view.page_size)
        ticket = store.admit(session.owner, 'test.read', 'invocation')
        result_id = store.publish(session.owner, ticket, envelope,
            {'command_id': 'test.read', 'default_view': key})
        session._save(result_id, key, view, None)
        return result_id

    return store, session, context, create


@pytest.mark.parametrize('payload,size', [
    ({'content': ''.join(f'line {i}\n' for i in range(600))}, 50),
    ({'items': [{'id': i} for i in range(250)]}, 10),
])
def test_absolute_and_get_all_reads_reuse_custom_layout(retained, monkeypatch, payload, size):
    store, session, context, create = retained
    result_id = create(payload)
    first = session.read(context, ResultReadRequest(result_id, page=0, page_size=size))
    total = first['delivery']['total_pages']
    assert total > 2 and first['delivery']['page_size'] == size

    def forbidden(*args, **kwargs):
        pytest.fail('unchanged custom navigation decoded or rebuilt the full source')

    monkeypatch.setattr(store, 'load', forbidden)
    monkeypatch.setattr(result_delivery, 'build_view', forbidden)
    for page in (2, 1, 0, 2):
        frame = session.read(context, ResultReadRequest(result_id, page=page,
            continuation=first['delivery']['continuation']))
        assert frame['delivery']['page'] == (page or 1)
        assert frame['delivery']['total_pages'] == total
        assert frame['delivery']['view_id'] == first['delivery']['view_id']
        assert frame['delivery']['page_size'] == size

    with pytest.raises(ValueError, match='explicit source anchor'):
        session.read(context, ResultReadRequest(result_id, page=2, page_size=size))


def test_old_cursor_resize_requires_anchor_even_when_target_is_cached(retained, monkeypatch):
    store, session, context, create = retained
    result_id = create({'items': list(range(250))})
    original = session.read(context, ResultReadRequest(result_id))
    session.read(context, ResultReadRequest(result_id, page=0, page_size=10))

    def forbidden(*args, **kwargs):
        pytest.fail('unanchored cursor resize opened the full source')

    monkeypatch.setattr(store, 'load', forbidden)
    with pytest.raises(ValueError, match='explicit source anchor'):
        session.read(context, ResultReadRequest(result_id,
            continuation=original['delivery']['continuation'], page_size=10))


def test_explicit_default_field_limit_preserves_both_cached_layouts(retained, monkeypatch):
    store, session, context, create = retained
    data = {f'field{i}': i for i in range(40)}
    result_id = create(data)
    compact = session.read(context, ResultReadRequest(result_id))
    explicit = session.read(context, ResultReadRequest(result_id, page=0, page_size=25))
    assert compact['result'] == data and compact['delivery']['total_pages'] == 1
    assert len(explicit['result']['fields']) == 25 and explicit['delivery']['total_pages'] == 2
    assert compact['delivery']['view_id'] != explicit['delivery']['view_id']

    def forbidden(*args, **kwargs):
        pytest.fail('cached field layouts reloaded or rebuilt the source')

    monkeypatch.setattr(store, 'load', forbidden)
    monkeypatch.setattr(result_delivery, 'build_view', forbidden)
    second = session.read(context, ResultReadRequest(result_id, continuation=explicit['delivery']['continuation']))
    assert len(second['result']['fields']) == 15
    assert session.read(context, ResultReadRequest(result_id))['result'] == data


def test_absolute_outside_selection_opens_extent_and_preserves_navigation(retained, monkeypatch):
    store, session, context, create = retained
    result_id = create({'items': list(range(250))})
    first = session.read(context, ResultReadRequest(result_id, page=0, page_size=10, max_pages=3))

    def forbidden(*args, **kwargs):
        pytest.fail('absolute selection navigation reloaded or rebuilt the source')

    monkeypatch.setattr(store, 'load', forbidden)
    monkeypatch.setattr(result_delivery, 'build_view', forbidden)
    moved = session.read(context, ResultReadRequest(result_id, page=5,
        continuation=first['delivery']['continuation']))
    info = moved['delivery']
    assert moved['result']['items'] == list(range(40, 50))
    assert (info['page'], info['start_page'], info['end_page'], info['max_pages']) == (5, 5, 7, 3)
    assert (info['selected_pages'], info['remaining_pages'], info['pages_before_selection'],
            info['pages_after_selection']) == (3, 2, 4, 18)
    assert info['view_id'] == first['delivery']['view_id']
    assert info['selection_id'] != first['delivery']['selection_id']
    cursor = info['continuation']
    assert isinstance(cursor, str) and cursor.startswith('t1_')
    following = session.read(context, ResultReadRequest(result_id, continuation=cursor))
    assert following['result']['items'] == list(range(50, 60))
    assert following['delivery']['selection_id'] == info['selection_id']

    backward = session.read(context, ResultReadRequest(result_id, page=2, continuation=cursor))
    assert backward['result']['items'] == list(range(10, 20))
    assert (backward['delivery']['start_page'], backward['delivery']['end_page']) == (2, 4)
    assert backward['delivery']['selection_id'] != info['selection_id']

    for request in (ResultReadRequest(result_id, page=26, continuation=cursor),
                    ResultReadRequest(result_id, page='-1', continuation=cursor)):
        outside = session.read(context, request)
        assert outside['delivery']['status'] == 'out_of_range'
        assert outside['result'] is None
        assert outside['delivery']['selection_id'] == info['selection_id']
        assert outside['delivery']['total_pages'] == 25


def test_empty_get_all_at_new_size_is_complete(retained):
    _, session, context, create = retained
    result_id = create({'items': []})
    frame = session.read(context, ResultReadRequest(result_id, page=0, page_size=10))
    assert frame['delivery']['status'] == 'complete'
    assert frame['delivery']['total_pages'] == frame['delivery']['total_items'] == 0
    assert frame['delivery']['continuation'] is None


def _assert_bounded_read_reply(frame):
    from copy import deepcopy
    from _application.adapter import AdapterProjection
    from _application.result.read import ResultReadPayload
    from _application.result_navigation import body_fits
    from _application.results import Ok
    from brain_mcp._command_adapter import _tool_result
    from brain_mcp._result_content import canonical_tool_result_size, encoded_tool_result_size
    from _command_interface.script import _guard_cli_result, _cli_result_fits

    source = deepcopy(frame)
    reply = Ok('result.read', 1, ResultReadPayload(source))
    envelope = source
    wire = _tool_result(AdapterProjection(reply, False, 0, envelope)).model_dump(
        by_alias=True, exclude_none=True)
    assert body_fits(source['result'])
    assert canonical_tool_result_size(wire['structuredContent']) <= 8000
    assert encoded_tool_result_size(wire) <= 32768
    assert wire['structuredContent'] == envelope
    assert json.loads(wire['content'][0]['text']) == envelope
    cli, concise = _guard_cli_result(envelope, 'result.read: ok')
    assert cli == envelope and _cli_result_fits(cli, concise)


@pytest.mark.parametrize('items', [[], [{'id': 'one'}]])
def test_stored_empty_records_preserve_last_pass_context_and_invalid_reads_are_null(retained, monkeypatch, items):
    store, session, context, create = retained
    last_pass = {'pass_id': 'maintenance-pass', 'host': 'test-host',
                 'finished_at': '2026-10-21T00:00:00+00:00', 'outcome': 'ok',
                 'blocked': None, 'groups': [{'scope': 'router', 'outcome': 'repaired'}],
                 'counts': {'needs_person': 0, 'claim_expired': 0, 'failed': 0, 'deferred': 0}}
    payload = {'items': items, 'hidden_quiet': 0, 'last_pass': last_pass}
    result_id = create(payload)

    def forbidden(*args, **kwargs):
        pytest.fail('cached empty/context navigation loaded or rebuilt the full source')

    monkeypatch.setattr(store, 'load', forbidden)
    monkeypatch.setattr(result_delivery, 'build_view', forbidden)
    for page in (None, None, 1, 0):
        frame = session.read(context, ResultReadRequest(result_id, page=page))
        assert frame['result'] == payload
        assert frame['result']['last_pass']['outcome'] == 'ok'
        info = frame['delivery']
        assert info['status'] == 'complete' and info['availability'] == 'available'
        assert info['total_items'] == info['total_pages'] == len(items)
        assert info['continuation'] is None and info.get('outside_continuation') is None
        _assert_bounded_read_reply(frame)
    outside = session.read(context, ResultReadRequest(result_id, page=2))
    assert outside['result'] is None
    assert outside['delivery']['status'] == 'out_of_range'
    assert outside['delivery']['total_items'] == outside['delivery']['total_pages'] == len(items)
    _assert_bounded_read_reply(outside)


def test_stored_empty_records_recover_large_context_through_bounded_details(retained, monkeypatch):
    store, session, context, create = retained
    last_pass = {'finished_at': '2026-10-21T00:00:00+00:00', 'outcome': 'ok',
                 'log': '雪\\"😀' * 1500}
    result_id = create({'items': [], 'hidden_quiet': 0, 'last_pass': last_pass})

    def forbidden(*args, **kwargs):
        pytest.fail('empty/context detail navigation loaded or rebuilt the full source')

    monkeypatch.setattr(store, 'load', forbidden)
    monkeypatch.setattr(result_delivery, 'build_view', forbidden)
    for page in (None, None, 0):
        frame = session.read(context, ResultReadRequest(result_id, page=page))
        assert frame['result']['items'] == []
        assert frame['result']['hidden_quiet'] == 0
        assert frame['delivery']['total_items'] == frame['delivery']['total_pages'] == 0
        assert frame['delivery']['status'] == 'complete'
        reference = frame['result']['last_pass']['detail_reference']
        _assert_bounded_read_reply(frame)
    detail = session.read(context, ResultReadRequest(result_id, detail=reference))
    _assert_bounded_read_reply(detail)
    recovered = dict(detail['result'])
    assert recovered['outcome'] == 'ok' and recovered['finished_at'] == last_pass['finished_at']
    address = recovered['log']['detail_reference']
    parts = []
    fragment = session.read(context, ResultReadRequest(result_id, detail=address))
    while True:
        _assert_bounded_read_reply(fragment)
        assert 'fragments' not in fragment['result']
        parts.append(fragment['result']['content'])
        continuation = fragment['delivery']['continuation']
        if continuation is None:
            break
        fragment = session.read(context, ResultReadRequest(result_id, continuation=continuation))
    assert len(parts) > 1
    recovered['log'] = ''.join(parts)
    assert recovered == last_pass
    outside = session.read(context, ResultReadRequest(result_id, page=2))
    assert outside['result'] is None and outside['delivery']['status'] == 'out_of_range'
    _assert_bounded_read_reply(outside)


def test_cached_unit_anchors_reuse_layout_and_disclose_overlap(retained, monkeypatch):
    store, session, context, create = retained
    result_id = create({'items': list(range(250))})
    first = session.read(context, ResultReadRequest(result_id, page=0, page_size=10))

    def forbidden(*args, **kwargs):
        pytest.fail('cached anchor navigation decoded or rebuilt the full source')

    monkeypatch.setattr(store, 'load', forbidden)
    monkeypatch.setattr(result_delivery, 'build_view', forbidden)
    for anchor in (0, 9, 10, 11, 249):
        frame = session.read(context, ResultReadRequest(result_id, anchor=anchor, page_size=10, max_pages=2))
        descriptor = frame['delivery']
        assert descriptor['page'] == descriptor['start_page'] == anchor // 10 + 1
        assert descriptor['view_id'] == first['delivery']['view_id']
        assert descriptor['total_pages'] == 25 and descriptor['total_items'] == 250
        assert descriptor['first_item'] - 1 <= anchor < descriptor['last_item']
        assert descriptor['requested_anchor'] == anchor
        assert bool(descriptor.get('overlap_notice')) is (anchor % 10 != 0)
        assert anchor in frame['result']['items']


def test_explicit_old_cursor_resize_uses_cached_anchor_map(retained, monkeypatch):
    store, session, context, create = retained
    result_id = create({'items': list(range(250))})
    original = session.read(context, ResultReadRequest(result_id))
    session.read(context, ResultReadRequest(result_id, page=0, page_size=10))

    def forbidden(*args, **kwargs):
        pytest.fail('cached cursor resize decoded the full source')

    monkeypatch.setattr(store, 'load', forbidden)
    frame = session.read(context, ResultReadRequest(result_id,
        continuation=original['delivery']['continuation'], page_size=10, anchor=34))
    assert frame['delivery']['page'] == frame['delivery']['start_page'] == 4
    assert frame['delivery']['requested_anchor'] == 34
    assert frame['delivery']['overlap_notice']
    assert frame['result']['items'] == list(range(30, 40))


def test_cached_outcome_anchor_does_not_reload_source(retained, monkeypatch):
    store, session, context, create = retained
    result_id = create({'items': list(range(250))})
    first = session.read(context, ResultReadRequest(result_id, detail='@outcome', page_size=1))

    def forbidden(*args, **kwargs):
        pytest.fail('cached outcome anchor decoded or rebuilt the full source')

    monkeypatch.setattr(store, 'load', forbidden)
    monkeypatch.setattr(result_delivery, 'build_view', forbidden)
    frame = session.read(context, ResultReadRequest(result_id, detail='@outcome', page_size=1, anchor=0))
    assert frame['delivery']['view_id'] == first['delivery']['view_id']
    assert frame['delivery']['total_items'] == first['delivery']['total_items']
    assert frame['delivery']['requested_anchor'] == 0
    assert frame['result'] == first['result']


def test_cached_anchor_uses_logarithmic_bounded_page_reads(retained, monkeypatch):
    store, session, context, create = retained
    result_id = create({'items': list(range(257))})
    first = session.read(context, ResultReadRequest(result_id, page=0, page_size=1))
    original = store._read
    pages = []

    def bounded_read(path, limit):
        assert path.name != 'body.json'
        if path.stem.isdigit():
            pages.append(path)
            assert limit == store.limits.max_page_bytes
        return original(path, limit)

    monkeypatch.setattr(store, '_read', bounded_read)
    frame = session.read(context, ResultReadRequest(result_id, anchor=256, page_size=1))
    assert frame['result']['items'] == [256]
    assert 1 <= len(pages) <= ceil(log2(first['delivery']['total_pages'] + 1))
    assert len({path.parent for path in pages}) == 1


def test_anchor_counts_page_and_observation_share_one_generation_during_shrink(retained, monkeypatch):
    store, session, _, create = retained
    result_id = create({'items': list(range(100))})
    key = session._view_key('records', 10)
    old = build_view({'status': 'ok', 'result': {'items': list(range(100))}}, 10)
    new = build_view({'status': 'ok', 'result': {'items': list(range(30))}}, 10)
    session._save(result_id, key, old, 'old')
    writer = FileResultStore(store._root, 'brain', 'reader')
    other = ResultDeliverySession(writer, session.credential)
    reading, release, refreshing = Event(), Event(), Event()
    original = store._read

    def held_page(path, limit):
        value = original(path, limit)
        if path.stem.isdigit() and not reading.is_set():
            reading.set()
            if not release.wait(5):
                raise RuntimeError('anchor reader coordination failed')
        return value

    def refresh():
        refreshing.set()
        other._save(result_id, key, new, 'new')

    monkeypatch.setattr(store, '_read', held_page)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reader = pool.submit(session._select, result_id, key,
            page=1, start=1, maximum=2, anchor=51, query_revision='opening')
        try:
            assert reading.wait(5)
            update = pool.submit(refresh)
            assert refreshing.wait(5) and not update.done()
        finally:
            release.set()
        frame, observation = reader.result(timeout=5)
        update.result(timeout=5)
    assert frame['delivery']['page'] == 6
    assert frame['delivery']['total_pages'] == 10 and frame['delivery']['total_items'] == 100
    assert frame['delivery']['source_revision'] == observation.revision == 'old'
    assert frame['result']['items'] == list(range(50, 60))
    store.commit_page_observation(observation)
    with pytest.raises(ResultStoreError, match='outside the current view'):
        session._select(result_id, key, page=1, start=1, maximum=2, anchor=51)


def test_text_modes_cache_continuations_and_character_anchors(retained, monkeypatch):
    store, session, context, create = retained
    text = ''.join(f'{i}: café 🧠\n' for i in range(180))
    result_id = create({'content': text})
    original = session.read(context, ResultReadRequest(result_id))
    assert original['delivery']['unit'] == 'bytes'
    lines = session.read(context, ResultReadRequest(result_id, page=0, page_unit='lines', page_size=7))
    assert lines['delivery']['unit'] == 'lines'
    assert lines['result']['content'] == ''.join(text.splitlines(keepends=True)[:7])
    cursor = lines['delivery']['continuation']
    assert isinstance(cursor, str) and cursor.startswith('t1_')
    following = session.read(context, ResultReadRequest(result_id, continuation=cursor))
    assert following['delivery']['unit'] == 'lines' and following['delivery']['page_size'] == 7
    assert following['result']['content_range']['start'] == lines['result']['content_range']['end']
    with pytest.raises(ValueError, match='explicit source anchor'):
        session.read(context, ResultReadRequest(result_id, continuation=cursor, page_unit='bytes'))
    switched = session.read(context, ResultReadRequest(result_id, continuation=cursor,
        page_unit='bytes', page_size=100, anchor=following['result']['content_range']['start']))
    assert switched['delivery']['unit'] == 'bytes'
    bounds = switched['result']['content_range']
    assert bounds['start'] <= following['result']['content_range']['start'] < bounds['end']
    assert len(switched['result']['content'].encode('utf-8')) <= 100
    assert '_text_line' not in switched and '_text_column' not in switched

    def forbidden(*args, **kwargs):
        pytest.fail('cached mode navigation rebuilt or loaded the immutable source')

    monkeypatch.setattr(store, 'load', forbidden)
    monkeypatch.setattr(result_delivery, 'build_view', forbidden)
    again = session.read(context, ResultReadRequest(result_id, page=0, page_unit='lines', page_size=7))
    assert again['result']['content'] == lines['result']['content']
    session.read(context, ResultReadRequest(result_id, continuation=again['delivery']['continuation']))


def test_text_continuation_cannot_reinterpret_missing_or_tampered_units(retained):
    _, session, context, create = retained
    result_id = create({'content': 'abc\n' * 2000})
    initial = session.read(context, ResultReadRequest(result_id))
    cursor = session._cursor.decode(result_id, initial['delivery']['continuation'])
    for changed in ({key: value for key, value in cursor.items() if key != 'page_unit'},
                    {**cursor, 'page_unit': 'lines'}, {**cursor, 'page_size': 12}):
        with pytest.raises(ValueError, match='pin the current page_unit'):
            session.read(context, ResultReadRequest(result_id, continuation=session._cursor.encode(result_id, changed)))


def test_continuation_identity_error_names_field_and_recovery(retained):
    _, session, context, create = retained
    result_id = create({'content': 'a' * 5000})
    first = session.read(context, ResultReadRequest(result_id))
    other_id = create({'content': 'b' * 5000})
    with pytest.raises(ValueError, match='Continuation is invalid.*unchanged.*page 0'):
        session.read(context, ResultReadRequest(other_id, continuation=first['delivery']['continuation']))


@pytest.mark.parametrize('payload,mode', [({'content': 'hello'}, 'records'),
    ({'items': list(range(40))}, 'bytes'), ({'answer': 42}, 'lines')])
def test_incompatible_mode_names_supported_mode(retained, payload, mode):
    _, session, context, create = retained
    result_id = create(payload)
    with pytest.raises(ValueError, match='supported modes'):
        session.read(context, ResultReadRequest(result_id, page_unit=mode, page=0))


def test_search_byte_views_keeps_logical_line_coordinates(retained):
    _, session, context, create = retained
    from _application.result.search import ResultSearchRequest
    text = 'head\n' + 'x' * 9000 + 'needle\nlast needle\n'
    result_id = create({'content': text})
    response = session.search(context, ResultSearchRequest(result_id, literal='needle', scan_pages=20))
    assert [item['line'] for item in response['matches']] == [2, 3]


def test_text_detail_modes_resume_without_replaying_source(retained, monkeypatch):
    store, session, context, create = retained
    text = (('café needle 🧠 ' * 30 + '\n') * 12)
    result_id = create({'log': text})
    opening = session.read(context, ResultReadRequest(result_id))
    detail = opening['result']['log']['detail_reference']
    default = session.read(context, ResultReadRequest(result_id, detail=detail, page_unit='bytes', page_size=4000))
    assert default['delivery']['unit'] == 'bytes'
    first = session.read(context, ResultReadRequest(result_id, detail=detail,
        page=0, page_unit='lines', page_size=5))
    assert first['delivery']['unit'] == 'lines'
    assert first['result']['content'] == ''.join(text.splitlines(keepends=True)[:5])
    cursor = first['delivery']['continuation']
    assert isinstance(cursor, str) and cursor.startswith('t1_')

    def forbidden(*args, **kwargs):
        pytest.fail('cached detail navigation loaded the original source')

    monkeypatch.setattr(store, 'load', forbidden)
    monkeypatch.setattr(result_delivery, 'build_view', forbidden)
    original_metadata = store.view_metadata
    metadata_reads = []

    def counted_metadata(owner, result, key):
        metadata_reads.append(key)
        return original_metadata(owner, result, key)

    monkeypatch.setattr(store, 'view_metadata', counted_metadata)
    monkeypatch.setattr(store, 'read_view_page', forbidden)
    second = session.read(context, ResultReadRequest(result_id, continuation=cursor))
    assert second['result']['content_range']['start'] == len(first['result']['content'])
    assert second['delivery']['page'] == 2
    assert metadata_reads == [first['delivery']['view_id']]


def test_text_detail_honours_default_mode_pages_anchors_and_relative_errors(retained):
    from _application.result.read import execute
    from _application.results import Error
    _, session, context, create = retained
    context.result_delivery = session
    text = '0123456789\n' * 1100
    result_id = create({'log': text})
    detail = session.read(context, ResultReadRequest(result_id))['result']['log']['detail_reference']
    second = session.read(context, ResultReadRequest(result_id, detail=detail, page=2))
    assert second['delivery']['page'] == 2
    bounds = second['result']['content_range']
    assert bounds['start'] > 0
    assert second['result']['content'] == text[bounds['start']:bounds['end']]
    anchored = session.read(context, ResultReadRequest(result_id, detail=detail, anchor=5000))
    bounds = anchored['result']['content_range']
    assert bounds['start'] <= 5000 < bounds['end']
    outside = session.read(context, ResultReadRequest(result_id, detail=detail, page=99999))
    assert outside['delivery']['status'] == 'out_of_range' and outside['result'] is None
    relative = execute(context, ResultReadRequest(result_id, detail=detail, page='+1'))
    assert isinstance(relative, Error) and 'origin' in relative.error.message


def test_linked_detail_view_cannot_be_used_as_ordinary_paged_cursor(retained):
    from _application.result.read import execute
    from _application.results import Error
    _, session, context, create = retained
    context.result_delivery = session
    result_id = create({'log': 'long text\n' * 1000})
    opening = session.read(context, ResultReadRequest(result_id))
    detail = opening['result']['log']['detail_reference']
    cursor = {'result_id': result_id, 'view_id': 'detail:' + detail,
              'page': 1, 'page_unit': 'bytes', 'page_size': 4000}
    response = execute(context, ResultReadRequest(result_id, continuation=session._cursor.encode(result_id, cursor)))
    assert isinstance(response, Error)
    assert 'linked detail' in response.error.message


def test_linked_structured_details_reject_ignored_controls_and_keep_continuation(retained):
    _, session, context, create = retained
    result_id = create({'nested': {f'field{i}': 'x' * 100 for i in range(50)}})
    opening = session.read(context, ResultReadRequest(result_id))
    detail = opening['result']['nested']['detail_reference']
    first = session.read(context, ResultReadRequest(result_id, detail=detail))
    for controls in ({'page': 2}, {'anchor': 10}, {'max_pages': 1}):
        with pytest.raises(ValueError, match='Omit page, anchor and max_pages'):
            session.read(context, ResultReadRequest(result_id, detail=detail, **controls))
    with pytest.raises(ValueError, match='do not carry page coordinates'):
        session.read(context, ResultReadRequest(result_id,
            continuation=session._cursor.encode(result_id, {**session._cursor.decode(result_id, first['delivery']['continuation']), 'page': 2})))
    next_page = session.read(context, ResultReadRequest(result_id, continuation=first['delivery']['continuation']))
    assert next_page['delivery']['page'] == 2


def test_outside_anchor_is_a_corrective_conflict_not_storage_failure(retained):
    from _application.result.read import execute
    from _application.results import Error, ErrorCode
    _, session, context, create = retained
    context.result_delivery = session
    result_id = create({f'field{i}': i for i in range(35)})
    failed = execute(context, ResultReadRequest(result_id, anchor=500))
    assert isinstance(failed, Error) and failed.error.code == ErrorCode.CONFLICT
    assert 'Anchor is outside' in failed.error.message
    assert 'page 0 and no anchor' in failed.error.message
    recovered = session.read(context, ResultReadRequest(result_id, page=0))
    assert recovered['delivery']['status'] == 'complete'


@pytest.mark.parametrize('field,value', [('page', {}), ('start_page', 'bad'),
    ('origin_page', False), ('page_size', []), ('view_id', []),
    ('page', None), ('start_page', None)])
def test_text_detail_rejects_malformed_continuations_at_command_boundary(retained, field, value):
    from _application.result.read import execute
    from _application.results import Error
    _, session, context, create = retained
    context.result_delivery = session
    result_id = create({'log': ('long log line\n' * 500)})
    opening = session.read(context, ResultReadRequest(result_id))
    detail = opening['result']['log']['detail_reference']
    first = session.read(context, ResultReadRequest(result_id, detail=detail,
        page=0, page_unit='lines', page_size=5))
    malformed = {**session._cursor.decode(result_id, first['delivery']['continuation']), field: value}
    result = execute(context, ResultReadRequest(result_id, continuation=session._cursor.encode(result_id, malformed)))
    assert isinstance(result, Error)
    assert 'Continuation' in result.error.message


def test_selection_end_messages_point_to_available_navigation(retained):
    _, session, context, create = retained
    result_id = create({'items': list(range(13))})
    first = session.read(context, ResultReadRequest(result_id, page=0, page_size=4, max_pages=2))
    assert first['delivery']['continuation'] is not None
    assert 'with result_id and continuation' in first['delivery']['message']
    selected_last = session.read(context, ResultReadRequest(result_id,
        continuation=first['delivery']['continuation']))
    info = selected_last['delivery']
    assert info['continuation'] is None and not info['has_more']
    assert info['outside_continuation'] is not None
    assert 'Selection complete' in info['message'] and 'outside_continuation' in info['message']
    later = session.read(context, ResultReadRequest(result_id, continuation=info['outside_continuation']))
    last = session.read(context, ResultReadRequest(result_id, continuation=later['delivery']['continuation']))
    info = last['delivery']
    assert info['continuation'] is None and info.get('outside_continuation') is None
    assert info['pages_before_selection'] == 2 and info['pages_after_selection'] == 0
    assert info['status'] == 'truncated' and not info['has_more']
    assert 'Earlier pages' in info['message'] and 'absolute page' in info['message']


@pytest.mark.parametrize('value', [{}, {'view_id': 'v', 'page': 2}, [], True, 2])
def test_public_continuation_requires_an_opaque_string(value):
    from _application.result.read import decode
    with pytest.raises(ValueError, match='continuation must be the returned opaque token; pass it unchanged'):
        decode({'result_id': 'example-result', 'continuation': value})
