"""Real-disk result-session isolation, retention and publication behaviour."""
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import shutil
import stat
import time

import pytest

from _command_interface.result_store import FileResultStore, ResultStoreError, ResultStoreLimits


@pytest.fixture
def store(tmp_path):
    root = tmp_path / 'Brain'
    root.mkdir()
    return FileResultStore(root, 'brain', 'alice')


def owner(store):
    credential = store.issue()
    return credential, store.authenticate(credential)


def publish(store, owner_id, command='test.read', body=None):
    ticket = store.admit(owner_id, command, 'invocation')
    result_id = store.publish(owner_id, ticket, body or {'text': 'retained'}, {'scope': 'normal'})
    return result_id


def error(code, call):
    with pytest.raises(ResultStoreError) as caught:
        call()
    assert caught.value.code == code
    return caught.value


def test_resume_hash_only_no_expiry(store, monkeypatch):
    credential, who = owner(store)
    result_id = publish(store, who)
    monkeypatch.setattr(time, 'time', lambda: 999999999999)
    resumed = FileResultStore(store._root, 'brain', 'alice')
    error('invalid_credential', lambda: resumed.load(who, result_id))
    assert resumed.authenticate(credential) == who
    assert resumed.load(who, result_id) == {
        'envelope': {'text': 'retained'}, 'authority': {'scope': 'normal'}, 'query': None}
    raw = b''.join(p.read_bytes() for p in store._directory.rglob('*') if p.is_file())
    assert credential.encode() not in raw
    assert hashlib.sha256(credential.encode()).hexdigest().encode() in raw
    assert len(bytes.fromhex(credential)) == 32


def test_foreign_principal_brain_and_forged_credentials(store):
    credential, who = owner(store)
    result_id = publish(store, who)
    foreign = FileResultStore(store._root, 'brain', 'bob')
    error('invalid_credential', lambda: foreign.authenticate(credential))
    error('invalid_credential', lambda: foreign.load(who, result_id))
    error('invalid_credential', lambda: store.authenticate(who))
    error('invalid_credential', lambda: store.authenticate('f' * 64))
    other_brain = FileResultStore(store._root, 'another', 'alice')
    error('invalid_credential', lambda: other_brain.authenticate(credential))
    assert owner(other_brain)[1] != who


def test_two_agents_same_principal_and_commands_are_independent(store):
    _, first = owner(store)
    _, second = owner(store)
    a = publish(store, first)
    b = publish(store, second)
    c = publish(store, first, 'other.read')
    error('unavailable', lambda: store.load(first, b))
    error('unavailable', lambda: store.metadata(second, a))
    assert store.load(first, a) == store.load(second, b)
    assert store.metadata(first, c)['command_id'] == 'other.read'


def test_admission_order_pending_error_and_late_completion(store):
    _, who = owner(store)
    old = publish(store, who)
    first = store.admit(who, 'test.read', 'first')
    error('unavailable', lambda: store.load(who, old))
    error('unavailable', lambda: store.load(who, first['result_id']))
    second = store.admit(who, 'test.read', 'second')
    error('unavailable', lambda: store.publish(who, first, {}, {}))
    latest = store.publish(who, second, {'status': 'error'}, {})
    assert store.load(who, latest)['envelope']['status'] == 'error'
    assert second['sequence'] == first['sequence'] + 1
    assert not (store._owner_path(who) / old).exists()


def test_tickets_bound_to_owner_command_invocation_sequence_and_once(store):
    _, who = owner(store)
    _, other = owner(store)
    ticket = store.admit(who, 'test.read', 'original')
    for key, value in [('sequence', 99), ('command_id', 'other'), ('invocation_id', 'other'), ('owner_id', other)]:
        error('invalid_ticket', lambda: store.publish(who, {**ticket, key: value}, {}, {}))
    error('unavailable', lambda: store.publish(other, ticket, {}, {}))
    store.publish(who, ticket, {}, {})
    error('invalid_ticket', lambda: store.publish(who, ticket, {}, {}))


def test_clear_revocation_and_reconstructed_authenticated_instance(store):
    credential, who = owner(store)
    resumed = FileResultStore(store._root, 'brain', 'alice')
    resumed.authenticate(credential)
    old = publish(store, who)
    pending = store.admit(who, 'other.read', 'pending')
    store.clear(who)
    error('unavailable', lambda: store.load(who, old))
    error('invalid_ticket', lambda: store.publish(who, pending, {}, {}))
    assert resumed.authenticate(credential) == who
    assert store.admit(who, 'other.read', 'next')['sequence'] == pending['sequence'] + 1
    resumed.revoke(who)
    error('invalid_credential', lambda: store.authenticate(credential))
    error('invalid_credential', lambda: store.admit(who, 'test.read', 'next'))


