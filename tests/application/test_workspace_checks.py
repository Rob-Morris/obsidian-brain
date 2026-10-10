"""Discoverable workspace failures and historical membership diagnostics."""

import pytest
from brain_test_support import link_folder, register_other_brain
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


# ---------------------------------------------------------------------------
# The workspace link, seen from both ends (DD-083 item 6)
# ---------------------------------------------------------------------------

def _link(root, tmp_path, key, manifest):
    """A registry row for ``key`` and, unless ``manifest`` is None, the text of the manifest at its folder."""
    return link_folder(root, tmp_path / f"linked-{key}", key, manifest)


def _registry_findings(root):
    from _bootstrap.diagnostics import collect_registry_check_findings

    return {item["file"]: item for item in collect_registry_check_findings(root)}


@pytest.fixture
def linked_root(command_vault_clone, tmp_path):
    import vault_registry

    root = command_vault_clone.vault_root
    vault_registry.register(root, "brain")
    register_other_brain(tmp_path)
    return root


DISAGREES = ("workspace_link_disagreement", "warning", True)


def _unverifiable(reason):
    return ("workspace_link_unverifiable", "info", False, reason)


@pytest.mark.parametrize(("manifest", "expected"), [
    ("brain: brain\nslug: elsewhere\nlinks:\n  workspace: key\n", None),
    ("brain: brain\r\nslug: s\r\nlinks:\r\n  workspace: key\r\n", None),
    ("brain: brain\nslug: s\nlinks:\n  workspace: another\n", DISAGREES),
    ("brain: other\nslug: s\nlinks:\n  workspace: key\n", DISAGREES),
    ("brain: unknown-here\nslug: s\nlinks:\n  workspace: key\n", _unverifiable("brain_unresolved")),
    (None, _unverifiable("no_manifest")),
    ("brain: [unclosed\n", _unverifiable("unreadable")),
    ("brain: brain\nslug: bind-era\n", _unverifiable("key_missing")),
    # A hub key that is not a valid key names no hub, so it proves nothing (never other_key).
    ("brain: brain\nlinks:\n  workspace: 5\n", _unverifiable("key_invalid")),
    ("brain: brain\nlinks:\n  workspace: ''\n", _unverifiable("key_invalid")),
    ("brain: brain\nlinks:\n  workspace: [alpha]\n", _unverifiable("key_invalid")),
    ("brain: brain\nlinks:\n  workspace: 'Not A Key!'\n", _unverifiable("key_invalid")),
])
def test_each_row_is_verified_against_the_manifest_in_its_folder(linked_root, tmp_path, manifest, expected):
    folder = _link(linked_root, tmp_path, "key", manifest)
    before = sorted(str(path) for path in folder.rglob("*"))

    findings = _registry_findings(linked_root)

    row = findings.get(".brain/local/workspaces.json#key")
    if expected is None:
        assert findings == {}, "an agreeing row is quiet even when the slug differs from the key"
    else:
        assert (row["code"], row["severity"], "repair" in row) == expected[:3]
        assert row["evidence"]["key"] == "key" and row["evidence"]["path"] == str(folder)
        if len(expected) == 4:
            assert row["evidence"]["reason"] == expected[3]
            assert expected[3] not in row["message"], "the message is prose, not the verdict token"
            assert row["fix"] in row["message"]
    assert sorted(str(path) for path in folder.rglob("*")) == before, "verification writes nothing in the folder"


def test_a_row_recording_a_vault_root_is_unverifiable_and_not_sent_to_setup(linked_root):
    import workspace_registry

    workspace_registry.register_workspace(linked_root, "itself", linked_root)

    row = _registry_findings(linked_root)[".brain/local/workspaces.json#itself"]

    assert (row["code"], row["evidence"]["reason"]) == ("workspace_link_unverifiable", "vault_root")
    assert "workspace setup" not in row["fix"] and "workspace unregister" in row["fix"]


def test_an_unreachable_folder_and_a_malformed_file_are_reported_with_their_own_identity(linked_root, tmp_path):
    import shutil

    folder = _link(linked_root, tmp_path, "gone", "brain: brain\nlinks:\n  workspace: gone\n")
    shutil.rmtree(folder)
    findings = _registry_findings(linked_root)
    assert findings[".brain/local/workspaces.json#gone"]["code"] == "workspace_folder_unreachable"
    assert findings[".brain/local/workspaces.json#gone"]["severity"] == "info"
    assert findings[".brain/local/workspaces.json#gone"]["evidence"] == {"key": "gone", "path": str(folder)}

    (linked_root / ".brain/local/workspaces.json").write_text('{"workspaces": {"relative": "foreign"}}\n')
    malformed = _registry_findings(linked_root)[".brain/local/workspaces.json"]
    assert (malformed["code"], malformed["severity"], malformed["repair"]["scope"]) == (
        "workspace_registry_malformed", "warning", "registry")


@pytest.mark.parametrize("content, reason", [("{broken\n", "invalid_json"), ("[]\n", "not_an_object"),
                                             ('{"workspaces": [1]}\n', "workspaces_not_an_object"),
                                             (b"\xff{}", "not_utf8")])
