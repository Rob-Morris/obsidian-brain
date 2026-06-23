"""Tests for check.py — router-driven vault compliance checker."""

import json
import os
import sys
import time

import pytest

import check
import compile_router as cr
import _lifecycle.semantic_repairs as semantic_repairs
import _search.index as search_index
import _search.paths as search_paths

from brain_test_support import make_router, write_md
from brain_test_support import filesystem_is_case_sensitive


class TestCheckRootFiles:
    def test_clean_root(self, vault):
        tmp_path, router = vault
        findings = check.check_root_files(str(tmp_path), router)
        assert len(findings) == 0

    def test_orphan_flagged(self, vault):
        tmp_path, router = vault
        (tmp_path / "readme.md").write_text("# Readme\n")
        findings = check.check_root_files(str(tmp_path), router)
        assert len(findings) == 1
        assert findings[0]["severity"] == "error"
        assert "readme.md" in findings[0]["file"]

    def test_non_md_orphan_flagged(self, vault):
        tmp_path, router = vault
        (tmp_path / "eval-viewer.html").write_text("<html></html>")
        findings = check.check_root_files(str(tmp_path), router)
        assert len(findings) == 1
        assert "eval-viewer.html" in findings[0]["file"]

    def test_dot_prefixed_ignored(self, vault):
        tmp_path, router = vault
        (tmp_path / ".obsidian").mkdir(exist_ok=True)
        (tmp_path / ".git").mkdir(exist_ok=True)
        findings = check.check_root_files(str(tmp_path), router)
        assert len(findings) == 0

    def test_underscore_prefixed_ignored(self, vault):
        tmp_path, router = vault
        (tmp_path / "_Assets").mkdir(exist_ok=True)
        findings = check.check_root_files(str(tmp_path), router)
        assert len(findings) == 0

    def test_artefact_folders_allowed(self, vault):
        tmp_path, router = vault
        # Wiki and Designs are artefact folders — should not be flagged
        findings = check.check_root_files(str(tmp_path), router)
        assert not any("Wiki" in f.get("file", "") for f in findings)

    def test_root_allow_files_pass(self, vault):
        tmp_path, router = vault
        (tmp_path / "AGENTS.md").write_text("# Agents\n")
        (tmp_path / "Agents.md").write_text("# Legacy Agents\n")
        (tmp_path / "CLAUDE.md").write_text("# Claude\n")
        (tmp_path / "AGENTS.local.md").write_text("# Local Canonical\n")
        (tmp_path / "agents.local.md").write_text("# Local\n")
        (tmp_path / ".mcp.json").write_text("{}")
        findings = check.check_root_files(str(tmp_path), router)
        assert len(findings) == 0

    def test_non_canonical_agents_md_flagged_on_case_sensitive_fs(self, vault):
        tmp_path, router = vault
        if not filesystem_is_case_sensitive(tmp_path):
            pytest.skip("case-insensitive filesystem accepts alternate casing automatically")
        (tmp_path / "agents.md").write_text("# lower-case\n")
        findings = check.check_root_files(str(tmp_path), router)
        assert len(findings) == 1
        assert findings[0]["file"] == "agents.md"


# ---------------------------------------------------------------------------
# TestCheckNaming
# ---------------------------------------------------------------------------

class TestCheckNaming:
    def test_good_key_passes(self, vault):
        tmp_path, router = vault
        findings = check.check_naming(str(tmp_path), router)
        wiki_findings = [f for f in findings if "Wiki" in f.get("file", "")]
        assert len(wiki_findings) == 0

    def test_spaces_allowed_in_living_names(self, vault):
        """Living types use {name}.md — spaces are valid."""
        tmp_path, router = vault
        write_md(tmp_path / "Wiki" / "My Page.md",
                 {"type": "living/wiki", "tags": ["test"]})
        findings = check.check_naming(str(tmp_path), router)
        bad = [f for f in findings if "My Page" in f.get("file", "")]
        assert len(bad) == 0

    def test_archive_skipped(self, vault):
        tmp_path, router = vault
        archive = tmp_path / "Wiki" / "_Archive"
        archive.mkdir()
        write_md(archive / "Old Bad Name.md",
                 {"type": "living/wiki", "tags": ["test"]})
        findings = check.check_naming(str(tmp_path), router)
        assert not any("_Archive" in f.get("file", "") for f in findings)

    def test_freeform_title_passes(self, vault):
        tmp_path, router = vault
        # Already created: "20260315 - Rust Lifetimes.md" in Notes
        findings = check.check_naming(str(tmp_path), router)
        note_findings = [f for f in findings if "Notes" in f.get("file", "")]
        assert len(note_findings) == 0

    def test_daily_note_passes(self, vault):
        tmp_path, router = vault
        findings = check.check_naming(str(tmp_path), router)
        dn_findings = [f for f in findings if "Daily Notes" in f.get("file", "")]
        assert len(dn_findings) == 0

    def test_log_double_dash_passes(self, vault):
        tmp_path, router = vault
        findings = check.check_naming(str(tmp_path), router)
        log_findings = [f for f in findings if "Logs" in f.get("file", "")]
        assert len(log_findings) == 0

    def test_unconfigured_type_skipped(self, vault):
        tmp_path, router = vault
        write_md(tmp_path / "Projects" / "anything goes.md",
                 {"type": "living/project", "tags": ["project"]})
        findings = check.check_naming(str(tmp_path), router)
        proj_findings = [f for f in findings if "Projects" in f.get("file", "")]
        assert len(proj_findings) == 0


