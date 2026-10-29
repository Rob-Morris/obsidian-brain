"""Durable semantic state is guarded while projections and markers remain usable.

These tests exercise owner boundaries; Git observations are deterministic so a
checkout change can be placed between exact publication phases.
"""

import pytest

from _bootstrap import branch_guard
from _bootstrap.local_config import patch_local_config, read_local_config
from _relationships.model import validate_definition
from _relationships.storage import Store
from _semantic.config import (
    set_semantic_engine_installed,
    set_semantic_flags,
    set_semantic_retrieval_enabled,
)


@pytest.fixture
def guarded(tmp_path, monkeypatch):
    (tmp_path / '.brain').mkdir()
    state = {'branch': 'dev'}
    monkeypatch.setattr(branch_guard, 'observe_git',
                        lambda root: branch_guard.GitState(str(tmp_path), state['branch']))
    patch_local_config(tmp_path, lambda data: data.update(
        branch_guard={'allowed_branches': ['dev', 'feature']}, unrelated={'keep': 'value'}))
    return tmp_path, state


def predicate(identity):
    record = {'kind': 'predicate', 'id': identity, 'name': identity,
              'meaning': 'Durable definition', 'cardinality': 'many'}
    return {**record, 'contract_revision': validate_definition(record)}


def test_source_write_and_delete_refuse_but_projection_refresh_works(guarded):
    root, state = guarded
    store = Store(root)
    store.write([predicate('retained')])
    original = store.path('predicate', 'retained').read_bytes()
    state['branch'] = 'main'
    with pytest.raises(branch_guard.BranchGuardError):
        store.write([predicate('blocked')])
    with pytest.raises(branch_guard.BranchGuardError):
        store.write([], [('predicate', 'retained')])
    assert not store.path('predicate', 'blocked').exists()
    assert store.path('predicate', 'retained').read_bytes() == original
    assert store.refresh()['complete']
    assert store.read('predicate', 'retained')['meaning'] == 'Durable definition'


def test_feature_flags_are_protected_but_support_marker_preserves_policy(guarded):
    root, state = guarded
    state['branch'] = 'main'
    before = read_local_config(root)
    with pytest.raises(branch_guard.BranchGuardError):
        set_semantic_flags(root, retrieval=True, processing=True)
    assert read_local_config(root).revision == before.revision
    assert set_semantic_engine_installed(root)
    after = read_local_config(root).document
    assert after['branch_guard'] == before.document['branch_guard']
    assert after['unrelated'] == before.document['unrelated']
    assert after['defaults']['local_runtime']['semantic_engine_installed'] is True
    assert 'flags' not in after['defaults']


def test_locked_flag_patch_does_not_reacquire_shared_lock(guarded):
    from _bootstrap.file_lock import vault_mutation_lock
    root, _ = guarded
    with vault_mutation_lock(root), branch_guard.operation(root):
        assert set_semantic_retrieval_enabled(root, lock_held=True)
        assert set_semantic_engine_installed(root, lock_held=True)
        branch_guard.checkpoint(root)
    assert not set_semantic_retrieval_enabled(root)
    assert read_local_config(root).document['unrelated'] == {'keep': 'value'}


def test_batch_switch_never_restores_written_sources_and_loss_is_sticky(guarded, monkeypatch):
    from _bootstrap import file_transaction
    root, state = guarded
    store = Store(root)
    applied = []
    original_apply = file_transaction._apply

    def apply(path, content):
        original_apply(path, content)
        applied.append(path)
        state['branch'] = 'feature'  # Both names are allowed, continuity still fails.

    monkeypatch.setattr(file_transaction, '_apply', apply)
    with branch_guard.operation(root):
        with pytest.raises(branch_guard.BranchGuardError):
            store.write([predicate('first'), predicate('second')])
        state['branch'] = 'dev'
        with pytest.raises(branch_guard.BranchGuardError):
            store.write([predicate('later')])
    assert len(applied) == 1
    assert applied[0].exists()
    assert not store.path('predicate', 'later').exists()
    assert sum(store.path('predicate', item).exists() for item in ('first', 'second')) == 1