def test_a_registry_whose_rows_cannot_be_read_is_a_judgement_finding_with_no_repair(linked_root, content, reason):
    from _repair_common import JUDGEMENT_FINDINGS

    path = linked_root / ".brain/local/workspaces.json"
    path.write_bytes(content) if isinstance(content, bytes) else path.write_text(content)

    findings = list(_registry_findings(linked_root).values())

    assert [(item["code"], item["severity"], "repair" in item, item["evidence"]) for item in findings] == [
        ("workspace_registry_unparseable", "warning", False, {"reason": reason})]
    assert ("workspace_registry", "workspace_registry_unparseable") in JUDGEMENT_FINDINGS
    assert "allow_row_loss" in findings[0]["message"]


def test_an_unreadable_vault_registry_is_reported_at_the_brain_end(linked_root, tmp_path, monkeypatch):
    import vault_registry

    _link(linked_root, tmp_path, "key", "brain: brain\nslug: s\nlinks:\n  workspace: another\n")

    def unreadable(*_args, **_kwargs):
        raise vault_registry.RegistryReadError("vault registry unreadable")

    monkeypatch.setattr(vault_registry, "brain_id_for_path", unreadable)
    findings = list(_registry_findings(linked_root).values())

    assert [(item["code"], item["severity"], item["evidence"]) for item in findings] == [
        ("workspace_links_unverified", "info", {"reason": "vault_registry_unreadable"})]


def test_an_unreadable_registry_is_reported_without_an_automatic_repair(linked_root):
    import os
    import sys

    if sys.platform == "win32" or os.geteuid() == 0:
        pytest.skip("POSIX permission bits that bind the test user")
    path = linked_root / ".brain/local/workspaces.json"
    path.write_text('{"workspaces": {}}\n')
    path.chmod(0)
    try:
        findings = _registry_findings(linked_root)
    finally:
        path.chmod(0o644)
    assert [(item["code"], "repair" in item, item["evidence"]) for item in findings.values()] == [
        ("workspace_registry_unreadable", False, {"reason": "EACCES"})]
    from _repair_common import JUDGEMENT_FINDINGS

    assert ("workspace_registry", "workspace_registry_unreadable") in JUDGEMENT_FINDINGS
    assert "Restore read access" in next(iter(findings.values()))["message"]


def test_an_unregistered_brain_verifies_no_row_and_its_repair_is_a_noop(command_vault_clone, tmp_path):
    import vault_registry
    import workspace_registry
    from _application.workspace.repair_registry import RegistryRepairStatus, WorkspaceRepairRegistryRequest
    from command_application import application_for

    root = command_vault_clone.vault_root
    register_other_brain(tmp_path)
    _link(root, tmp_path, "key", "brain: other\nlinks:\n  workspace: key\n")
    registry = (root / ".brain/local/workspaces.json").read_bytes()

    assert _registry_findings(root) == {}
    result = application_for(root).invoke(WorkspaceRepairRegistryRequest())

    assert result.status == "ok", result
    assert (result.result.status, result.result.reason) == (
        RegistryRepairStatus.NOOP, workspace_registry.Unverified.BRAIN_UNREGISTERED.describe())
    assert (root / ".brain/local/workspaces.json").read_bytes() == registry


@pytest.mark.parametrize("row", ["absent", "other-path", "present", "trailing-slash", "extra-key", "embedded",
                                 "list-shaped", "workspaces-list", "invalid-json"])
def test_the_workspace_end_reports_a_missing_row_as_information(scoped, row):
    import workspace_registry

    root, local, app, _parents = scoped
    manifest = read_workspace_manifest(local)
    manifest.pop("defaults")
    save_workspace_manifest_data(local, manifest)
    if row == "other-path":
        workspace_registry.save_registry(root, {"alpha": {"path": "/elsewhere"}})
    elif row == "present":
        workspace_registry.save_registry(root, {"alpha": {"path": workspace_registry.canonical_path(local)}})
    elif row == "trailing-slash":
        workspace_registry.save_registry(root, {"alpha": {"path": workspace_registry.canonical_path(local) + "/"}})
    elif row == "extra-key":
        workspace_registry.save_registry(root, {"alpha": {"path": str(local), "mode": "linked"}})
    elif row == "embedded":
        (root / workspace_registry.EMBEDDED_DATA_DIR / "alpha").mkdir(parents=True)
    elif row != "absent":
        # The Brain end reports a registry whose rows cannot be read; this end must not crash on it.
        content = {"list-shaped": "[]\n", "workspaces-list": '{"workspaces": [1]}\n', "invalid-json": "{broken\n"}[row]
        (root / workspace_registry.REGISTRY_REL).write_text(content)

    result = app.invoke(VaultCheckRequest(check="workspace_contract"))

    missing = [item for item in result.result.findings if item.code == "workspace_registry_missing"]
    if row not in {"absent", "other-path"}:
        assert missing == []
    else:
        assert [(item.severity, item.file) for item in missing] == [("info", str(local / ".brain/local/workspace.yaml"))]
