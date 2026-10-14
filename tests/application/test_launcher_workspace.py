"""Caller-local workspace upgrades, discovery and independent phase receipts."""

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import pytest

CLI = Path(__file__).resolve().parents[2] / "cli"
if str(CLI) not in sys.path:
    sys.path.insert(0, str(CLI))

from _launcher.context import LauncherContext, ProviderBindings
from _launcher.contracts import Ok, Partial, ReceiptState
from _launcher.invocation import LauncherInvocation
from _launcher.owners import LAUNCHER_OWNERS
from _launcher.workspace import (
    UpgradeScope, WorkspaceUpgradeRequest, WorkspaceDiscoverRequest,
    execute_upgrade, execute_discover, invoke_upgrade_phase,
)
from launcher_catalogue import LAUNCHER_CATALOGUE
from _bootstrap.workspace_binding import read_workspace_manifest
from _common._yaml import dump_mapping_text
import vault_registry
import workspace_registry


class Authority:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.calls = []

    def allows(self, **request):
        self.calls.append(request)
        return self.allowed


class Receipts:
    def __init__(self):
        self.values = []

    def write(self, value):
        self.values.append(value)


class Clock:
    def now(self):
        return datetime.now(timezone.utc)


class Filesystem:
    provider_id = "caller_filesystem"
    available = True


def context(tmp_path, root=None, folder=None, **kwargs):
    return LauncherContext(profile="local-operator", authority=Authority(),
        providers=ProviderBindings((Filesystem(),)), correlation_id="workspace-tests",
        invocation_id="workspace-tests", receipt_writer=Receipts(), clock=Clock(),
        caller_dir=tmp_path.resolve(), home_dir=(tmp_path / "home").resolve(),
        cli_version="5.0.1", cli_binary=(tmp_path / "bin/brain").resolve(),
        current_vault=root, workspace_dir=folder, **kwargs)


@pytest.fixture
def linked(command_vault_clone, tmp_path, monkeypatch):
    from _application.workspace.create import WorkspaceCreateRequest
    from test_workspace_membership_policy import application
    root = command_vault_clone.vault_root
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    vault_registry.register(root, "selected")
    result = application(root).invoke(WorkspaceCreateRequest("shared"))
    assert result.status == "ok", result
    folder = tmp_path / "repo folder"
    folder.mkdir()
    manifest = folder / ".brain/local/workspace.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(dump_mapping_text({"brain": "selected", "slug": "old-local-name",
        "links": {"workspace": "shared", "project": "optional"}, "unknown": {"nested": [1, 2]}}))
    return root, folder, context(tmp_path, root, folder)


def test_upgrade_verifies_legacy_identity_and_recovers_missing_row(linked):
    root, folder, ctx = linked
    result = LauncherInvocation(ctx, LAUNCHER_CATALOGUE, LAUNCHER_OWNERS).invoke(WorkspaceUpgradeRequest())
    assert isinstance(result, Ok), result
    assert result.result.complete
    manifest = read_workspace_manifest(folder)
    assert manifest["schema_version"] == 1
    assert manifest["slug"] == "shared"
    assert manifest["links"] == {"project": "optional"}
    assert manifest["unknown"] == {"nested": [1, 2]}
    assert workspace_registry.load_registry_strict(root)[str(folder.resolve())]["slug"] == "shared"
    assert ctx.receipt_writer.values[-1].state is ReceiptState.COMMITTED
    backups = [Path(effect.subject[5:]) for effect in result.committed_effects if ".workspace-" in effect.subject]
    assert backups and json.loads(backups[0].read_text())["files"][0]["before"] is not None
    again = execute_upgrade(ctx, WorkspaceUpgradeRequest())
    assert again.result.folders[0].status == "noop"
    assert not again.committed_effects


