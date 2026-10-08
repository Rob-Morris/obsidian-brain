"""Every classified family-less finding declares exactly the evidence its row promises (DD-086).

The producers run for real over fixture vaults; the evidence they declare is
pinned per code, and representative codes are dismissed through the real
maintenance command path to show the same condition stays quiet and a
changed one reopens.
"""

from __future__ import annotations

from datetime import timedelta
import json
import os

import pytest

import check
import compile_router
from _application.artefact.archive import ArtefactArchiveRequest
from _application.artefact.set_status import ArtefactSetStatusRequest
from _application.maintenance import _detection
from _application.maintenance.claim import MaintenanceClaimRequest
from _application.maintenance.dismiss import MaintenanceDismissRequest
from _application.maintenance.list import MaintenanceListRequest
from _application.maintenance.run import MaintenanceRunRequest
from _application.results import ErrorCode, WarningCode
from _bootstrap.maintenance_decisions import DISMISSAL_RETENTION, ItemState
from _bootstrap.maintenance_findings import Identity, finding_fingerprint, finding_key
from _bootstrap.workspace_binding import read_workspace_manifest, save_workspace_manifest_data
from _common import parse_frontmatter, serialize_frontmatter
from _common._workspace import BindingState, InvalidBindingCause
from _lifecycle import workspace_checks
from _repair_common import JUDGEMENT_FINDINGS
from test_maintenance_decisions import DECISIONS, NOW, _Clock, _Sibling, _invoke, _read
from test_workspace_mutation_context import scoped  # noqa: F401 - fixture


PLAN = "_Temporal/Plans/20260917-plan~Probe.md"


def _write(root, rel_path, fields, body="# Probe\n"):
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize_frontmatter(fields, body=body))
    return rel_path


def _edit_frontmatter(root, reference, app, **changes):
    """Rewrite a living artefact's frontmatter directly, bypassing the handlers that would refuse it."""
    from _lifecycle.derived_cache_state import require_fresh_compiled_router

    path = root / require_fresh_compiled_router(root)["artefact_index"][reference]["path"]
    fields, body = parse_frontmatter(path.read_text())
    fields.update(changes)
    path.write_text(serialize_frontmatter(fields, body=body))
    return str(path.relative_to(root))


def _recompile(root):
    compile_router.persist_compiled_router(str(root), compile_router.compile(str(root)))


def _findings(root, workspace_dir=None, router=None):
    if router is None:
        _recompile(root)
    return check.run_checks(str(root), router, workspace_dir=workspace_dir)["findings"]


def _finding(findings, code, file=None, check_name="workspace_contract"):
    matches = [item for item in findings if item["check"] == check_name and item.get("code") == code
               and (file is None or item["file"] == file)]
    assert len(matches) == 1, [(item["check"], item.get("code"), item["file"]) for item in findings]
    return matches[0]


def _classified(raw):
    (finding,) = _detection.classify([raw])
    assert finding.breach is None, finding.breach
    assert finding.identity is JUDGEMENT_FINDINGS[(raw["check"], raw.get("code"))]
    return finding


# ---------------------------------------------------------------------------
# One case per code: setup returns (code, file, expected evidence)
# ---------------------------------------------------------------------------

def case_reference_malformed(root, local, app):
    return "workspace_reference_malformed", _write(root, PLAN, {"type": "temporal/plan", "workspace": "bad"}), {"workspace": "bad"}


def case_reference_missing(root, local, app):
    return ("workspace_reference_missing", _write(root, PLAN, {"type": "temporal/plan", "workspace": "workspace/missing"}),
            {"workspace": "workspace/missing"})


def case_reference_archived(root, local, app):
    from _application.workspace.ensure_registration import WorkspaceEnsureRegistrationRequest

    assert app.invoke(WorkspaceEnsureRegistrationRequest("history")).status == "ok"
    assert app.invoke(ArtefactArchiveRequest("workspace/history")).status == "ok"
    return ("workspace_reference_archived", _write(root, PLAN, {"type": "temporal/plan", "workspace": "workspace/history"}),
            {"workspace": "workspace/history"})


def case_reference_wrong_type(root, local, app):
    _edit_frontmatter(root, "workspace/beta", app, type="living/project")
    return ("workspace_reference_wrong_type", _write(root, PLAN, {"type": "temporal/plan", "workspace": "workspace/beta"}),
            {"workspace": "workspace/beta", "type": "living/project"})


