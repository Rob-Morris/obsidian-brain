"""Tests for workspace binding convergence and manifest migration."""

from __future__ import annotations

from _bootstrap.workspace_binding import converge_workspace_binding


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
