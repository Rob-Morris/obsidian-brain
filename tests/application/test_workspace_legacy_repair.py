"""Explicit repair preserves both project-owned and workspace-owned legacy branches."""

import compile_router

from _application.artefact.create import ArtefactCreateRequest
from _application.artefact.reparent import ArtefactReparentRequest
from _application.artefact.set_workspace import ArtefactSetWorkspaceRequest
from _application.workspace.create import WorkspaceCreateRequest
from _application.workspace.update_policy import WorkspaceUpdatePolicyRequest
from _application.workspace_context import WorkspaceSelector
from _common import parse_frontmatter, serialize_frontmatter
from _lifecycle.ownership_graph import read_ownership_graph
from command_application import application_for


def _seed(root, path, fields, body):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(serialize_frontmatter(fields, body=body))


def _repair(app):
    detached = app.invoke(ArtefactReparentRequest("workspace/legacy", None,
        workspace_context=WorkspaceSelector("global")))
    assert detached.status == "ok", detached.error.message
    for owner in ("workspace/legacy", "project/legacy"):
        adopted = app.invoke(ArtefactSetWorkspaceRequest(owner, recursive=True,
            workspace_context=WorkspaceSelector("workspace/legacy")))
        assert adopted.status == "ok", adopted.error.message
    policy = app.invoke(WorkspaceUpdatePolicyRequest("workspace/legacy", "project/legacy"))
    assert policy.status == "ok", policy


def test_legacy_project_and_workspace_branches_repair_preserves_parentage_and_repeats(command_vault_clone):
    root = command_vault_clone.vault_root
    app = application_for(root)
    project = app.invoke(ArtefactCreateRequest("living/project", "Legacy", key="legacy"))
    workspace = app.invoke(WorkspaceCreateRequest("legacy"))
    assert project.status == workspace.status == "ok"
    old_hub = root / workspace.result.path
    hub_fields, hub_body = parse_frontmatter(old_hub.read_text())
    hub_fields["parent"] = "project/legacy"
    hub_fields["tags"].append("project/legacy")
    old_hub.unlink()
    _seed(root, "Workspaces/project~legacy/Legacy.md", hub_fields, hub_body)
    records = [
        ("Designs/project~legacy/Project Note.md", {"type": "living/design", "key": "project-note", "parent": "project/legacy"}, "Project-owned body.\n"),
        ("Designs/project~legacy/workspace~legacy/Workspace Wiki.md", {"type": "living/design", "key": "workspace-wiki", "parent": "workspace/legacy"}, "Workspace-owned body.\n"),
        ("_Temporal/Plans/project~legacy/workspace~legacy/20261015-plan~Evidence.md", {"type": "temporal/plan", "parent": "workspace/legacy"}, "Temporal evidence.\n"),
        ("_Archive/Designs/Archived Child.md", {"type": "living/design", "key": "archived-child", "parent": "design/workspace-wiki", "archiveddate": "2026-10-14"}, "Archived evidence.\n"),
    ]
    for path, fields, body in records:
        _seed(root, path, fields, body)
    compile_router.persist_compiled_router(str(root), compile_router.compile(str(root)))
    _repair(app)
    graph = read_ownership_graph(root)
    hub = graph.resolve("workspace/legacy")
    assert hub.parent is None
    assert hub.fields["default_parent"] == "project/legacy"
    assert graph.resolve("project/legacy").parent is None
    assert graph.resolve("design/project-note").parent == "project/legacy"
    assert graph.resolve("design/workspace-wiki").parent == "workspace/legacy"
    assert graph.resolve("design/archived-child").parent == "design/workspace-wiki"
    assert graph.resolve("design/archived-child").archived
    for reference in ("project/legacy", "design/project-note", "design/workspace-wiki", "design/archived-child"):
        assert graph.resolve(reference).fields["workspace"] == "workspace/legacy"
    temporal = next(item for item in graph.records if item.body == "Temporal evidence.\n")
    assert temporal.fields["workspace"] == "workspace/legacy"
    assert temporal.parent == "workspace/legacy"
    _repair(app)
    created = app.invoke(ArtefactCreateRequest("living/design", "Default Child", key="default-child",
        workspace_context=WorkspaceSelector("workspace/legacy")))
    assert created.status == "ok", created
    fields, _body = parse_frontmatter((root / created.result.path).read_text())
    assert fields["parent"] == "project/legacy"
    assert fields["workspace"] == "workspace/legacy"
    for _path, _fields, body in records:
        assert any(item.body == body for item in read_ownership_graph(root).records)