def case_hub_invalid(root, local, app):
    _edit_frontmatter(root, "workspace/beta", app, parent="project/vanished")
    return ("workspace_hub_invalid", _write(root, PLAN, {"type": "temporal/plan", "workspace": "workspace/beta"}),
            {"workspace": "workspace/beta"})


def case_ownership_parent(root, local, app):
    return ("workspace_ownership_invalid",
            _write(root, PLAN, {"type": "temporal/plan", "workspace": "workspace/alpha", "parent": "project/vanished"}),
            {"cause": "parent", "parent": "project/vanished"})


def case_ownership_archived_parent(root, local, app):
    from _application.artefact.create import ArtefactCreateRequest
    from _application.workspace_context import WorkspaceSelector

    created = app.invoke(ArtefactCreateRequest("living/design", "Doomed", key="doomed",
                                               workspace_context=WorkspaceSelector("workspace/alpha")))
    assert created.status == "ok", created
    archived = app.invoke(ArtefactArchiveRequest("design/doomed"))
    assert archived.status == "ok", archived
    return ("workspace_ownership_invalid",
            _write(root, PLAN, {"type": "temporal/plan", "workspace": "workspace/alpha", "parent": "design/doomed"}),
            {"cause": "archived_parent", "parent": "design/doomed"})


def case_ownership_workspace_mismatch(root, local, app):
    return ("workspace_ownership_invalid",
            _write(root, PLAN, {"type": "temporal/plan", "workspace": "workspace/beta", "parent": "project/local"}),
            {"cause": "workspace_mismatch", "parent": "project/local", "workspace": "workspace/beta"})


def case_ownership_lineage(root, local, app):
    _write(root, "Designs/A.md", {"type": "living/design", "key": "a", "workspace": "workspace/alpha", "parent": "design/b"})
    return ("workspace_ownership_invalid",
            _write(root, "Designs/B.md", {"type": "living/design", "key": "b", "workspace": "workspace/alpha", "parent": "design/a"}),
            {"cause": "lineage", "parent": "design/a"})


def case_ownership_reference(root, local, app):
    """Two living files with one identity: the router refuses to compile them, so the scan runs on the last router."""
    from _lifecycle.derived_cache_state import require_fresh_compiled_router

    _write(root, "Designs/Twin One.md", {"type": "living/design", "key": "twin", "workspace": "workspace/alpha"})
    _recompile(root)
    router = require_fresh_compiled_router(root)
    return ("workspace_ownership_invalid",
            _write(root, "Designs/Twin Two.md", {"type": "living/design", "key": "twin", "workspace": "workspace/alpha"}),
            {"cause": "reference", "parent": None}, router)


def case_ownership_membership(root, local, app):
    file = _edit_frontmatter(root, "workspace/beta", app, workspace="workspace/alpha")
    return "workspace_ownership_invalid", file, {"cause": "membership", "parent": None, "workspace": "workspace/alpha"}


def case_policy_invalid(root, local, app):
    file = _edit_frontmatter(root, "workspace/alpha", app, default_parent="project/vanished")
    return "workspace_policy_invalid", file, {"default_parent": "project/vanished", "default_tags": ["shared", "both"]}


def _manifest(local, **changes):
    manifest = read_workspace_manifest(local)
    manifest.update(changes)
    save_workspace_manifest_data(local, manifest)
    return str(local / ".brain/local/workspace.yaml")


def case_binding_terminal_inactive(root, local, app):
    assert app.invoke(ArtefactSetStatusRequest("workspace/alpha", "completed")).status == "ok"
    return "workspace_binding_terminal_inactive", str(local / ".brain/local/workspace.yaml"), {"workspace": "workspace/alpha"}


def case_binding_configured_invalid_link(root, local, app):
    return "workspace_binding_configured_invalid", _manifest(local, links="not-a-mapping"), {"workspace": None, "cause": "link"}


def case_binding_configured_invalid_brain_slug(root, local, app):
    return ("workspace_binding_configured_invalid", _manifest(local, slug=""),
            {"workspace": "workspace/alpha", "cause": "brain_slug"})


def case_binding_configured_invalid_alias(root, local, app):
    return ("workspace_binding_configured_invalid", _manifest(local, brain="unregistered-elsewhere"),
            {"workspace": "workspace/alpha", "cause": "alias"})


def case_binding_configured_invalid_hub(root, local, app):
    return ("workspace_binding_configured_invalid", _manifest(local, links={"workspace": "nope"}),
            {"workspace": "workspace/nope", "cause": "hub"})


