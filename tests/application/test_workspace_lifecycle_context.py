"""Workspace policy and discoverable integrity across existing lifecycle owners."""

from dataclasses import replace
from pathlib import Path
import json

import pytest
from pytest_bdd import given, scenarios, when, then

from _application.artefact.archive import ArtefactArchiveRequest
from _application.artefact.convert import ArtefactConvertRequest
from _application.artefact.delete import ArtefactDeleteRequest
from _application.artefact.rename import ArtefactRenameRequest
from _application.artefact.reparent import ArtefactReparentRequest
from _application.artefact.reparent_children import ArtefactReparentChildrenRequest, ReparentChildrenMode
from _application.artefact.set_key import ArtefactSetKeyRequest
from _application.artefact.set_status import ArtefactSetStatusRequest
from _application.artefact.set_naming_field import ArtefactSetNamingFieldRequest
from _application.artefact.unarchive import ArtefactUnarchiveRequest
from _application.artefact.create import ArtefactCreateRequest
from _application.workspace.ensure_registration import WorkspaceEnsureRegistrationRequest
from _application.workspace.update_policy import WorkspaceUpdatePolicyRequest
from _application.workspace_context import WorkspaceSelector, WorkspaceMutationPartial, WorkspaceAwareRequest
from _application.registry import current_application_catalogue, current_request_resolver
from _application.projection import minimal_request_payload, request_schema, canonical_wire_value
from _application._mutation_support import FrontmatterField
from _application.semantic_mutations import SEMANTIC_ARTEFACT_COMMANDS, MAINTENANCE_ARTEFACT_COMMANDS, applies_policy_tags
from _bootstrap.workspace_binding import read_workspace_manifest, save_workspace_manifest_data
from _common import parse_frontmatter, serialize_frontmatter, document_revision_at
from _lifecycle.derived_cache_state import require_fresh_compiled_router
from command_application import application_for
from test_workspace_mutation_context import scoped, create_request, edit_request
from test_workspace_skill_preparation import MatchingAdmission


REQUESTS = (ArtefactRenameRequest, ArtefactSetNamingFieldRequest, ArtefactSetStatusRequest,
    ArtefactSetKeyRequest, ArtefactConvertRequest, ArtefactReparentRequest, ArtefactReparentChildrenRequest,
    ArtefactArchiveRequest, ArtefactUnarchiveRequest, ArtefactDeleteRequest)
OWNERS = ("rename", "naming", "status", "key", "convert", "reparent", "children", "archive", "unarchive", "delete")

scenarios("../features/workspace_lifecycle.feature")


@given("an artefact in a bound workspace with policy tags removed", target_fixture="subject_path")
def untagged_subject(scoped):
    root, _workspace, app, _parents = scoped
    return _create_without_policy_tags(root, app)


@when("its canonical key is changed", target_fixture="lifecycle_result")
def change_subject_key(scoped, subject_path):
    return scoped[2].invoke(ArtefactSetKeyRequest(subject_path, "changed"))


@when("its file is renamed", target_fixture="lifecycle_result")
def rename_subject_file(scoped, subject_path):
    destination = str(Path(subject_path).with_name("Renamed Subject.md"))
    return scoped[2].invoke(ArtefactRenameRequest(subject_path, destination))


@then("its workspace membership is preserved and policy tags are restored")
def assert_preserved_membership(scoped, lifecycle_result):
    assert lifecycle_result.status == "ok", lifecycle_result
    target = getattr(lifecycle_result.result, "new_path", None) or lifecycle_result.result.path
    fields, _body = parse_frontmatter((scoped[0] / target).read_text())
    assert fields["workspace"] == "workspace/alpha"
    assert {"shared", "local", "both"} <= set(fields["tags"])


@given("an artefact selected as the workspace default parent", target_fixture="subject_path")
def default_parent_subject(scoped):
    path = untagged_subject(scoped)
    assert scoped[2].invoke(WorkspaceUpdatePolicyRequest("workspace/alpha", "design/subject")).status == "ok"
    return path


@when("archival of that default parent is requested", target_fixture="lifecycle_result")
def archive_default_parent(scoped, subject_path):
    return scoped[2].invoke(ArtefactArchiveRequest(subject_path))


