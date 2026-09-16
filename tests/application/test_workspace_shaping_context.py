"""Shaping is one workspace-aware composite semantic mutation."""

from dataclasses import replace

import pytest
from pytest_bdd import given, scenarios, then, when

from _application.shaping.start import ShapingStartRequest, ShapingMode, execute
from _application.shaping._start_preparation import prepare_session
from _application.workspace_context import WorkspaceSelector, WorkspaceMutationPartial
from _application.workspace.update_policy import WorkspaceUpdatePolicyRequest
from _application.projection import canonical_wire_value, request_schema
from _application.registry import current_request_resolver
from _bootstrap.workspace_binding import read_workspace_manifest, save_workspace_manifest_data
from _common import parse_frontmatter, serialize_frontmatter, PartialApplyError
from test_workspace_mutation_context import scoped  # noqa: F401
from test_workspace_skill_preparation import MatchingAdmission
from test_shaping_start_owner import DESIGN_REFERENCE

scenarios("../features/workspace_shaping.feature")


@given("a shaping caller bound to shared and local workspace policy")
def shaping_caller(scoped):
    assert scoped[2] is not None


@when("the caller opens a shaping session", target_fixture="shaped")
def open_session(scoped):
    result = scoped[2].invoke(ShapingStartRequest(DESIGN_REFERENCE, ShapingMode.REFINE))
    assert result.status == "ok", result
    return result.result


@then("the new transcript is scoped with the local parent and both policy tag sets")
def scoped_transcript(scoped, shaped):
    fields, _ = parse_frontmatter((scoped[0] / shaped.transcript_path).read_text())
    assert fields["workspace"] == "workspace/alpha"
    assert fields["parent"] == "project/local"
    assert {"shared", "local", "both"} <= set(fields["tags"])


def test_new_transcript_preserves_explicit_template_tags(scoped):
    from start_shaping_session import _transcript_artefact
    from _lifecycle.derived_cache_state import require_fresh_compiled_router
    root, _directory, app, _parents = scoped
    definition = _transcript_artefact(require_fresh_compiled_router(str(root)))
    path = root / (definition["template_file"].removesuffix(".md") + ".md")
    fields, body = parse_frontmatter(path.read_text())
    fields["tags"] = ["explicit-transcript", "both"]
    path.write_text(serialize_frontmatter(fields, body=body))
    result = app.invoke(ShapingStartRequest(DESIGN_REFERENCE, ShapingMode.REFINE))
    assert result.status == "ok", result
    transcript_content = (root / result.result.transcript_path).read_text()
    transcript, _ = parse_frontmatter(transcript_content)
    assert {"explicit-transcript", "shared", "local", "both", "project/local"} <= set(transcript["tags"])
    assert transcript["tags"].count("both") == 1


@pytest.mark.parametrize("selection,workspace,parent,tags", [
    (None, "workspace/alpha", "project/local", {"shared", "local", "both"}),
    ("global", None, None, set()),
    ("workspace/beta", "workspace/beta", "workspace/beta", {"beta"}),
])
def test_shaping_creation_policy_and_preserved_source(scoped, selection, workspace, parent, tags):
    root, _directory, app, _parents = scoped
    request = ShapingStartRequest(DESIGN_REFERENCE, ShapingMode.REFINE,
        workspace_context=WorkspaceSelector(selection) if selection else None)
    prepared = prepare_session(app._context, request)
    admission = MatchingAdmission(prepared)
    result = execute(replace(app._context, admission=admission), request)
    assert result.status == "ok", result
    assert admission.calls == 1
    transcript_content = (root / result.result.transcript_path).read_text()
    transcript, _ = parse_frontmatter(transcript_content)
    source, _ = parse_frontmatter((root / result.result.target_path).read_text())
    assert transcript.get("workspace") == workspace
    assert transcript.get("parent") == parent
    assert tags <= set(transcript.get("tags", []))
    assert "designs" in transcript["tags"]
    assert "SOURCE_" not in transcript_content
    assert transcript == prepared.review["transcript_fields"]
    assert tags <= set(source.get("tags", []))
    assert source.get("workspace") is None
    assert source.get("parent") == "project/command-fixture"
    if selection != "workspace/beta":
        assert "beta" not in transcript.get("tags", [])
    else:
        assert "local" not in transcript.get("tags", [])
    assert result.result.mutation_context.workspace == workspace
    assert canonical_wire_value(result.result.mutation_context) == prepared.review["mutation_context"]
    assert "key" not in transcript


def test_continuation_preserves_membership_parent_and_restores_both_subjects(scoped):
    root, _directory, app, _parents = scoped
    request = ShapingStartRequest(DESIGN_REFERENCE, ShapingMode.REFINE)
    first = app.invoke(request)
    assert first.status == "ok", first
    for path in (first.result.target_path, first.result.transcript_path):
        fields, body = parse_frontmatter((root / path).read_text())
        fields["tags"] = ["explicit-transcript-or-source"]
        (root / path).write_text(serialize_frontmatter(fields, body=body))
    alternate = app.invoke(replace(request, workspace_context=WorkspaceSelector("workspace/beta")))
    assert alternate.status == "ok", alternate
    assert alternate.result.transcript_path == first.result.transcript_path
    assert alternate.result.transcript_operation.value == "appended"
    for path in alternate.result.changed_paths:
        fields, _ = parse_frontmatter((root / path).read_text())
        assert {"beta", "explicit-transcript-or-source"} <= set(fields["tags"])
        assert "local" not in fields["tags"]
    transcript, _ = parse_frontmatter((root / first.result.transcript_path).read_text())
    assert transcript["workspace"] == "workspace/alpha"
    assert transcript["parent"] == "project/local"
    assert alternate.result.mutation_context.parent == "project/local"
    assert alternate.result.mutation_context.parent_source == "preserved"


