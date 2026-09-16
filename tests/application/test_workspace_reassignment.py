"""Complete explicit adoption and reassignment, including archived ownership."""

from dataclasses import replace
from pathlib import Path
import pytest
from pytest_bdd import scenarios, given, when, then

from _application.artefact.set_workspace import ArtefactSetWorkspaceRequest
from _application.artefact.create import ArtefactCreateRequest
from _application.workspace_context import WorkspaceSelector
from _application.registry import current_application_catalogue
from _common import parse_frontmatter, serialize_frontmatter
from _lifecycle.derived_cache_state import require_fresh_compiled_router
from command_application import application_for
from test_workspace_mutation_context import scoped
from test_workspace_skill_preparation import MatchingAdmission

scenarios("../features/workspace_reassignment.feature")


@given("an unscoped owner with terminal, temporal and archived descendants", target_fixture="adoption_tree")
def adoption_tree(scoped):
    return seed_tree(scoped[0], scoped[2])


@when("the owner is recursively adopted into the bound workspace", target_fixture="adoption_result")
def adopt_tree(scoped, adoption_tree):
    return scoped[2].invoke(ArtefactSetWorkspaceRequest(adoption_tree[0], recursive=True))


@then("every owned record has explicit destination membership and policy tags")
def adopted_tree(scoped, adoption_tree, adoption_result):
    assert adoption_result.status == "ok", adoption_result
    assert set(adoption_result.result.subjects) == {adoption_tree[0], *adoption_tree[1]}
    for path in adoption_result.result.subjects:
        fields, _body = parse_frontmatter((scoped[0] / path).read_text())
        assert fields["workspace"] == "workspace/alpha"
        assert {"shared", "local", "both"} <= set(fields["tags"])


def request(path, destination="workspace/alpha", **kwargs):
    return ArtefactSetWorkspaceRequest(path, workspace_context=WorkspaceSelector(destination), **kwargs)


def seed_tree(root, app):
    global_app = application_for(root)
    parent = global_app.invoke(ArtefactCreateRequest("living/project", "Adoption", key="adoption"))
    assert parent.status == "ok", parent
    child = global_app.invoke(ArtefactCreateRequest("living/design", "Owned", key="owned", parent="project/adoption"))
    assert child.status == "ok", child
    from _application.artefact.set_status import ArtefactSetStatusRequest
    terminal = global_app.invoke(ArtefactSetStatusRequest(child.result.path, "implemented"))
    assert terminal.status == "ok", terminal
    temporal = root / "_Temporal/Plans/project~adoption/design~owned/20260917-plan~Owned.md"
    temporal.parent.mkdir(parents=True, exist_ok=True)
    temporal.write_text(serialize_frontmatter({"type": "temporal/plan", "parent": "design/owned", "tags": ["workspace/legacy"]}, body="# Plan\n"))
    archive = root / "_Archive/Designs/project~adoption/20260917-History.md"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text(serialize_frontmatter({"type": "living/design", "key": "history", "parent": "project/adoption", "status": "implemented", "archiveddate": "2026-09-17"}, body="# History\n"))
    archived_child = root / "_Archive/_Temporal/Plans/20260917-Historical-plan.md"
    archived_child.parent.mkdir(parents=True, exist_ok=True)
    archived_child.write_text(serialize_frontmatter({"type": "temporal/plan", "parent": "design/history", "archiveddate": "2026-09-17"}, body="# Historical plan\n"))
    return parent.result.path, (terminal.result.path, str(temporal.relative_to(root)), str(archive.relative_to(root)), str(archived_child.relative_to(root)))


