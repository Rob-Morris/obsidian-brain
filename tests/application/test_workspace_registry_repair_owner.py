"""Owner behaviour for linked-workspace registry repair."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _application.registry import current_application_catalogue, current_request_resolver
from _application.results import ErrorCode
from _application.types import Authority, EffectClass, RetryClass
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
    assert result.result.backup_path.startswith(
        ".brain/local/workspaces.json."
    )
    assert result.result.backup_path.endswith(".bak")
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
    assert list(path.parent.glob("workspaces.json.*.bak")) == []


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
    assert list(path.parent.glob("workspaces.json.*.bak")) == []


def test_workspace_registry_restore_failure_is_known_partial(
    command_vault_clone,
    monkeypatch,
):
    root = command_vault_clone.vault_root
    path = _registry(root)
    path.write_text("{broken json\n")
    monkeypatch.setattr(
        registry_maintenance.workspace_registry,
        "save_registry",
        lambda *_args: (_ for _ in ()).throw(OSError("registry write failed")),
    )
    real_rename = Path.rename

    def fail_restore(self, target):
        if self.name.endswith(".bak"):
            raise OSError("registry restore failed")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", fail_restore)

    result = application_for(root).invoke(WorkspaceRepairRegistryRequest())

    assert result.status == "partial"
    assert result.error.code is ErrorCode.CONFLICT
    assert result.committed_effects[0].subject.endswith(".bak")
    assert not path.exists()
    assert (root / result.committed_effects[0].subject).is_file()


def test_workspace_registry_repair_transport_is_strict_operator_command():
    request = current_request_resolver().resolve("workspace.repair-registry", {})
    entry = current_application_catalogue().resolve(request)

    assert type(request) is WorkspaceRepairRegistryRequest
    assert entry.authority is Authority.OPERATOR
    assert entry.effect_class is EffectClass.SELECTED_BRAIN_MUTATION
    assert entry.retry_class is RetryClass.RECEIPT_REQUIRED
    with pytest.raises(ValueError, match="unexpected fields"):
        current_request_resolver().resolve(
            "workspace.repair-registry",
            {"force": True},
        )
