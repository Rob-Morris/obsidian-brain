"""Effective workspace policy at shared semantic mutation and admission boundaries."""

from dataclasses import replace
import json

import pytest
from pytest_bdd import given, scenarios, then, when
import vault_registry
from _application.artefact.create import ArtefactCreateRequest
from _application.artefact.set_status import ArtefactSetStatusRequest
from _application._mutation_support import FrontmatterField, InlineContent
from _application.document._types import DocumentLocator, DocumentResource
from _application.document.write_body import DocumentWriteBodyRequest, DocumentWriteBodyOperation
from _application.document.update_frontmatter import DocumentUpdateFrontmatterRequest
from _application.document.replace_text import DocumentReplaceTextRequest, UniqueMatch
from _application.document.structured_edit import DocumentStructuredEditRequest, ReplaceStructure, HeadingSelection, HeadingPart
from _application.registry import current_application_catalogue, current_request_resolver
from _application.projection import canonical_wire_value, request_schema, minimal_request_payload
from _application.workspace.ensure_registration import WorkspaceEnsureRegistrationRequest
from _application.workspace.update_policy import WorkspaceUpdatePolicyRequest
from _application.workspace_context import WorkspaceSelector
from _bootstrap.workspace_binding import read_workspace_manifest, save_workspace_manifest_data
from _common import document_revision_at, parse_frontmatter
from command_application import application_for
from test_workspace_skill_preparation import MatchingAdmission


COMMANDS = (ArtefactCreateRequest, DocumentWriteBodyRequest, DocumentUpdateFrontmatterRequest,
            DocumentReplaceTextRequest, DocumentStructuredEditRequest)

scenarios("../features/workspace_mutations.feature")


@pytest.fixture
def scoped(command_vault_clone, tmp_path):
    root = command_vault_clone.vault_root
    app = application_for(root)
    for key in ("alpha", "beta"):
        result = app.invoke(WorkspaceEnsureRegistrationRequest(key))
        assert result.status == "ok", result
    parents = {}
    for key in ("shared", "local"):
        result = app.invoke(ArtefactCreateRequest("living/project", key.title(), key=key,
            workspace_context=WorkspaceSelector("workspace/alpha")))
        assert result.status == "ok", result
        parents[key] = result.result.path
    policy = app.invoke(WorkspaceUpdatePolicyRequest("workspace/alpha", "project/shared",
        default_tags=("shared", "both")))
    assert policy.status == "ok", policy
    assert app.invoke(WorkspaceUpdatePolicyRequest("workspace/beta", "workspace/beta",
        default_tags=("beta",))).status == "ok"
    workspace = tmp_path / "repo"
    workspace.mkdir()
    vault_registry.register(root, "brain")
    save_workspace_manifest_data(workspace, {"brain": "brain", "slug": "repo",
        "links": {"workspace": "alpha"}, "defaults": {"parent": "project/local", "tags": ["local", "both"]}})
    return root, workspace, application_for(root, workspace_dir=workspace), parents


def create_request(**kwargs):
    return ArtefactCreateRequest("living/design", "Subject", key="subject",
        content=InlineContent("# Subject\n\n## Notes\n\nOriginal text.\n"), **kwargs)


def edit_request(kind, root, path, **kwargs):
    document = DocumentLocator(DocumentResource.ARTEFACT, path)
    revision = document_revision_at(root / path)
    if kind == "body":
        return DocumentWriteBodyRequest(document, revision, DocumentWriteBodyOperation.APPEND,
            InlineContent("\nAppended.\n"), **kwargs)
    if kind == "frontmatter":
        return DocumentUpdateFrontmatterRequest(document, revision, (FrontmatterField("tags", None),), **kwargs)
    if kind == "replace":
        return DocumentReplaceTextRequest(document, revision, "Original text.", "Changed text.", UniqueMatch(), **kwargs)
    return DocumentStructuredEditRequest(document, revision,
        ReplaceStructure(HeadingSelection("Notes", HeadingPart.BODY), InlineContent("Changed notes.\n")), **kwargs)


@given("a bound workspace with shared and local mutation policy")
def bound_workspace(scoped):
    assert scoped[2] is not None


@when("I create an artefact using startup context", target_fixture="semantic_fields")
def create_using_startup_context(scoped):
    root, _workspace, app, _parents = scoped
    result = app.invoke(create_request())
    assert result.status == "ok", result
    return parse_frontmatter((root / result.result.path).read_text())[0]