def test_recursive_adoption_covers_complete_graph_without_tag_inference(scoped):
    root, _workspace, app, _parents = scoped
    path, descendants = seed_tree(root, app)
    refused = app.invoke(request(path))
    assert refused.status == "error" and "recursive" in refused.error.message
    result = app.invoke(request(path, recursive=True))
    assert result.status == "ok", result
    assert set(result.result.subjects) == {path, *descendants}
    for subject in result.result.subjects:
        fields, _ = parse_frontmatter((root / subject).read_text())
        assert fields["workspace"] == "workspace/alpha"
        assert {"shared", "local", "both"} <= set(fields["tags"])
    assert "workspace/legacy" in parse_frontmatter((root / descendants[1]).read_text())[0]["tags"]
    require_fresh_compiled_router(root)


@pytest.mark.parametrize("destination", ["workspace/alpha", "workspace/beta", "global"])
@pytest.mark.parametrize("parent_mode", ["omit", "replace", "clear"])
def test_reassignment_parent_choice_is_atomic(scoped, destination, parent_mode):
    root, _workspace, app, parents = scoped
    created = app.invoke(ArtefactCreateRequest("living/design", "Leaf", key="leaf"))
    path = created.result.path
    options = {"parent": "workspace/beta" if destination == "workspace/beta" else "project/shared"} if parent_mode == "replace" else {"clear_parent": True} if parent_mode == "clear" else {}
    result = app.invoke(request(path, destination, **options))
    expected_error = (parent_mode == "omit" and destination != "workspace/alpha") or (parent_mode == "replace" and destination == "global")
    if expected_error:
        assert result.status == "error" and (root / path).exists(), result
        return
    assert result.status == "ok", result
    fields, _ = parse_frontmatter((root / result.result.path).read_text())
    assert fields.get("workspace") == (None if destination == "global" else destination)
    assert fields.get("parent") == (None if parent_mode == "clear" else options.get("parent", "project/local"))
    require_fresh_compiled_router(root)


@pytest.mark.parametrize("case", ["duplicate", "temporal-owner", "cycle"])
def test_ambiguous_or_invalid_graph_fails_closed(scoped, case):
    root, _workspace, app, _parents = scoped
    path, descendants = seed_tree(root, app)
    if case == "duplicate":
        target = root / descendants[2]
        fields, body = parse_frontmatter(target.read_text())
        fields["key"] = "owned"
    elif case == "temporal-owner":
        target = root / descendants[1]
        fields, body = parse_frontmatter(target.read_text())
        fields["key"] = "vestigial"
        target.write_text(serialize_frontmatter(fields, body=body))
        target = root / descendants[3]
        fields, body = parse_frontmatter(target.read_text())
        fields["parent"] = "plan/vestigial"
    else:
        target = root / path
        fields, body = parse_frontmatter(target.read_text())
        fields["parent"] = "design/owned"
    target.write_text(serialize_frontmatter(fields, body=body))
    if case == "cycle":
        import compile_router
        compile_router.persist_compiled_router(str(root), compile_router.compile(str(root)))
    result = app.invoke(request(path, recursive=True))
    assert result.status == "error" and result.effects == "none", result
    assert not parse_frontmatter((root / path).read_text())[0].get("workspace")


def test_temporal_key_is_vestigial_and_archived_root_stays_in_archive(scoped):
    root, _workspace, app, _parents = scoped
    path, descendants = seed_tree(root, app)
    temporal = root / descendants[1]
    fields, body = parse_frontmatter(temporal.read_text())
    fields["key"] = "vestigial"
    temporal.write_text(serialize_frontmatter(fields, body=body))
    assert app.invoke(request(path, recursive=True)).status == "ok"
    result = app.invoke(request(descendants[2], "global", recursive=True, clear_parent=True))
    assert result.status == "ok", result
    assert result.result.path == descendants[2] and not result.result.moves
    assert parse_frontmatter(temporal.read_text())[0]["key"] == "vestigial"


