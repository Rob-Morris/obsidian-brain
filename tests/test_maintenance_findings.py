"""Pure maintenance helpers: identity, grouping, temporaries, summary (DD-082)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path

import pytest

from _bootstrap import stranded_temporaries
from _bootstrap.maintenance_findings import (
    Disposition,
    Owner,
    MaintenanceFinding,
    family_key,
    finding_fingerprint,
    finding_key,
    group_by_family,
)
from _bootstrap.maintenance_summary import build_summary, empty_counts, read_advisory, read_last_pass, write_last_pass
from _bootstrap.stranded_temporaries import find_stranded_temporaries
from _common._filesystem import atomic_temporary_prefix, is_atomic_write_temporary, safe_write_json


NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


class TestIdentity:
    def test_key_is_stable_and_discriminator_driven(self):
        key = finding_key("brain", "workspace_contract:workspace_reference_missing", {"file": "a.md"})
        assert key == finding_key("brain", "workspace_contract:workspace_reference_missing", {"file": "a.md"})
        assert len(key) == 16 and all(ch in "0123456789abcdef" for ch in key)
        assert key != finding_key("brain", "workspace_contract:workspace_reference_missing", {"file": "b.md"})
        assert family_key("brain", "router") == finding_key("brain", "family", {"scope": "router"})

    def test_fingerprint_follows_declared_evidence_only(self):
        key = finding_key("brain", "k", {"file": "a.md"})
        assert finding_fingerprint(key, None) == key
        assert finding_fingerprint(key, {"workspace": "x"}) != finding_fingerprint(key, {"workspace": "y"})
        assert finding_fingerprint(key, {"workspace": "x"}) == finding_fingerprint(key, {"workspace": "x"})

    def test_group_by_family_collapses_families_and_keeps_findings_apart(self):
        router_key = family_key("brain", "router")
        ownership_key = family_key("brain", "ownership")
        findings = (
            MaintenanceFinding("router", "error", None, "missing", Disposition.AUTOMATIC, scope="router", owner=Owner.BRAIN, key=router_key),
            MaintenanceFinding("parent_contract", "warning", "A.md", "drift", Disposition.JUDGEMENT, scope="ownership", owner=Owner.BRAIN, key=ownership_key),
            MaintenanceFinding("parent_contract", "warning", "B.md", "drift", Disposition.JUDGEMENT, scope="ownership", owner=Owner.BRAIN, key=ownership_key),
            MaintenanceFinding("workspace_contract", "error", "C.md", "gone", Disposition.JUDGEMENT, code="workspace_reference_missing",
                               owner=Owner.BRAIN, key=finding_key("brain", "workspace_contract:workspace_reference_missing", {"file": "C.md"}),
                               evidence={"workspace": "workspace/x"}),
            MaintenanceFinding("naming", "warning", "D.md", "prose only", Disposition.REPORT_ONLY),
        )

        groups = group_by_family(findings)

        by_scope = {group.scope: group for group in groups if group.scope}
        assert by_scope["router"].fingerprint == router_key
        assert len(by_scope["ownership"].members) == 2
        assert by_scope["ownership"].fingerprint == finding_fingerprint(ownership_key, {"files": ["A.md", "B.md"]})
        assert by_scope["ownership"].file is None
        single = next(group for group in groups if group.scope is None)
        assert single.file == "C.md" and single.code == "workspace_reference_missing"
        assert single.fingerprint == finding_fingerprint(single.key, {"workspace": "workspace/x"})
        assert len(groups) == 3, "report-only findings never reach the maintenance surface"

    def test_counted_findings_require_a_key(self):
        with pytest.raises(ValueError, match="require a key"):
            MaintenanceFinding("x", "info", None, "m", Disposition.JUDGEMENT)


class TestTemporaries:
    def test_naming_rule_agrees_with_both_writers(self, tmp_path):
        target = tmp_path / "retrieval-index.json"
        seen = []
        safe_write_json(target, {"a": 1})
        assert atomic_temporary_prefix("retrieval-index.json") == "retrieval-index.json."
        from _bootstrap.file_transaction import _write_bytes
        import tempfile

        real = tempfile.mkstemp

        def spy(**kwargs):
            seen.append(kwargs)
            return real(**kwargs)

        tempfile.mkstemp = spy
        try:
            _write_bytes(tmp_path / "ledger.json", b"{}")
            safe_write_json(target, {"a": 2})
        finally:
            tempfile.mkstemp = real
        names = [f"{item['prefix']}abcd1234{item['suffix']}" for item in seen]
        assert names and all(is_atomic_write_temporary(name) for name in names)
        assert not is_atomic_write_temporary("retrieval-index.json")
        assert not is_atomic_write_temporary("retrieval-index.json.tmp")
        assert not is_atomic_write_temporary("retrieval-index.json.ABCD1234.tmp")
        assert not is_atomic_write_temporary(".x.0123456789abcdef0123456789abcdef.tmp")

    def test_lister_takes_only_old_top_level_regular_temporaries(self, tmp_path, monkeypatch):
        local = tmp_path / ".brain" / "local"
        (local / "nested").mkdir(parents=True)
        old = local / "retrieval-index.json.ab12cd34.tmp"
        young = local / "compiled-router.json.ab12cd34.tmp"
        foreign = local / "notes.tmp"
        nested = local / "nested" / "x.json.ab12cd34.tmp"
        for path in (old, young, foreign, nested):
            path.write_text("x")
        (local / "link.json.ab12cd34.tmp").symlink_to(old)
        stale = (NOW - timedelta(days=2)).timestamp()
        os.utime(old, (stale, stale))
        os.utime(nested, (stale, stale))
        os.utime(foreign, (stale, stale))
        # ctime cannot be set; collapse the age rule so the fixture's real ctime qualifies.
        real_now = datetime.now(timezone.utc)

        assert find_stranded_temporaries(tmp_path, real_now) == (), "both mtime and ctime must be older than the threshold"
        assert find_stranded_temporaries(tmp_path / "missing", real_now) == ()
        monkeypatch.setattr(stranded_temporaries, "MINIMUM_AGE", timedelta(0))
        assert find_stranded_temporaries(tmp_path, real_now) == (
            ".brain/local/compiled-router.json.ab12cd34.tmp",
            ".brain/local/retrieval-index.json.ab12cd34.tmp",
        ), "sorted by name; symlinks, nested files and foreign names never match"

    def test_a_young_mtime_alone_disqualifies_a_temporary(self, tmp_path):
        local = tmp_path / ".brain" / "local"
        local.mkdir(parents=True)
        old = local / "retrieval-index.json.ab12cd34.tmp"
        young = local / "compiled-router.json.ab12cd34.tmp"
        old.write_text("x")
        young.write_text("x")
        real_now = datetime.now(timezone.utc)
        stale = (real_now - timedelta(days=2)).timestamp()
        fresh = (real_now + timedelta(hours=24)).timestamp()
        os.utime(old, (stale, stale))
        os.utime(young, (fresh, fresh))

        # A clock 25 hours ahead ages every real ctime past the rule; only the mtime can disqualify.
        assert find_stranded_temporaries(tmp_path, real_now + timedelta(hours=25)) == (
            ".brain/local/retrieval-index.json.ab12cd34.tmp",)


class TestSummary:
    def test_round_trip_and_advisory_age(self, tmp_path):
        path = tmp_path / "last-pass.json"
        summary = build_summary(pass_id="abc123", host="h", finished_at=NOW, outcome="ok",
                                groups={"router": "repaired"}, counts={**empty_counts(), "needs_person": 2})
        write_last_pass(path, summary)

        assert read_last_pass(path) == summary
        advisory = read_advisory(path, NOW + timedelta(hours=2))
        assert advisory == {"needs_person": 2, "claim_expired": 0, "failed": 0, "deferred": 0,
                            "blocked": None, "finished_at": NOW.isoformat(), "age_seconds": 7200}

    def test_advisory_is_none_when_missing_unparsable_or_another_schema(self, tmp_path):
        path = tmp_path / "last-pass.json"
        assert read_advisory(path, NOW) is None
        path.write_text("{not json")
        assert read_advisory(path, NOW) is None
        path.write_text('{"schema": "brain.maintenance-pass/2"}')
        assert read_advisory(path, NOW) is None

    def test_blocked_summary_carries_no_groups_or_counts(self):
        blocked = build_summary(pass_id="p", host="h", finished_at=NOW, outcome="error", groups={},
                                counts=empty_counts(), blocked="detection_failed")
        assert blocked["groups"] == {} and blocked["blocked"] == "detection_failed"
        with pytest.raises(ValueError, match="no groups"):
            build_summary(pass_id="p", host="h", finished_at=NOW, outcome="error", groups={"router": "failed"},
                          counts=empty_counts(), blocked="detection_failed")
        with pytest.raises(ValueError, match="groups"):
            build_summary(pass_id="p", host="h", finished_at=NOW, outcome="ok", groups={"router": "done"},
                          counts=empty_counts())


class TestLogVocabulary:
    def test_operational_log_closed_sets_match_the_shared_pass_vocabulary(self):
        from _bootstrap.maintenance_summary import COUNT_FIELDS, GROUP_OUTCOMES, PASS_OUTCOMES
        from _common import _operational_log as log

        assert log._MAINTENANCE_GROUP_OUTCOMES == frozenset(GROUP_OUTCOMES)
        assert log._MAINTENANCE_PASS_OUTCOMES == frozenset(PASS_OUTCOMES)
        assert log._MAINTENANCE_DECISIONS == {"claim", "release", "dismiss"}
        finished = log._EVENT_FIELDS["maintenance.pass_finished"]
        assert set(finished) == {"pass_id", "outcome", "duration_ms", *COUNT_FIELDS}


def test_each_linked_workspace_row_is_its_own_judgement_finding():
    from _application.maintenance._detection import classify

    def unreachable(key, path):
        return {"check": "workspace_registry", "code": "workspace_folder_unreachable", "severity": "info",
                "file": f".brain/local/workspaces.json#{key}", "message": f"{key} is away",
                "evidence": {"key": key, "path": path}}

    first, second = classify([unreachable("a", "/drive/a"), unreachable("b", "/drive/b")])
    assert first.disposition is second.disposition is Disposition.JUDGEMENT
    assert first.key != second.key

    moved, unchanged = classify([unreachable("a", "/elsewhere/a"), unreachable("b", "/drive/b")])
    assert (moved.key, unchanged.key) == (first.key, second.key)
    assert finding_fingerprint(moved.key, moved.evidence) != finding_fingerprint(first.key, first.evidence)
    assert finding_fingerprint(unchanged.key, unchanged.evidence) == finding_fingerprint(second.key, second.evidence)


class TestErrorSeverityRule:
    """Every error is repaired automatically or listed for a person (DD-082)."""

    @staticmethod
    def _raw(check, severity, file, *, code=None, evidence=None):
        raw = {"check": check, "severity": severity, "file": file, "message": f"{check} at {file}"}
        if code is not None:
            raw["code"] = code
        if evidence is not None:
            raw["evidence"] = evidence
        return raw

    @pytest.mark.parametrize("code", [
        "workspace_hub_invalid", "workspace_ownership_invalid", "workspace_reference_wrong_type",
        "workspace_reference_malformed", "workspace_policy_invalid", "workspace_scan_unreadable",
        "workspace_binding_terminal_inactive", "workspace_binding_configured_invalid",
    ])
    def test_a_family_less_error_code_is_a_keyed_judgement_item(self, code):
        from _application.maintenance._detection import classify

        (finding,) = classify([self._raw("workspace_contract", "error", "Notes/a.md", code=code)])

        assert finding.disposition is Disposition.JUDGEMENT and finding.owner is Owner.BRAIN
        assert finding.key == finding_key("brain", f"workspace_contract:{code}", {"file": "Notes/a.md"})
        (group,) = group_by_family((finding,))
        assert (group.file, group.fingerprint) == ("Notes/a.md", finding.key), "no declared evidence: the key"

    @pytest.mark.parametrize("check", ["root_files", "living_key_fields"])
    def test_a_code_less_error_is_keyed_per_file_by_its_check(self, check):
        from _application.maintenance._detection import classify

        first, second = classify([self._raw(check, "error", "a.md"), self._raw(check, "error", "b.md")])

        assert first.disposition is second.disposition is Disposition.JUDGEMENT
        assert first.key == finding_key("brain", check, {"file": "a.md"})
        assert second.key == finding_key("brain", check, {"file": "b.md"})
        assert len(group_by_family((first, second))) == 2

    def test_a_family_less_warning_or_info_stays_report_only(self):
        from _application.maintenance._detection import classify

        findings = classify([
            self._raw("naming", "warning", "a.md"),
            self._raw("workspace_contract", "info", "b.md", code="workspace_adoption_candidate"),
        ])

        assert {finding.disposition for finding in findings} == {Disposition.REPORT_ONLY}
        assert group_by_family(findings) == ()

    @pytest.mark.parametrize("code", ["workspace_reference_missing", "workspace_reference_archived"])
    def test_codes_judged_before_the_rule_keep_their_keys(self, code):
        from _application.maintenance._detection import classify

        (finding,) = classify([self._raw("workspace_contract", "error", "Notes/a.md", code=code,
                                         evidence={"workspace": "workspace/x"})])

        assert finding.key == finding_key("brain", f"workspace_contract:{code}", {"file": "Notes/a.md"})
        (group,) = group_by_family((finding,))
        assert group.fingerprint == finding_fingerprint(finding.key, {"workspace": "workspace/x"})

    def test_same_key_findings_are_one_group_over_every_members_evidence(self):
        from _application.maintenance._detection import classify

        def finding(workspace):
            return self._raw("workspace_contract", "error", "Notes/a.md", code="workspace_reference_missing",
                             evidence={"workspace": workspace})

        pair = classify([finding("workspace/y"), finding("workspace/x")])
        (group,) = group_by_family(pair)

        assert group.key == pair[0].key and len(group.members) == 2
        assert group.file == "Notes/a.md" and not group.is_family
        assert group.fingerprint == finding_fingerprint(
            group.key, {"members": [{"workspace": "workspace/x"}, {"workspace": "workspace/y"}]})
        (reordered,) = group_by_family(tuple(reversed(pair)))
        assert reordered.fingerprint == group.fingerprint, "member order never changes the fingerprint"
        (changed,) = group_by_family(classify([finding("workspace/x"), finding("workspace/z")]))
        assert changed.fingerprint != group.fingerprint, "a member's changed evidence reopens a dismissal"
