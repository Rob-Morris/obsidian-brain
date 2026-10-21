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
        frame = session.read(context, ResultReadRequest(result_id, page=page, page_size=size))
        assert frame['delivery']['page'] == (page or 1)
        assert frame['delivery']['total_pages'] == total
        assert frame['delivery']['view_id'] == first['delivery']['view_id']
        assert frame['delivery']['page_size'] == size


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
    assert (cursor['page'], cursor['start_page'], cursor['max_pages']) == (6, 5, 3)
    assert cursor['selection_id'] == info['selection_id']
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
    from _application.projection import canonical_result_envelope
    from _application.result.read import ResultReadPayload
    from _application.result_navigation import body_fits
    from _application.results import Ok
    from brain_mcp._command_adapter import _tool_result
    from brain_mcp._result_content import canonical_tool_result_size, encoded_tool_result_size
    from _command_interface.script import _guard_cli_result, _cli_result_fits

    source = deepcopy(frame)
    delivery = source.pop('delivery')
    reply = Ok('result.read', 1, ResultReadPayload(source))
    envelope = {**canonical_result_envelope(reply), 'delivery': delivery}
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
        assert info['continuation'] is None and info['outside_continuation'] is None
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
    while address:
        fragment = session.read(context, ResultReadRequest(result_id, detail=address))
        _assert_bounded_read_reply(fragment)
        parts.extend(item['text'] + item['line_ending'] for item in fragment['result']['fragments'])
        continuation = fragment['delivery']['continuation']
        address = continuation['detail'] if continuation else None
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
        assert bool(descriptor['overlap_notice']) is (anchor % 10 != 0)
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