def test_preview_has_no_writes_and_reports_files(linked):
    root, folder, ctx = linked
    before = {str(path): path.read_bytes() for path in folder.rglob("*") if path.is_file()}
    result = execute_upgrade(replace(ctx, dry_run=True), WorkspaceUpgradeRequest())
    assert result.result.complete
    assert result.result.folders[0].status == "planned"
    assert result.result.folders[0].files
    assert before == {str(path): path.read_bytes() for path in folder.rglob("*") if path.is_file()}
    assert not (root / ".brain/local/workspaces.json").exists()


def test_upgrade_preserves_other_paths_sharing_slug(linked, tmp_path):
    root, folder, ctx = linked
    other = tmp_path / "other"
    other.mkdir()
    workspace_registry.register_workspace(root, "shared", other)
    result = execute_upgrade(ctx, WorkspaceUpgradeRequest())
    assert result.result.complete
    assert set(workspace_registry.load_registry_strict(root)) == {str(folder), str(other)}


@pytest.mark.parametrize("mutate", [
    {"schema_version": 99, "brain": "selected", "slug": "shared"},
    {"schema_version": True},
    {"brain": "selected", "links": {"workspace": "missing"}},
    {"brain": "other", "links": {"workspace": "shared"}},
])
def test_unsupported_or_unverifiable_identity_has_no_metadata_writes(linked, mutate):
    root, folder, ctx = linked
    manifest = folder / ".brain/local/workspace.yaml"
    manifest.write_text(dump_mapping_text(mutate))
    before = manifest.read_bytes()
    result = execute_upgrade(ctx, WorkspaceUpgradeRequest())
    assert not result.result.complete
    assert result.result.folders[0].status == "blocked"
    assert manifest.read_bytes() == before
    assert not (root / ".brain/local/workspaces.json").exists()
    assert not result.committed_effects


def test_unlinked_legacy_normalises_without_inferred_registration(linked):
    root, folder, ctx = linked
    (folder / ".brain/local/workspace.yaml").write_text("slug: local-only\ndefault_tags: []\nunknown: retained\n")
    result = execute_upgrade(ctx, WorkspaceUpgradeRequest())
    assert result.result.complete
    assert read_workspace_manifest(folder) == {"schema_version": 1, "default_tags": [], "unknown": "retained"}
    assert not (root / ".brain/local/workspaces.json").exists()


def test_legacy_location_moves_with_original_bytes_preserved(linked):
    _, folder, ctx = linked
    canonical = folder / ".brain/local/workspace.yaml"
    legacy = folder / ".brain/workspace.yaml"
    canonical.rename(legacy)
    result = execute_upgrade(ctx, WorkspaceUpgradeRequest())
    assert result.result.complete
    assert canonical.exists() and not legacy.exists()


def test_unavailable_row_retained_without_recreating_folder(linked, tmp_path):
    root, _, ctx = linked
    missing = tmp_path / "missing"
    workspace_registry.register_workspace(root, "shared", missing)
    result = execute_upgrade(ctx, WorkspaceUpgradeRequest(UpgradeScope.BRAIN))
    assert not result.result.complete
    assert not missing.exists()
    assert str(missing) in workspace_registry.load_registry_strict(root)


def test_malformed_machine_inventory_refuses_before_writes(linked, tmp_path):
    _, folder, ctx = linked
    Path(vault_registry.registry_path()).write_text("malformed\n")
    before = (folder / ".brain/local/workspace.yaml").read_bytes()
    result = execute_upgrade(ctx, WorkspaceUpgradeRequest(UpgradeScope.MACHINE))
    assert result.status == "error"
    assert before == (folder / ".brain/local/workspace.yaml").read_bytes()


def test_exact_scope_can_recover_when_machine_inventory_is_unavailable(linked):
    _, _, ctx = linked
    # A stale unrelated Brain is incomplete breadth, not ambiguity in exact binding.
    with Path(vault_registry.registry_path()).open("a") as stream:
        stream.write("stale\tlocal\t/unavailable/brain\n")
    result = execute_upgrade(ctx, WorkspaceUpgradeRequest())
    assert result.result.complete


