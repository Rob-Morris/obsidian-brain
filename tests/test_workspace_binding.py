"""Tests for workspace binding convergence and manifest migration."""

from __future__ import annotations

import pytest

import _bootstrap.workspace_binding as workspace_binding
from _bootstrap.workspace_binding import (
    WORKSPACE_ERROR_FILESYSTEM_ACCESS,
    WorkspaceBindingError,
    converge_workspace_binding,
    load_workspace_manifest_state,
)


def test_converge_workspace_binding_migrates_legacy_manifest(tmp_path):
    workspace = tmp_path / "workspace"
    legacy = workspace / ".brain" / "workspace.yaml"
    canonical = workspace / ".brain" / "local" / "workspace.yaml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        "brain: old-brain\n"
        "slug: legacy-slug\n"
        "defaults:\n"
        "  tags:\n"
        "    - workspace/legacy\n",
        encoding="utf-8",
    )

    result = converge_workspace_binding(
        workspace,
        brain="brain",
        allow_rebind=True,
    )

    assert result.status == "changed"
    assert result.migrated_legacy is True
    assert result.slug == "legacy-slug"
    assert not legacy.exists()
    assert canonical.read_text(encoding="utf-8") == (
        "brain: brain\n"
        "slug: legacy-slug\n"
        "defaults:\n"
        "  tags:\n"
        "    - workspace/legacy\n"
    )


def test_converge_workspace_binding_preserves_existing_custom_slug(tmp_path):
    workspace = tmp_path / "workspace"
    canonical = workspace / ".brain" / "local" / "workspace.yaml"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("slug: custom-slug\n", encoding="utf-8")

    result = converge_workspace_binding(
        workspace,
        brain="brain",
        allow_rebind=False,
    )

    assert result.status == "changed"
    assert result.slug == "custom-slug"
    assert canonical.read_text(encoding="utf-8") == "brain: brain\nslug: custom-slug\n"


def test_unreadable_workspace_manifest_uses_filesystem_code(tmp_path, monkeypatch):
    """Manifest read failures carry filesystem_access so callers can give correct remediation."""
    manifest = tmp_path / ".brain" / "local" / "workspace.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("brain: x\nslug: ws\n")

    def unreadable(_path):
        raise PermissionError("denied")

    monkeypatch.setattr(workspace_binding, "load_mapping_file", unreadable)

    with pytest.raises(WorkspaceBindingError) as exc_info:
        load_workspace_manifest_state(tmp_path)
    assert exc_info.value.code == WORKSPACE_ERROR_FILESYSTEM_ACCESS
