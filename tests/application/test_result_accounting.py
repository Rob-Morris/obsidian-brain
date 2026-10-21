"""Durable quota counters, reservations and recovery on real private files."""
from contextlib import contextmanager
import json
import os
from pathlib import Path

import pytest

from _common._file_lock import exclusive_file_lock
from _command_interface.result_accounting import (
    COUNTER, COUNTER_BYTES, COUNTER_TEMP, DIRECTORY_CHARGE, LOCK_RESERVE, MARKER,
    FileResultAccounting, ResultAccountingError,
)


@contextmanager
def locked(accounting):
    with exclusive_file_lock(accounting.root / '.lock', follow_symlinks=False):
        with accounting.transaction():
            yield accounting


@pytest.fixture
def ledger(tmp_path):
    root = tmp_path / 'results'
    root.mkdir(mode=0o700)
    return FileResultAccounting(root, brain_bytes=10**8, owner_bytes=10**7, result_bytes=10**6)


def hierarchy(ledger):
    binding = ledger.root / ('b' * 64)
    owner = binding / ('c' * 64)
    result = owner / ('d' * 64)
    for path in (binding, owner, result):
        ledger.mkdir(path)
    return binding, owner, result


def test_subtotals_include_fixed_control_and_rewrite_reservations(ledger):
    with locked(ledger):
        binding, owner, result = hierarchy(ledger)
        ledger.write(result / 'body', [b'hello'], 100)
        assert ledger.total(result) == DIRECTORY_CHARGE + 5
        assert ledger.total(owner) == 2 * DIRECTORY_CHARGE + 5
        assert ledger.total(binding) == 3 * DIRECTORY_CHARGE + 5
        assert ledger.total(ledger.root) == 4 * DIRECTORY_CHARGE + LOCK_RESERVE + 1 + 5
    assert (ledger.root / MARKER).read_bytes() == b'0'
    for path in (ledger.root, binding, owner, result):
        assert (path / COUNTER).stat().st_size == COUNTER_BYTES
        assert not (path / COUNTER_TEMP).exists()
        assert (path / COUNTER).stat().st_mode & 0o777 == 0o600


def test_clean_writes_and_reconstruction_never_enumerate_unrelated_files(ledger, monkeypatch):
    with locked(ledger):
        _, _, result = hierarchy(ledger)
    def no_walk(*args, **kwargs):
        pytest.fail('clean accounting walked the result tree')
    monkeypatch.setattr(os, 'scandir', no_walk)
    monkeypatch.setattr(os, 'walk', no_walk)
    for _ in range(2):
        fresh = FileResultAccounting(ledger.root, brain_bytes=10**8, owner_bytes=10**7, result_bytes=10**6)
        with locked(fresh):
            fresh.write(result / 'temporary', [b'revision'], 100)
            fresh.replace(result / 'temporary', result / 'state')
            assert fresh.total(result) == DIRECTORY_CHARGE + len(b'revision')


def test_dirty_recovery_counts_staging_sibling_and_legacy_bytes(ledger):
    with locked(ledger):
        _, _, result = hierarchy(ledger)
    (ledger.root / MARKER).write_bytes(b'1')
    (result / '.tmp-data').write_bytes(b'x' * 31)
    legacy = ledger.root / 'legacy'
    legacy.mkdir()
    (legacy / 'body').write_bytes(b'y' * 47)
    with locked(ledger):
        assert ledger.total(result) == DIRECTORY_CHARGE + 31
        assert ledger.total(legacy) == DIRECTORY_CHARGE + 47
        assert ledger.total(ledger.root) == 5 * DIRECTORY_CHARGE + LOCK_RESERVE + 1 + 78
    assert (result / '.tmp-data').read_bytes() == b'x' * 31


@pytest.mark.parametrize('missing', [COUNTER, MARKER])
def test_missing_root_accounting_recovers_existing_bytes(ledger, missing):
    with locked(ledger):
        _, _, result = hierarchy(ledger)
        ledger.write(result / 'body', [b'body'], 100)
    (ledger.root / missing).unlink()
    with locked(ledger):
        assert ledger.total(result) == DIRECTORY_CHARGE + 4