def test_machine_scope_reports_older_contract_even_with_empty_registry(linked, tmp_path):
    _, _, ctx = linked
    old = tmp_path / "old"
    (old / ".brain-core").mkdir(parents=True)
    (old / ".brain-core/VERSION").write_text("0.10.0")
    vault_registry.register(old, "old")
    result = execute_upgrade(ctx, WorkspaceUpgradeRequest(UpgradeScope.MACHINE))
    assert not result.result.complete
    assert any("supported workspace schema" in item for item in result.result.coverage)


def test_discovery_is_read_only_and_prunes_symlinks_infrastructure(linked, tmp_path):
    root, folder, ctx = linked
    hidden = tmp_path / ".git/hidden/.brain/local"
    hidden.mkdir(parents=True)
    (hidden / "workspace.yaml").write_text("slug: hidden\n")
    alias = tmp_path / "alias"
    alias.symlink_to(folder, target_is_directory=True)
    before = (folder / ".brain/local/workspace.yaml").read_bytes()
    result = execute_discover(ctx, WorkspaceDiscoverRequest((tmp_path,)))
    assert result.result.complete, result
    assert [row.path for row in result.result.folders] == [str(folder)]
    row = result.result.folders[0]
    assert row.registration == "unregistered"
    assert "--vault" in row.command and "workspace upgrade" in row.command
    assert before == (folder / ".brain/local/workspace.yaml").read_bytes()
    assert not (root / ".brain/local/workspaces.json").exists()


def test_discovery_unknown_registration_when_inventory_is_incomplete(linked):
    _, folder, ctx = linked
    Path(vault_registry.registry_path()).write_text("bad row\n")
    result = execute_discover(ctx, WorkspaceDiscoverRequest((folder,)))
    assert not result.result.complete
    assert result.result.folders[0].registration == "unknown"


