"""One observed foreign-claim rule across local workspace mutation owners."""

from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cli'))
from _bootstrap.file_transaction import FilePlan, FileTransactionError
from _bootstrap.workspace_claims import inspect_path_claims, WorkspaceClaimConflict
from _application.workspace._registration import observe_path_claims
from _bootstrap import mcp_registration
from _launcher.workspace import _cross_brain_claims
import vault_registry
import workspace_registry


@pytest.fixture
def claims(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    roots = [tmp_path / name for name in ('selected', 'foreign')]
    for root in roots:
        version = root / '.brain-core/VERSION'
        version.parent.mkdir(parents=True)
        version.write_text('0.72.0\n')
        vault_registry.register(root, root.name)
    folder = tmp_path / 'folder'
    folder.mkdir()
    return *roots, folder


@pytest.mark.parametrize('owner', ['register', 'mcp', 'upgrade'])
def test_mutation_owners_refuse_the_same_foreign_claim(claims, monkeypatch, owner):
    selected, foreign, folder = claims
    workspace_registry.register_workspace(foreign, 'shared', folder)
    # MCP target admission is independent of the foreign-claim boundary tested here.
    monkeypatch.setattr(mcp_registration, 'plan_target_admission',
                        lambda *_: SimpleNamespace(linked=True, slug='shared'))
    calls = {
        'register': lambda: observe_path_claims(selected, folder),
        'mcp': lambda: mcp_registration.plan_reverse_registration(FilePlan(), selected, folder),
        'upgrade': lambda: _cross_brain_claims(FilePlan(), selected, folder),
    }
    with pytest.raises(ValueError, match='Conflicting workspace registration claims'):
        calls[owner]()
    assert not (selected / workspace_registry.REGISTRY_REL).exists()


def test_rebind_allows_only_its_explicit_source(claims):
    selected, source, folder = claims
    workspace_registry.register_workspace(source, 'shared', folder)
    inspection = inspect_path_claims(FilePlan(), selected, folder, allowed_roots=(source,))
    inspection.require_unclaimed()
    assert not inspection.claims and not inspection.coverage
    foreign = inspect_path_claims(FilePlan(), selected, folder)
    assert foreign.claims[0].brain_root == source
    assert foreign.claims[0].slug == 'shared'
    with pytest.raises(WorkspaceClaimConflict):
        foreign.require_unclaimed()


@pytest.mark.parametrize('owner', ['register', 'mcp', 'upgrade'])
def test_unknown_coverage_policy_stays_with_mutation_owner(claims, monkeypatch, owner):
    selected, foreign, folder = claims
    (foreign / '.brain-core/VERSION').unlink()
    monkeypatch.setattr(mcp_registration, 'plan_target_admission',
                        lambda *_: SimpleNamespace(linked=True, slug='shared'))
    if owner == 'upgrade':
        inspection = _cross_brain_claims(FilePlan(), selected, folder)
        assert inspection.coverage
        with pytest.raises(ValueError):
            _cross_brain_claims(FilePlan(), selected, folder, strict=True)
    elif owner == 'mcp':
        plan = FilePlan()
        mcp_registration.plan_reverse_registration(plan, selected, folder)
        assert plan.changes()
        assert not (selected / workspace_registry.REGISTRY_REL).exists()
    else:
        with pytest.raises(ValueError):
            observe_path_claims(selected, folder)


@pytest.mark.parametrize('failure', ['malformed', 'unreadable'])
def test_mcp_unreachable_tolerance_does_not_tolerate_foreign_read_failures(claims, monkeypatch, failure):
    selected, foreign, folder = claims
    registry = foreign / workspace_registry.REGISTRY_REL
    registry.parent.mkdir(parents=True)
    registry.write_text('invalid JSON')
    monkeypatch.setattr(mcp_registration, 'plan_target_admission',
                        lambda *_: SimpleNamespace(linked=True, slug='shared'))
    plan = FilePlan()
    if failure == 'unreadable':
        original = plan.read_bytes

        def read(path):
            if path == registry:
                raise PermissionError('foreign registry is unreadable')
            return original(path)

        monkeypatch.setattr(plan, 'read_bytes', read)
    with pytest.raises((OSError, ValueError)):
        mcp_registration.plan_reverse_registration(plan, selected, folder)
    assert not plan.changes()


def test_claim_evidence_is_revalidated_before_later_writes(claims):
    selected, foreign, folder = claims
    plan = FilePlan()
    inspection = inspect_path_claims(plan, selected, folder)
    inspection.require_unclaimed()
    registry = foreign / workspace_registry.REGISTRY_REL
    assert registry in inspection.observed_files
    workspace_registry.register_workspace(foreign, 'shared', folder)
    with pytest.raises(FileTransactionError, match='changed'):
        plan.validate_dependencies()


def test_incomplete_scope_does_not_hide_known_foreign_claim(claims, tmp_path):
    selected, foreign, folder = claims
    broken = tmp_path / 'broken'
    version = broken / '.brain-core/VERSION'
    version.parent.mkdir(parents=True)
    version.write_text('0.72.0\n')
    vault_registry.register(broken, 'broken')
    registry = broken / workspace_registry.REGISTRY_REL
    registry.parent.mkdir(parents=True)
    registry.write_text('invalid JSON')
    workspace_registry.register_workspace(foreign, 'shared', folder)
    inspection = inspect_path_claims(FilePlan(), selected, folder, require_complete=False)
    assert inspection.coverage
    with pytest.raises(WorkspaceClaimConflict):
        inspection.require_unclaimed()
