"""Caller manifest updates remain usable without admitting durable vault edits."""

import pytest

from _application.context import Capability
from _application.results import ErrorCode
from _application.types import Availability
from _application.workspace.update_metadata import (
    WorkspaceMetadataLink,
    WorkspaceUpdateMetadataRequest,
)
from _bootstrap import branch_guard
from _bootstrap.local_config import patch_local_config, read_local_config
from _bootstrap.workspace_binding import read_workspace_manifest
from command_application import application_for


class CallerFilesystemProvider:
    provider_id = "caller_filesystem"


@pytest.mark.parametrize("permitted", [True, False])
def test_metadata_updates_only_separate_caller_on_blocked_vault(tmp_path, monkeypatch, permitted):
    root = tmp_path / "vault"
    root.mkdir()
    patch_local_config(root, lambda data: data.update(
        branch_guard={"allowed_branches": ["dev"]}))
    monkeypatch.setattr(branch_guard, "observe_git",
                        lambda _: branch_guard.GitState(str(root), "main"))
    assert branch_guard.inspect_guard(root)["status"] == "blocked"
    policy_before = read_local_config(root)
    hub = root / "Workspaces" / "example.md"
    hub.parent.mkdir()
    hub.write_text("Authoritative workspace hub remains unchanged.\n")
    hub_before = hub.read_bytes()

    caller = tmp_path / "caller"
    manifest = caller / ".brain/local/workspace.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "schema_version: 1\nbrain: command-vault\nslug: example\n"
        "defaults:\n  tags: [project/old]\nlinks:\n  repository: old\n")
    manifest_before = manifest.read_bytes()
    app = application_for(
        root, workspace_dir=caller,
        providers=(CallerFilesystemProvider(),),
        capabilities=(Capability("caller_filesystem", Availability.AVAILABLE),),
        allowed_commands=None if permitted else (),
    )
    result = app.invoke(WorkspaceUpdateMetadataRequest(
        clear_tags=True, links=(WorkspaceMetadataLink("repository", "new"),)))

    assert read_local_config(root) == policy_before
    assert hub.read_bytes() == hub_before
    if not permitted:
        assert result.status == "error" and result.effects == "none"
        assert result.error.code is ErrorCode.AUTHORITY_DENIED
        assert manifest.read_bytes() == manifest_before
        return
    assert result.status == "ok", result
    assert read_workspace_manifest(caller) == {
        "schema_version": 1, "brain": "command-vault", "slug": "example",
        "links": {"repository": "new"},
    }
    assert [(effect.kind, effect.subject) for effect in result.committed_effects] == [
        ("workspace.update-metadata", "caller-workspace:.brain/local/workspace.yaml")]