@when("I semantically edit an existing global artefact", target_fixture="semantic_fields")
def edit_global_using_startup_context(scoped):
    root, _workspace, app, _parents = scoped
    created = app.invoke(create_request(workspace_context=WorkspaceSelector("global")))
    assert created.status == "ok", created
    result = app.invoke(edit_request("body", root, created.result.path))
    assert result.status == "ok", result
    return parse_frontmatter((root / result.result.path).read_text())[0]


@then("its membership is the bound workspace")
def bound_membership(semantic_fields):
    assert semantic_fields["workspace"] == "workspace/alpha"


@then("its parent is the local default")
def local_parent(semantic_fields):
    assert semantic_fields["parent"] == "project/local"


@then("it remains unscoped with no parent")
def remains_global(semantic_fields):
    assert semantic_fields.get("workspace") is None
    assert semantic_fields.get("parent") is None


@then("it has both shared and local policy tags")
def shared_and_local_tags(semantic_fields):
    assert {"shared", "local", "both"} <= set(semantic_fields["tags"])


@pytest.mark.parametrize("request_type", COMMANDS)
def test_selector_shared_codec_schema_and_round_trip(request_type):
    from jsonschema import Draft202012Validator
    resolver = current_request_resolver()
    schema = request_schema(request_type)
    for selection in (None, "global", "workspace/alpha"):
        payload = {**minimal_request_payload(request_type), "workspace_context": selection}
        if "document" in payload:
            payload["document"] = {"resource": "artefact", "reference": "wiki/example"}
        Draft202012Validator(schema).validate(payload)
        request = resolver.resolve(request_type.COMMAND_ID, payload)
        assert canonical_wire_value(request)["workspace_context"] == selection
        assert request == resolver.resolve(request_type.COMMAND_ID, canonical_wire_value(request))
    for invalid in ("/tmp/repo", "../repo", "alpha", "workspace~alpha", "workspace/../alpha", "workspace/123", " workspace/alpha "):
        with pytest.raises(ValueError):
            resolver.resolve(request_type.COMMAND_ID, {**minimal_request_payload(request_type), "workspace_context": invalid})
        assert not Draft202012Validator(schema["properties"]["workspace_context"]).is_valid(invalid)


@pytest.mark.parametrize("parent,selection,expected_parent,expected_workspace,expected_tags", [
    (None, None, "project/local", "workspace/alpha", {"shared", "local", "both", "explicit"}),
    ("project/shared", None, "project/shared", "workspace/alpha", {"shared", "local", "both", "explicit"}),
    (None, "workspace/beta", "workspace/beta", "workspace/beta", {"beta", "explicit"}),
    (None, "global", None, None, {"explicit"}),
])
def test_create_effective_membership_parent_and_tags(scoped, parent, selection, expected_parent, expected_workspace, expected_tags):
    root, _workspace, app, _parents = scoped
    result = app.invoke(create_request(parent=parent,
        workspace_context=WorkspaceSelector(selection) if selection else None,
        frontmatter=(FrontmatterField("tags", ("explicit", "explicit")),)))
    assert result.status == "ok", result.error.message
    fields, _ = parse_frontmatter((root / result.result.path).read_text())
    assert fields.get("workspace") == expected_workspace
    assert fields.get("parent") == expected_parent
    assert expected_tags <= set(fields["tags"])
    assert len(fields["tags"]) == len(set(fields["tags"]))
    if expected_parent:
        assert expected_parent in fields["tags"]
    if selection == "workspace/beta":
        assert "local" not in fields["tags"] and "shared" not in fields["tags"]
    if selection == "global":
        assert not {"local", "shared", "both", "beta"} & set(fields["tags"])
    effective = result.result.mutation_context
    assert effective.workspace == expected_workspace
    assert effective.parent == expected_parent
    assert effective.tags == tuple(fields["tags"])
    assert effective.local_overrides == (selection is None)


def test_shared_parent_used_without_local_override_and_no_default_remains_root(scoped):
    root, workspace, app, _parents = scoped
    manifest = read_workspace_manifest(workspace)
    manifest["defaults"].pop("parent")
    save_workspace_manifest_data(workspace, manifest)
    created = app.invoke(create_request())
    assert created.status == "ok", created
    assert created.result.parent == "project/shared"
    assert created.result.mutation_context.parent_source == "shared"
    assert app.invoke(WorkspaceUpdatePolicyRequest("workspace/alpha", clear_parent=True)).status == "ok"
    another = app.invoke(ArtefactCreateRequest("living/design", "Root", key="root"))
    assert another.status == "ok", another
    assert another.result.path == "Designs/Root.md"
    assert another.result.parent is None
    assert parse_frontmatter((root / another.result.path).read_text())[0]["workspace"] == "workspace/alpha"


