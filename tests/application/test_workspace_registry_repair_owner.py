"""Owner behaviour for linked-workspace registry repair."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brain_test_support import link_folder, manifest_text

from _application.registry import current_application_catalogue, current_request_resolver
from _application.results import ErrorCode
from _application.types import Authority, EffectClass, InitialAuthorisationClass, Projection, RetryClass
from _application.workspace.repair_registry import (
    RegistryRepairStatus,
    WorkspaceRepairRegistryRequest,
)
from _portable import registry_maintenance
from command_application import application_for


REGISTRY_PATH = ".brain/local/workspaces.json"
# The person's explicit rebuild of a file whose rows cannot be read.
LOSSY = WorkspaceRepairRegistryRequest(allow_row_loss=True)


def _registry(root: Path) -> Path:
    path = root / REGISTRY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def test_workspace_registry_repair_is_noop_when_registry_is_canonical(
    command_vault_clone,
):
    root = command_vault_clone.vault_root
    path = _registry(root)
    path.write_text('{"workspaces": {}}\n')

    result = application_for(root).invoke(WorkspaceRepairRegistryRequest())

    assert result.status == "ok"
    assert result.result.status is RegistryRepairStatus.NOOP
    assert result.result.entry_count == 0
    assert result.result.backup_path is None
    assert result.committed_effects == ()


def test_workspace_registry_repair_normalises_legacy_entries(command_vault_clone):
    root = command_vault_clone.vault_root
    path = _registry(root)
    path.write_text(json.dumps({"workspaces": {"external": "/tmp/external"}}))

    result = application_for(root).invoke(WorkspaceRepairRegistryRequest())

    assert result.status == "ok"
    assert result.result.status is RegistryRepairStatus.CHANGED
    assert result.result.entry_count == 1
    assert result.result.backup_path is None
    assert result.committed_effects[0].subject == REGISTRY_PATH
    assert json.loads(path.read_text()) == {
        "workspaces": {"external": {"path": "/tmp/external"}}
    }


def test_workspace_registry_repair_preserves_malformed_input(command_vault_clone):
    root = command_vault_clone.vault_root
    path = _registry(root)
    malformed = "{broken json\n"
    path.write_text(malformed)

    result = application_for(root).invoke(LOSSY)

    assert result.status == "ok"
    assert result.result.status is RegistryRepairStatus.CHANGED
    assert result.result.backup_path == ".brain/local/workspaces.json.bak"
    assert json.loads(path.read_text()) == {"workspaces": {}}
    backup = root / result.result.backup_path
    assert backup.read_text() == malformed
    assert tuple(effect.subject for effect in result.committed_effects) == (
        REGISTRY_PATH,
        result.result.backup_path,
    )


def test_workspace_registry_repair_dry_run_does_not_create_backup(
    command_vault_clone,
):
    root = command_vault_clone.vault_root
    path = _registry(root)
    malformed = "[broken\n"
    path.write_text(malformed)

    result = application_for(root, dry_run=True).invoke(
        LOSSY
    )

    assert result.status == "ok"
    assert result.result.status is RegistryRepairStatus.PLANNED
    assert result.committed_effects == ()
    assert path.read_text() == malformed
    assert not (path.parent / "workspaces.json.bak").exists()


def test_workspace_registry_write_failure_restores_original_and_has_no_effect(
    command_vault_clone,
    monkeypatch,
):
    root = command_vault_clone.vault_root
    path = _registry(root)
    malformed = "{broken json\n"
    path.write_text(malformed)
    monkeypatch.setattr(
        registry_maintenance.workspace_registry,
        "replace_registry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("registry write failed")),
    )

    result = application_for(root).invoke(LOSSY)

    assert result.error.code is ErrorCode.CONFLICT
    assert result.effects == "none"
    assert path.read_text() == malformed
    assert not (path.parent / "workspaces.json.bak").exists()


def test_workspace_registry_backup_failure_after_the_save_is_known_partial(
    command_vault_clone,
    monkeypatch,
):
    root = command_vault_clone.vault_root
    path = _registry(root)
    malformed = "{broken json\n"
    path.write_text(malformed)
    real_replace = Path.replace

    def fail_backup(self, target):
        if Path(target).name == "workspaces.json.bak":
            raise OSError("backup move failed")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_backup)

    result = application_for(root).invoke(LOSSY)

    assert result.status == "partial"
    assert result.error.code is ErrorCode.CONFLICT
    staged = root / result.committed_effects[0].subject
    assert staged.read_text() == malformed
    assert json.loads(path.read_text()) == {"workspaces": {}}


def test_workspace_registry_repair_refuses_an_unreadable_file_without_effect(command_vault_clone):
    import os
    import sys

    if sys.platform == "win32" or os.geteuid() == 0:
        pytest.skip("POSIX permission bits that bind the test user")
    root = command_vault_clone.vault_root
    path = _registry(root)
    path.write_text(json.dumps({"workspaces": {"kept": {"path": "/tmp/kept"}}}))
    before = path.read_bytes()
    path.chmod(0)
    try:
        result = application_for(root).invoke(WorkspaceRepairRegistryRequest())
    finally:
        path.chmod(0o644)

    assert result.status == "error"
    assert result.error.code is ErrorCode.CONFLICT
    assert result.effects == "none"
    assert "could not be read" in result.error.message
    assert path.read_bytes() == before
    assert not (path.parent / "workspaces.json.bak").exists()


def test_workspace_registry_failed_save_keeps_the_earlier_backup(command_vault_clone, monkeypatch):
    root = command_vault_clone.vault_root
    path = _registry(root)
    backup = path.parent / "workspaces.json.bak"
    backup.write_text("earlier backup\n")
    path.write_text("{broken json\n")
    monkeypatch.setattr(
        registry_maintenance.workspace_registry,
        "replace_registry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("registry write failed")),
    )

    result = application_for(root).invoke(LOSSY)

    assert result.effects == "none"
    assert backup.read_text() == "earlier backup\n"
    assert path.read_text() == "{broken json\n"
    assert sorted(item.name for item in path.parent.glob("*workspaces.json*")) == ["workspaces.json", "workspaces.json.bak"]


def test_workspace_registry_repair_is_an_ordinary_derived_cache_command():
    request = current_request_resolver().resolve("workspace.repair-registry", {})
    entry = current_application_catalogue().resolve(request)

    assert type(request) is WorkspaceRepairRegistryRequest
    assert entry.initial_class is InitialAuthorisationClass.OBSERVATION
    assert entry.authority is Authority.MAINTAINER
    assert entry.effect_class is EffectClass.DERIVED_CACHE_WRITE
    assert entry.retry_class is RetryClass.SAFE
    assert set(entry.eligible_projections) == {Projection.CLI, Projection.SCRIPT, Projection.PYTHON}
    with pytest.raises(ValueError, match="unexpected fields"):
        current_request_resolver().resolve(
            "workspace.repair-registry",
            {"force": True},
        )
    with pytest.raises(ValueError, match="allow_row_loss must be a boolean"):
        current_request_resolver().resolve(
            "workspace.repair-registry",
            {"allow_row_loss": "yes"},
        )


def test_workspace_registry_repair_overwrites_the_one_fixed_name_backup(command_vault_clone):
    root = command_vault_clone.vault_root
    path = _registry(root)
    backup = path.parent / "workspaces.json.bak"
    for malformed in ("{first\n", "{second\n"):
        path.write_text(malformed)
        result = application_for(root).invoke(LOSSY)
        assert result.result.backup_path == ".brain/local/workspaces.json.bak"
        assert backup.read_text() == malformed
    assert sorted(item.name for item in path.parent.glob("workspaces.json*")) == ["workspaces.json", "workspaces.json.bak"]


@pytest.mark.parametrize("content", ["{broken json\n", "[1]\n", '{"workspaces": []}\n', b"\xff\xfe"])
def test_an_unattended_repair_never_rebuilds_a_file_whose_rows_cannot_be_read(command_vault_clone, content):
    root = command_vault_clone.vault_root
    path = _registry(root)
    path.write_bytes(content if isinstance(content, bytes) else content.encode())
    before = path.read_bytes()

    result = application_for(root).invoke(WorkspaceRepairRegistryRequest())

    assert result.status == "error"
    assert result.error.code is ErrorCode.CONFLICT
    assert result.effects == "none"
    assert "never rebuilt automatically" in result.error.message
    assert "allow_row_loss" in result.error.message
    assert path.read_bytes() == before
    assert not (path.parent / "workspaces.json.bak").exists()


def test_invalid_rows_are_dropped_unattended_and_the_file_is_backed_up(command_vault_clone):
    root = command_vault_clone.vault_root
    path = _registry(root)
    original = json.dumps({"workspaces": {
        "kept": {"path": "/tmp/kept"}, "relative": "foreign", "empty": "", "nul": "/tmp/a\u0000b",
        "Not A Key!": "/tmp/bad-key", "no-path": {"mode": "linked"},
    }})
    path.write_text(original)

    result = application_for(root).invoke(WorkspaceRepairRegistryRequest())

    assert result.status == "ok"
    assert result.result.status is RegistryRepairStatus.CHANGED
    assert result.result.dropped == ()
    assert json.loads(path.read_text()) == {"workspaces": {"kept": {"path": "/tmp/kept"}}}
    assert (root / result.result.backup_path).read_text() == original


@pytest.mark.parametrize("dry_run", [True, False])
def test_a_dropped_row_names_its_key_and_the_folder_it_recorded(command_vault_clone, tmp_path, dry_run):
    import vault_registry
    import workspace_registry
    from _application.workspace.repair_registry import RegistryDroppedRow

    root = command_vault_clone.vault_root
    vault_registry.register(root, "owner-brain")
    folder = link_folder(root, tmp_path / "stale", "stale", manifest_text("another", brain="owner-brain"))
    before = _registry(root).read_bytes()

    result = application_for(root, dry_run=dry_run).invoke(WorkspaceRepairRegistryRequest())

    assert result.status == "ok", result
    assert result.result.status is (RegistryRepairStatus.PLANNED if dry_run else RegistryRepairStatus.CHANGED)
    assert result.result.dropped == (RegistryDroppedRow("stale", str(folder)),)
    assert result.result.backup_path is None, "a well-formed file is not backed up; `dropped` is the record"
    if dry_run:
        assert _registry(root).read_bytes() == before
        assert result.committed_effects == ()
    else:
        assert workspace_registry.load_registry(root) == {}


def test_a_row_mcp_commits_inside_the_repairs_locked_window_survives(command_vault_clone, tmp_path, monkeypatch):
    """DM1: the repair compare-and-swaps the bytes it read under the lock, so MCP's committed row is never lost."""
    import vault_registry
    import workspace_registry
    from _bootstrap.file_transaction import FilePlan, apply_file_changes

    root = command_vault_clone.vault_root
    vault_registry.register(root, "owner-brain")
    link_folder(root, tmp_path / "stale", "stale", manifest_text("another", brain="owner-brain"))
    mcp_folder = (tmp_path / "by-mcp").resolve()
    mcp_folder.mkdir()
    real = registry_maintenance._still_disagrees

    def then_mcp_commits(*args):
        verdict = real(*args)
        plan = FilePlan()
        workspace_registry.stage_link_row(plan, root, "by-mcp", mcp_folder)
        apply_file_changes(plan.changes())
        return verdict

    monkeypatch.setattr(registry_maintenance, "_still_disagrees", then_mcp_commits)
    result = application_for(root).invoke(WorkspaceRepairRegistryRequest())

    assert result.status == "error" and result.effects == "none" and result.retryable
    assert result.error.code is ErrorCode.CONFLICT
    assert set(workspace_registry.load_registry(root)) == {"stale", "by-mcp"}, "MCP's row survives; the next pass re-detects"