def case_binding_configured_invalid_hub_policy(root, local, app):
    _edit_frontmatter(root, "workspace/alpha", app, default_parent="project/vanished")
    return ("workspace_binding_configured_invalid", str(local / ".brain/local/workspace.yaml"),
            {"workspace": "workspace/alpha", "cause": "hub_policy"})


def case_binding_configured_invalid_local_defaults(root, local, app):
    return ("workspace_binding_configured_invalid", _manifest(local, defaults={"parent": "workspace/beta"}),
            {"workspace": "workspace/alpha", "cause": "local_defaults"})


def case_binding_configured_invalid_manifest(root, local, app):
    (local / ".brain/local/workspace.yaml").write_text("brain: [unclosed\n")
    return "workspace_binding_configured_invalid", str(local), {"workspace": None, "cause": "manifest", "error": "invalid_binding"}


def _plant_out_of_bounds_source(root, tmp_path):
    """A candidate source the scan refuses: a symlink that resolves outside the vault."""
    outside = tmp_path / "outside" / "Elsewhere.md"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text(serialize_frontmatter({"type": "living/wiki", "key": "elsewhere"}, body="# Elsewhere\n"))
    (root / "Wiki").mkdir(exist_ok=True)
    (root / "Wiki" / "Elsewhere.md").symlink_to(outside)


def case_scan_unreadable(root, local, app):
    _plant_out_of_bounds_source(root, local.parent)
    return "workspace_scan_unreadable", None, None


def case_living_key_fields_missing(root, local, app):
    return "living_key_fields", _write(root, "Wiki/Keyless.md", {"type": "living/wiki", "tags": ["wiki"]}), {"key": None}


def case_living_key_fields_invalid(root, local, app):
    return ("living_key_fields", _write(root, "Wiki/Keyless.md", {"type": "living/wiki", "key": "Not A Key!"}),
            {"key": "Not A Key!"})


def case_root_files(root, local, app):
    (root / "stray.md").write_text("# Stray\n")
    return "root_files", "stray.md", None


def case_os_error(root, local, app):
    if os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0):
        pytest.skip("permission mode evidence requires non-root POSIX")
    file = _write(root, "Wiki/Blocked.md", {"type": "living/wiki"})
    (root / file).chmod(0)
    return "os_error", file, {"errno": "EACCES"}


def case_not_utf8(root, local, app):
    file = _write(root, "Wiki/Legacy.md", {"type": "living/wiki"})
    (root / file).write_bytes(b"legacy text\xff")
    return "not_utf8", file, None


def case_not_text(root, local, app):
    file = _write(root, "Wiki/Binary.md", {"type": "living/wiki"})
    (root / file).write_bytes(b"text\x00")
    return "not_text", file, None


def case_skill_ownership_unavailable(root, local, app):
    router = check.load_router(root)
    file = ".brain/skill-sources.json"
    (root / file).write_bytes(b"{broken")
    return "skill_ownership_unavailable", file, {"reason": "TrackingError"}, router


CASES = [value for name, value in sorted(globals().items()) if name.startswith("case_")]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.__name__.removeprefix("case_"))
def test_each_code_declares_exactly_the_evidence_its_row_promises(scoped, case):
    root, local, app, _parents = scoped
    code, file, evidence, *router = case(root, local, app)
    if code in {"living_key_fields", "root_files"}:
        check_name, finding_code = code, None
    elif code in {"os_error", "not_utf8", "not_text"}:
        check_name, finding_code = "unreadable_file", code
    elif code == "skill_ownership_unavailable":
        check_name, finding_code = "text_scan", code
    else:
        check_name, finding_code = "workspace_contract", code

    raw = _finding(_findings(root, workspace_dir=local, router=router[0] if router else None),
                   finding_code, file, check_name)

    assert raw.get("evidence") == evidence and raw["severity"] == "error"
    finding = _classified(raw)
    assert finding.file == file
    for value in (evidence or {}).values():
        assert not isinstance(value, str) or len(value) < 80, "evidence is a value or a rule, never prose"


def test_the_cases_cover_every_classified_code_cause_and_binding_state():
    """The parametrised cases are the gate: a new row, cause or state needs one."""
    covered = {case.__name__ for case in CASES}
    for (check_name, code), identity in JUDGEMENT_FINDINGS.items():
        if check_name == "workspace_registry":
            continue  # pinned with their reasons in test_workspace_checks
        stem = (code or check_name).removeprefix("workspace_").removesuffix("_invalid") if code == "workspace_ownership_invalid" \
            else (code or check_name).removeprefix("workspace_")
        assert any(name.startswith(f"case_{stem}") for name in covered), f"no evidence case for {check_name}:{code}"
    for cause in workspace_checks.OWNERSHIP_CAUSES:
        if cause != "parent_missing":  # unreachable through the graph: a resolved parent is always indexed
            assert f"case_ownership_{cause}" in covered, cause
    for cause in InvalidBindingCause:
        assert f"case_binding_configured_invalid_{cause.value}" in covered, cause
    for state in BindingState:
        if state not in {BindingState.VALID, BindingState.UNCONFIGURED}:
            assert any(name.startswith(f"case_binding_{state.value}") for name in covered), state