@pytest.mark.parametrize("parent,selection", [("project/command-fixture", None), ("workspace/beta", None), ("project/shared", "global")])
def test_create_rejects_cross_workspace_and_scoped_unscoped_parent_edges(scoped, parent, selection):
    _root, _workspace, app, _parents = scoped
    result = app.invoke(create_request(parent=parent, workspace_context=WorkspaceSelector(selection) if selection else None))
    assert result.status == "error", result
    assert result.effects == "none"
    assert "share one workspace" in result.error.message


@pytest.mark.parametrize("selection", [None, "workspace/beta", "global"])
@pytest.mark.parametrize("broken", ["missing", "terminal", "stale-parent", "malformed"])
def test_invalid_startup_binding_cannot_be_bypassed(scoped, selection, broken):
    root, workspace, app, _parents = scoped
    manifest = read_workspace_manifest(workspace)
    if broken == "terminal":
        assert app.invoke(ArtefactSetStatusRequest("workspace/alpha", "completed")).status == "ok"
    elif broken == "missing":
        manifest["links"]["workspace"] = "missing"
    elif broken == "stale-parent":
        manifest["defaults"]["parent"] = "workspace/beta"
    else:
        (workspace / ".brain/local/workspace.yaml").write_text("not: [valid yaml")
    if broken in {"missing", "stale-parent"}:
        save_workspace_manifest_data(workspace, manifest)
    result = app.invoke(create_request(workspace_context=WorkspaceSelector(selection) if selection else None))
    assert result.status == "error", result
    assert result.effects == "none"
    assert not (root / "Designs/Subject.md").exists()


@pytest.mark.parametrize("kind", ["body", "frontmatter", "replace", "structured"])
def test_every_semantic_document_owner_restores_tags_without_adoption(scoped, kind):
    root, _workspace, app, _parents = scoped
    created = app.invoke(create_request(workspace_context=WorkspaceSelector("global")))
    path = created.result.path
    before, _ = parse_frontmatter((root / path).read_text())
    result = app.invoke(edit_request(kind, root, path))
    assert result.status == "ok", result
    fields, _ = parse_frontmatter((root / path).read_text())
    assert fields.get("workspace") == before.get("workspace") is None
    assert fields.get("parent") == before.get("parent") is None
    assert {"shared", "local", "both"} <= set(fields["tags"])
    assert result.result.mutation_context.workspace == "workspace/alpha"
    if kind == "frontmatter":
        assert "tags" not in result.result.removed_fields
        assert "tags" in result.result.updated_fields


def test_alternate_policy_edit_and_global_removal_preserve_existing_membership(scoped):
    root, _workspace, app, _parents = scoped
    created = app.invoke(create_request())
    path = created.result.path
    result = app.invoke(edit_request("frontmatter", root, path, workspace_context=WorkspaceSelector("workspace/beta")))
    assert result.status == "ok", result
    fields, _ = parse_frontmatter((root / path).read_text())
    assert fields["workspace"] == "workspace/alpha" and fields["parent"] == "project/local"
    assert "beta" in fields["tags"] and "local" not in fields["tags"]
    global_edit = app.invoke(edit_request("frontmatter", root, path, workspace_context=WorkspaceSelector("global")))
    assert global_edit.status == "ok", global_edit
    fields, _ = parse_frontmatter((root / path).read_text())
    assert fields["workspace"] == "workspace/alpha" and fields["parent"] == "project/local"
    assert not {"shared", "local", "beta"} & set(fields.get("tags", []))


@pytest.mark.parametrize("value", [None, "workspace/beta"])
def test_workspace_frontmatter_is_handler_owned_for_create_and_edit(scoped, value):
    root, _workspace, app, _parents = scoped
    denied = app.invoke(create_request(frontmatter=(FrontmatterField("workspace", value),)))
    assert denied.status == "error" and denied.effects == "none"
    created = app.invoke(create_request())
    path = created.result.path
    request = DocumentUpdateFrontmatterRequest(DocumentLocator(DocumentResource.ARTEFACT, path),
        document_revision_at(root / path), (FrontmatterField("workspace", value),))
    denied = app.invoke(request)
    assert denied.status == "error" and denied.effects == "none"
    assert "handler-owned" in denied.error.message