@pytest.mark.parametrize("allow_row_loss", [False, True])
def test_a_file_that_tears_between_the_read_and_the_lock_is_never_rebuilt(command_vault_clone, tmp_path, monkeypatch,
                                                                          allow_row_loss):
    """DS2: a lossy rebuild is admitted only for the unparseable file the person acted on."""
    import vault_registry
    import workspace_registry

    root = command_vault_clone.vault_root
    vault_registry.register(root, "owner-brain")
    link_folder(root, tmp_path / "kept", "kept", manifest_text("kept", brain="owner-brain"))
    link_folder(root, tmp_path / "stale", "stale", manifest_text("another", brain="owner-brain"))
    path = _registry(root)
    real = registry_maintenance._disagreeing

    def then_tear(verification):
        planned = real(verification)
        path.write_text("{torn")
        return planned

    monkeypatch.setattr(registry_maintenance, "_disagreeing", then_tear)
    result = application_for(root).invoke(WorkspaceRepairRegistryRequest(allow_row_loss=allow_row_loss))

    assert result.status == "error" and result.effects == "none"
    assert path.read_text() == "{torn"
    assert not (path.parent / "workspaces.json.bak").exists()


def test_the_lossy_rebuild_names_its_backup_and_the_way_back(command_vault_clone):
    root = command_vault_clone.vault_root
    path = _registry(root)
    path.write_text("{broken json\n")

    result = application_for(root).invoke(LOSSY)

    assert result.result.status is RegistryRepairStatus.CHANGED
    assert "workspaces.json.bak" in result.result.reason and "workspace setup" in result.result.reason
    assert "never rebuilt automatically" not in result.result.reason


def test_a_tilde_row_is_normalised_to_its_canonical_path(command_vault_clone, tmp_path, monkeypatch):
    import workspace_registry
    from _bootstrap.diagnostics import RegistryCondition, inspect_registry

    root = command_vault_clone.vault_root
    monkeypatch.setenv("HOME", str(tmp_path))
    path = _registry(root)
    path.write_text(json.dumps({"workspaces": {"home": {"path": "~/linked"}}}))

    assert inspect_registry(root).condition is RegistryCondition.NORMALISABLE
    result = application_for(root).invoke(WorkspaceRepairRegistryRequest())

    assert result.result.status is RegistryRepairStatus.CHANGED and result.result.backup_path is None
    assert workspace_registry.load_registry(root) == {"home": {"path": str(tmp_path / "linked")}}