def test_metadata_and_pages_never_read_whole_body_or_other_pages(store, monkeypatch):
    _, who = owner(store)
    result_id = publish(store, who, body={'text': 'large' * 10000})
    store.save_view(who, result_id, '../../view', [{'text': 'one'}, {'text': 'two'}], {'revision': 'r1'})
    original = store._read
    seen = []
    def checked(path, limit):
        seen.append(path.name)
        assert path.name != 'body.json'
        assert path.name != '1.json'
        return original(path, limit)
    monkeypatch.setattr(store, '_read', checked)
    assert store.metadata(who, result_id)['body_bytes'] > 50000
    assert store.view_metadata(who, result_id, '../../view')['page_count'] == 2
    assert store.read_view_page(who, result_id, '../../view', 2) == {'text': 'two'}
    for page in [0, -1, True, 3]:
        error('page_out_of_range', lambda: store.read_view_page(who, result_id, '../../view', page))
    assert 'body.json' not in seen


def test_private_permissions_and_path_validation(store, tmp_path):
    _, who = owner(store)
    result_id = publish(store, who, command='../../caller/path')
    store.save_view(who, result_id, '/absolute/view', [{'text': 'one'}], {})
    if os.name != 'nt':
        for path in [store._results_directory, store._results_directory / '.lock',
                     store._directory, *store._directory.rglob('*')]:
            assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600)
    error('unavailable', lambda: store.load(who, '../escape'))
    error('unavailable', lambda: store.load('../../owner', result_id))
    result = store._owner_path(who) / result_id
    (result / 'body.json').unlink()
    outside = tmp_path / 'private'
    outside.write_text('{"secret":true}')
    (result / 'body.json').symlink_to(outside)
    error('unsafe_path', lambda: store.load(who, result_id))


def test_symlinked_roots_and_infrastructure_rejected(tmp_path):
    real = tmp_path / 'real'
    real.mkdir()
    link = tmp_path / 'link'
    link.symlink_to(real, target_is_directory=True)
    error('unsafe_path', lambda: FileResultStore(link, 'brain', 'alice'))
    (real / '.brain').symlink_to(tmp_path, target_is_directory=True)
    error('unsafe_path', lambda: FileResultStore(real, 'brain', 'alice'))


@pytest.mark.parametrize('change', ['move', 'copy', 'replacement'])
def test_new_binding_allows_fresh_sessions_without_adopting_old_output(store, tmp_path, change):
    credential, selected = owner(store)
    result_id = publish(store, selected)
    old_key = store._directory.name
    original_bytes = {str(p.relative_to(store._directory)): p.read_bytes()
                      for p in store._directory.rglob('*') if p.is_file()}
    destination = tmp_path / 'relocated'
    if change == 'move':
        store._root.rename(destination)
    elif change == 'copy':
        shutil.copytree(store._root, destination)
    else:
        old = tmp_path / 'retired'
        store._root.rename(old)
        shutil.copytree(old, store._root)
        destination = store._root
        error('scope_mismatch', lambda: store.authenticate(credential))
    fresh = FileResultStore(destination, 'brain', 'alice')
    assert fresh._directory.name != old_key
    error('invalid_credential', lambda: fresh.authenticate(credential))
    new_credential, new_owner = owner(fresh)
    new_result = publish(fresh, new_owner)
    assert fresh.load(new_owner, new_result)['envelope'] == {'text': 'retained'}
    fresh.clear(new_owner)
    assert fresh.authenticate(new_credential) == new_owner
    retired = fresh._results_directory / old_key
    assert {str(p.relative_to(retired)): p.read_bytes()
            for p in retired.rglob('*') if p.is_file()} == original_bytes
    assert (retired / selected / result_id / 'body.json').exists()