# ---------------------------------------------------------------------------
# Through the command path: same condition quiet, changed condition open
# ---------------------------------------------------------------------------

def _item(root, key):
    listed = _invoke(root, MaintenanceListRequest(all=True))
    assert listed.status == "ok", listed
    return next(item for item in listed.result.items if item.key == key)


def test_an_ownership_dismissal_reopens_when_the_cause_changes_on_the_same_file(scoped):
    root, _local, _app, _parents = scoped
    _write(root, PLAN, {"type": "temporal/plan", "workspace": "workspace/alpha", "parent": "project/vanished"})
    _recompile(root)
    key = finding_key("brain", "workspace_contract:workspace_ownership_invalid", {"file": PLAN})

    first = _item(root, key)
    assert first.fingerprint == finding_fingerprint(key, {"cause": "parent", "parent": "project/vanished"})
    assert _invoke(root, MaintenanceDismissRequest(key, first.fingerprint, "parent lands next week", "rob")).status == "ok"
    assert _item(root, key).state is ItemState.QUIET

    _write(root, PLAN, {"type": "temporal/plan", "workspace": "workspace/beta", "parent": "project/local"})
    _recompile(root)
    changed = _item(root, key)
    assert changed.fingerprint == finding_fingerprint(
        key, {"cause": "workspace_mismatch", "parent": "project/local", "workspace": "workspace/beta"})
    assert changed.state is ItemState.OPEN


def test_living_key_fields_reopens_when_a_missing_key_becomes_a_list_and_a_legacy_record_reopens_once(scoped):
    root, _local, _app, _parents = scoped
    page = root / _write(root, "Wiki/Keyless.md", {"type": "living/wiki"})
    _recompile(root)
    key = finding_key("brain", "living_key_fields", {"file": "Wiki/Keyless.md"})
    (root / DECISIONS).parent.mkdir(parents=True, exist_ok=True)
    (root / DECISIONS).write_text(json.dumps({"schema": "brain.maintenance-decisions/1", "claims": {}, "dismissals": {
        key: {"fingerprint": key, "dismissed_by": "rob", "reason": "pre-DD-086", "dismissed_at": (NOW - timedelta(days=1)).isoformat()}}}))

    missing = _item(root, key)
    assert missing.fingerprint == finding_fingerprint(key, {"key": None}) and missing.state is ItemState.OPEN, (
        "a dismissal recorded at the key fingerprint reopens once")
    assert _invoke(root, MaintenanceDismissRequest(key, missing.fingerprint, "hand-authored", "rob")).status == "ok"
    assert _item(root, key).state is ItemState.QUIET

    page.write_text("---\ntype: living/wiki\nkey:\n---\n\n# Keyless\n")
    listed = _item(root, key)
    assert listed.fingerprint == finding_fingerprint(key, {"key": []}) and listed.state is ItemState.OPEN, (
        "an empty key is a non-string key, not a missing one")


def test_scan_unreadable_is_listed_at_its_key_claimable_and_never_dismissible(scoped, tmp_path):
    root, _local, _app, _parents = scoped
    _plant_out_of_bounds_source(root, tmp_path)
    _recompile(root)
    key = finding_key("brain", "workspace_contract:workspace_scan_unreadable", {"file": None})

    item = _item(root, key)
    assert (item.fingerprint, item.file, item.state) == (key, None, ItemState.OPEN)

    refused = _invoke(root, MaintenanceDismissRequest(key, key, "known broken file", "rob"))
    assert refused.status == "error" and refused.error.code is ErrorCode.INVALID_REQUEST
    assert refused.error.details.field == "key" and refused.effects == "none"
    assert not (root / DECISIONS).exists(), "a refusal writes nothing"
    claimed = _invoke(root, MaintenanceClaimRequest(key, "rob"))
    assert claimed.status == "ok" and claimed.result.item.state is ItemState.HELD