def test_missing_touched_counter_reconciles_before_growth(ledger):
    with locked(ledger):
        _, _, result = hierarchy(ledger)
        ledger.write(result / 'body', [b'body'], 100)
    (result / COUNTER).unlink()
    with locked(ledger):
        ledger.write(result / 'extra', [b'xx'], 100)
        assert ledger.total(result) == DIRECTORY_CHARGE + 6


@pytest.mark.parametrize('scope', ['brain', 'owner', 'result'])
def test_quota_reservation_precedes_data_writes_and_failed_stream_is_removed(ledger, scope):
    with locked(ledger):
        _, owner, result = hierarchy(ledger)
        total = ledger.total({'brain': ledger.root, 'owner': owner, 'result': result}[scope])
    setattr(ledger, scope + '_bytes', total + 3)
    with pytest.raises(ResultAccountingError) as caught:
        with locked(ledger):
            ledger.write(result / 'body', [b'ab', b'cd'], 100)
    assert caught.value.code == 'capacity_exceeded'
    assert not (result / 'body').exists()
    with locked(ledger):
        assert ledger.total(result) == DIRECTORY_CHARGE


def test_atomic_replacement_counts_overlap_then_releases_old_bytes(ledger):
    with locked(ledger):
        _, _, result = hierarchy(ledger)
        ledger.write(result / 'state', [b'old'], 100)
        ledger.write(result / 'temp', [b'newer'], 100)
        assert ledger.total(result) == DIRECTORY_CHARGE + 8
        ledger.replace(result / 'temp', result / 'state')
        assert ledger.total(result) == DIRECTORY_CHARGE + 5
    with locked(ledger):
        ledger.remove(result / 'state')
        assert ledger.total(result) == DIRECTORY_CHARGE


def test_directory_move_and_subtree_delete_transfer_cached_counters(ledger):
    with locked(ledger):
        binding, owner, result = hierarchy(ledger)
        stage = owner / '.tmp-stage'
        ledger.mkdir(stage)
        ledger.write(stage / 'body', [b'new'], 100)
        target = owner / ('e' * 64)
        ledger.replace(stage, target)
        assert ledger.total(target) == DIRECTORY_CHARGE + 3
        ledger.remove(result)
    with locked(ledger):
        assert ledger.total(owner) == 2 * DIRECTORY_CHARGE + 3
        ledger.replace(owner, binding / '.revoked-owner', enforce=False)
        ledger.remove(binding / '.revoked-owner')
        assert ledger.total(binding) == DIRECTORY_CHARGE


def test_dirty_marker_is_durable_before_first_data_write_and_failure_stays_dirty(ledger, monkeypatch):
    with locked(ledger):
        _, _, result = hierarchy(ledger)
    original_open = os.open
    def fail_body(path, flags, *args, **kwargs):
        if Path(path).name == 'body':
            assert (ledger.root / MARKER).read_bytes() == b'1'
            raise OSError('disk failure')
        return original_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', fail_body)
    with pytest.raises(OSError):
        with locked(ledger):
            ledger.write(result / 'body', [b'secret-body'], 100)
    assert (ledger.root / MARKER).read_bytes() == b'1'
    assert b'secret-body' not in b''.join(p.read_bytes() for p in ledger.root.rglob(COUNTER))


def test_partial_counter_commit_failure_requires_recovery(ledger, monkeypatch):
    with locked(ledger):
        _, _, result = hierarchy(ledger)
    original = ledger._save_total
    def fail_root(path, total):
        if path == ledger.root:
            raise OSError('interrupted counter commit')
        original(path, total)
    monkeypatch.setattr(ledger, '_save_total', fail_root)
    with pytest.raises(OSError):
        with locked(ledger):
            ledger.write(result / 'body', [b'persisted'], 100)
    assert (ledger.root / MARKER).read_bytes() == b'1'
    monkeypatch.setattr(ledger, '_save_total', original)
    with locked(ledger):
        assert ledger.total(result) == DIRECTORY_CHARGE + 9