@then("archival is refused without moving the default parent")
def assert_default_parent_not_moved(scoped, subject_path, lifecycle_result):
    assert lifecycle_result.status == "error" and lifecycle_result.effects == "none"
    assert "default_parent" in lifecycle_result.error.message
    assert (scoped[0] / subject_path).exists()


def _create_without_policy_tags(root, app, **kwargs):
    created = app.invoke(create_request(**kwargs))
    assert created.status == "ok", created
    path = created.result.path
    cleared = app.invoke(edit_request("frontmatter", root, path, workspace_context=WorkspaceSelector("global")))
    assert cleared.status == "ok", cleared
    return path


def _request(owner, path, root, app, monkeypatch, selection=None):
    kw = {"workspace_context": WorkspaceSelector(selection) if selection else None}
    if owner == "rename":
        return ArtefactRenameRequest(path, str(Path(path).with_name("Renamed.md")), **kw)
    if owner == "naming":
        from _lifecycle import derived_cache_state
        router = require_fresh_compiled_router(str(root))
        definition = next(item for item in router["artefacts"] if item.get("frontmatter_type") == "living/design")
        definition["naming"] = {"pattern": "{Code}-{Title}.md", "folder": "Designs/",
            "rules": [{"match_field": None, "match_values": None, "pattern": "{Code}-{Title}.md", "date_source": None}],
            "placeholders": [{"name": "Code", "field": "code", "required_when_field": None,
                "required_values": None, "regex": None}]}
        monkeypatch.setattr(derived_cache_state, "require_fresh_compiled_router", lambda _root: router)
        return ArtefactSetNamingFieldRequest(path, "code", "changed", **kw)
    if owner == "status":
        return ArtefactSetStatusRequest(path, "implemented", **kw)
    if owner == "key":
        return ArtefactSetKeyRequest(path, "changed", **kw)
    if owner == "convert":
        return ArtefactConvertRequest(path, "living/idea", **kw)
    if owner == "reparent":
        return ArtefactReparentRequest(path, "project/shared", **kw)
    if owner == "children":
        return ArtefactReparentChildrenRequest("project/local", ReparentChildrenMode.PARENT, "project/shared", **kw)
    if owner == "archive":
        return ArtefactArchiveRequest(path, **kw)
    if owner == "unarchive":
        archived = app.invoke(ArtefactArchiveRequest(path, workspace_context=WorkspaceSelector("global")))
        assert archived.status == "ok", archived
        return ArtefactUnarchiveRequest(archived.result.new_path, **kw)
    return ArtefactDeleteRequest(path, **kw)


@pytest.mark.parametrize("owner", OWNERS)
@pytest.mark.parametrize("selection", [None, "workspace/beta", "global"])
def test_every_owner_applies_only_selected_policy_to_surviving_subjects(scoped, monkeypatch, owner, selection):
    root, _workspace, app, _parents = scoped
    path = _create_without_policy_tags(root, app)
    request = _request(owner, path, root, app, monkeypatch, selection)
    result = app.invoke(request)
    assert result.status == "ok", result
    effective = result.result.mutation_context
    assert effective.workspace == (None if selection == "global" else selection or "workspace/alpha")
    assert effective.local_overrides == (selection is None)
    if owner == "delete":
        assert not (root / path).exists()
        return
    if owner == "children":
        target = next(item.new_path for item in result.result.moves if item.old_path == path)
    else:
        target = getattr(result.result, "new_path", None) or result.result.path
    fields, _ = parse_frontmatter((root / target).read_text())
    assert fields["workspace"] == "workspace/alpha"
    assert fields["parent"] == ("project/shared" if owner in {"children", "reparent"} else "project/local")
    assert effective.parent_source == ("explicit" if owner in {"children", "reparent"} else "preserved")
    configured = set(fields["tags"]) & {"shared", "local", "both", "beta"}
    assert configured == ({"shared", "local", "both"} if selection is None else {"beta"} if selection != "global" else set())