@pytest.mark.parametrize("drift", ["new-child", "parent", "policy"])
def test_preparation_binds_graph_parent_and_policy(scoped, drift):
    root, workspace, app, _parents = scoped
    path, descendants = seed_tree(root, app)
    command = request(path, recursive=True)
    entry = current_application_catalogue().resolve(command)
    binding = entry.preparation.prepare(app._context, command)
    admission = MatchingAdmission(binding)
    if drift == "policy":
        from _bootstrap.workspace_binding import read_workspace_manifest, save_workspace_manifest_data
        manifest = read_workspace_manifest(workspace)
        manifest["defaults"]["tags"].append("changed")
        save_workspace_manifest_data(workspace, manifest)
    else:
        target = root / descendants[1]
        fields, body = parse_frontmatter(target.read_text())
        if drift == "new-child":
            target = target.with_name("20260918-plan~New.md")
        else:
            fields["parent"] = "project/adoption"
        target.write_text(serialize_frontmatter(fields, body=body))
    result = entry.executor(replace(app._context, admission=admission), command)
    assert result.status == "error" and admission.calls == 0
    assert not parse_frontmatter((root / path).read_text())[0].get("workspace")


def test_reassignment_index_failure_retains_exact_context_and_subjects(scoped, monkeypatch):
    import json
    from _portable import router_maintenance
    from _application.workspace_context import WorkspaceMutationPartial
    from _application.projection import canonical_wire_value
    root, _workspace, app, _parents = scoped
    path, descendants = seed_tree(root, app)
    command = request(path, recursive=True)
    binding = current_application_catalogue().resolve(command).preparation.prepare(app._context, command)
    def fail(*args, **kwargs):
        raise OSError("forced index failure")
    monkeypatch.setattr(router_maintenance, "maintain_router", fail)
    result = app.invoke(command)
    assert isinstance(result, WorkspaceMutationPartial), result
    assert canonical_wire_value(result.mutation_context) == json.loads(binding.review_json)["mutation_context"]
    assert {item.subject for item in result.committed_effects} == {path, *descendants}
    assert result.error.next_action.command_id == "runtime.refresh-router"


def test_reassignment_requires_explicit_global_and_guards_default_parent(scoped):
    root, _workspace, app, parents = scoped
    unbound = application_for(root)
    implicit = unbound.invoke(ArtefactSetWorkspaceRequest(parents["shared"]))
    assert implicit.status == "error" and "explicit" in implicit.error.message
    guarded = app.invoke(request(parents["shared"], "global", clear_parent=True))
    assert guarded.status == "error" and "default_parent" in guarded.error.message


def test_reassignment_projection_contract(scoped):
    from _application.projection import request_schema, canonical_wire_value, project_identity
    from _application.registry import current_request_resolver
    from _application.types import Projection, Authority
    from jsonschema import Draft202012Validator
    command = request("project/adoption", recursive=True, clear_parent=True)
    payload = canonical_wire_value(command)
    Draft202012Validator(request_schema(type(command))).validate(payload)
    assert current_request_resolver().resolve(command.COMMAND_ID, payload) == command
    entry = current_application_catalogue().resolve(command)
    assert entry.authority is Authority.CONTRIBUTOR
    assert any(item.projection is Projection.MCP and item.supported for item in entry.projections)
    assert project_identity(command.COMMAND_ID).cli_argv == ("artefact", "set-workspace")


def test_recursive_parent_replacement_projects_active_and_terminal_not_archive(scoped):
    root, _workspace, app, _parents = scoped
    path, descendants = seed_tree(root, app)
    result = app.invoke(request(path, recursive=True, parent="project/local"))
    assert result.status == "ok", result
    moves = {item.old_path: item.new_path for item in result.result.moves}
    assert {path, descendants[0], descendants[1]} == set(moves)
    assert "+Implemented/" in moves[descendants[0]]
    assert moves[path] == "Projects/local/Adoption.md"
    assert all("project~local/" in moves[child] for child in descendants[:2])
    assert all((root / subject).exists() for subject in result.result.subjects)
    assert all((root / subject).exists() for subject in descendants[2:])
    require_fresh_compiled_router(root)