@pytest.mark.parametrize('limit', ['max_result_bytes', 'max_owner_bytes', 'max_brain_bytes'])
def test_quota_refusal_no_partial_publication_or_eviction(store, limit):
    _, who = owner(store)
    _, other = owner(store)
    preserved = publish(store, other)
    bounded = FileResultStore(store._root, 'brain', 'alice', {limit: 5000})
    # Authenticate the existing holder without exposing it through files.
    credential = bounded.issue()
    selected = bounded.authenticate(credential)
    ticket = bounded.admit(selected, 'large.read', 'large')
    exc = error('capacity_exceeded', lambda: bounded.publish(selected, ticket, {'text': 'x' * 10000}, {}))
    assert exc.capacity_scope == limit.removeprefix('max_').removesuffix('_bytes')
    assert exc.actual_limit == 5000
    assert exc.representation_scope is None
    error('unavailable', lambda: bounded.load(selected, ticket['result_id']))
    assert store.load(other, preserved)['envelope']['text'] == 'retained'
    assert not list(store._directory.rglob('.tmp-*'))


def test_page_and_view_quota_failure_cleans_partial_map(store):
    credential, who = owner(store)
    result_id = publish(store, who)
    bounded = FileResultStore(store._root, 'brain', 'alice', {'max_page_bytes': 100})
    bounded.authenticate(credential)
    error('representation_too_large', lambda: bounded.save_view(who, result_id, 'large', [{'x': 1}, {'x': 'x' * 500}], {}))
    error('unavailable', lambda: bounded.view_metadata(who, result_id, 'large'))
    assert not list(store._directory.rglob('.tmp-*'))
    assert store.load(who, result_id)['envelope']['text'] == 'retained'


def test_failed_disk_write_is_content_safe_and_cleans_staging(store, monkeypatch):
    _, who = owner(store)
    ticket = store.admit(who, 'test.read', 'one')
    def fail(*args):
        raise OSError('private credential or body')
    with monkeypatch.context() as failure:
        failure.setattr(os, 'fsync', fail)
        exc = error('storage_error', lambda: store.publish(who, ticket, {'secret': 'body'}, {}))
    assert 'private' not in str(exc)
    assert not list(store._directory.rglob('.tmp-*'))
    error('unavailable', lambda: store.load(who, ticket['result_id']))


def _process_admit(root, credential, number):
    store = FileResultStore(root, 'brain', 'alice')
    who = store.authenticate(credential)
    return store.admit(who, 'test.read', str(number))


def test_cross_process_admission_is_serial_and_latest_wins(store):
    credential, who = owner(store)
    with ProcessPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(_process_admit, str(store._root), credential, n) for n in range(9)]
        tickets = [f.result(timeout=30) for f in futures]
    assert sorted(t['sequence'] for t in tickets) == list(range(1, 10))
    latest = max(tickets, key=lambda t: t['sequence'])
    for ticket in tickets:
        if ticket != latest:
            error('unavailable', lambda: store.publish(who, ticket, {}, {}))
    assert store.publish(who, latest, {'latest': True}, {}) == latest['result_id']


def test_threads_independent_instances_share_lock(store):
    credential, who = owner(store)
    with ThreadPoolExecutor(max_workers=4) as pool:
        tickets = list(pool.map(lambda n: _process_admit(str(store._root), credential, n), range(12)))
    assert sorted(t['sequence'] for t in tickets) == list(range(1, 13))


def test_view_refresh_is_atomic_and_failed_refresh_preserves_prior_pages(store, monkeypatch):
    _, who = owner(store)
    result_id = publish(store, who)
    store.save_view(who, result_id, 'view', [{'text': 'old'}], {'revision': 1})
    original = os.replace
    def fail_pointer(source, destination):
        if Path(destination).parent.name == 'views' and str(destination).endswith('.json'):
            raise OSError('private write failure')
        return original(source, destination)
    monkeypatch.setattr(os, 'replace', fail_pointer)
    error('storage_error', lambda: store.save_view(who, result_id, 'view', [{'text': 'new'}], {'revision': 2}))
    assert store.read_view_page(who, result_id, 'view', 1) == {'text': 'old'}
    assert store.view_metadata(who, result_id, 'view')['revision'] == 1
    monkeypatch.setattr(os, 'replace', original)
    store.save_view(who, result_id, 'view', [{'text': 'new'}, {'text': 'second'}], {'revision': 2})
    assert store.read_view_page(who, result_id, 'view', 2) == {'text': 'second'}
    assert store.view_metadata(who, result_id, 'view')['revision'] == 2
    views = store._owner_path(who) / result_id / 'views'
    assert len([p for p in views.iterdir() if p.is_dir()]) == 1
    assert not list(views.glob('.tmp-*'))