@pytest.mark.parametrize("selection", ["unconfigured", "global", "scoped-unchanged"])
def test_rename_preserves_exact_bytes_when_policy_does_not_change_fields(scoped, selection):
    root, _workspace, app, _parents = scoped
    if selection == "scoped-unchanged":
        created = app.invoke(create_request())
        assert created.status == "ok", created
        path = created.result.path
    else:
        path = _create_without_policy_tags(root, app)
    source = root / path
    content = source.read_text().replace("type: living/design", "type:   'living/design'")
    content = content.replace("---\n", "---\n# Authored frontmatter comment\n", 1)
    content = content.replace("\n---\n", "\n---\n\n\n", 1)
    source.write_bytes(content.encode("utf-8"))
    destination = str(Path(path).with_name("Renamed Subject.md"))
    if selection == "unconfigured":
        app = application_for(root)
    result = app.invoke(ArtefactRenameRequest(path, destination,
        workspace_context=WorkspaceSelector("global") if selection == "global" else None))
    assert result.status == "ok", result
    assert not source.exists()
    assert (root / destination).read_bytes() == content.encode("utf-8")
    require_fresh_compiled_router(root)


@pytest.mark.parametrize("request_type", REQUESTS)
def test_lifecycle_selector_schema_versions_and_codec(request_type):
    from jsonschema import Draft202012Validator
    payload = {**minimal_request_payload(request_type), "workspace_context": "workspace/alpha"}
    Draft202012Validator(request_schema(request_type)).validate(payload)
    request = current_request_resolver().resolve(request_type.COMMAND_ID, payload)
    assert canonical_wire_value(request)["workspace_context"] == "workspace/alpha"
    assert request_type.COMMAND_VERSION == (3 if request_type in {ArtefactArchiveRequest, ArtefactUnarchiveRequest, ArtefactDeleteRequest, ArtefactConvertRequest} else 2)
    with pytest.raises(ValueError):
        current_request_resolver().resolve(request_type.COMMAND_ID, {**payload, "workspace_context": "/tmp/repo"})


def test_shared_inventory_covers_semantic_requests_and_excludes_maintenance():
    catalogue = current_application_catalogue()
    aware = {entry.command_id for entry in catalogue.entries if issubclass(entry.request_type, WorkspaceAwareRequest)}
    assert aware == SEMANTIC_ARTEFACT_COMMANDS
    assert not aware & MAINTENANCE_ARTEFACT_COMMANDS
    assert all(not applies_policy_tags(command) for command in (*MAINTENANCE_ARTEFACT_COMMANDS, "artefact.delete"))


@pytest.mark.parametrize("operation", ["delete", "archive", "key", "status", "convert"])
@pytest.mark.parametrize("policy", ["shared", "local"])
def test_configured_default_parent_cannot_be_invalidated(scoped, monkeypatch, operation, policy):
    root, workspace, app, _parents = scoped
    path = _create_without_policy_tags(root, app)
    if policy == "shared":
        assert app.invoke(WorkspaceUpdatePolicyRequest("workspace/alpha", "design/subject")).status == "ok"
    else:
        manifest = read_workspace_manifest(workspace)
        manifest["defaults"]["parent"] = "design/subject"
        save_workspace_manifest_data(workspace, manifest)
    before = (root / path).read_bytes()
    request = _request(operation, path, root, app, monkeypatch)
    if operation == "convert":
        request = ArtefactConvertRequest(path, "temporal/plan")
    result = app.invoke(request)
    assert result.status == "error" and result.effects == "none", result
    assert "discoverable references" in result.error.message
    assert ("default_parent" if policy == "shared" else "defaults.parent") in result.error.message
    assert (root / path).read_bytes() == before