def test_legacy_scope_rename_composes_guarded_commands_without_losing_local_metadata(
    command_vault_clone, tmp_path
):
    import asyncio

    import vault_registry
    import workspace_registry
    from mcp.server import MCPServer
    from brain_mcp._command_adapter import register_application_tools
    from _application.registry import current_application_catalogue, current_request_resolver
    from _application.artefact.rename import ArtefactRenameRequest
    from _application.artefact.set_key import ArtefactSetKeyRequest
    from _application.workspace.register import WorkspaceRegisterRequest
    from _application.workspace.unregister import WorkspaceUnregisterRequest
    from _application.workspace.update_metadata import WorkspaceUpdateMetadataRequest, WorkspaceMetadataLink
    from _bootstrap.workspace_binding import read_workspace_manifest
    from test_workspace_membership_policy import application

    root = command_vault_clone.vault_root
    folder = tmp_path / "case-planning-folder"
    folder.mkdir()
    vault_registry.register(root, "fixture-brain")
    app = application(root)
    local = application(root, folder)
    old = "workspace/case-planning-financial-analysis"
    new = "workspace/case-planning"
    project = "project/case-planning"
    global_scope = WorkspaceSelector("global")

    def invoke(owner, request):
        result = owner.invoke(request)
        assert result.status == "ok", result.error.message if result.status == "error" else result
        return result

    invoke(app, ArtefactCreateRequest("living/project", "Case Planning", key="case-planning"))
    created = invoke(app, WorkspaceCreateRequest("case-planning-financial-analysis"))
    hub_path = root / created.result.path
    fields, _body = parse_frontmatter(hub_path.read_text())
    fields.update(parent=project, default_parent=project)
    hub_path.unlink()
    hub_body = "The whole Case Planning scope, including financial analysis.\n"
    _seed(root, "Workspaces/project~case-planning/Financial Analysis.md", fields, hub_body)
    records = [
        ("Designs/project~case-planning/Project Branch.md", {"type": "living/design", "key": "project-branch", "parent": project}, "Project branch.\n"),
        ("Designs/project~case-planning/workspace~case-planning-financial-analysis/Wiki.md", {"type": "living/design", "key": "wiki", "parent": old}, "Whole-scope wiki.\n"),
        ("_Temporal/Plans/Scope.md", {"type": "temporal/plan", "parent": "design/wiki"}, "Temporal scope.\n"),
        ("_Archive/Designs/Evidence.md", {"type": "living/design", "key": "evidence", "parent": "design/wiki", "archiveddate": "2026-10-14"}, "Archived scope.\n"),
    ]
    for path, metadata, body in records:
        _seed(root, path, metadata, body)
    compile_router.persist_compiled_router(str(root), compile_router.compile(str(root)))

    invoke(app, ArtefactReparentRequest(old, None, workspace_context=global_scope))
    invoke(app, WorkspaceUpdatePolicyRequest(old, clear_parent=True))
    for owner in (old, project):
        invoke(app, ArtefactSetWorkspaceRequest(owner, recursive=True, workspace_context=WorkspaceSelector(old)))
    invoke(app, WorkspaceUpdatePolicyRequest(old, project, default_tags=("scope",)))
    invoke(local, WorkspaceRegisterRequest(slug=old.split("/", 1)[1]))
    invoke(local, WorkspaceUpdateMetadataRequest(parent=project, tags=("local",),
        links=(WorkspaceMetadataLink("repository", "fixture-repository"),)))
    manifest_before = read_workspace_manifest(folder)
    graph_before = read_ownership_graph(root)
    parents = {item.body: item.parent for item in graph_before.records
               if item.body in {body for _, _, body in records} | {hub_body}}

    mcp = MCPServer("legacy-workspace-rename")
    register_application_tools(mcp, catalogue=current_application_catalogue(),
        resolver=current_request_resolver(), context_factory=lambda **_metadata: app._context,
        invocation_guard=lambda: None)
    blocked = asyncio.run(mcp.call_tool("artefact_set-key", {"path": old, "key": "case-planning",
        "workspace_context": "global"}))
    assert blocked.is_error, blocked
    assert "workspaces.json" in str(blocked)
    assert read_workspace_manifest(folder) == manifest_before

    invoke(local, WorkspaceUnregisterRequest())
    unlinked = read_workspace_manifest(folder)
    assert unlinked == {key: value for key, value in manifest_before.items() if key not in {"brain", "slug"}}
    blocked = app.invoke(ArtefactSetKeyRequest(old, "case-planning", workspace_context=global_scope))
    assert blocked.status == "error" and blocked.effects == "none", blocked
    assert "workspace" in blocked.error.message
    blocked = app.invoke(ArtefactSetWorkspaceRequest(project, recursive=True, workspace_context=global_scope))
    assert blocked.status == "error" and blocked.effects == "none", blocked
    assert "default_parent" in blocked.error.message

    invoke(app, WorkspaceUpdatePolicyRequest(old, clear_parent=True))
    blocked = local.invoke(ArtefactSetWorkspaceRequest(project, recursive=True, workspace_context=global_scope))
    assert blocked.status == "error" and blocked.effects == "none", blocked
    assert "defaults.parent" in blocked.error.message
    invoke(local, WorkspaceUpdateMetadataRequest(clear_parent=True))
    blocked = app.invoke(ArtefactSetWorkspaceRequest(old, recursive=True, workspace_context=global_scope))
    assert blocked.status == "error" and blocked.effects == "none", blocked
    assert "self-scoped" in blocked.error.message
    invoke(app, ArtefactSetWorkspaceRequest("design/wiki", recursive=True, clear_parent=True,
        workspace_context=global_scope))
    invoke(app, ArtefactSetWorkspaceRequest(project, recursive=True, workspace_context=global_scope))
    invoke(app, ArtefactSetKeyRequest(old, "case-planning", workspace_context=global_scope))
    hub = read_ownership_graph(root).resolve(new)
    invoke(app, ArtefactRenameRequest(hub.path, "Workspaces/Case Planning.md", workspace_context=global_scope))
    invoke(app, ArtefactSetWorkspaceRequest("design/wiki", recursive=True, parent=new,
        workspace_context=WorkspaceSelector(new)))
    invoke(app, ArtefactSetWorkspaceRequest(project, recursive=True, workspace_context=WorkspaceSelector(new)))
    invoke(app, WorkspaceUpdatePolicyRequest(new, project))
    invoke(local, WorkspaceRegisterRequest(slug="case-planning"))
    invoke(local, WorkspaceUpdateMetadataRequest(parent=project))

    assert read_workspace_manifest(folder) == {**manifest_before, "slug": "case-planning"}
    rows = workspace_registry.load_registry_strict(root)
    assert set(rows) == {str(folder.resolve())}
    assert rows[str(folder.resolve())]["slug"] == "case-planning"
    graph = read_ownership_graph(root)
    assert graph.resolve(new).path == "Workspaces/Case Planning.md"
    assert graph.resolve(new).body == hub_body
    assert graph.resolve(new).parent is None
    assert graph.resolve(new).fields["default_parent"] == project
    assert graph.resolve(new).fields["default_tags"] == ["scope"]
    assert graph.resolve(project).parent is None
    assert graph.resolve(project).fields["workspace"] == new
    assert all(item.reference != old for item in graph.records)
    assert len(parents) == len(records) + 1
    for body, parent in parents.items():
        item = next(item for item in graph.records if item.body == body)
        assert item.parent == (new if parent == old else parent)
        if body != hub_body:
            assert item.fields["workspace"] == new
    assert graph.resolve("design/evidence").archived
    child = invoke(local, ArtefactCreateRequest("living/design", "Default Child", key="renamed-default-child"))
    child_fields, _body = parse_frontmatter((root / child.result.path).read_text())
    assert child_fields["parent"] == project
    assert child_fields["workspace"] == new