def test_index_write_failure_leaves_pending_and_cleans_body(store, monkeypatch):
    _, who = owner(store)
    ticket = store.admit(who, 'test.read', 'one')
    original = os.replace
    def fail_index(source, destination):
        if Path(destination).name == 'slots.json':
            raise OSError('disk full')
        return original(source, destination)
    monkeypatch.setattr(os, 'replace', fail_index)
    error('storage_error', lambda: store.publish(who, ticket, {'outcome': 'committed'}, {}))
    assert not (store._owner_path(who) / ticket['result_id']).exists()
    error('unavailable', lambda: store.load(who, ticket['result_id']))
    assert not list(store._directory.rglob('.tmp-*'))
    monkeypatch.setattr(os, 'replace', original)
    # Retention can retry with the known envelope; it never executes the source.
    store.publish(who, ticket, {'outcome': 'committed'}, {})


def test_result_capacity_includes_page_maps_and_metadata(store):
    credential, who = owner(store)
    result_id = publish(store, who)
    result = store._owner_path(who) / result_id
    initial = store._size(result)
    bounded = FileResultStore(store._root, 'brain', 'alice', {'max_result_bytes': initial + 200})
    bounded.authenticate(credential)
    error('capacity_exceeded', lambda: bounded.save_view(who, result_id, 'map', [{'text': 'a' * 70}] * 3, {}))
    assert store._size(result) == initial
    assert store.load(who, result_id)['envelope']['text'] == 'retained'


@pytest.mark.parametrize('scope', ['owner', 'brain'])
def test_aggregate_capacity_includes_other_commands_and_owners(store, scope):
    credential, who = owner(store)
    preserved = publish(store, who, 'first.read', {'text': 'x' * 1500})
    credential2, other = owner(store)
    limit = store._size(store._owner_path(who) if scope == 'owner' else store._results_directory) + 2000
    bounded = FileResultStore(store._root, 'brain', 'alice', {f'max_{scope}_bytes': limit})
    selected = bounded.authenticate(credential if scope == 'owner' else credential2)
    ticket = bounded.admit(selected, 'second.read', 'second')
    error('capacity_exceeded', lambda: bounded.publish(selected, ticket, {'text': 'x' * 2500}, {}))
    assert store.load(who, preserved)['envelope']['text'] == 'x' * 1500


def test_explicit_clear_works_even_after_limits_are_lowered(store):
    credential, who = owner(store)
    old = publish(store, who, body={'text': 'x' * 10000})
    bounded = FileResultStore(store._root, 'brain', 'alice', {'max_owner_bytes': 2000})
    bounded.authenticate(credential)
    bounded.clear(who)
    assert store._size(store._owner_path(who)) < 2000
    error('unavailable', lambda: store.load(who, old))


def test_missing_binding_fails_closed_and_does_not_adopt_registry(store):
    credential, _ = owner(store)
    (store._directory / 'binding.json').unlink()
    error('scope_mismatch', lambda: store.authenticate(credential))
    error('scope_mismatch', lambda: FileResultStore(store._root, 'brain', 'alice'))


def test_unserialisable_payload_is_content_safe_and_leaves_no_partial_result(store):
    _, who = owner(store)
    ticket = store.admit(who, 'test.read', 'one')
    error('storage_error', lambda: store.publish(who, ticket, {'secret': object()}, {}))
    assert not list(store._directory.rglob('.tmp-*'))
    error('unavailable', lambda: store.metadata(who, ticket['result_id']))


def test_reader_and_clear_are_serialised_without_partial_page_reads(store, monkeypatch):
    from threading import Event
    credential, who = owner(store)
    result_id = publish(store, who)
    store.save_view(who, result_id, 'view', [{'text': 'complete'}], {})
    cleaner = FileResultStore(store._root, 'brain', 'alice')
    cleaner.authenticate(credential)
    reading, release, clearing = Event(), Event(), Event()
    original = store._read
    def held_read(path, limit):
        if path.name == '1.json':
            reading.set()
            if not release.wait(5):
                raise RuntimeError('reader coordination failed')
        return original(path, limit)
    monkeypatch.setattr(store, '_read', held_read)
    def clear():
        clearing.set()
        cleaner.clear(who)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reader = pool.submit(store.read_view_page, who, result_id, 'view', 1)
        try:
            assert reading.wait(5)
            cleanup = pool.submit(clear)
            assert clearing.wait(5)
            assert not cleanup.done()
        finally:
            release.set()
        assert reader.result(timeout=5) == {'text': 'complete'}
        cleanup.result(timeout=5)
    error('unavailable', lambda: store.read_view_page(who, result_id, 'view', 1))


