"""Tests for brain_mcp._server_artefacts — pure helpers (no I/O)."""

from brain_mcp._server_artefacts import (
    format_wikilink_fixes,
    format_wikilink_warnings,
)


class TestFormatWikilinkWarnings:
    def test_empty_findings_returns_empty_string(self):
        assert format_wikilink_warnings([]) == ""

    def test_none_returns_empty_string(self):
        assert format_wikilink_warnings(None) == ""

    def test_broken_only(self):
        findings = [
            {"stem": "Helix", "status": "broken", "resolved_to": None,
             "strategy": "none", "candidates": []},
            {"stem": "Skogarmaor", "status": "broken", "resolved_to": None,
             "strategy": "none", "candidates": []},
        ]
        out = format_wikilink_warnings(findings)
        assert out == "⚠ Broken wikilinks: [[Helix]], [[Skogarmaor]]"

    def test_resolvable_only(self):
        findings = [
            {"stem": "old name", "status": "resolvable",
             "resolved_to": "20260416-research~old name",
             "strategy": "slug_to_title", "candidates": []},
        ]
        out = format_wikilink_warnings(findings)
        assert "⚠ Resolvable wikilinks (use fix-links to fix all or selected):" in out
        assert "[[old name]] → [[20260416-research~old name]]" in out

    def test_mixed(self):
        findings = [
            {"stem": "gone", "status": "broken", "resolved_to": None,
             "strategy": "none", "candidates": []},
            {"stem": "slug", "status": "resolvable", "resolved_to": "Real Title",
             "strategy": "slug_to_title", "candidates": []},
            {"stem": "dup", "status": "ambiguous", "resolved_to": None,
             "strategy": "ambiguous", "candidates": ["a/dup.md", "b/dup.md"]},
        ]
        out = format_wikilink_warnings(findings)
        assert "⚠ Broken wikilinks: [[gone]]" in out
        assert "⚠ Resolvable wikilinks" in out
        assert "[[slug]] → [[Real Title]]" in out
        assert "⚠ Ambiguous wikilinks: [[dup]] matches 2 files" in out

    def test_single_ambiguous(self):
        findings = [
            {"stem": "dup", "status": "ambiguous", "resolved_to": None,
             "strategy": "ambiguous", "candidates": ["a.md", "b.md", "c.md"]},
        ]
        out = format_wikilink_warnings(findings)
        assert out == "⚠ Ambiguous wikilinks: [[dup]] matches 3 files"


class TestFormatWikilinkFixes:
    def test_empty_returns_empty_string(self):
        assert format_wikilink_fixes(None) == ""
        assert format_wikilink_fixes({}) == ""
        assert format_wikilink_fixes({"applied": 0, "fixes": []}) == ""

    def test_fix_list_renders(self):
        fixes = {
            "applied": 2,
            "fixes": [
                {"target": "old-one", "resolved_to": "Old One", "strategy": "slug_to_title"},
                {"target": "old-two", "resolved_to": "Old Two", "strategy": "slug_to_title"},
            ],
        }
        out = format_wikilink_fixes(fixes)
        assert "✔ Wikilink fixes applied (2):" in out
        assert "[[old-one]] → [[Old One]]" in out
        assert "[[old-two]] → [[Old Two]]" in out
