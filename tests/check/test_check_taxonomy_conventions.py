"""Tests for the router-driven ``taxonomy_conventions`` drift check."""

import check
from compile_router import legacy_month_folder, match_convention_rule


def _router(*folders):
    return {
        "artefacts": [
            {
                "path": f"_Temporal/T{index}",
                "key": f"t{index}",
                "classification": "temporal",
                "configured": True,
                "taxonomy_file": f"_Config/Taxonomy/Temporal/t{index}.md",
                "naming": {"folder": folder, "pattern": "yyyymmdd-x~{Title}.md", "rules": []},
            }
            for index, folder in enumerate(folders)
        ]
    }


class TestLegacyMonthFolder:
    def test_matches_only_a_trailing_month_segment(self):
        assert legacy_month_folder("_Temporal/Logs/yyyy-mm/") == "_Temporal/Logs/"
        assert legacy_month_folder("_Temporal/Logs/yyyy-mm") == "_Temporal/Logs/"
        assert legacy_month_folder("_Temporal/Logs/") is None
        assert legacy_month_folder("Daily Notes/") is None
        assert legacy_month_folder("yyyy-mm/") is None, "a bare month folder has no type root"
        assert legacy_month_folder(None) is None


class TestCheckTaxonomyConventions:
    def test_flags_legacy_folder_as_non_repairable_info(self, tmp_path):
        findings = check.check_taxonomy_conventions(str(tmp_path), _router("_Temporal/T0/yyyy-mm/"))
        assert len(findings) == 1
        finding = findings[0]
        assert finding["check"] == "taxonomy_conventions"
        assert finding["severity"] == "info"
        assert finding["repairable"] is False
        assert finding["file"] == "_Config/Taxonomy/Temporal/t0.md"
        assert "_Temporal/T0/" in finding["message"]
        assert finding["rule"] == "flatten-temporal-month-folder"
        assert "sync" in finding["fix"]
        assert "artefact_sync_exclude" in finding["fix"]
        assert "repair" not in finding

    def test_current_convention_is_clean(self, tmp_path):
        assert check.check_taxonomy_conventions(str(tmp_path), _router("_Temporal/T0/", "_Temporal/T1/")) == []

    def test_flags_through_the_shared_rule_table(self, tmp_path):
        router = _router("_Temporal/T0/yyyy-mm/", "_Temporal/T1/", "_Temporal/T2/yyyy-mm/")
        flagged = {f["file"] for f in check.check_taxonomy_conventions(str(tmp_path), router)}
        expected = {
            art["taxonomy_file"]
            for art in router["artefacts"]
            if match_convention_rule("temporal", art["naming"]["folder"]) is not None
        }
        assert flagged == expected == {
            "_Config/Taxonomy/Temporal/t0.md",
            "_Config/Taxonomy/Temporal/t2.md",
        }

    def test_living_types_are_out_of_the_rule_scope(self, tmp_path):
        router = _router("Sketches/yyyy-mm/")
        router["artefacts"][0]["classification"] = "living"
        assert check.check_taxonomy_conventions(str(tmp_path), router) == []

    def test_registered_in_all_checks(self):
        assert check.check_taxonomy_conventions in check.ALL_CHECKS
        assert not hasattr(check, "check_month_folders")