@pytest.mark.parametrize("operation", ["delete", "archive", "key", "convert"])
@pytest.mark.parametrize("source", ["active", "terminal", "temporal", "archived"])
def test_hub_guards_discover_members_in_every_source_class(scoped, monkeypatch, operation, source):
    import compile_router
    root, _workspace, _app, _parents = scoped
    app = application_for(root)
    path = {"active": "Ideas/member.md", "terminal": "Ideas/+Adopted/member.md",
        "temporal": "_Temporal/Plans/member.md", "archived": "_Archive/Ideas/member.md"}[source]
    fields = {"type": "temporal/plan" if source == "temporal" else "living/idea",
        "workspace": "workspace/alpha", "status": "adopted" if source == "terminal" else "ready"}
    if source != "temporal":
        fields["key"] = "member"
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_text(serialize_frontmatter(fields, body="# Member\n"))
    compile_router.persist_compiled_router(str(root), compile_router.compile(str(root)))
    hub = require_fresh_compiled_router(str(root))["artefact_index"]["workspace/alpha"]["path"]
    request = _request(operation, hub, root, app, monkeypatch, "global")
    result = app.invoke(request)
    assert result.status == "error" and result.effects == "none", result
    assert path in result.error.message
    assert "disconnected" in result.error.message


def test_hub_active_local_binding_blocks_key_change_without_members(scoped):
    root, workspace, _app, _parents = scoped
    app = application_for(root)
    assert app.invoke(WorkspaceEnsureRegistrationRequest("empty")).status == "ok"
    save_workspace_manifest_data(workspace, {"brain": "brain", "slug": "repo", "links": {"workspace": "empty"}})
    bound = application_for(root, workspace_dir=workspace)
    result = bound.invoke(ArtefactSetKeyRequest("workspace/empty", "renamed", workspace_context=WorkspaceSelector("global")))
    assert result.status == "error" and "links.workspace" in result.error.message


def test_unreferenced_hub_can_change_key_but_conversion_cannot_clear_self_scope(scoped):
    root, _workspace, _app, _parents = scoped
    app = application_for(root)
    assert app.invoke(WorkspaceEnsureRegistrationRequest("empty")).status == "ok"
    changed = app.invoke(ArtefactSetKeyRequest("workspace/empty", "renamed"))
    assert changed.status == "ok", changed
    assert "workspace/renamed" in require_fresh_compiled_router(str(root))["artefact_index"]
    denied = app.invoke(ArtefactConvertRequest(changed.result.path, "living/project"))
    assert denied.status == "error" and denied.effects == "none"


@pytest.mark.parametrize("owner", ["reparent", "children", "convert"])
def test_parent_edges_cannot_cross_workspace(scoped, owner):
    root, _workspace, app, _parents = scoped
    path = _create_without_policy_tags(root, app)
    request = {"reparent": ArtefactReparentRequest(path, "workspace/beta"),
        "children": ArtefactReparentChildrenRequest("project/local", ReparentChildrenMode.PARENT, "workspace/beta"),
        "convert": ArtefactConvertRequest(path, "living/idea", parent="workspace/beta")}[owner]
    result = app.invoke(request)
    assert result.status == "error" and result.effects == "none"
    assert "share one workspace" in result.error.message


def test_terminal_workspace_remains_identity_but_not_effective_context(scoped):
    root, _workspace, app, _parents = scoped
    path = _create_without_policy_tags(root, app)
    closing = app.invoke(ArtefactSetStatusRequest("workspace/alpha", "completed"))
    assert closing.status == "ok", closing
    unbound = application_for(root)
    selected = unbound.invoke(ArtefactSetKeyRequest(path, "renamed", workspace_context=WorkspaceSelector("workspace/alpha")))
    assert selected.status == "error" and "terminal" in selected.error.message
    historical = unbound.invoke(ArtefactSetKeyRequest(path, "renamed", workspace_context=WorkspaceSelector("global")))
    assert historical.status == "ok", historical
    assert parse_frontmatter((root / historical.result.path).read_text())[0]["workspace"] == "workspace/alpha"


@pytest.mark.parametrize("owner", ["rename", "key", "archive", "children"])
def test_lifecycle_policy_drift_conflicts_before_write(scoped, monkeypatch, owner):
    root, workspace, app, _parents = scoped
    path = _create_without_policy_tags(root, app)
    request = _request(owner, path, root, app, monkeypatch)
    entry = current_application_catalogue().resolve(request)
    binding = entry.preparation.prepare(app._context, request)
    admission = MatchingAdmission(binding)
    manifest = read_workspace_manifest(workspace)
    manifest["defaults"]["tags"].append("drift")
    save_workspace_manifest_data(workspace, manifest)
    result = entry.executor(replace(app._context, admission=admission), request)
    assert result.status == "error" and result.effects == "none"
    assert admission.calls == 0 and (root / path).exists()