def _crash_before_publication(root, credential, ticket):
    store = FileResultStore(root, 'brain', 'alice')
    who = store.authenticate(credential)
    original = store._atomic
    def crash(path, value, limit, **kwargs):
        if path.name == 'slots.json':
            os._exit(23)
        return original(path, value, limit, **kwargs)
    store._atomic = crash
    store.publish(who, ticket, {'outcome': 'committed'}, {})


def test_crash_after_body_rename_never_reveals_unpublished_output(store):
    import multiprocessing
    credential, who = owner(store)
    ticket = store.admit(who, 'test.read', 'crash')
    process = multiprocessing.get_context('spawn').Process(
        target=_crash_before_publication, args=(str(store._root), credential, ticket))
    process.start()
    process.join(10)
    if process.is_alive():
        process.terminate()
        process.join(5)
        pytest.fail('crash probe did not terminate')
    assert process.exitcode == 23
    resumed = FileResultStore(store._root, 'brain', 'alice')
    assert resumed.authenticate(credential) == who
    error('unavailable', lambda: resumed.load(who, ticket['result_id']))
    resumed.clear(who)
    assert not (store._owner_path(who) / ticket['result_id']).exists()


def test_page_observations_are_independent_and_survive_generation_replacement(store):
    credential, who = owner(store)
    result_id = publish(store, who)
    pages = [{'text': 'first'}, {'text': 'second'}]
    store.save_view(who, result_id, 'dynamic', pages, {'revision': 'r1'})
    assert store.observe_page(who, result_id, 'dynamic', 2, 'r1') is None
    assert store.observe_page(who, result_id, 'dynamic', 1, 'r1') is None
    store.save_view(who, result_id, 'dynamic', pages, {'revision': 'r2'})
    assert store.observe_page(who, result_id, 'dynamic', 2, 'r2') == 'r1'
    assert store.observe_page(who, result_id, 'dynamic', 2, 'r2') == 'r2'
    # Page 2's observation must not consume page 1's independent change notice.
    assert store.observe_page(who, result_id, 'dynamic', 1, 'r2') == 'r1'
    resumed = FileResultStore(store._root, 'brain', 'alice')
    resumed.authenticate(credential)
    resumed.save_view(who, result_id, 'dynamic', pages, {'revision': 'r3'})
    assert resumed.observe_page(who, result_id, 'dynamic', 1, 'r3') == 'r2'
    assert resumed.observe_page(who, result_id, 'dynamic', 2, 'r3') == 'r2'
    assert resumed.load(who, result_id)['envelope'] == {'text': 'retained'}


def test_observations_are_isolated_by_owner_result_view_and_latest_slot(store):
    _, who = owner(store)
    _, other = owner(store)
    result_id = publish(store, who)
    foreign = publish(store, other)
    for selected, result in [(who, result_id), (other, foreign)]:
        for view in ['a', 'b']:
            store.save_view(selected, result, view, [{'text': 'page'}], {})
    assert store.observe_page(who, result_id, 'a', 1, 'one') is None
    assert store.observe_page(who, result_id, 'b', 1, 'two') is None
    assert store.observe_page(other, foreign, 'a', 1, 'three') is None
    error('unavailable', lambda: store.observe_page(other, result_id, 'a', 1, 'foreign'))
    error('page_out_of_range', lambda: store.observe_page(who, result_id, 'a', 2, 'missing'))
    for invalid in [0, -1, True]:
        error('page_out_of_range', lambda: store.observe_page(who, result_id, 'a', invalid, 'invalid'))
    error('invalid_input', lambda: store.observe_page(who, result_id, 'a', 1, 'x' * 1025))
    store.admit(who, 'test.read', 'replacement')
    error('unavailable', lambda: store.observe_page(who, result_id, 'a', 1, 'old'))


def test_observation_failure_preserves_previous_revision_and_cleans_staging(store, monkeypatch):
    _, who = owner(store)
    result_id = publish(store, who)
    store.save_view(who, result_id, 'view', [{'text': 'page'}], {})
    assert store.observe_page(who, result_id, 'view', 1, 'r1') is None
    original = os.replace
    def fail_observation(source, destination):
        if 'observations' in Path(destination).parts:
            raise OSError('private write failure')
        return original(source, destination)
    monkeypatch.setattr(os, 'replace', fail_observation)
    error('storage_error', lambda: store.observe_page(who, result_id, 'view', 1, 'r2'))
    monkeypatch.setattr(os, 'replace', original)
    assert store.observe_page(who, result_id, 'view', 1, 'r2') == 'r1'
    assert not list(store._directory.rglob('.tmp-*'))