def test_symlinked_control_and_data_fail_closed(ledger, tmp_path):
    with locked(ledger):
        _, _, result = hierarchy(ledger)
    outside = tmp_path / 'outside'
    outside.write_text('private')
    (result / COUNTER).unlink()
    (result / COUNTER).symlink_to(outside)
    with pytest.raises(ResultAccountingError) as caught:
        with locked(ledger):
            ledger.total(result)
    assert caught.value.code == 'unsafe_path'
    assert outside.read_text() == 'private'


def _crash_writer(root, point):
    ledger = FileResultAccounting(Path(root), brain_bytes=10**8, owner_bytes=10**7, result_bytes=10**6)
    with locked(ledger):
        _, _, result = hierarchy(ledger)
        if point == 'before-write':
            original = os.open
            def crash(path, *args, **kwargs):
                if Path(path).name == 'partial':
                    os._exit(41)
                return original(path, *args, **kwargs)
            os.open = crash
        elif point == 'during-counter-commit':
            original = ledger._save_total
            def crash(path, total):
                if path == ledger.root:
                    os._exit(43)
                return original(path, total)
            ledger._save_total = crash
        ledger.write(result / 'partial', [b'persisted'], 100)
        if point == 'after-write':
            os._exit(42)


@pytest.mark.parametrize('point, expected', [('before-write', 0), ('after-write', 9), ('during-counter-commit', 9)])
def test_process_crash_reconciles_durable_bytes_before_next_write(ledger, point, expected):
    import multiprocessing
    process = multiprocessing.get_context('spawn').Process(target=_crash_writer, args=(str(ledger.root), point))
    process.start()
    process.join(10)
    if process.is_alive():
        process.terminate()
        process.join(5)
        pytest.fail('crash probe did not terminate')
    assert process.exitcode in (41, 42, 43)
    assert (ledger.root / MARKER).read_bytes() == b'1'
    with locked(ledger):
        _, _, result = hierarchy(ledger)
        assert ledger.total(result) == DIRECTORY_CHARGE + expected
        ledger.write(result / 'after', [b'next'], 100)
    assert (ledger.root / MARKER).read_bytes() == b'0'


def _concurrent_writer(root, number):
    ledger = FileResultAccounting(Path(root), brain_bytes=5000, owner_bytes=10**7, result_bytes=10**6)
    try:
        with locked(ledger):
            _, _, result = hierarchy(ledger)
            ledger.write(result / ('body-' + str(number)), [b'x' * 1000], 1000)
        return True
    except ResultAccountingError as exc:
        if exc.code != 'capacity_exceeded':
            raise
        return False


def test_processes_cannot_overspend_shared_reservations(ledger):
    from concurrent.futures import ProcessPoolExecutor
    with locked(ledger):
        hierarchy(ledger)
    with ProcessPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(_concurrent_writer, [str(ledger.root)] * 6, range(6)))
    assert sum(results) == 3
    ledger.brain_bytes = 5000
    with locked(ledger):
        assert ledger.total(ledger.root) == 4 * DIRECTORY_CHARGE + LOCK_RESERVE + 1 + 3000


def test_counter_and_marker_metadata_are_bounded_and_never_store_bodies(ledger):
    with locked(ledger):
        _, _, result = hierarchy(ledger)
        ledger.write(result / 'body', [b'private-credential-and-content'], 100)
    for path in ledger.root.rglob(COUNTER):
        assert len(path.read_bytes()) == COUNTER_BYTES
        assert set(json.loads(path.read_bytes())) == {'version', 'bytes'}
    assert (ledger.root / MARKER).read_bytes() == b'0'


def test_over_limit_recovery_allows_explicit_deletion_without_eviction(ledger):
    with locked(ledger):
        _, _, result = hierarchy(ledger)
        ledger.write(result / 'body', [b'x' * 1000], 2000)
    ledger.brain_bytes = 100
    (ledger.root / MARKER).write_bytes(b'1')
    with locked(ledger):
        assert (result / 'body').exists()
        ledger.remove(result)
    assert not result.exists()