def test_discovery_and_machine_upgrade_do_not_require_selected_brain(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    ctx = context(tmp_path)
    discovered = execute_discover(ctx, WorkspaceDiscoverRequest((tmp_path,)))
    assert discovered.result.complete
    assert execute_upgrade(ctx, WorkspaceUpgradeRequest(UpgradeScope.MACHINE)).result.complete


def test_discovery_requires_explicit_roots():
    with pytest.raises(ValueError, match="roots"):
        WorkspaceDiscoverRequest(())


def test_upgrade_child_has_independent_authority_and_receipt(linked):
    root, folder, ctx = linked
    workspace_registry.register_workspace(root, "shared", folder)
    result = invoke_upgrade_phase(ctx)
    assert result["outcome"] == "ok", result
    assert result["invocation_id"] == "workspace-tests-workspaces"
    receipt = ctx.receipt_writer.values[-1]
    assert receipt.command_id == "workspace.upgrade"
    assert receipt.reference.invocation_id != ctx.invocation_id
    assert ctx.authority.calls[-1]["command_id"] == "workspace.upgrade"


def test_upgrade_child_denial_has_no_effects_and_is_not_retried(linked):
    _, folder, ctx = linked
    denied = replace(ctx, authority=Authority(False))
    before = (folder / ".brain/local/workspace.yaml").read_bytes()
    phase = invoke_upgrade_phase(denied)
    assert phase["outcome"] == "partial"
    assert phase["result"]["error"]["code"] == "authority_denied"
    assert len(denied.authority.calls) == 1
    assert before == (folder / ".brain/local/workspace.yaml").read_bytes()


@pytest.mark.parametrize("scope", list(UpgradeScope))
@pytest.mark.parametrize("dry_run", [False, True])
def test_cross_brain_claim_conflict_refuses_all_scopes(linked, tmp_path, scope, dry_run):
    root, folder, ctx = linked
    other = tmp_path / "other-brain"
    (other / ".brain-core").mkdir(parents=True)
    (other / ".brain-core/VERSION").write_text("0.71.4")
    vault_registry.register(other, "other")
    workspace_registry.register_workspace(root, "shared", folder)
    workspace_registry.register_workspace(other, "shared", folder)
    before = (folder / ".brain/local/workspace.yaml").read_bytes()
    result = execute_upgrade(replace(ctx, dry_run=dry_run), WorkspaceUpgradeRequest(scope))
    assert result.status == "error" or not result.result.complete
    assert not result.committed_effects if isinstance(result, Ok) else True
    assert before == (folder / ".brain/local/workspace.yaml").read_bytes()


def test_claim_added_after_outer_inspection_is_refused_under_coordination(linked, tmp_path, monkeypatch):
    from _launcher import workspace
    root, folder, ctx = linked
    other = tmp_path / "other-brain"
    (other / ".brain-core").mkdir(parents=True)
    (other / ".brain-core/VERSION").write_text("0.71.4")
    vault_registry.register(other, "other")
    original = workspace._stage_folder
    def add_late_claim(*args, **kwargs):
        result = original(*args, **kwargs)
        workspace_registry.register_workspace(other, "shared", folder)
        return result
    monkeypatch.setattr(workspace, "_stage_folder", add_late_claim)
    before = (folder / ".brain/local/workspace.yaml").read_bytes()
    result = execute_upgrade(ctx, WorkspaceUpgradeRequest())
    assert not result.result.complete
    assert "Conflicting" in result.result.folders[0].message
    assert before == (folder / ".brain/local/workspace.yaml").read_bytes()


def test_partial_retains_structured_folder_progress(linked, tmp_path):
    root, folder, ctx = linked
    workspace_registry.register_workspace(root, "shared", folder)
    missing = tmp_path / "missing"
    workspace_registry.register_workspace(root, "shared", missing)
    result = execute_upgrade(ctx, WorkspaceUpgradeRequest(UpgradeScope.BRAIN))
    assert isinstance(result, Partial), result
    progress = result.error.details.progress
    assert not progress.complete
    assert {row.status for row in progress.folders} == {"changed", "blocked"}
    assert {row.path for row in progress.folders} == {str(folder), str(missing)}


def test_discovery_detects_other_brain_same_slug_as_conflict(linked, tmp_path):
    _, folder, ctx = linked
    other = tmp_path / "other-brain"
    (other / ".brain-core").mkdir(parents=True)
    (other / ".brain-core/VERSION").write_text("0.71.4")
    vault_registry.register(other, "other")
    workspace_registry.register_workspace(other, "shared", folder)
    result = execute_discover(ctx, WorkspaceDiscoverRequest((folder,)))
    assert result.result.folders[0].registration == "conflict"
    assert result.result.folders[0].command is None


def test_discovery_overlapping_roots_do_not_repeat_traversal_or_write(linked, tmp_path, monkeypatch):
    from _launcher import workspace as owner

    _, folder, ctx = linked
    nested = folder / 'nested'
    nested.mkdir()
    marker = nested / '.brain/local/workspace.yaml'
    marker.parent.mkdir(parents=True)
    marker.write_text('schema_version: 1\nunknown: retained\n')
    before = {str(path): path.read_bytes() for path in folder.rglob('*') if path.is_file()}
    walked = []
    real_walk = owner.os.walk

    def observed_walk(root, **kwargs):
        walked.append(root)
        yield from real_walk(root, **kwargs)

    monkeypatch.setattr(owner.os, 'walk', observed_walk)
    result = execute_discover(ctx, WorkspaceDiscoverRequest((folder, nested, folder)))
    assert result.result.complete
    assert {row.path for row in result.result.folders} == {str(folder), str(nested)}
    assert [Path(path) for path in walked if Path(path).is_relative_to(folder)] == [folder]
    assert before == {str(path): path.read_bytes() for path in folder.rglob('*') if path.is_file()}
