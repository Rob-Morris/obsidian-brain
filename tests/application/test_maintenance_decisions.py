"""Claims and dismissals: the only persistent maintenance state (DD-082, phase 3)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import uuid

import pytest

from command_application import context_for
from _application.application import CommandApplication
from _application.maintenance import _detection
from _application.maintenance.claim import MaintenanceClaimRequest
from _application.maintenance.dismiss import MaintenanceDismissRequest
from _application.maintenance.list import MaintenanceListRequest
from _application.maintenance.release import MaintenanceReleaseRequest
from _application.maintenance.run import MaintenanceRunRequest
from _bootstrap.maintenance_decisions import ItemState
from _bootstrap.maintenance_summary import GroupOutcome
from _application.registry import current_request_resolver
from _application.results import ErrorCode
from _bootstrap import maintenance_decisions as decisions_module
from _bootstrap.maintenance_decisions import (
    CLAIM_LEASE,
    DISMISSAL_RETENTION,
    Claim,
    Decisions,
    Dismissal,
    apply_decisions,
    prune,
    read_decisions,
    write_decisions,
)
from _bootstrap.maintenance_findings import Disposition, MaintenanceFinding, Owner, family_key, finding_key, group_by_family
from _repair_common import REPAIR_SCOPES


DECISIONS = ".brain/local/maintenance/decisions.json"
ROUTER = ".brain/local/compiled-router.json"
INDEX = ".brain/local/retrieval-index.json"
NOW = datetime(2026, 9, 30, 10, 0, tzinfo=timezone.utc)
MISSING_KEY = finding_key("brain", "workspace_contract:workspace_reference_missing", {"file": "Notes/a.md"})


class _Clock:
    def __init__(self, now=NOW):
        self.now_value = now

    def now(self):
        return self.now_value


def _judgement(file="Notes/a.md", workspace="workspace/x"):
    return MaintenanceFinding("workspace_contract", "error", file, "gone", Disposition.JUDGEMENT,
                              code="workspace_reference_missing", owner=Owner.BRAIN,
                              key=finding_key("brain", "workspace_contract:workspace_reference_missing", {"file": file}),
                              evidence={"workspace": workspace})


def _ownership(file):
    return MaintenanceFinding("parent_contract", "warning", file, "drift", Disposition.JUDGEMENT, scope="ownership",
                              owner=Owner.BRAIN, key=family_key("brain", "ownership"))


@pytest.fixture
def fake_detection(monkeypatch):
    """Replace live detection with a controllable finding set."""
    findings = {"value": ()}

    def detect(_context):
        return tuple(findings["value"])

    monkeypatch.setattr("_application.maintenance._items.detect", detect)
    return findings


class _Sibling:
    def __init__(self, root, clock):
        self.root, self.clock, self.calls = root, clock, []

    def repair(self, family, *, invocation_id):
        from command_application import application_for

        self.calls.append(family.command_id)
        request = current_request_resolver().resolve(family.command_id, dict(family.request))
        return application_for(self.root, invocation_id=invocation_id, context_kind="standalone", clock=self.clock).invoke(request)


def _invoke(root, request, *, clock=None, invoker=None, dry_run=False, diagnostics=None):
    context = context_for(root, context_kind="standalone", invocation_id=f"inv-{uuid.uuid4().hex[:8]}",
                          dry_run=dry_run, clock=clock or _Clock())
    context = replace(context, maintenance=invoker)
    if diagnostics is not None:
        context = replace(context, diagnostics=diagnostics)
    context = replace(context, access=context.authorisation.bind(context))
    return CommandApplication(context, context.authorisation.catalogue).invoke(request)


def _read(root):
    return json.loads((root / DECISIONS).read_text())


# ---------------------------------------------------------------------------
# Pure module
# ---------------------------------------------------------------------------

class TestDecisionsModule:
    def test_round_trip_and_missing_file_is_empty(self, tmp_path):
        path = tmp_path / "decisions.json"
        assert read_decisions(path) == decisions_module.EMPTY
        value = Decisions({"k1": Claim("rob", NOW + CLAIM_LEASE)},
                          {"k2": Dismissal("fp", "rob", "intentional", NOW)})
        write_decisions(path, value)
        assert read_decisions(path) == value

    @pytest.mark.parametrize("text", ["{nope", '{"schema": "other/1"}', '{"schema": "brain.maintenance-decisions/1", "claims": []}',
                                      '{"schema": "brain.maintenance-decisions/1", "claims": {"k": {"claimant": "x", "expires_at": "soon"}}}'])
    def test_unreadable_files_name_the_path(self, tmp_path, text):
        path = tmp_path / "decisions.json"
        path.write_text(text)
        with pytest.raises(decisions_module.DecisionsUnreadable) as exc:
            read_decisions(path)
        assert str(path) in str(exc.value)

    def test_prune_drops_only_lapsed_records(self):
        value = Decisions(
            {"fresh": Claim("a", NOW + CLAIM_LEASE), "lapsed": Claim("b", NOW - timedelta(days=31))},
            {"kept": Dismissal("f", "a", "r", NOW - timedelta(days=29)), "old": Dismissal("f", "a", "r", NOW - timedelta(days=31))},
        )
        pruned = prune(value, NOW)
        assert set(pruned.claims) == {"fresh"} and set(pruned.dismissals) == {"kept"}

    def test_apply_decisions_marks_held_expired_quiet_and_never_holds_router(self):
        router = MaintenanceFinding("router", "error", None, "m", Disposition.AUTOMATIC, scope="router", owner=Owner.BRAIN, key=family_key("brain", "router"))
        lexical = MaintenanceFinding("lexical_index", "warning", None, "m", Disposition.AUTOMATIC, scope="lexical", owner=Owner.BRAIN, key=family_key("brain", "lexical"))
        groups = group_by_family((router, lexical, _judgement(), _judgement("Notes/b.md"), _ownership("A.md")))
        by_key = {group.key: group for group in groups}
        judgement = by_key[MISSING_KEY]
        value = Decisions(
            {router.key: Claim("x", NOW + CLAIM_LEASE), lexical.key: Claim("x", NOW + CLAIM_LEASE),
             finding_key("brain", "workspace_contract:workspace_reference_missing", {"file": "Notes/b.md"}): Claim("y", NOW - timedelta(minutes=1)),
             "undetected": Claim("z", NOW + CLAIM_LEASE)},
            {judgement.key: Dismissal(judgement.fingerprint, "x", "fine", NOW - timedelta(days=1)),
             family_key("brain", "ownership"): Dismissal("stale-fingerprint", "x", "fine", NOW)},
        )

        states = apply_decisions(groups, value, NOW, never_held=frozenset({"router"}))

        assert states[router.key].state == "open", "router is never held"
        assert states[lexical.key].state == "held" and states[lexical.key].claimant == "x"
        assert states[judgement.key].state == "quiet"
        assert states[finding_key("brain", "workspace_contract:workspace_reference_missing", {"file": "Notes/b.md"})].state == "claim_expired"
        assert states[family_key("brain", "ownership")].state == "open", "changed evidence reopens a dismissal"
        assert "undetected" not in states

        lapsed = Decisions({lexical.key: Claim("x", NOW - decisions_module.EXPIRED_CLAIM_RETENTION)}, {})
        assert apply_decisions(groups, lapsed, NOW)[lexical.key].state == "open", "a claim past retention is absent"

    def test_dismissal_past_retention_resurfaces_the_finding(self):
        groups = group_by_family((_judgement(),))
        group = groups[0]
        fresh = Decisions({}, {group.key: Dismissal(group.fingerprint, "x", "r", NOW - DISMISSAL_RETENTION + timedelta(days=1))})
        lapsed = Decisions({}, {group.key: Dismissal(group.fingerprint, "x", "r", NOW - DISMISSAL_RETENTION - timedelta(days=1))})
        assert apply_decisions(groups, fresh, NOW)[group.key].state == "quiet"
        assert apply_decisions(groups, lapsed, NOW)[group.key].state == "open"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def test_claim_writes_extends_refuses_and_replaces_after_expiry(command_vault_clone, fake_detection):
    root = command_vault_clone.vault_root
    fake_detection["value"] = (_judgement(),)
    clock = _Clock()

    first = _invoke(root, MaintenanceClaimRequest(MISSING_KEY, "rob"), clock=clock)
    assert first.status == "ok", first
    assert first.result.kind == "claim" and first.result.item.state is ItemState.HELD
    assert first.result.item.claimant == "rob" and first.result.item.expires_at == NOW + CLAIM_LEASE
    assert first.committed_effects[0].subject.endswith("decisions.json")
    assert _read(root)["claims"][MISSING_KEY]["claimant"] == "rob"

    clock.now_value = NOW + timedelta(minutes=30)
    extended = _invoke(root, MaintenanceClaimRequest(MISSING_KEY, "rob"), clock=clock)
    assert extended.result.item.expires_at == NOW + timedelta(minutes=30) + CLAIM_LEASE

    refused = _invoke(root, MaintenanceClaimRequest(MISSING_KEY, "sam"), clock=clock)
    assert refused.status == "error" and refused.error.code is ErrorCode.CONFLICT
    assert "'rob' holds" in refused.error.message

    clock.now_value = NOW + timedelta(hours=3)
    replaced = _invoke(root, MaintenanceClaimRequest(MISSING_KEY, "sam"), clock=clock)
    assert replaced.status == "ok" and replaced.result.item.claimant == "sam"


def test_claim_refuses_router_and_unknown_keys_but_holds_other_automatic_groups(command_vault_clone, fake_detection):
    root = command_vault_clone.vault_root
    router = MaintenanceFinding("router", "error", None, "m", Disposition.AUTOMATIC, scope="router", owner=Owner.BRAIN, key=family_key("brain", "router"))
    lexical = MaintenanceFinding("lexical_index", "warning", None, "m", Disposition.AUTOMATIC, scope="lexical", owner=Owner.BRAIN, key=family_key("brain", "lexical"))
    fake_detection["value"] = (router, lexical)

    refused = _invoke(root, MaintenanceClaimRequest(router.key, "rob"))
    assert refused.status == "error" and refused.error.code is ErrorCode.INVALID_REQUEST
    assert "never held" in refused.error.message

    unknown = _invoke(root, MaintenanceClaimRequest("0123456789abcdef", "rob"))
    assert unknown.status == "error" and unknown.error.code is ErrorCode.NOT_FOUND

    held = _invoke(root, MaintenanceClaimRequest(lexical.key, "rob"))
    assert held.status == "ok" and held.result.item.state is ItemState.HELD


def test_a_claimed_router_still_runs(command_vault_clone):
    root = command_vault_clone.vault_root
    clock = _Clock(datetime.now(timezone.utc))
    (root / ROUTER).unlink()
    (root / ".brain/local/maintenance").mkdir(parents=True)
    write_decisions(root / DECISIONS, Decisions({family_key("brain", "router"): Claim("rob", clock.now() + CLAIM_LEASE)}, {}))
    invoker = _Sibling(root, clock)

    result = _invoke(root, MaintenanceRunRequest(), clock=clock, invoker=invoker)

    assert result.status == "ok" and invoker.calls == ["runtime.refresh-router"]
    assert {item.scope: item.outcome for item in result.result.groups} == {"router": GroupOutcome.REPAIRED}


def test_a_claimed_lexical_group_is_held_and_the_pass_never_writes_decisions(command_vault_clone, monkeypatch):
    from _bootstrap import stranded_temporaries
    import os

    root = command_vault_clone.vault_root
    (root / INDEX).unlink()
    stranded = root / ".brain/local/retrieval-index.json.ab12cd34.tmp"
    stranded.write_text("x")
    stale = (datetime.now(timezone.utc) - timedelta(days=2)).timestamp()
    os.utime(stranded, (stale, stale))
    monkeypatch.setattr(stranded_temporaries, "MINIMUM_AGE", timedelta(0))
    clock = _Clock(datetime.now(timezone.utc))
    (root / ".brain/local/maintenance").mkdir(parents=True)
    write_decisions(root / DECISIONS, Decisions({family_key("brain", "lexical"): Claim("rob", clock.now() + CLAIM_LEASE)}, {}))
    before = (root / DECISIONS).read_text()
    invoker = _Sibling(root, clock)

    result = _invoke(root, MaintenanceRunRequest(), clock=clock, invoker=invoker)

    assert result.status == "ok"
    assert invoker.calls == ["runtime.remove-temporaries"], "the held lexical group is skipped"
    assert {item.scope: item.outcome for item in result.result.groups} == {"temporaries": GroupOutcome.REPAIRED}
    held = [item for item in result.result.attention if item.scope == "lexical"]
    assert held and held[0].state is ItemState.HELD and held[0].claimant == "rob"
    assert result.result.counts.needs_person == 0
    assert (root / DECISIONS).read_text() == before, "the pass never writes the decisions file"


def test_expired_claim_is_a_review_item_but_only_a_live_claim_withholds_the_pass(command_vault_clone):
    root = command_vault_clone.vault_root
    clock = _Clock(datetime.now(timezone.utc))
    (root / INDEX).unlink()
    (root / ".brain/local/maintenance").mkdir(parents=True)
    write_decisions(root / DECISIONS, Decisions({family_key("brain", "lexical"): Claim("rob", clock.now() - timedelta(minutes=5))}, {}))
    invoker = _Sibling(root, clock)

    listed = _invoke(root, MaintenanceListRequest(), clock=clock)
    item = next(item for item in listed.result.items if item.scope == "lexical")
    assert item.state is ItemState.CLAIM_EXPIRED and item.claimant == "rob"

    result = _invoke(root, MaintenanceRunRequest(), clock=clock, invoker=invoker)

    assert result.status == "ok" and invoker.calls == ["retrieval.refresh-lexical"], "an expired claim no longer holds (D5)"
    assert result.result.counts.claim_expired == 1 and result.result.counts.needs_person == 0
    assert (root / INDEX).exists(), "the repair ran; the next detection finds nothing under the expired claim"


def test_dismiss_quiets_a_judgement_finding_until_its_evidence_changes(command_vault_clone, fake_detection):
    root = command_vault_clone.vault_root
    fake_detection["value"] = (_judgement(),)
    listed = _invoke(root, MaintenanceListRequest())
    item = listed.result.items[0]

    stale = _invoke(root, MaintenanceDismissRequest(item.key, "0000000000000000", "fine", "rob"))
    assert stale.status == "error" and stale.error.code is ErrorCode.CONFLICT and item.fingerprint in stale.error.message

    dismissed = _invoke(root, MaintenanceDismissRequest(item.key, item.fingerprint, "intentional link", "rob"))
    assert dismissed.status == "ok" and dismissed.result.item.state is ItemState.QUIET
    assert _read(root)["dismissals"][item.key]["dismissed_by"] == "rob"

    quiet = _invoke(root, MaintenanceListRequest())
    assert quiet.result.items == () and quiet.result.hidden_quiet == 1
    assert len(_invoke(root, MaintenanceListRequest(all=True)).result.items) == 1

    fake_detection["value"] = (_judgement(workspace="workspace/y"),)
    reopened = _invoke(root, MaintenanceListRequest())
    assert reopened.result.items[0].state is ItemState.OPEN, "changed evidence reopens the finding"


def test_dismiss_refuses_automatic_groups_and_respects_live_claims(command_vault_clone, fake_detection):
    root = command_vault_clone.vault_root
    temporaries = MaintenanceFinding("temporaries", "info", ".brain/local/x.ab12cd34.tmp", "m", Disposition.AUTOMATIC,
                                     scope="temporaries", owner=Owner.BRAIN, key=family_key("brain", "temporaries"))
    fake_detection["value"] = (temporaries, _judgement())

    refused = _invoke(root, MaintenanceDismissRequest(temporaries.key, temporaries.key, "noise", "rob"))
    assert refused.status == "error" and refused.error.code is ErrorCode.INVALID_REQUEST
    assert "never dismissible" in refused.error.message

    assert _invoke(root, MaintenanceClaimRequest(MISSING_KEY, "rob")).status == "ok"
    item = next(item for item in _invoke(root, MaintenanceListRequest()).result.items if item.key == MISSING_KEY)
    other = _invoke(root, MaintenanceDismissRequest(MISSING_KEY, item.fingerprint, "fine", "sam"))
    assert other.status == "error" and other.error.code is ErrorCode.CONFLICT

    owner = _invoke(root, MaintenanceDismissRequest(MISSING_KEY, item.fingerprint, "fine", "rob"))
    assert owner.status == "ok"
    assert MISSING_KEY not in _read(root)["claims"], "a dismissal retires the claimant's own claim"


def test_dismissing_one_member_covers_the_scope_wide_group(command_vault_clone, fake_detection):
    root = command_vault_clone.vault_root
    fake_detection["value"] = (_ownership("A.md"), _ownership("B.md"))
    group = _invoke(root, MaintenanceListRequest()).result.items[0]
    assert group.members == 2 and group.kind == "family"

    dismissed = _invoke(root, MaintenanceDismissRequest(group.key, group.fingerprint, "known moves", "rob"))
    assert dismissed.status == "ok"
    assert _invoke(root, MaintenanceListRequest()).result.hidden_quiet == 1

    fake_detection["value"] = (_ownership("A.md"), _ownership("B.md"), _ownership("C.md"))
    assert _invoke(root, MaintenanceListRequest()).result.items[0].state is ItemState.OPEN, "a new member reopens it"


def test_release_requires_the_claimant_while_live_and_anyone_after_expiry(command_vault_clone, fake_detection):
    root = command_vault_clone.vault_root
    fake_detection["value"] = (_judgement(),)
    clock = _Clock()

    unclaimed = _invoke(root, MaintenanceReleaseRequest(MISSING_KEY, "rob"), clock=clock)
    assert unclaimed.status == "error" and unclaimed.error.code is ErrorCode.NOT_FOUND

    assert _invoke(root, MaintenanceClaimRequest(MISSING_KEY, "rob"), clock=clock).status == "ok"
    other = _invoke(root, MaintenanceReleaseRequest(MISSING_KEY, "sam"), clock=clock)
    assert other.status == "error" and other.error.code is ErrorCode.CONFLICT

    clock.now_value = NOW + timedelta(hours=2)
    released = _invoke(root, MaintenanceReleaseRequest(MISSING_KEY, "sam"), clock=clock)
    assert released.status == "ok" and released.result.item.state is ItemState.OPEN
    assert _read(root)["claims"] == {}


def test_decision_writes_prune_lapsed_records_and_readers_never_do(command_vault_clone, fake_detection):
    root = command_vault_clone.vault_root
    fake_detection["value"] = (_judgement(),)
    (root / ".brain/local/maintenance").mkdir(parents=True)
    lapsed = Decisions({"gone": Claim("x", NOW - timedelta(days=40))}, {"old": Dismissal("f", "x", "r", NOW - timedelta(days=40))})
    write_decisions(root / DECISIONS, lapsed)
    before = (root / DECISIONS).read_text()

    assert _invoke(root, MaintenanceListRequest()).status == "ok"
    assert (root / DECISIONS).read_text() == before, "list never writes"

    assert _invoke(root, MaintenanceClaimRequest(MISSING_KEY, "rob")).status == "ok"
    written = _read(root)
    assert set(written["claims"]) == {MISSING_KEY} and written["dismissals"] == {}


def test_dry_run_decision_admits_but_writes_nothing(command_vault_clone, fake_detection):
    root = command_vault_clone.vault_root
    fake_detection["value"] = (_judgement(),)

    result = _invoke(root, MaintenanceClaimRequest(MISSING_KEY, "rob"), dry_run=True)

    assert result.status == "ok" and result.committed_effects == ()
    assert result.result.item.state is ItemState.OPEN
    assert not (root / DECISIONS).exists()


def test_corrupt_decisions_file_is_refused_with_its_path(command_vault_clone, fake_detection):
    root = command_vault_clone.vault_root
    fake_detection["value"] = (_judgement(),)
    (root / ".brain/local/maintenance").mkdir(parents=True)
    (root / DECISIONS).write_text("{nope")

    for request in (MaintenanceListRequest(), MaintenanceClaimRequest(MISSING_KEY, "rob")):
        result = _invoke(root, request)
        assert result.status == "error" and result.error.code is ErrorCode.INTERNAL_ERROR
        assert "decisions.json" in result.error.message


def test_decisions_record_through_the_diagnostics_port(command_vault_clone, fake_detection):
    class _Recorder:
        def __init__(self):
            self.records = []

        def report_failure(self, **failure):
            pass

        def record(self, event, *, family, **fields):
            self.records.append((event, family, fields))

    root = command_vault_clone.vault_root
    fake_detection["value"] = (_judgement(),)
    diagnostics = _Recorder()

    assert _invoke(root, MaintenanceClaimRequest(MISSING_KEY, "rob"), diagnostics=diagnostics).status == "ok"
    assert _invoke(root, MaintenanceReleaseRequest(MISSING_KEY, "rob"), diagnostics=diagnostics).status == "ok"

    assert diagnostics.records == [
        ("maintenance.decision_recorded", "maintenance", {"kind": "claim", "key": MISSING_KEY}),
        ("maintenance.decision_recorded", "maintenance", {"kind": "release", "key": MISSING_KEY}),
    ]


def test_workspace_reference_findings_declare_their_workspace_as_evidence(command_vault_clone):
    from _lifecycle.workspace_checks import workspace_findings
    from _common import load_compiled_router

    root = command_vault_clone.vault_root
    (root / "Wiki").mkdir(exist_ok=True)
    (root / "Wiki" / "Orphaned Member.md").write_text(
        "---\ntype: living/wiki\nkey: orphaned-member\nworkspace: workspace/vanished\ntags: [wiki]\n---\n\n# Orphaned\n"
    )

    findings = [item for item in workspace_findings(str(root), load_compiled_router(str(root)))
                if item.get("code") == "workspace_reference_missing"]

    assert findings and findings[0]["evidence"] == {"workspace": "workspace/vanished"}
    classified = _detection.classify(findings)
    assert classified[0].disposition == Disposition.JUDGEMENT and classified[0].evidence == {"workspace": "workspace/vanished"}
    assert classified[0].key == finding_key("brain", "workspace_contract:workspace_reference_missing",
                                            {"file": findings[0]["file"]})


def test_a_missing_router_leaves_the_decisions_file_intact(command_vault_clone):
    root = command_vault_clone.vault_root
    clock = _Clock(datetime.now(timezone.utc))
    (root / ".brain/local/maintenance").mkdir(parents=True)
    write_decisions(root / DECISIONS, Decisions({family_key("brain", "lexical"): Claim("rob", clock.now() + CLAIM_LEASE)}, {}))
    before = (root / DECISIONS).read_bytes()
    (root / ".brain/local/compiled-router.json").unlink()

    result = _invoke(root, MaintenanceRunRequest(), clock=clock, invoker=_Sibling(root, clock))
    listed = _invoke(root, MaintenanceListRequest(), clock=clock)

    assert result.status == "ok" and listed.status == "ok"
    assert (root / DECISIONS).read_bytes() == before