def test_unrelated_symlink_is_rejected_during_recovery(ledger, tmp_path):
    with locked(ledger):
        _, _, result = hierarchy(ledger)
    outside = tmp_path / 'outside-directory'
    outside.mkdir()
    (result / 'link').symlink_to(outside, target_is_directory=True)
    (ledger.root / MARKER).write_bytes(b'1')
    with pytest.raises(ResultAccountingError) as caught:
        with locked(ledger):
            pass
    assert caught.value.code == 'unsafe_path'
    assert outside.is_dir()


@pytest.mark.parametrize('unrelated_pages', [1, 1000])
def test_changed_page_commit_and_admission_do_not_walk_unrelated_storage(tmp_path, monkeypatch, unrelated_pages):
    from _command_interface.result_store import FileResultStore
    root = tmp_path / 'Brain'
    root.mkdir()
    store = FileResultStore(root, 'brain', 'principal')
    who = store.authenticate(store.issue())
    foreign = store.authenticate(store.issue())
    retained = store.publish(foreign, store.admit(foreign, 'large.read', 'large'), {}, {})
    store.save_view(foreign, retained, 'large', [{}] * unrelated_pages, {})
    result = store.publish(who, store.admit(who, 'small.read', 'small'), {}, {})
    store.save_view(who, result, 'small', [{}], {'source_revision': 'changed', 'empty': {}})
    _, _, observation = store.read_view_selection(who, result, 'small', 1, observe=True)
    counters = []
    original = store._accounting._load_total
    def counted(path):
        counters.append(path)
        return original(path)
    def no_walk(*args, **kwargs):
        pytest.fail('changed page observation scanned unrelated storage')
    monkeypatch.setattr(os, 'scandir', no_walk)
    monkeypatch.setattr(os, 'walk', no_walk)
    monkeypatch.setattr(store._accounting, '_load_total', counted)
    store.commit_page_observation(observation)
    assert len(counters) <= 10
    assert all(not path.is_relative_to(store._owner_path(foreign)) for path in counters)
    counters.clear()
    store.admit(who, 'another.read', 'another')
    assert len(counters) <= 5


def test_caught_mutation_failure_cannot_publish_clean_incorrect_counters(ledger, monkeypatch):
    with locked(ledger):
        _, _, result = hierarchy(ledger)
        ledger.write(result / 'state', [b'old'], 100)
    original = os.replace
    def fail_data(source, target):
        if Path(source).name == 'temp':
            raise OSError('rename failed')
        return original(source, target)
    monkeypatch.setattr(os, 'replace', fail_data)
    with pytest.raises(ResultAccountingError, match='requires recovery'):
        with locked(ledger):
            ledger.write(result / 'temp', [b'new'], 100)
            with pytest.raises(OSError):
                ledger.replace(result / 'temp', result / 'state')
            ledger.remove(result / 'temp')
    assert (ledger.root / MARKER).read_bytes() == b'1'
    with locked(ledger):
        assert ledger.total(result) == DIRECTORY_CHARGE + 3
        assert (result / 'state').read_bytes() == b'old'


def test_format_bound_does_not_claim_scope_capacity(ledger):
    with pytest.raises(ResultAccountingError) as caught:
        with locked(ledger):
            _, _, result = hierarchy(ledger)
            ledger.write(result / 'page', [b'x' * 101], 100, representation_scope='page')
    assert caught.value.code == 'representation_too_large'
    assert caught.value.representation_scope == 'page'
    assert caught.value.actual_limit == 100 and caught.value.capacity_scope is None


def test_failure_attributes_only_accept_bounded_primitive_limit_metadata():
    exc = ResultAccountingError('storage_error', 'Unavailable.',
        capacity_scope='/private/backend', representation_scope='secret-body', actual_limit='raw-error')
    assert exc.capacity_scope is None and exc.representation_scope is None and exc.actual_limit is None
    assert ResultAccountingError('storage_error', 'Unavailable.', actual_limit=2**100).actual_limit is None