# ---------------------------------------------------------------------------
# TestCheckFrontmatterType
# ---------------------------------------------------------------------------

class TestCheckFrontmatterType:
    def test_correct_type_passes(self, vault):
        tmp_path, router = vault
        findings = check.check_frontmatter_type(str(tmp_path), router)
        assert len(findings) == 0

    def test_wrong_type_flagged(self, vault):
        tmp_path, router = vault
        write_md(tmp_path / "Wiki" / "foo.md",
                 {"type": "temporal/plan", "tags": ["test"]})
        findings = check.check_frontmatter_type(str(tmp_path), router)
        bad = [f for f in findings if "foo.md" in f.get("file", "")]
        assert len(bad) == 1
        assert "temporal/plan" in bad[0]["message"]

    def test_no_frontmatter_skipped(self, vault):
        tmp_path, router = vault
        (tmp_path / "Wiki" / "bare.md").write_text("# No frontmatter\n")
        findings = check.check_frontmatter_type(str(tmp_path), router)
        assert not any("bare.md" in f.get("file", "") for f in findings)


# ---------------------------------------------------------------------------
# TestCheckFrontmatterRequired
# ---------------------------------------------------------------------------

class TestCheckFrontmatterRequired:
    def test_all_present_passes(self, vault):
        tmp_path, router = vault
        findings = check.check_frontmatter_required(str(tmp_path), router)
        wiki_findings = [f for f in findings if "rust-lifetimes" in f.get("file", "")]
        assert len(wiki_findings) == 0

    def test_missing_field_flagged(self, vault):
        tmp_path, router = vault
        write_md(tmp_path / "Designs" / "bar.md",
                 {"type": "living/design", "tags": ["design"]})
        # Missing 'status' which is required for designs
        findings = check.check_frontmatter_required(str(tmp_path), router)
        bad = [f for f in findings if "bar.md" in f.get("file", "")]
        assert len(bad) == 1
        assert "status" in bad[0]["message"]

    def test_empty_tags_counts_as_present(self, vault):
        tmp_path, router = vault
        write_md(tmp_path / "Wiki" / "empty-tags.md",
                 {"type": "living/wiki", "tags": []})
        findings = check.check_frontmatter_required(str(tmp_path), router)
        bad = [f for f in findings if "empty-tags" in f.get("file", "")]
        # tags is present (even if empty list), type is present → no findings
        assert len(bad) == 0

    def test_no_frontmatter_skipped(self, vault):
        tmp_path, router = vault
        (tmp_path / "Wiki" / "no-fm.md").write_text("Just text\n")
        findings = check.check_frontmatter_required(str(tmp_path), router)
        assert not any("no-fm" in f.get("file", "") for f in findings)


# ---------------------------------------------------------------------------
# TestCheckDuplicateFrontmatter
# ---------------------------------------------------------------------------