def test_archived_transcript_is_not_reopened_by_parent_independent_lookup(scoped):
    root, _directory, app, _parents = scoped
    request = ShapingStartRequest(DESIGN_REFERENCE, ShapingMode.REFINE)
    first = app.invoke(request)
    assert first.status == "ok", first
    transcript = root / first.result.transcript_path
    archived = root / "_Archive" / first.result.transcript_path
    archived.parent.mkdir(parents=True)
    transcript.rename(archived)
    source = root / first.result.target_path
    source.write_text(source.read_text().replace(first.result.transcript_path.removesuffix(".md"),
        archived.relative_to(root).as_posix().removesuffix(".md")))
    before = archived.read_bytes()
    next_session = app.invoke(request)
    assert next_session.status == "ok", next_session
    assert next_session.result.transcript_operation.value == "created"
    assert archived.read_bytes() == before


@pytest.mark.parametrize("selection", [None, "global", "workspace/beta"])
def test_invalid_binding_fails_before_any_shaping_write(scoped, selection):
    root, directory, app, _parents = scoped
    manifest = read_workspace_manifest(directory)
    manifest["links"]["workspace"] = "missing"
    save_workspace_manifest_data(directory, manifest)
    before = {path: path.read_bytes() for path in root.rglob("*.md")}
    result = app.invoke(ShapingStartRequest(DESIGN_REFERENCE, ShapingMode.REFINE,
        workspace_context=WorkspaceSelector(selection) if selection else None))
    assert result.status == "error", result
    assert result.effects == "none"
    assert before == {path: path.read_bytes() for path in root.rglob("*.md")}


@pytest.mark.parametrize("drift", ["shared", "local", "parent"])
def test_shaping_policy_drift_is_not_admitted(scoped, drift):
    root, directory, app, parents = scoped
    request = ShapingStartRequest(DESIGN_REFERENCE, ShapingMode.REFINE)
    binding = prepare_session(app._context, request)
    admission = MatchingAdmission(binding)
    if drift == "shared":
        assert app.invoke(WorkspaceUpdatePolicyRequest("workspace/alpha", default_tags=("changed",))).status == "ok"
    elif drift == "local":
        manifest = read_workspace_manifest(directory)
        manifest["defaults"]["tags"] = ["changed"]
        save_workspace_manifest_data(directory, manifest)
    else:
        path = root / parents["local"]
        path.write_text(path.read_text() + "\nParent changed.\n")
    before = {path: path.read_bytes() for path in root.rglob("*.md")}
    result = execute(replace(app._context, admission=admission), request)
    assert result.status == "error", result
    assert result.effects == "none"
    assert admission.calls == 0
    assert before == {path: path.read_bytes() for path in root.rglob("*.md")}


@pytest.mark.parametrize("failure", ["backlink", "index", "both"])
def test_shaping_partial_retains_exact_context_and_actual_subjects(scoped, monkeypatch, failure):
    import start_shaping_session
    from _portable import router_maintenance
    root, _directory, app, _parents = scoped
    request = ShapingStartRequest(DESIGN_REFERENCE, ShapingMode.REFINE)
    binding = prepare_session(app._context, request)
    if failure in {"backlink", "both"}:
        def fail_backlink(*args, **kwargs):
            raise PartialApplyError("repair missing transcript backlink")
        monkeypatch.setattr(start_shaping_session, "_add_transcript_link", fail_backlink)
    if failure in {"index", "both"}:
        def fail_index(*args, **kwargs):
            raise OSError("index unavailable")
        monkeypatch.setattr(router_maintenance, "maintain_router", fail_index)
    result = execute(replace(app._context, admission=MatchingAdmission(binding)), request)
    assert isinstance(result, WorkspaceMutationPartial), result
    assert canonical_wire_value(result.mutation_context) == binding.review["mutation_context"]
    subjects = tuple(effect.subject for effect in result.committed_effects)
    assert len(subjects) == 2
    assert all((root / path).is_file() for path in subjects)
    transcript = next(path for path in subjects if path.startswith("_Temporal/"))
    fields, _ = parse_frontmatter((root / transcript).read_text())
    assert fields["workspace"] == "workspace/alpha"
    assert fields["parent"] == "project/local"
    assert {"shared", "local"} <= set(fields["tags"])
    if failure in {"index", "both"}:
        assert result.error.next_action.command_id == "runtime.refresh-router"
    if failure in {"backlink", "both"}:
        assert "repair missing transcript backlink" in result.error.message


def test_shaping_selector_version_and_projection():
    from jsonschema import Draft202012Validator
    from _application.semantic_mutations import SEMANTIC_ARTEFACT_COMMANDS
    assert ShapingStartRequest.COMMAND_VERSION == 2
    assert "shaping.start" in SEMANTIC_ARTEFACT_COMMANDS
    schema = request_schema(ShapingStartRequest)
    for selection in (None, "global", "workspace/alpha"):
        payload = {"target": DESIGN_REFERENCE, "mode": "refine", "workspace_context": selection}
        Draft202012Validator(schema).validate(payload)
        request = current_request_resolver().resolve("shaping.start", payload)
        assert canonical_wire_value(request) == payload
    with pytest.raises(ValueError):
        current_request_resolver().resolve("shaping.start", {"target": DESIGN_REFERENCE,
            "mode": "refine", "workspace_context": "/tmp/workspace"})