@pytest.mark.parametrize("destination", ["workspace/missing", "workspace/beta"])
def test_missing_or_terminal_destination_is_rejected(scoped, destination):
    from _application.artefact.set_status import ArtefactSetStatusRequest
    root, _workspace, app, _parents = scoped
    path, _descendants = seed_tree(root, app)
    if destination == "workspace/beta":
        from _application.workspace.update_policy import WorkspaceUpdatePolicyRequest
        assert app.invoke(WorkspaceUpdatePolicyRequest("workspace/beta", clear_parent=True)).status == "ok"
        assert app.invoke(ArtefactSetStatusRequest("workspace/beta", "completed")).status == "ok"
    result = app.invoke(request(path, destination, recursive=True))
    assert result.status == "error" and result.effects == "none"


def test_reassignment_lock_admission_and_receipt_are_single_boundaries(scoped, monkeypatch):
    from contextlib import contextmanager
    import _common
    root, _workspace, app, _parents = scoped
    path, _descendants = seed_tree(root, app)
    calls = []
    actual = _common.vault_mutation_lock
    @contextmanager
    def tracked(*args, **kwargs):
        calls.append("enter")
        with actual(*args, **kwargs):
            yield
        calls.append("exit")
    monkeypatch.setattr(_common, "vault_mutation_lock", tracked)
    result = app.invoke(request(path, recursive=True))
    assert result.status == "ok", result
    assert calls == ["enter", "exit"]
    assert len(app._context.authorisation.receipts.intents) == 1
    assert len(app._context.authorisation.receipts.outcomes) == 1
    assert {item.subject for item in result.committed_effects} == set(result.result.changed_subjects)
    assert len(result.committed_effects) == 5


def test_reassignment_mid_write_failure_reports_only_committed_subjects(scoped, monkeypatch):
    import edit
    from _application.workspace_context import WorkspaceMutationPartial
    root, _workspace, app, _parents = scoped
    path, _descendants = seed_tree(root, app)
    actual = edit.safe_write_active_or_archived_artefact
    calls = []
    def fail_second(*args, **kwargs):
        calls.append(args[0])
        if len(calls) == 2:
            raise OSError("forced second metadata failure")
        return actual(*args, **kwargs)
    monkeypatch.setattr(edit, "safe_write_active_or_archived_artefact", fail_second)
    result = app.invoke(request(path, recursive=True))
    assert isinstance(result, WorkspaceMutationPartial), result
    assert {effect.subject for effect in result.committed_effects} == {path}
    assert result.mutation_context.workspace == "workspace/alpha"
    require_fresh_compiled_router(root)
    monkeypatch.setattr(edit, "safe_write_active_or_archived_artefact", actual)
    retry = app.invoke(request(path, recursive=True))
    assert retry.status == "ok", retry
    assert path not in retry.result.changed_subjects
    assert {effect.subject for effect in retry.committed_effects} == set(retry.result.changed_subjects)
    repeated = app.invoke(request(path, recursive=True))
    assert repeated.status == "ok" and not repeated.result.changed
    assert not repeated.committed_effects


def test_legacy_recovery_create_cannot_bypass_workspace_policy(scoped, capsys):
    import create
    root, _workspace, _app, _parents = scoped
    with pytest.raises(SystemExit):
        create.main(["--vault", str(root), "--type", "living/design", "--title", "Legacy bypass", "--json"])
    assert "wholly unscoped" in capsys.readouterr().out
    assert not list(root.rglob("Legacy bypass.md"))


def test_legacy_recovery_create_cannot_create_workspace_hub(command_vault_clone, capsys):
    import create
    root = command_vault_clone.vault_root
    with pytest.raises(SystemExit):
        create.main(["--vault", str(root), "--type", "living/workspace", "--title", "Legacy Hub", "--json"])
    assert "command.py artefact create" in capsys.readouterr().out
    assert not list(root.rglob("Legacy Hub.md"))