def test_observations_count_towards_result_capacity(store):
    credential, who = owner(store)
    result_id = publish(store, who)
    store.save_view(who, result_id, 'view', [{'text': 'page'}], {})
    assert store.observe_page(who, result_id, 'view', 1, 'r1') is None
    size = store._size(store._owner_path(who) / result_id)
    bounded = FileResultStore(store._root, 'brain', 'alice', {'max_result_bytes': size + 10})
    bounded.authenticate(credential)
    error('capacity_exceeded', lambda: bounded.observe_page(who, result_id, 'view', 1, 'r2'))
    assert store.observe_page(who, result_id, 'view', 1, 'r2') == 'r1'
    assert not list(store._directory.rglob('.tmp-*'))


def test_detail_frames_are_separate_bounded_views_without_body_republication(store):
    credential, who = owner(store)
    result_id = publish(store, who, body={'result': {'field': 'large' * 20000}})
    store.save_view(who, result_id, 'summary', [{'detail_id': 'first'}], {})
    for detail_id, text in [('first', 'first detail'), ('second', 'second detail')]:
        frame = {'detail_id': detail_id, 'text': text}
        store.save_view(who, result_id, 'detail:' + detail_id, [frame], {'kind': 'detail'})
        assert store.read_view_page(who, result_id, 'detail:' + detail_id, 1) == frame
    bounded = FileResultStore(store._root, 'brain', 'alice', {'max_page_bytes': 100})
    bounded.authenticate(credential)
    error('representation_too_large', lambda: bounded.save_view(
        who, result_id, 'detail:first', [{'text': 'x' * 1000}], {'kind': 'detail'}))
    assert store.read_view_page(who, result_id, 'detail:first', 1)['text'] == 'first detail'
    assert store.load(who, result_id)['envelope']['result']['field'] == 'large' * 20000


def test_authentication_uses_one_digest_keyed_record_without_listing_owners(store, monkeypatch):
    holders = [owner(store) for _ in range(12)]
    selected_credential, selected = holders[5]
    assert selected == hashlib.sha256(selected_credential.encode('utf-8')).hexdigest()
    original_read = store._read
    observed = []
    def bounded_read(path, limit):
        if path.name == 'owner.json':
            observed.append(path.parent.name)
            assert path.parent.name == selected
        return original_read(path, limit)
    def no_enumeration(*args, **kwargs):
        pytest.fail('authentication enumerated result namespaces')
    monkeypatch.setattr(store, '_read', bounded_read)
    monkeypatch.setattr(Path, 'iterdir', no_enumeration)
    monkeypatch.setattr(os, 'scandir', no_enumeration)
    assert store.authenticate(selected_credential) == selected
    assert observed == [selected]


def test_forged_proof_does_not_read_other_owner_records(store, monkeypatch):
    credential, selected = owner(store)
    original_read = store._read
    observed = []
    def bounded_read(path, limit):
        if path.name == 'owner.json':
            observed.append(path.parent.name)
            assert path.parent.name != selected
        return original_read(path, limit)
    monkeypatch.setattr(store, '_read', bounded_read)
    error('invalid_credential', lambda: store.authenticate('f' * 64))
    error('invalid_credential', lambda: store.authenticate(selected))
    assert len(observed) == 2
    assert credential not in repr(observed)


def test_digest_lookup_checks_record_proof_and_scope_not_just_filename(store):
    credential, selected = owner(store)
    path = store._owner_path(selected) / 'owner.json'
    import json
    original = json.loads(path.read_text())
    for changes in [{'credential_hash': '0' * 64}, {'principal': 'foreign'},
                    {'binding': {**original['binding'], 'brain': 'foreign'}}]:
        path.write_text(json.dumps({**original, **changes}))
        error('invalid_credential', lambda: store.authenticate(credential))
    path.write_text(json.dumps(original))
    assert store.authenticate(credential) == selected


