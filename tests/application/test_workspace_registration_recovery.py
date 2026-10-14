"""Registration and binding from folders that have no prior Brain context."""

import pytest

import vault_registry
from brain_test_support import folder_tree
from _bootstrap.workspace_binding import read_workspace_manifest
from _application.results import ErrorCode
from _application.workspace.register import WorkspaceRegisterRequest, prepare_register
from _application.workspace.register import decode

from _application.workspace.create import WorkspaceCreateRequest
from test_workspace_membership_policy import application


def test_registration_accepts_advisory_project_context_without_a_parent(command_vault_clone):
    root = command_vault_clone.vault_root
    template = root / "_Config/Templates/Living/Workspaces.md"
    template.write_text(template.read_text().replace(
        "type: living/workspace\n", "type: living/workspace\ntags: [project/command-fixture]\n"))
    app = application(root)

    created = app.invoke(WorkspaceCreateRequest("new-repo"))

    assert created.status == "ok", created
    assert created.result.status == "created"
    hub = root / created.result.path
    from _common import parse_frontmatter
    fields, _ = parse_frontmatter(hub.read_text())
    assert fields.get("parent") is None
    assert "project/command-fixture" in fields["tags"]
    again = app.invoke(WorkspaceCreateRequest("new-repo"))
    assert again.status == "ok", again
    assert again.result.status == "attached"
    assert again.committed_effects == ()


def test_setup_returns_creation_action_then_binds_without_project_defaults(command_vault_clone, tmp_path):
    root = command_vault_clone.vault_root
    workspace = tmp_path / "repo folder"
    workspace.mkdir()
    vault_registry.register(root, "specific-brain")
    app = application(root, workspace)
    request = WorkspaceRegisterRequest(slug="new-repo")
    before_folder = folder_tree(workspace)
    registry_path = root / ".brain/local/workspaces.json"
    before_registry = registry_path.read_bytes() if registry_path.exists() else None

    missing = app.invoke(request)

    assert missing.status == "error", missing
    assert missing.error.code is ErrorCode.NOT_FOUND
    assert missing.effects == "none"
    assert missing.error.next_action.command_id == "workspace.create"
    assert missing.error.next_action.arguments[0].value == "new-repo"
    assert "--vault" in missing.error.message and str(root) in missing.error.message
    assert "workspace/new-repo" in missing.error.message
    assert folder_tree(workspace) == before_folder
    assert (registry_path.read_bytes() if registry_path.exists() else None) == before_registry
    with pytest.raises(ValueError, match="Create it explicitly"):
        prepare_register(app._context, request)

    created = app.invoke(WorkspaceCreateRequest("new-repo"))
    assert created.status == "ok", created
    from _common import parse_frontmatter
    fields, _ = parse_frontmatter((root / created.result.path).read_text())
    assert fields.get("parent") is None
    assert fields.get("default_parent") is None
    bound = app.invoke(request)
    assert bound.status == "ok", bound
    assert bound.result.registration.status == "attached"
    assert not any(effect.kind == "workspace.registered" for effect in bound.committed_effects)
    assert read_workspace_manifest(workspace)["slug"] == "new-repo"
    again = app.invoke(request)
    assert again.status == "ok", again
    assert again.committed_effects == ()


@pytest.mark.parametrize("value", [True, False, None])
def test_setup_rejects_removed_creation_mode(value):
    with pytest.raises(ValueError, match="unexpected fields: require_existing"):
        decode({"require_existing": value})


def test_default_setup_refuses_a_missing_workspace(command_vault_clone, tmp_path):
    root = command_vault_clone.vault_root
    workspace = tmp_path / "missing-workspace"
    workspace.mkdir()
    vault_registry.register(root, "specific-brain")
    result = application(root, workspace).invoke(decode({}))
    assert result.status == "error", result
    assert "explicit" in result.error.message and "slug" in result.error.message
    assert folder_tree(workspace) == {}


def test_setup_refuses_terminal_workspace(command_vault_clone, tmp_path):
    root = command_vault_clone.vault_root
    workspace = tmp_path / "repo"
    workspace.mkdir()
    vault_registry.register(root, "specific-brain")
    app = application(root, workspace)
    created = app.invoke(WorkspaceCreateRequest("closed"))
    assert created.status == "ok", created
    hub = root / created.result.path
    hub.write_text(hub.read_text().replace("status: active", "status: completed"))
    import compile_router
    compile_router.persist_compiled_router(str(root), compile_router.compile(str(root)))
    before = folder_tree(workspace)

    result = app.invoke(WorkspaceRegisterRequest(slug="closed"))

    assert result.status == "error", result
    assert "terminal" in result.error.message
    assert result.effects == "none"
    assert folder_tree(workspace) == before