@pytest.mark.parametrize("owner", ["rename", "status", "key", "archive", "children", "delete"])
def test_index_failure_returns_observed_committed_subjects_and_policy(scoped, monkeypatch, owner):
    from _portable import router_maintenance
    root, _workspace, app, _parents = scoped
    path = _create_without_policy_tags(root, app)
    request = _request(owner, path, root, app, monkeypatch)
    binding = current_application_catalogue().resolve(request).preparation.prepare(app._context, request)
    def fail(*_args, **_kwargs):
        raise OSError("forced index failure")
    monkeypatch.setattr(router_maintenance, "maintain_router", fail)
    result = app.invoke(request)
    assert isinstance(result, WorkspaceMutationPartial), result
    assert canonical_wire_value(result.mutation_context) == json.loads(binding.review_json)["mutation_context"]
    assert result.error.next_action.command_id == "runtime.refresh-router"
    assert result.committed_effects
    for effect in result.committed_effects:
        if owner == "delete" and effect.subject == path:
            assert not (root / effect.subject).exists()
        else:
            assert (root / effect.subject).exists()


@pytest.mark.parametrize("owner", ["archive", "unarchive", "convert", "children"])
def test_recursive_and_multi_subject_policy_excludes_incidental_subjects(scoped, owner):
    root, _workspace, app, _parents = scoped
    path = _create_without_policy_tags(root, app)
    parent = "project/local" if owner == "children" else "design/subject"
    child = app.invoke(ArtefactCreateRequest("living/idea", "Child", key="child", parent=parent))
    assert child.status == "ok", child
    child_path = child.result.path
    assert app.invoke(edit_request("frontmatter", root, child_path, workspace_context=WorkspaceSelector("global"))).status == "ok"
    fields, body = parse_frontmatter((root / path).read_text())
    (root / path).write_text(serialize_frontmatter(fields, body=body + "\n[[Subject]]\n"))
    incidental = root / "Ideas/Incidental.md"
    incidental.write_text("---\ntype: living/idea\nworkspace: workspace/beta\ntags: []\n---\n[[Subject]]\n")
    if owner == "children":
        request = ArtefactReparentChildrenRequest("project/local", ReparentChildrenMode.PARENT, "project/shared")
    elif owner == "convert":
        request = ArtefactConvertRequest(path, "living/idea", recursive=True)
    else:
        request = ArtefactArchiveRequest(path, recursive=True)
        if owner == "unarchive":
            archived = app.invoke(replace(request, workspace_context=WorkspaceSelector("global")))
            assert archived.status == "ok", archived
            request = ArtefactUnarchiveRequest(archived.result.new_path, recursive=True)
    result = app.invoke(request)
    assert result.status == "ok", result
    records = []
    for candidate in root.rglob("*.md"):
        if ".brain-core" in candidate.parts:
            continue
        fields, _body = parse_frontmatter(candidate.read_text())
        if fields.get("key") in {"subject", "child"}:
            records.append(fields)
    assert len(records) == 2
    for fields in records:
        assert fields["workspace"] == "workspace/alpha"
        assert {"shared", "local", "both"} <= set(fields["tags"])
    fields, _body = parse_frontmatter(incidental.read_text())
    assert fields["tags"] == [] and fields["workspace"] == "workspace/beta"


def test_reference_created_after_preparation_blocks_hub_change(scoped):
    root, _workspace, _app, _parents = scoped
    app = application_for(root)
    assert app.invoke(WorkspaceEnsureRegistrationRequest("empty")).status == "ok"
    request = ArtefactSetKeyRequest("workspace/empty", "changed")
    entry = current_application_catalogue().resolve(request)
    binding = entry.preparation.prepare(app._context, request)
    path = root / "_Temporal/Plans/New reference.md"
    path.write_text("---\ntype: temporal/plan\nworkspace: workspace/empty\n---\nNew member\n")
    admission = MatchingAdmission(binding)
    result = entry.executor(replace(app._context, admission=admission), request)
    assert result.status == "error" and admission.calls == 0
    assert "New reference.md" in result.error.message


