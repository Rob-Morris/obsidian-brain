"""Unparented temporal files in subfolders are reported by ``parent_contract``."""

import check
from brain_test_support import write_md


def _temporal_findings(tmp_path, router, rel_path):
    return [
        f for f in check.check_parent_contract(str(tmp_path), router)
        if f["file"] == rel_path
    ]


class TestTemporalPlacement:
    def test_flat_unparented_temporal_file_is_clean(self, vault):
        tmp_path, router = vault
        rel = "_Temporal/Logs/20260401-log.md"
        write_md(tmp_path / rel, {"type": "temporal/log", "tags": ["log"], "date": "2026-04-01"})
        assert _temporal_findings(tmp_path, router, rel) == []

    def test_legacy_month_folder_is_reported_as_an_orphan(self, vault):
        tmp_path, router = vault
        rel = "_Temporal/Logs/2026-04/20260401-log.md"
        write_md(tmp_path / rel, {"type": "temporal/log", "tags": ["log"], "date": "2026-04-01"})
        findings = _temporal_findings(tmp_path, router, rel)
        assert len(findings) == 1
        assert findings[0]["check"] == "parent_contract"
        assert findings[0]["severity"] == "warning"
        assert "2026-04" in findings[0]["message"]
        assert "base folder" in findings[0]["fix"]

    def test_owner_folder_without_parent_field_implies_the_parent(self, vault):
        tmp_path, router = vault
        rel = "_Temporal/Logs/design~auth-redesign/20260401-log.md"
        write_md(tmp_path / rel, {"type": "temporal/log", "tags": ["log"], "date": "2026-04-01"})
        findings = _temporal_findings(tmp_path, router, rel)
        assert len(findings) == 1
        assert findings[0]["folder_implies"] == "design/auth-redesign"
        assert findings[0]["repairable"] is False