def test_a_producer_that_breaks_its_promise_degrades_one_finding_and_blocks_nothing(scoped, monkeypatch):
    """Fail safe (DD-086): the finding is kind-only and warned about; the pass and other findings proceed."""
    root, _local, _app, _parents = scoped
    (root / "stray.md").write_text("# Stray\n")

    def check_rogue(vault_root, router, *, ctx=None):
        return [{"check": "rogue", "severity": "error", "file": "Wiki/Any.md", "message": "unclassified"}]

    monkeypatch.setattr(check, "ALL_CHECKS", [*check.ALL_CHECKS, check_rogue])
    _recompile(root)
    rogue_key = finding_key("brain", "rogue", {"file": "Wiki/Any.md"})
    stray_key = finding_key("brain", "root_files", {"file": "stray.md"})
    (root / DECISIONS).parent.mkdir(parents=True, exist_ok=True)
    (root / DECISIONS).write_text(json.dumps({"schema": "brain.maintenance-decisions/1", "claims": {}, "dismissals": {
        rogue_key: {"fingerprint": rogue_key, "dismissed_by": "rob", "reason": "x", "dismissed_at": (NOW - timedelta(days=1)).isoformat()}}}))

    listed = _invoke(root, MaintenanceListRequest())
    assert listed.status == "ok"
    assert [(warning.code, "rogue" in warning.message and "does not classify" in warning.message)
            for warning in listed.warnings] == [(WarningCode.DEGRADED_CAPABILITY, True)]
    states = {item.key: item.state for item in listed.result.items}
    assert states[rogue_key] is ItemState.OPEN, "a stored dismissal never quiets a demoted finding"
    assert states[stray_key] is ItemState.OPEN and listed.result.hidden_quiet == 0

    refused = _invoke(root, MaintenanceDismissRequest(rogue_key, rogue_key, "x", "rob"))
    assert refused.status == "error" and refused.error.code is ErrorCode.INVALID_REQUEST
    assert "does not classify" in refused.error.message and "until Brain Core fixes" in refused.error.message
    assert _invoke(root, MaintenanceClaimRequest(rogue_key, "rob")).status == "ok"
    assert _invoke(root, MaintenanceDismissRequest(stray_key, stray_key, "fine", "rob")).status == "ok", "unrelated findings are untouched"

    clock = _Clock(NOW + timedelta(days=1))
    passed = _invoke(root, MaintenanceRunRequest(), clock=clock, invoker=_Sibling(root, clock))
    assert passed.status == "ok" and [warning.code for warning in passed.warnings] == [WarningCode.DEGRADED_CAPABILITY]
    assert rogue_key in {item.key for item in passed.result.attention}


def test_a_stored_dismissal_of_a_kind_only_finding_is_pruned_at_retention(scoped, tmp_path):
    root, _local, _app, _parents = scoped
    _plant_out_of_bounds_source(root, tmp_path)
    _recompile(root)
    key = finding_key("brain", "workspace_contract:workspace_scan_unreadable", {"file": None})
    (root / DECISIONS).parent.mkdir(parents=True, exist_ok=True)
    (root / DECISIONS).write_text(json.dumps({"schema": "brain.maintenance-decisions/1", "claims": {}, "dismissals": {
        key: {"fingerprint": key, "dismissed_by": "rob", "reason": "x", "dismissed_at": (NOW - timedelta(days=1)).isoformat()}}}))

    assert _item(root, key).state is ItemState.OPEN
    pruned = _invoke(root, MaintenanceClaimRequest(key, "rob"), clock=_Clock(NOW + DISMISSAL_RETENTION))
    assert pruned.status == "ok" and key not in _read(root)["dismissals"]



def test_unreadable_subject_dismissal_survives_edits_but_a_new_code_reopens(scoped):
    root, _local, _app, _parents = scoped
    file = _write(root, "Wiki/Deliberate.md", {"type": "living/wiki"})
    path = root / file
    path.write_bytes(b"legacy\xff")
    _recompile(root)
    key = finding_key("brain", "unreadable_file:not_utf8", {"file": file})
    first = _item(root, key)
    assert first.fingerprint == key
    assert _invoke(root, MaintenanceDismissRequest(key, first.fingerprint, "deliberately retained", "rob")).status == "ok"
    path.write_bytes(b"edited legacy\xfe")
    _recompile(root)
    assert _item(root, key).state is ItemState.QUIET
    path.write_bytes(b"binary\x00")
    _recompile(root)
    changed_key = finding_key("brain", "unreadable_file:not_text", {"file": file})
    assert _item(root, changed_key).state is ItemState.OPEN