class TestCheckDuplicateFrontmatter:
    def test_duplicate_frontmatter_flagged(self, vault):
        tmp_path, router = vault
        (tmp_path / "Wiki" / "dup.md").write_text(
            "---\n"
            "type: living/wiki\n"
            "tags:\n"
            "  - wiki\n"
            "key: dup\n"
            "---\n"
            "---\n"
            "status: shaping\n"
            "---\n"
            "# Dup\n"
        )

        findings = check.check_duplicate_frontmatter(str(tmp_path), router)

        hits = [f for f in findings if f.get("file") == "Wiki/dup.md"]
        assert len(hits) == 1
        assert hits[0]["severity"] == "warning"
        assert hits[0]["check"] == "duplicate_frontmatter"

    def test_duplicate_frontmatter_uses_context_cache_when_available(self, vault, monkeypatch):
        tmp_path, router = vault
        checked_paths = []

        class FakeContext:
            def duplicate_frontmatter(self, path):
                checked_paths.append(path)
                if path.endswith("Wiki/shared.md"):
                    return {"merged_fields": {}, "body": "# Body\n"}
                return None

        (tmp_path / "Wiki" / "shared.md").write_text("# handled by fake ctx\n")

        findings = check.check_duplicate_frontmatter(str(tmp_path), router, ctx=FakeContext())

        assert str(tmp_path / "Wiki" / "shared.md") in checked_paths
        hits = [f for f in findings if f.get("file") == "Wiki/shared.md"]
        assert len(hits) == 1
        assert hits[0]["repair"]["scope"] == "frontmatter"

    def test_run_checks_keeps_outer_authority_for_other_checks(self, vault):
        tmp_path, router = vault
        (tmp_path / "Designs" / "dup.md").write_text(
            "---\n"
            "type: living/design\n"
            "tags:\n"
            "  - design\n"
            "key: dup\n"
            "---\n"
            "---\n"
            "status: shaping\n"
            "---\n"
            "# Dup\n"
        )

        result = check.run_checks(str(tmp_path), router)

        assert any(
            f["check"] == "duplicate_frontmatter"
            and f.get("file") == "Designs/dup.md"
            for f in result["findings"]
        )
        assert any(
            f["check"] == "frontmatter_required"
            and f.get("file") == "Designs/dup.md"
            for f in result["findings"]
        )


# ---------------------------------------------------------------------------
# TestCheckMonthFolders
# ---------------------------------------------------------------------------

class TestCheckMonthFolders:
    def test_file_in_month_folder_passes(self, vault):
        tmp_path, router = vault
        findings = check.check_month_folders(str(tmp_path), router)
        assert len(findings) == 0

    def test_stray_file_flagged(self, vault):
        tmp_path, router = vault
        write_md(tmp_path / "_Temporal" / "Plans" / "stray.md",
                 {"type": "temporal/plan", "tags": ["plan"], "status": "draft"})
        findings = check.check_month_folders(str(tmp_path), router)
        assert len(findings) == 1
        assert "stray.md" in findings[0]["file"]

    def test_living_types_skipped(self, vault):
        tmp_path, router = vault
        # Wiki is living — should not be checked for month folders
        findings = check.check_month_folders(str(tmp_path), router)
        assert not any("Wiki" in f.get("file", "") for f in findings)


# ---------------------------------------------------------------------------
# TestCheckMissingTimestamps
# ---------------------------------------------------------------------------

class TestCheckMissingTimestamps:
    def test_file_with_both_timestamps_passes(self, vault):
        tmp_path, router = vault
        write_md(tmp_path / "Wiki" / "with-ts.md",
                 {"type": "living/wiki", "tags": ["wiki"],
                  "created": "2026-03-15T09:00:00+11:00",
                  "modified": "2026-03-15T09:00:00+11:00"})
        findings = check.check_missing_timestamps(str(tmp_path), router)
        assert not any("with-ts" in f.get("file", "") for f in findings)

    def test_missing_created_flagged_as_warning(self, vault):
        tmp_path, router = vault
        write_md(tmp_path / "Wiki" / "no-created.md",
                 {"type": "living/wiki", "tags": ["wiki"],
                  "modified": "2026-03-15T09:00:00+11:00"})
        findings = check.check_missing_timestamps(str(tmp_path), router)
        hits = [f for f in findings if "no-created" in f.get("file", "")]
        assert len(hits) == 1
        assert hits[0]["severity"] == "warning"
        assert "created" in hits[0]["message"]

    def test_missing_modified_flagged(self, vault):
        tmp_path, router = vault
        write_md(tmp_path / "Wiki" / "no-modified.md",
                 {"type": "living/wiki", "tags": ["wiki"],
                  "created": "2026-03-15T09:00:00+11:00"})
        findings = check.check_missing_timestamps(str(tmp_path), router)
        hits = [f for f in findings if "no-modified" in f.get("file", "")]
        assert len(hits) == 1
        assert "modified" in hits[0]["message"]

    def test_missing_both_flagged_once(self, vault):
        tmp_path, router = vault
        write_md(tmp_path / "Wiki" / "no-ts.md",
                 {"type": "living/wiki", "tags": ["wiki"]})
        findings = check.check_missing_timestamps(str(tmp_path), router)
        hits = [f for f in findings if "no-ts" in f.get("file", "")]
        assert len(hits) == 1
        assert "created" in hits[0]["message"]
        assert "modified" in hits[0]["message"]

    def test_no_frontmatter_skipped(self, vault):
        tmp_path, router = vault
        (tmp_path / "Wiki" / "plain.md").write_text("# No frontmatter\n")
        findings = check.check_missing_timestamps(str(tmp_path), router)
        assert not any("plain" in f.get("file", "") for f in findings)

