"""Discoverable workspace failures and historical membership diagnostics."""

import pytest
from _common import parse_frontmatter, serialize_frontmatter
from _lifecycle.workspace_checks import workspace_findings
from _lifecycle.derived_cache_state import require_fresh_compiled_router
from _application.vault.check import VaultCheckRequest
from _application.artefact.set_status import ArtefactSetStatusRequest
from _bootstrap.workspace_binding import read_workspace_manifest, save_workspace_manifest_data
from test_workspace_mutation_context import scoped


@pytest.mark.parametrize("explicit", [False, True])
def test_archived_hub_self_membership_is_valid_but_member_reference_is_not(scoped, explicit):
    from _application.artefact.archive import ArtefactArchiveRequest
    from _application.artefact.set_workspace import ArtefactSetWorkspaceRequest
    from _application.workspace.ensure_registration import WorkspaceEnsureRegistrationRequest
    from _application.workspace_context import WorkspaceSelector

    root, _local, app, _parents = scoped
    assert app.invoke(WorkspaceEnsureRegistrationRequest("history")).status == "ok"
    if explicit:
        assigned = app.invoke(ArtefactSetWorkspaceRequest("workspace/history",
            workspace_context=WorkspaceSelector("workspace/history")))
        assert assigned.status == "ok", assigned
    archived = app.invoke(ArtefactArchiveRequest("workspace/history"))
    assert archived.status == "ok", archived
    assert not workspace_findings(root, require_fresh_compiled_router(root))

    member = root / "_Temporal/Plans/20260917-plan~Stranded.md"
    member.parent.mkdir(parents=True, exist_ok=True)
    member.write_text(serialize_frontmatter({"type": "temporal/plan", "workspace": "workspace/history"}, body="# Stranded\n"))
    findings = workspace_findings(root, require_fresh_compiled_router(root))
    assert [(item["code"], item["file"]) for item in findings] == [
        ("workspace_reference_archived", str(member.relative_to(root)))]


@pytest.mark.parametrize("workspace,code", [("bad", "workspace_reference_malformed"),
    ("workspace/missing", "workspace_reference_missing"),
    ("workspace/beta", "workspace_ownership_invalid"),
    (None, "workspace_ownership_invalid")])
def test_checks_report_explicit_reference_and_ownership_failures(scoped, workspace, code):
    root, _local, _app, _parents = scoped
    path = root / "_Temporal/Plans/20260917-plan~Check.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = {"type": "temporal/plan", "parent": "project/local"}
    if workspace is not None:
        fields["workspace"] = workspace
    path.write_text(serialize_frontmatter(fields, body="# Check\n"))
    findings = workspace_findings(root, require_fresh_compiled_router(root))
    assert any(item["code"] == code and item["file"] == str(path.relative_to(root)) and item["fix"] for item in findings)


@pytest.mark.parametrize("kind", ["archived", "wrong_type", "terminal"])
def test_historical_terminal_identity_is_valid_but_removed_hubs_are_not(scoped, kind):
    import compile_router
    root, local, app, _parents = scoped
    router = require_fresh_compiled_router(root)
    source = root / router["artefact_index"]["workspace/alpha"]["path"]
    if kind == "terminal":
        assert app.invoke(ArtefactSetStatusRequest("workspace/alpha", "completed")).status == "ok"
    elif kind == "archived":
        target = root / "_Archive/Workspaces/20260917-Alpha.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)
    else:
        fields, body = parse_frontmatter(source.read_text())
        fields["type"] = "living/project"
        source.write_text(serialize_frontmatter(fields, body=body))
    compile_router.persist_compiled_router(str(root), compile_router.compile(str(root)))
    findings = workspace_findings(root, require_fresh_compiled_router(root), workspace_dir=local)
    codes = {item["code"] for item in findings}
    if kind == "terminal":
        assert codes == {"workspace_binding_terminal_inactive"}
    else:
        assert "workspace_reference_" + kind in codes


def test_checks_surface_local_policy_and_tag_only_adoption_candidates(scoped):
    root, local, app, _parents = scoped
    manifest = read_workspace_manifest(local)
    manifest["defaults"]["parent"] = "workspace/beta"
    save_workspace_manifest_data(local, manifest)
    path = root / "_Temporal/Plans/20260917-plan~Legacy.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize_frontmatter({"type": "temporal/plan", "tags": ["workspace/alpha"]}, body="# Legacy\n"))
    result = app.invoke(VaultCheckRequest(check="workspace_contract", actionable=True))
    assert result.status == "ok", result
    codes = {item.code for item in result.result.findings}
    assert {"workspace_binding_configured_invalid", "workspace_adoption_candidate"} <= codes
    assert "workspace" not in parse_frontmatter(path.read_text())[0]