def test_generic_type_edit_cannot_remove_implicit_workspace_hub_membership(scoped):
    from _lifecycle.derived_cache_state import require_fresh_compiled_router

    root, _workspace, app, _parents = scoped
    hub = require_fresh_compiled_router(str(root))["artefact_index"]["workspace/alpha"]["path"]
    revision = document_revision_at(root / hub)
    result = app.invoke(DocumentUpdateFrontmatterRequest(
        DocumentLocator(DocumentResource.ARTEFACT, hub), revision,
        (FrontmatterField("type", "living/project"),)))
    assert result.status == "error" and result.effects == "none"
    assert "preserve workspace membership" in result.error.message
    assert document_revision_at(root / hub) == revision


@pytest.mark.parametrize("request_type", COMMANDS[1:])
@pytest.mark.parametrize("resource", ["memory", "skill", "style", "template"])
def test_non_artefact_documents_reject_semantic_selector(request_type, resource):
    payload = minimal_request_payload(request_type)
    payload["document"] = {"resource": resource, "reference": "example"}
    payload["workspace_context"] = "global"
    with pytest.raises(ValueError, match="only for artefact"):
        current_request_resolver().resolve(request_type.COMMAND_ID, payload)


@pytest.mark.parametrize("operation", ["create", "edit"])
@pytest.mark.parametrize("source", ["workspace", "manifest", "parent"])
def test_policy_sources_are_admission_bound_before_any_write(scoped, operation, source):
    root, workspace, app, parents = scoped
    if operation == "create":
        request = create_request()
    else:
        created = app.invoke(create_request())
        request = edit_request("body", root, created.result.path)
    entry = current_application_catalogue().resolve(request)
    context = app._context
    binding = entry.preparation.prepare(context, request)
    assert json.loads(binding.review_json)["mutation_context"]["workspace"] == "workspace/alpha"
    admission = MatchingAdmission(binding)
    if source == "workspace":
        from _lifecycle.derived_cache_state import require_fresh_compiled_router
        hub = root / require_fresh_compiled_router(str(root))["artefact_index"]["workspace/alpha"]["path"]
        hub.write_text(hub.read_text() + "\nPolicy source revised.\n")
    elif source == "manifest":
        manifest = read_workspace_manifest(workspace)
        manifest["defaults"]["tags"].append("drift")
        save_workspace_manifest_data(workspace, manifest)
    else:
        parent = root / parents["local"]
        parent.write_text(parent.read_text() + "\nParent source revised.\n")
    result = entry.executor(replace(context, admission=admission), request)
    assert result.status == "error", result
    assert result.effects == "none"
    assert admission.calls == 0


@pytest.mark.parametrize("operation", ["create", "edit"])
def test_index_failure_retains_committed_subject_and_exact_mutation_context(scoped, monkeypatch, operation):
    from _portable import router_maintenance
    from _application.projection import canonical_result_envelope
    from _application.receipts import OutcomeReference, ReceiptState
    from _application.preparation import content_digest
    from brain_application.results import WorkspaceMutationPartial

    root, workspace, app, parents = scoped
    if operation == "create":
        request = create_request()
    else:
        created = app.invoke(create_request())
        path = created.result.path
        request = DocumentUpdateFrontmatterRequest(DocumentLocator(DocumentResource.ARTEFACT, path),
            document_revision_at(root / path),
            (FrontmatterField("tags", None), FrontmatterField("type", "living/project")))
    entry = current_application_catalogue().resolve(request)
    prepared = entry.preparation.prepare(app._context, request)
    expected = json.loads(prepared.review_json)["mutation_context"]
    calls = []

    def fail_router(*args, **kwargs):
        calls.append(args)
        raise OSError("forced index failure")

    monkeypatch.setattr(router_maintenance, "maintain_router", fail_router)
    result = app.invoke(request)
    assert isinstance(result, WorkspaceMutationPartial), result
    assert len(calls) == 1
    assert result.error.next_action.command_id == "runtime.refresh-router"
    assert "changes committed" in result.error.message
    assert canonical_wire_value(result.mutation_context) == expected
    assert result.mutation_context.workspace == "workspace/alpha"
    assert result.mutation_context.parent == "project/local"
    sources = {(source.kind, source.identity): source.revision for source in result.mutation_context.sources}
    assert sources["workspace-manifest", str(workspace / ".brain/local/workspace.yaml")] == content_digest(
        (workspace / ".brain/local/workspace.yaml").read_bytes())
    assert sources["workspace-parent", "project/local"] == document_revision_at(root / parents["local"])
    assert any(kind == "workspace-policy" and identity == "workspace/alpha" for kind, identity in sources)
    assert len(result.committed_effects) == 1
    effect = result.committed_effects[0]
    assert effect.kind == ("artefact.created" if operation == "create" else request.COMMAND_ID)
    fields, _body = parse_frontmatter((root / effect.subject).read_text())
    assert fields["workspace"] == "workspace/alpha" and fields["parent"] == "project/local"
    assert tuple(fields["tags"]) == result.mutation_context.tags
    envelope = canonical_result_envelope(result)
    assert envelope["result"] == {
        "committed_effects": [{"kind": effect.kind, "subject": effect.subject}],
        "mutation_context": expected,
    }
    receipt = app._context.receipt_reader.read(OutcomeReference(app._context.invocation_id)).outcome.receipt
    assert receipt.state is ReceiptState.KNOWN_PARTIAL
    assert receipt.committed_effects == result.committed_effects
    assert "mutation_context" not in canonical_wire_value(receipt)