def test_unscoped_legacy_rename_precedes_adoption_and_preserves_local_metadata(
    command_vault_clone, tmp_path
):
    import vault_registry
    import workspace_registry
    from _application.artefact.rename import ArtefactRenameRequest
    from _application.artefact.set_key import ArtefactSetKeyRequest
    from _application.workspace.register import WorkspaceRegisterRequest
    from _application.workspace.unregister import WorkspaceUnregisterRequest
    from _application.workspace.update_metadata import WorkspaceUpdateMetadataRequest, WorkspaceMetadataLink
    from _bootstrap.workspace_binding import read_workspace_manifest
    from test_workspace_membership_policy import application

    root = command_vault_clone.vault_root
    folder = tmp_path / "existing-case-planning"
    folder.mkdir()
    vault_registry.register(root, "fixture-brain")
    app, local = application(root), application(root, folder)
    old = "workspace/case-planning-financial-analysis"
    new = "workspace/case-planning"
    project = "project/case-planning"
    global_scope = WorkspaceSelector("global")

    def invoke(owner, request):
        result = owner.invoke(request)
        assert result.status == "ok", result.error.message if result.status == "error" else result
        return result

    invoke(app, ArtefactCreateRequest("living/project", "Case Planning", key="case-planning"))
    created = invoke(app, WorkspaceCreateRequest("case-planning-financial-analysis"))
    invoke(local, WorkspaceRegisterRequest(slug="case-planning-financial-analysis"))
    invoke(local, WorkspaceUpdateMetadataRequest(tags=("local",),
        links=(WorkspaceMetadataLink("repository", "existing-repository"),)))
    manifest = read_workspace_manifest(folder)
    hub_path = root / created.result.path
    fields, _body = parse_frontmatter(hub_path.read_text())
    assert "default_parent" not in fields
    fields["parent"] = project
    hub_path.unlink()
    hub_body = "Whole Case Planning scope.\n"
    _seed(root, "Workspaces/project~case-planning/Financial Analysis.md", fields, hub_body)
    records = [
        ("Designs/project~case-planning/Project Note.md", {"type": "living/design", "key": "project-note", "parent": project}, "Project evidence.\n"),
        ("Designs/project~case-planning/workspace~case-planning-financial-analysis/Wiki.md", {"type": "living/design", "key": "wiki", "parent": old}, "Workspace evidence.\n"),
        ("_Temporal/Plans/Scope.md", {"type": "temporal/plan", "parent": old}, "Temporal evidence.\n"),
        ("_Archive/Designs/History.md", {"type": "living/design", "key": "history", "parent": "design/wiki", "archiveddate": "2026-10-14"}, "Archived evidence.\n"),
    ]
    for path, metadata, body in records:
        _seed(root, path, metadata, body)
    compile_router.persist_compiled_router(str(root), compile_router.compile(str(root)))
    before = read_ownership_graph(root)
    assert all("workspace" not in item.fields for item in before.records if item.fields.get("type") != "living/workspace")

    invoke(app, ArtefactReparentRequest(old, None, workspace_context=global_scope))
    blocked = app.invoke(ArtefactSetKeyRequest(old, "case-planning", workspace_context=global_scope))
    assert blocked.status == "error" and blocked.effects == "none", blocked
    assert "workspaces.json" in blocked.error.message
    invoke(local, WorkspaceUnregisterRequest())
    assert read_workspace_manifest(folder) == {key: value for key, value in manifest.items() if key not in {"brain", "slug"}}
    invoke(app, ArtefactSetKeyRequest(old, "case-planning", workspace_context=global_scope))
    graph = read_ownership_graph(root)
    assert graph.resolve("design/wiki").parent == new
    assert all("workspace" not in item.fields for item in graph.records if item.fields.get("type") != "living/workspace")
    assert "default_parent" not in graph.resolve(new).fields
    invoke(app, ArtefactRenameRequest(graph.resolve(new).path, "Workspaces/Case Planning.md",
        workspace_context=global_scope))
    for owner in (new, project):
        invoke(app, ArtefactSetWorkspaceRequest(owner, recursive=True, workspace_context=WorkspaceSelector(new)))
    invoke(app, WorkspaceUpdatePolicyRequest(new, project))
    invoke(local, WorkspaceRegisterRequest(slug="case-planning"))

    assert read_workspace_manifest(folder) == {**manifest, "slug": "case-planning"}
    rows = workspace_registry.load_registry_strict(root)
    assert set(rows) == {str(folder.resolve())}
    assert rows[str(folder.resolve())]["slug"] == "case-planning"
    graph = read_ownership_graph(root)
    hub = graph.resolve(new)
    assert hub.path == "Workspaces/Case Planning.md"
    assert hub.body == hub_body and hub.parent is None
    assert hub.fields["default_parent"] == project
    assert graph.resolve(project).parent is None
    assert graph.resolve(project).fields["workspace"] == new
    for _path, metadata, body in records:
        item = next(item for item in graph.records if item.body == body)
        assert item.parent == (new if metadata["parent"] == old else metadata["parent"])
        assert item.fields["workspace"] == new
    assert graph.resolve("design/history").archived
    child = invoke(local, ArtefactCreateRequest("living/design", "Default Child", key="unscoped-default-child"))
    child_fields, _body = parse_frontmatter((root / child.result.path).read_text())
    assert child_fields["parent"] == project
    assert child_fields["workspace"] == new
