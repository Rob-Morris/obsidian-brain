"""Tests for workspace binding planning, manifest writes and legacy migration."""

from __future__ import annotations

import pytest

import _bootstrap.workspace_binding as workspace_binding
from _bootstrap.workspace_binding import (
    WORKSPACE_ERROR_FILESYSTEM_ACCESS,
    WorkspaceBindingError,
    load_workspace_manifest_state,
    plan_workspace_binding,
    save_workspace_manifest_data,
)


def _plan_then_save(workspace, **kwargs):
    state, payload = plan_workspace_binding(workspace, **kwargs)
    return payload, save_workspace_manifest_data(workspace, payload, state=state)


def test_binding_write_migrates_legacy_manifest(tmp_path):
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

    payload, result = _plan_then_save(workspace, brain="brain", allow_rebind=True)

    assert result.status == "changed"
    assert result.migrated_legacy is True
    assert payload["slug"] == "legacy-slug"
    assert not legacy.exists()
    assert canonical.read_text(encoding="utf-8") == (
        "brain: brain\n"
        "slug: legacy-slug\n"
        "defaults:\n"
        "  tags:\n"
        "    - workspace/legacy\n"
    )


def test_binding_write_preserves_existing_custom_slug(tmp_path):
    workspace = tmp_path / "workspace"
    canonical = workspace / ".brain" / "local" / "workspace.yaml"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("slug: custom-slug\n", encoding="utf-8")

    payload, result = _plan_then_save(workspace, brain="brain", allow_rebind=False)

    assert result.status == "changed"
    assert payload["slug"] == "custom-slug"
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


@pytest.fixture
def linked_brain(tmp_path):
    import vault_registry

    vault = tmp_path / "Brain"
    (vault / ".brain-core").mkdir(parents=True)
    (vault / ".brain-core" / "VERSION").write_text("1.0.0\n")
    vault_registry.register(str(vault), brain_id="this-brain")
    other = tmp_path / "Other"
    (other / ".brain-core").mkdir(parents=True)
    (other / ".brain-core" / "VERSION").write_text("1.0.0\n")
    vault_registry.register(str(other), brain_id="other-brain")
    return vault


def _folder(tmp_path, manifest):
    folder = tmp_path / "folder"
    folder.mkdir(exist_ok=True)
    if manifest is not None:
        path = folder / ".brain" / "local" / "workspace.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(manifest)
    return folder


@pytest.mark.parametrize(("manifest", "verdict"), [
    ("brain: this-brain\nslug: s\nlinks:\n  workspace: key\n", "matches"),
    (None, "no_manifest"),
    ("brain: [unclosed\n", "unreadable"),
    ("slug: s\nlinks:\n  workspace: key\n", "brain_unresolved"),
    ("brain: unknown-here\nlinks:\n  workspace: key\n", "brain_unresolved"),
    ("brain: other-brain\nlinks:\n  workspace: key\n", "other_brain"),
    ("brain: this-brain\nlinks:\n  workspace: another\n", "other_key"),
    ("brain: this-brain\nslug: bind-era\n", "key_missing"),
])
def test_classify_link_gives_one_verdict_per_folder(tmp_path, linked_brain, manifest, verdict):
    from _bootstrap.workspace_binding import classify_link

    folder = _folder(tmp_path, manifest)
    before = sorted(str(path) for path in folder.rglob("*"))

    assert classify_link(linked_brain, folder, "key").verdict.value == verdict
    assert sorted(str(path) for path in folder.rglob("*")) == before


def test_classify_link_reports_an_unreachable_folder_and_a_vault_root(tmp_path, linked_brain):
    from _bootstrap.workspace_binding import LinkVerdict, classify_link

    assert classify_link(linked_brain, tmp_path / "gone", "key").verdict is LinkVerdict.UNREACHABLE
    assert classify_link(linked_brain, linked_brain, "key").verdict is LinkVerdict.VAULT_ROOT


def test_unlinked_payload_removes_only_the_link_fields():
    from _bootstrap.workspace_binding import LINK_FIELDS, linked_payload, states_a_link, unlinked_payload

    manifest = {"brain": "b", "slug": "s", "defaults": {"tags": ["t"]}, "links": {"workspace": "k", "repo": "r"}}

    assert LINK_FIELDS == ("brain", "links.workspace")
    assert unlinked_payload(manifest) == {"slug": "s", "defaults": {"tags": ["t"]}, "links": {"repo": "r"}}
    assert unlinked_payload({"brain": "b", "links": {"workspace": "k"}}) == {}
    assert not states_a_link(unlinked_payload(manifest))
    assert linked_payload(unlinked_payload(manifest), key="k")["links"] == {"repo": "r", "workspace": "k"}