def test_session_exposes_distinct_canonical_shared_and_local_policy(scoped):
    from _application.session.start import SessionStartRequest
    from _application.types import DependencyTier
    from test_session_start_owner import _mark_ready

    root, workspace, _app, _parents = scoped
    _mark_ready(root)
    result = application_for(root, workspace_dir=workspace, dependency_tier=DependencyTier.MANAGED).invoke(SessionStartRequest())
    assert result.status == "ok", result
    policy = result.result.workspace_policy
    assert policy.workspace == "workspace/alpha"
    assert policy.shared.parent == "project/shared" and policy.local.parent == "project/local"
    assert policy.shared.tags == ("shared", "both") and policy.local.tags == ("local", "both")
    assert result.result.workspace_default_tags == policy.local.tags
    assert result.result.version == "3"


def test_direct_script_keeps_symbolic_selection_separate_from_trusted_workspace(scoped):
    from io import StringIO
    from _command_interface.script import run

    root, workspace, app, _parents = scoped
    stdout, stderr = StringIO(), StringIO()
    payload = canonical_wire_value(create_request(workspace_context=WorkspaceSelector("workspace/beta")))
    code = run(["artefact", "create", "--vault", str(root), "--workspace", str(workspace),
        "--request-json", json.dumps(payload), "--json"], stdout=stdout, stderr=stderr,
        context_factory=lambda **_options: app._context)
    assert code == 0, stderr.getvalue()
    result = json.loads(stdout.getvalue())
    effective = result["result"]["mutation_context"]
    assert effective["workspace"] == "workspace/beta"
    assert effective["local_overrides"] is False


def test_document_indexed_field_change_reconciles_the_index(scoped):
    from _lifecycle.derived_cache_state import require_fresh_compiled_router

    root, _workspace, app, _parents = scoped
    created = app.invoke(create_request())
    path = created.result.path
    result = app.invoke(DocumentUpdateFrontmatterRequest(
        DocumentLocator(DocumentResource.ARTEFACT, path), document_revision_at(root / path),
        (FrontmatterField("type", "living/project"),)))
    assert result.status == "ok", result
    router = require_fresh_compiled_router(str(root))
    entry = router["artefact_index"]["design/subject"]
    assert entry["type"] == "living/project"
    assert entry["workspace"] == "workspace/alpha"
    assert entry["parent"] == "project/local"


def test_maintenance_link_rewrites_do_not_apply_active_policy(scoped):
    import compile_router
    from _application.links.fix import LinksFixRequest

    root, _workspace, app, _parents = scoped
    target = root / "Wiki/Brain Inbox.md"
    referrer = root / "Wiki/linker.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("---\ntype: living/wiki\ntags: []\n---\n\n# Brain Inbox\n")
    referrer.write_text("---\ntype: living/wiki\ntags: []\n---\n\nSee [[brain-inbox]].\n")
    compile_router.persist_compiled_router(str(root), compile_router.compile(str(root)))
    result = app.invoke(LinksFixRequest(path="Wiki/linker.md"))
    assert result.status == "ok" and result.result.substitutions == 1
    fields, body = parse_frontmatter(referrer.read_text())
    assert fields["tags"] == [] and "workspace" not in fields
    assert "[[Brain Inbox]]" in body