def test_publication_retains_original_context_and_persistent_loss(guarded, monkeypatch):
    root, state = guarded
    store = Store(root)
    original_write = store.write

    def write(records, removals=()):
        result = original_write(records, removals)
        if any(row['kind'] == 'operation' for row in records):
            state['branch'] = 'feature'
        return result

    monkeypatch.setattr(store, 'write', write)
    with pytest.raises(branch_guard.BranchGuardError):
        store.publish('interrupted', [predicate('never-published')])
    journal = store.read('operation', 'interrupted')
    assert journal['branch_guard']['branch'] == 'dev'
    assert journal['state'] == 'pending'
    assert store.read('predicate', 'never-published') is None
    assert store._guard_loss_path('interrupted').exists()
    state['branch'] = 'dev'
    monkeypatch.setattr(store, 'write', original_write)
    before = store.path('operation', 'interrupted').read_bytes()
    # Repair is normally nested inside the application operation.
    with branch_guard.operation(root):
        with pytest.raises(branch_guard.BranchGuardError):
            store.repair('interrupted')
    assert store.path('operation', 'interrupted').read_bytes() == before
    with pytest.raises(branch_guard.BranchGuardError):
        store.abandon('interrupted', 'Returning to dev is insufficient')


def test_repair_refuses_legacy_journal_under_active_policy(guarded):
    root, _ = guarded
    store = Store(root)
    store.write([{'kind': 'operation', 'id': 'legacy', 'state': 'pending',
                  'records': [], 'events': [], 'removals': [], 'originals': {}}])
    before = store.path('operation', 'legacy').read_bytes()
    with branch_guard.operation(root):
        with pytest.raises(branch_guard.BranchGuardError):
            store.repair('legacy')
    assert store.path('operation', 'legacy').read_bytes() == before


def test_same_context_repair_after_projection_failure_does_not_replay_state(guarded, monkeypatch):
    root, _ = guarded
    store = Store(root)
    original_reconcile = store.reconcile

    def fail_projection(records, removals=()):
        if any(row['kind'] == 'operation' for row in records):
            raise OSError('projection unavailable')
        return original_reconcile(records, removals)

    monkeypatch.setattr(store, 'reconcile', fail_projection)
    with pytest.raises(OSError, match='projection unavailable'):
        store.publish('recoverable', [predicate('durable')])
    assert store.read('operation', 'recoverable')['state'] == 'projection-pending'
    monkeypatch.setattr(store, 'reconcile', original_reconcile)
    later = {**predicate('durable'), 'meaning': 'Later admitted meaning'}
    later['contract_revision'] = validate_definition(later)
    store.write([later])
    before = store.path('predicate', 'durable').read_bytes()
    with branch_guard.operation(root):
        assert store.repair('recoverable') == 0
    assert store.path('predicate', 'durable').read_bytes() == before
    assert store.read('operation', 'recoverable')['state'] == 'complete'


def test_guard_loss_evidence_changes_only_owned_operational_sidecar(guarded):
    import json
    root, state = guarded
    store = Store(root)
    store.publish('finished', [predicate('retained')])
    sources = {path: path.read_bytes() for base in (store.shared, store.config)
               for path in base.rglob('*.json')}
    state['branch'] = 'main'
    with branch_guard.operation(root) as operation:
        with pytest.raises(branch_guard.BranchGuardError):
            branch_guard.checkpoint(root)
        store._retain_guard_loss('failed', operation.evidence())
    assert {path: path.read_bytes() for path in sources} == sources
    sidecar = store._guard_loss_path('failed')
    assert sidecar.is_relative_to(root / '.brain/local/relationship-recovery')
    assert json.loads(sidecar.read_bytes())['lost'] is True
    assert set(path for base in (store.shared, store.config)
               for path in base.rglob('*.json')) == set(sources)


def test_guard_loss_sidecar_refuses_redirected_parent(guarded, tmp_path):
    from _bootstrap.owned_paths import UnsafeFilesystemLayoutError
    root, state = guarded
    outside = tmp_path / 'outside'
    outside.mkdir()
    parent = root / '.brain/local/relationship-recovery'
    try:
        parent.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip('directory symlinks unavailable')
    state['branch'] = 'main'
    with branch_guard.operation(root) as operation:
        with pytest.raises(branch_guard.BranchGuardError):
            branch_guard.checkpoint(root)
        with pytest.raises(UnsafeFilesystemLayoutError):
            Store(root)._retain_guard_loss('failed', operation.evidence())
    assert list(outside.iterdir()) == []


