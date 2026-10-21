"""Selection bodies, counts and revision belong to one stored generation."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest

from _application.result_navigation import build_view
from _application.result.read import ResultReadRequest
from _application.consent import ConsentError
from _command_interface.result_delivery import ResultDeliverySession
from _command_interface.result_store import FileResultStore, ResultStoreError


@pytest.fixture
def retained(tmp_path):
    root = tmp_path / 'Brain'
    root.mkdir()
    store = FileResultStore(root, 'brain', 'alice')
    credential = store.issue()
    session = ResultDeliverySession(store, credential)
    ticket = store.admit(session.owner, 'test.read', 'invocation')
    result_id = store.publish(session.owner, ticket, {'result': {}}, {})
    return store, session, credential, result_id


def save(session, result_id, items, revision):
    view = build_view({'status': 'ok', 'result': {'items': items}}, page_size=1)
    session._save(result_id, 'view', view, revision)


def accepted_selection(session, result_id, **selection):
    frame, observation = session._select(result_id, 'view', **selection)
    if observation is not None:
        session.store.commit_page_observation(observation)
    return frame


def accept_page(store, owner, result_id, page):
    metadata, frame, observation = store.read_view_selection(owner, result_id, 'view', page, observe=True)
    if observation is not None:
        store.commit_page_observation(observation)
    return metadata, frame, observation.previous_revision if observation else None


def test_refresh_cannot_mix_selection_counts_body_or_revision(retained, monkeypatch):
    store, session, credential, result_id = retained
    save(session, result_id, ['old-a', 'old-b'], 'old')
    accept_page(store, session.owner, result_id, 1)
    writer_store = FileResultStore(store._root, 'brain', 'alice')
    writer = ResultDeliverySession(writer_store, credential)
    reading, release, refreshing = Event(), Event(), Event()
    original = store._read

    def held_metadata(path, limit):
        value = original(path, limit)
        if path.name == 'metadata.json' and 'page_count' in value:
            reading.set()
            if not release.wait(5):
                raise RuntimeError('reader coordination failed')
        return value

    def refresh():
        refreshing.set()
        save(writer, result_id, ['new-a', 'new-b', 'new-c'], 'new')

    monkeypatch.setattr(store, '_read', held_metadata)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reader = pool.submit(session._select, result_id, 'view', page=1, start=1,
                             maximum=None, query_revision='opening')
        try:
            assert reading.wait(5)
            update = pool.submit(refresh)
            assert refreshing.wait(5)
            assert not update.done()
        finally:
            release.set()
        first, observation = reader.result(timeout=5)
        update.result(timeout=5)
    assert observation is None
    assert first['result']['items'] == ['old-a']
    assert first['delivery']['total_items'] == first['delivery']['total_pages'] == 2
    assert first['delivery']['source_revision'] == 'old'
    assert first['delivery']['change_notice'] is None
    second = accepted_selection(session, result_id, page=1, start=1, maximum=None,
                             query_revision='opening')
    assert second['result']['items'] == ['new-a']
    assert second['delivery']['total_items'] == second['delivery']['total_pages'] == 3
    assert second['delivery']['source_revision'] == 'new'
    assert second['delivery']['change_notice']
    assert not accepted_selection(session, result_id, page=1, start=1, maximum=None,
                               query_revision='opening')['delivery']['change_notice']
    stamped = session._stamp(second, result_id, 'view', 'stale-before-read', False)
    assert stamped['delivery']['source_revision'] == 'new'


@pytest.mark.parametrize('items,page,status', [([], 0, 'complete'), ([], 1, 'complete'),
                                              (['one'], 2, 'out_of_range')])
def test_empty_and_out_of_range_selections_keep_generation_metadata(retained, items, page, status):
    _, session, _, result_id = retained
    save(session, result_id, items, 'current')
    frame, observation = session._select(result_id, 'view', page=page, start=1, maximum=None)
    assert observation is None
    assert frame['result'] == ({'items': []} if not items else None)
    assert frame['delivery']['status'] == status
    assert frame['delivery']['total_items'] == frame['delivery']['total_pages'] == len(items)
    assert frame['delivery']['source_revision'] == 'current'


def test_atomic_selection_reads_no_full_body_or_other_pages(retained, monkeypatch):
    store, session, _, result_id = retained
    save(session, result_id, ['first', 'second', 'third'], 'current')
    original = store._read

    def bounded_read(path, limit):
        assert path.name not in {'body.json', '1.json', '3.json'}
        return original(path, limit)

    monkeypatch.setattr(store, '_read', bounded_read)
    metadata, frame, previous = store.read_view_selection(session.owner, result_id, 'view', 2)
    assert metadata['page_count'] == 3
    assert frame['result']['items'] == ['second']
    assert previous is None


def test_atomic_selection_rechecks_owner_and_latest_result(retained):
    store, session, _, result_id = retained
    save(session, result_id, ['first'], 'current')
    foreign = store.authenticate(store.issue())
    with pytest.raises(ResultStoreError, match='unavailable'):
        store.read_view_selection(foreign, result_id, 'view', 1)
    store.admit(session.owner, 'test.read', 'new-invocation')
    with pytest.raises(ResultStoreError, match='superseded'):
        store.read_view_selection(session.owner, result_id, 'view', 1)


@pytest.mark.parametrize('items,page,start,maximum', [([], 1, 1, None),
    (['one'], 2, 1, None), (['one', 'two'], 2, 1, 1), (['one', 'two'], 1, 2, None)])
def test_undelivered_pages_never_advance_observations(retained, items, page, start, maximum):
    store, session, _, result_id = retained
    save(session, result_id, items, 'current')
    frame, observation = session._select(result_id, 'view', page=page, start=start, maximum=maximum,
                    query_revision='opening')
    assert observation is None
    save(session, result_id, ['one', 'two'], 'later')
    _, _, previous = accept_page(store, session.owner, result_id, page)
    assert previous is None


def test_read_survives_shrink_after_atomic_observation_and_uses_observed_revision(retained, monkeypatch):
    store, session, _, _ = retained
    ticket = store.admit(session.owner, 'artefact.list', 'query')
    result_id = store.publish(session.owner, ticket, {'result': {}},
        {'command_id': 'artefact.list', 'default_view': 'view'},
        {'payload': {}, 'revision': 'opening'})
    save(session, result_id, ['one', 'two'], 'current')
    original = store.read_view_selection

    def shrink_after_selection(*args, **kwargs):
        selected = original(*args, **kwargs)
        save(session, result_id, ['shrunk'], 'later')
        return selected

    monkeypatch.setattr(store, 'read_view_selection', shrink_after_selection)
    monkeypatch.setattr(session, '_revision', lambda *args: 'current')
    context = SimpleNamespace(authorisation=SimpleNamespace(service=SimpleNamespace(
        check_result_delivery=lambda *args: None)))
    frame = session.read(context, ResultReadRequest(result_id, page=2))
    assert frame['result']['items'] == ['two']
    assert frame['delivery']['total_pages'] == frame['delivery']['total_items'] == 2
    assert frame['delivery']['source_revision'] == 'current'
    assert frame['delivery']['change_notice']
    metadata, _, previous = original(session.owner, result_id, 'view', 2, observe=True)
    assert metadata['page_count'] == 1
    assert previous is None
    save(session, result_id, ['one', 'two'], 'restored')
    _, _, observation = original(session.owner, result_id, 'view', 2, observe=True)
    assert observation.previous_revision == 'current'


def test_observations_are_per_page_and_use_stored_revision(retained):
    store, session, _, result_id = retained
    save(session, result_id, ['one', 'two'], 'first')
    _, _, previous = accept_page(store, session.owner, result_id, 1)
    assert previous is None
    save(session, result_id, ['edited', 'two'], 'second')
    for page, expected in [(1, 'first'), (2, None), (1, 'second'), (2, 'second')]:
        metadata, _, previous = accept_page(store, session.owner, result_id, page)
        assert metadata['source_revision'] == 'second'
        assert previous == expected


def test_read_uses_generation_revision_when_refresh_wins_before_selection(retained, monkeypatch):
    store, session, _, _ = retained
    ticket = store.admit(session.owner, 'artefact.list', 'query')
    result_id = store.publish(session.owner, ticket, {'result': {}},
        {'command_id': 'artefact.list', 'default_view': 'view'},
        {'payload': {}, 'revision': 'opening'})
    save(session, result_id, ['old'], 'opening')
    accept_page(store, session.owner, result_id, 1)
    original = store.read_view_selection

    def refresh_before_selection(*args, **kwargs):
        save(session, result_id, ['new', 'second'], 'new-revision')
        return original(*args, **kwargs)

    monkeypatch.setattr(store, 'read_view_selection', refresh_before_selection)
    monkeypatch.setattr(session, '_revision', lambda *args: 'opening')
    context = SimpleNamespace(authorisation=SimpleNamespace(service=SimpleNamespace(
        check_result_delivery=lambda *args: None)))
    frame = session.read(context, ResultReadRequest(result_id))
    assert frame['result']['items'] == ['new']
    assert frame['delivery']['total_items'] == frame['delivery']['total_pages'] == 2
    assert frame['delivery']['source_revision'] == 'new-revision'
    assert frame['delivery']['change_notice']
    metadata, _, observation = original(session.owner, result_id, 'view', 1, observe=True)
    assert metadata['source_revision'] == observation.previous_revision == 'new-revision'


def test_final_disclosure_denial_preserves_page_change_warning(retained, monkeypatch):
    store, session, _, _ = retained
    ticket = store.admit(session.owner, 'artefact.list', 'query')
    result_id = store.publish(session.owner, ticket, {'result': {}},
        {'command_id': 'artefact.list', 'default_view': 'view'},
        {'payload': {}, 'revision': 'opening'})
    save(session, result_id, ['original'], 'opening')
    accept_page(store, session.owner, result_id, 1)
    save(session, result_id, ['changed'], 'current')
    monkeypatch.setattr(session, '_revision', lambda *args: 'current')
    checks = 0

    def deny_final_check(*args):
        nonlocal checks
        checks += 1
        if checks == 2:
            raise ConsentError('authorisation_required', 'Disclosure ended before delivery.')

    service = SimpleNamespace(check_result_delivery=deny_final_check)
    context = SimpleNamespace(authorisation=SimpleNamespace(service=service))
    with pytest.raises(ConsentError, match='Disclosure ended'):
        session.read(context, ResultReadRequest(result_id))
    _, _, pending = store.read_view_selection(session.owner, result_id, 'view', 1, observe=True)
    assert pending.previous_revision == 'opening'
    service.check_result_delivery = lambda *args: None
    accepted = session.read(context, ResultReadRequest(result_id))
    assert accepted['delivery']['change_notice']
    assert accepted['delivery']['source_revision'] == 'current'
    assert not session.read(context, ResultReadRequest(result_id))['delivery']['change_notice']


def test_staging_observation_is_read_only_and_commits_snapshot_after_shrink(retained):
    store, session, _, result_id = retained
    save(session, result_id, ['one', 'two'], 'snapshot')
    _, _, pending = store.read_view_selection(session.owner, result_id, 'view', 2, observe=True)
    _, _, repeated = store.read_view_selection(session.owner, result_id, 'view', 2, observe=True)
    assert pending == repeated
    assert pending.previous_revision is None
    save(session, result_id, [], 'shrunk')
    store.commit_page_observation(pending)
    save(session, result_id, ['one', 'two'], 'restored')
    _, _, restored = store.read_view_selection(session.owner, result_id, 'view', 2, observe=True)
    assert restored.previous_revision == 'snapshot'


def test_delayed_commit_does_not_overwrite_competing_accepted_observation(retained):
    store, session, _, result_id = retained
    save(session, result_id, ['old'], 'old')
    _, _, old = store.read_view_selection(session.owner, result_id, 'view', 1, observe=True)
    save(session, result_id, ['new'], 'new')
    _, _, new = store.read_view_selection(session.owner, result_id, 'view', 1, observe=True)
    store.commit_page_observation(new)
    store.commit_page_observation(old)
    _, _, current = store.read_view_selection(session.owner, result_id, 'view', 1, observe=True)
    assert current.previous_revision == 'new'


@pytest.mark.parametrize('invalidate', ['clear', 'supersede', 'revoke'])
def test_staged_commit_cannot_restore_invalidated_result(retained, invalidate):
    store, session, _, result_id = retained
    save(session, result_id, ['one'], 'current')
    _, _, pending = store.read_view_selection(session.owner, result_id, 'view', 1, observe=True)
    if invalidate == 'supersede':
        store.admit(session.owner, 'test.read', 'next')
    else:
        getattr(store, invalidate)(session.owner)
    with pytest.raises(ResultStoreError):
        store.commit_page_observation(pending)
