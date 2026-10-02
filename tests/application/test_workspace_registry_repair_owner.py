"""Owner behaviour for linked-workspace registry repair."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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

    result = application_for(root).invoke(WorkspaceRepairRegistryRequest())

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
        WorkspaceRepairRegistryRequest()
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
        "save_registry",
        lambda *_args: (_ for _ in ()).throw(OSError("registry write failed")),
    )

    result = application_for(root).invoke(WorkspaceRepairRegistryRequest())

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

    result = application_for(root).invoke(WorkspaceRepairRegistryRequest())

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
        "save_registry",
        lambda *_args: (_ for _ in ()).throw(OSError("registry write failed")),
    )

    result = application_for(root).invoke(WorkspaceRepairRegistryRequest())

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


def test_workspace_registry_repair_overwrites_the_one_fixed_name_backup(command_vault_clone):
    root = command_vault_clone.vault_root
    path = _registry(root)
    backup = path.parent / "workspaces.json.bak"
    for malformed in ("{first\n", "{second\n"):
        path.write_text(malformed)
        result = application_for(root).invoke(WorkspaceRepairRegistryRequest())
        assert result.result.backup_path == ".brain/local/workspaces.json.bak"
        assert backup.read_text() == malformed
    assert sorted(item.name for item in path.parent.glob("workspaces.json*")) == ["workspaces.json", "workspaces.json.bak"]