def test_native_pending_journal_carries_starting_context_to_completion(guarded, monkeypatch):
    from types import SimpleNamespace
    from _application.relationship import _native
    from _common import PartialApplyError
    root, state = guarded
    context = SimpleNamespace(selected_brain=SimpleNamespace(vault_root=root), admission=None)
    plan = SimpleNamespace(operation='native-transition', records=[predicate('native-definition')],
                           events=[], result={'native_guards': {}})
    monkeypatch.setattr(_native, 'prepare', lambda *args, **kwargs: (plan, (), {}))
    with branch_guard.operation(root):
        assert _native.begin(context, None, None, {}) is plan
        journal = Store(root).read('operation', plan.operation)
        assert journal['state'] == 'native-pending'
        assert journal['branch_guard']['branch'] == 'dev'
        state['branch'] = 'feature'
        with pytest.raises(PartialApplyError):
            _native.complete(context, plan)
    assert Store(root).read('operation', plan.operation) == journal
    assert Store(root).read('predicate', 'native-definition') is None
    state['branch'] = 'dev'
    with pytest.raises(branch_guard.BranchGuardError):
        Store(root).repair(plan.operation)


def test_kernel_loss_hook_targets_only_admitted_native_pending_journal(guarded, monkeypatch):
    from types import SimpleNamespace
    from _application.relationship import _native
    root, state = guarded
    admission = SimpleNamespace(entered=True, frozen_inputs=None)
    context = SimpleNamespace(selected_brain=SimpleNamespace(vault_root=root), admission=admission)
    plan = SimpleNamespace(operation='native-aborted', records=[predicate('not-published')],
                           events=[], result={'native_guards': {}})
    monkeypatch.setattr(_native, 'prepare', lambda *args, **kwargs: (plan, (), {}))
    with branch_guard.operation(root):
        _native.begin(context, None, None, {})
        assert admission.frozen_inputs['relationship_operation'] == plan.operation
        store = Store(root)
        before = store.path('operation', plan.operation).read_bytes()
        state['branch'] = 'feature'
        with pytest.raises(branch_guard.BranchGuardError):
            branch_guard.checkpoint(root)
        _native.retain_context_loss(context)
        assert store.path('operation', plan.operation).read_bytes() == before
        assert store.read('predicate', 'not-published') is None
        assert store._guard_loss_path(plan.operation).exists()
    state['branch'] = 'dev'
    with pytest.raises(branch_guard.BranchGuardError):
        Store(root).repair(plan.operation)


def test_kernel_loss_hook_without_admitted_identity_does_not_read_journals(guarded, monkeypatch):
    from types import SimpleNamespace
    from _application.relationship import _native
    root, _ = guarded
    context = SimpleNamespace(selected_brain=SimpleNamespace(vault_root=root),
                              admission=SimpleNamespace(entered=True, frozen_inputs={}))

    def unexpected_read(*args):
        raise AssertionError('No journal may be opened without admitted identity')

    monkeypatch.setattr(Store, 'read', unexpected_read)
    _native.retain_context_loss(context)
    context.admission = None
    _native.retain_context_loss(context)


def test_semantic_enable_propagates_parent_lock_to_flags_and_marker(guarded, monkeypatch):
    from types import SimpleNamespace
    from _bootstrap.file_lock import vault_mutation_lock
    from _lifecycle.semantic_enable import enable_semantic
    from _semantic import provision
    root, _ = guarded
    monkeypatch.setattr(provision, 'probe_python', lambda *args, **kwargs: {'ok': True})
    monkeypatch.setattr(provision, 'sync_runtime_packages', lambda *args: False)
    monkeypatch.setattr(provision.semantic_model, 'provision_semantic_model',
                        lambda *args: SimpleNamespace(notes=[], downloaded=False, manifest_changed=False))
    monkeypatch.setattr(provision, 'refresh_semantic_assets', lambda *args: [])
    with vault_mutation_lock(root):
        result = enable_semantic(root, provision=True, lock_held=True)
    assert result['status'] == 'ok'
    document = read_local_config(root).document
    assert document['defaults']['flags']['semantic_retrieval'] is True
    assert document['defaults']['local_runtime']['semantic_engine_installed'] is True
    assert document['branch_guard']['allowed_branches'] == ['dev', 'feature']


def test_failed_provisioning_clears_marker_under_parent_lock_on_blocked_branch(guarded, monkeypatch):
    from _bootstrap.file_lock import vault_mutation_lock
    from _semantic import provision
    root, state = guarded
    set_semantic_engine_installed(root)
    state['branch'] = 'main'
    monkeypatch.setattr(provision, 'semantic_runtime_supported_platform',
                        lambda: (False, 'unsupported test platform'))
    with vault_mutation_lock(root):
        with pytest.raises(provision.SemanticProvisionError, match='unsupported test platform'):
            provision.provision_semantic_runtime(root, python_executable='unused', lock_held=True)
    document = read_local_config(root).document
    assert document['defaults']['local_runtime']['semantic_engine_installed'] is False
    assert document['branch_guard']['allowed_branches'] == ['dev', 'feature']