def test_revocation_removes_digest_lookup_and_failed_issue_cleans_endpoint(store, monkeypatch):
    credential, selected = owner(store)
    endpoint = store._directory / hashlib.sha256(credential.encode()).hexdigest()
    store.revoke(selected)
    assert not endpoint.exists()
    error('invalid_credential', lambda: store.authenticate(credential))
    original = store._atomic
    def fail_slots(path, *args, **kwargs):
        if path.name == 'slots.json':
            raise OSError('failed private issuance')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(store, '_atomic', fail_slots)
    error('storage_error', store.issue)
    assert not any(path.is_dir() for path in store._directory.iterdir())


def test_new_brain_identity_keeps_retired_bytes_and_session_isolation(store):
    credential, first = owner(store)
    result_id = publish(store, first, body={'text': 'old identity'})
    snapshot = {str(p.relative_to(store._directory)): p.read_bytes()
                for p in store._directory.rglob('*') if p.is_file()}
    changed = FileResultStore(store._root, 'registered-brain-id', 'alice')
    error('invalid_credential', lambda: changed.authenticate(credential))
    new_credential, second = owner(changed)
    third_credential, third = owner(changed)
    result2 = publish(changed, second, body={'text': 'new identity'})
    result3 = publish(changed, third)
    error('invalid_credential', lambda: store.authenticate(new_credential))
    error('invalid_credential', lambda: store.authenticate(third_credential))
    error('unavailable', lambda: changed.load(second, result3))
    error('unavailable', lambda: changed.load(third, result2))
    resumed = FileResultStore(store._root, 'registered-brain-id', 'alice')
    assert resumed.authenticate(new_credential) == second
    assert resumed.load(second, result2)['envelope']['text'] == 'new identity'
    assert store.load(first, result_id)['envelope']['text'] == 'old identity'
    assert {str(p.relative_to(store._directory)): p.read_bytes()
            for p in store._directory.rglob('*') if p.is_file()} == snapshot


def test_sibling_binding_bytes_and_staging_count_towards_shared_quota(store):
    _, first = owner(store)
    old = publish(store, first, body={'text': 'x' * 10000})
    other = FileResultStore(store._root, 'another-brain', 'alice')
    credential, selected = owner(other)
    # Unfinished writes from a retired binding are not free capacity.
    unfinished = store._directory / '.tmp-abandoned'
    unfinished.write_bytes(b'x' * 1000)
    current_bytes = store._size(store._results_directory)
    bounded = FileResultStore(store._root, 'another-brain', 'alice',
                              {'max_brain_bytes': current_bytes + 2000})
    bounded.authenticate(credential)
    ticket = bounded.admit(selected, 'second.read', 'second')
    error('capacity_exceeded', lambda: bounded.publish(selected, ticket, {'text': 'x' * 2500}, {}))
    assert store.load(first, old)['envelope']['text'] == 'x' * 10000
    assert unfinished.read_bytes() == b'x' * 1000
    assert not list(other._directory.rglob('.tmp-*'))
    # Changing the binding again cannot escape the same aggregate ceiling.
    error('capacity_exceeded', lambda: FileResultStore(
        store._root, 'third-brain', 'alice', {'max_brain_bytes': current_bytes}))
    assert len([p for p in store._results_directory.iterdir() if p.is_dir()]) == 2


def test_legacy_flat_storage_is_untouched_inaccessible_and_counts_towards_quota(store):
    credential, selected = owner(store)
    result_id = publish(store, selected, body={'text': 'legacy' * 2000})
    parent = store._results_directory
    # Recreate the pre-binding-child on-disk layout, without editing its records.
    for child in list(store._directory.iterdir()):
        child.rename(parent / child.name)
    store._directory.rmdir()
    from _command_interface.result_accounting import COUNTER, COUNTER_TEMP, MARKER
    # Filesystem administration invalidates accounting before the next writer.
    (parent / MARKER).write_bytes(b'1')
    snapshot = {str(p.relative_to(parent)): p.read_bytes()
                for p in parent.rglob('*') if p.is_file()
                and p.name not in ('.lock', COUNTER, COUNTER_TEMP, MARKER)}
    fresh = FileResultStore(store._root, 'brain', 'alice')
    error('invalid_credential', lambda: fresh.authenticate(credential))
    assert (parent / selected / result_id / 'body.json').exists()
    assert all((parent / path).read_bytes() == data for path, data in snapshot.items())
    new_credential, new_owner = owner(fresh)
    current_bytes = fresh._size(parent)
    limited = FileResultStore(store._root, 'brain', 'alice', {'max_brain_bytes': current_bytes + 2000})
    limited.authenticate(new_credential)
    ticket = limited.admit(new_owner, 'new.read', 'new')
    error('capacity_exceeded', lambda: limited.publish(new_owner, ticket, {'text': 'x' * 2500}, {}))
    assert all((parent / path).read_bytes() == data for path, data in snapshot.items())