def test_terminal_shared_workspace_policy_still_protects_default_parent(scoped):
    root, _workspace, app, _parents = scoped
    path = _create_without_policy_tags(root, app)
    assert app.invoke(WorkspaceUpdatePolicyRequest("workspace/alpha", "design/subject")).status == "ok"
    assert app.invoke(ArtefactSetStatusRequest("workspace/alpha", "completed")).status == "ok"
    result = application_for(root).invoke(ArtefactDeleteRequest(path))
    assert result.status == "error" and "default_parent" in result.error.message


def test_generic_document_type_edit_cannot_bypass_default_parent_guard(scoped):
    from _application.document.update_frontmatter import DocumentUpdateFrontmatterRequest
    from _application.document._types import DocumentLocator, DocumentResource
    root, _workspace, app, _parents = scoped
    path = _create_without_policy_tags(root, app)
    assert app.invoke(WorkspaceUpdatePolicyRequest("workspace/alpha", "design/subject")).status == "ok"
    result = app.invoke(DocumentUpdateFrontmatterRequest(DocumentLocator(DocumentResource.ARTEFACT, path),
        document_revision_at(root / path), (FrontmatterField("type", "temporal/plan"),)))
    assert result.status == "error" and "default_parent" in result.error.message


def test_mid_apply_partial_names_only_observed_paths_and_keeps_context(scoped, monkeypatch):
    import rename
    root, _workspace, app, _parents = scoped
    path = _create_without_policy_tags(root, app)
    child = app.invoke(ArtefactCreateRequest("living/idea", "Child", key="child", parent="design/subject"))
    assert child.status == "ok", child
    real_rename = rename.os.rename
    calls = []
    def fail_second(source, dest):
        calls.append((source, dest))
        if len(calls) == 2:
            raise OSError("forced second move failure")
        return real_rename(source, dest)
    monkeypatch.setattr(rename.os, "rename", fail_second)
    result = app.invoke(ArtefactArchiveRequest(path, recursive=True))
    assert isinstance(result, WorkspaceMutationPartial), result
    assert result.mutation_context.workspace == "workspace/alpha"
    assert len(calls) == 2
    assert all((root / effect.subject).exists() for effect in result.committed_effects)
    assert "move failure" in result.error.message
    require_fresh_compiled_router(str(root))


def test_frontmatter_repair_preserves_membership_without_policy_tags_and_refreshes_index(scoped):
    from _application.artefact.repair import ArtefactRepairRequest, ArtefactRepairScope
    from _lifecycle.derived_cache_state import inspect_router_cache
    root, _workspace, app, _parents = scoped
    path = _create_without_policy_tags(root, app)
    fields, body = parse_frontmatter((root / path).read_text())
    (root / path).write_text(serialize_frontmatter(fields, body="---\nstatus: ready\n---\n" + body))
    result = app.invoke(ArtefactRepairRequest(ArtefactRepairScope.FRONTMATTER))
    assert result.status == "ok", result
    after, body = parse_frontmatter((root / path).read_text())
    assert after["workspace"] == "workspace/alpha"
    assert not {"shared", "local", "both"} & set(after["tags"])
    assert not body.lstrip().startswith("---")
    assert not inspect_router_cache(root, verify_content=True).stale


def test_naming_migration_preserves_membership_without_policy_tags(scoped):
    from _application.artefact.migrate_naming import ArtefactMigrateNamingRequest
    root, _workspace, app, _parents = scoped
    path = _create_without_policy_tags(root, app)
    wrong = root / "Designs/Wrong Folder.md"
    (root / path).rename(wrong)
    import compile_router
    compile_router.persist_compiled_router(str(root), compile_router.compile(str(root)))
    result = app.invoke(ArtefactMigrateNamingRequest())
    assert result.status == "ok", result
    assert result.result.renamed > 0
    candidates = [candidate for candidate in root.glob("Designs/**/*.md")
                  if parse_frontmatter(candidate.read_text())[0].get("key") == "subject"]
    assert len(candidates) == 1
    fields, _ = parse_frontmatter(candidates[0].read_text())
    assert fields["workspace"] == "workspace/alpha"
    assert not {"shared", "local", "both"} & set(fields["tags"])