def test_missing_descriptor_is_not_recreated_even_for_empty_existing_binding(store):
    (store._directory / 'binding.json').unlink()
    error('scope_mismatch', lambda: FileResultStore(store._root, 'brain', 'alice'))
    assert not (store._directory / 'binding.json').exists()


def _hold_parent_lock(root, ready, release):
    selected = FileResultStore(root, 'brain', 'alice')
    with selected._locked():
        ready.set()
        if not release.wait(10):
            raise RuntimeError('parent lock coordination failed')


def test_different_bindings_share_cross_process_parent_lock(store, monkeypatch):
    import multiprocessing
    from threading import Event
    from _command_interface import result_store

    sibling = FileResultStore(store._root, 'sibling-brain', 'alice')
    spawn = multiprocessing.get_context('spawn')
    ready, release = spawn.Event(), spawn.Event()
    holder = spawn.Process(target=_hold_parent_lock, args=(str(store._root), ready, release))
    holder.start()
    attempted = Event()
    lock_paths = []
    original = result_store.exclusive_file_lock
    def observe_lock(path, **kwargs):
        lock_paths.append(path)
        attempted.set()
        return original(path, **kwargs)
    monkeypatch.setattr(result_store, 'exclusive_file_lock', observe_lock)
    try:
        assert ready.wait(10)
        with ThreadPoolExecutor(max_workers=1) as pool:
            issuer = pool.submit(sibling.issue)
            try:
                assert attempted.wait(5)
                assert not issuer.done()
                assert lock_paths == [store._results_directory / '.lock']
            finally:
                release.set()
            credential = issuer.result(timeout=10)
        assert sibling.authenticate(credential)
    finally:
        release.set()
        holder.join(10)
        if holder.is_alive():
            holder.terminate()
            holder.join(5)
    assert holder.exitcode == 0


@pytest.mark.parametrize('scope, field, ceiling', [
    ('page', 'max_page_bytes', 100),
    ('metadata', 'max_metadata_bytes', 1024),
])
def test_representation_bounds_have_separate_safe_format_metadata(store, scope, field, ceiling):
    credential, selected = owner(store)
    result_id = publish(store, selected)
    bounded = FileResultStore(store._root, 'brain', 'alice', {field: ceiling})
    bounded.authenticate(credential)
    pages = [{'text': 'x' * 2000}] if scope == 'page' else [{'text': 'small'}]
    metadata = {} if scope == 'page' else {'description': 'x' * 2000}
    exc = error('representation_too_large', lambda: bounded.save_view(
        selected, result_id, 'oversized', pages, metadata))
    assert exc.representation_scope == scope
    assert exc.actual_limit == ceiling and exc.capacity_scope is None
    assert str(store._root) not in str(exc)
    assert 'x' * 100 not in str(exc)
    assert store.load(selected, result_id)['envelope']['text'] == 'retained'
    assert not list(store._directory.rglob('.tmp-*'))


def test_real_result_capacity_is_not_misclassified_as_small_page_format(store):
    credential, selected = owner(store)
    result_id = publish(store, selected)
    ceiling = store._size(store._owner_path(selected) / result_id) + 1000
    bounded = FileResultStore(store._root, 'brain', 'alice', {'max_result_bytes': ceiling})
    bounded.authenticate(credential)
    exc = error('capacity_exceeded', lambda: bounded.save_view(
        selected, result_id, 'fits-format', [{'text': 'x' * 800}], {}))
    assert exc.capacity_scope == 'result' and exc.actual_limit == ceiling
    assert exc.representation_scope is None


def test_body_size_is_retention_capacity_not_a_page_representation_limit(store):
    credential, selected = owner(store)
    bounded = FileResultStore(store._root, 'brain', 'alice', {'max_result_bytes': 4000})
    bounded.authenticate(credential)
    ticket = bounded.admit(selected, 'large.read', 'large')
    exc = error('capacity_exceeded', lambda: bounded.publish(
        selected, ticket, {'content': 'x' * 5000}, {}))
    assert exc.capacity_scope == 'result' and exc.actual_limit == 4000
    assert exc.representation_scope is None
