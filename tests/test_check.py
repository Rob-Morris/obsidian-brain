"""Tests for check.py — router-driven vault compliance checker."""

import json
import os

import pytest

import check
import compile_router as cr

from conftest import make_router, write_md


# ---------------------------------------------------------------------------
# Fixture vault with pre-written compiled router
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path):
    """Vault fixture with compiled router and test files."""
    # .brain-core/VERSION
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.9.11\n")
    (bc / "session-core.md").write_text("# Session Core\n")

    # Wiki — living, configured, {slug}.md
    wiki_art = {
        "folder": "Wiki", "type": "living/wiki", "key": "wiki",
        "classification": "living", "configured": True,
        "path": "Wiki",
        "naming": {"pattern": "{name}.md", "folder": "Wiki/"},
        "frontmatter": {
            "type": "living/wiki",
            "required": ["type", "tags"],
            "status_enum": None,
            "terminal_statuses": None,
        },
        "taxonomy_file": "_Config/Taxonomy/Living/wiki.md",
        "template_file": None, "trigger": None,
    }

    # Designs — living, configured, with status enum + terminal statuses
    designs_art = {
        "folder": "Designs", "type": "living/design", "key": "designs",
        "classification": "living", "configured": True,
        "path": "Designs",
        "naming": {"pattern": "{name}.md", "folder": "Designs/"},
        "frontmatter": {
            "type": "living/design",
            "required": ["type", "tags", "status"],
            "status_enum": ["shaping", "active", "implemented", "parked"],
            "terminal_statuses": ["implemented"],
        },
        "taxonomy_file": "_Config/Taxonomy/Living/designs.md",
        "template_file": None, "trigger": None,
    }

    # Notes — living, configured, yyyymmdd - {Title}.md
    notes_art = {
        "folder": "Notes", "type": "living/note", "key": "notes",
        "classification": "living", "configured": True,
        "path": "Notes",
        "naming": {"pattern": "yyyymmdd - {Title}.md", "folder": "Notes/"},
        "frontmatter": {
            "type": "living/note",
            "required": ["type", "tags"],
            "status_enum": None,
            "terminal_statuses": None,
        },
        "taxonomy_file": "_Config/Taxonomy/Living/notes.md",
        "template_file": None, "trigger": None,
    }

    # Daily Notes — living, configured, yyyy-mm-dd ddd.md
    daily_art = {
        "folder": "Daily Notes", "type": "living/daily-note", "key": "daily-notes",
        "classification": "living", "configured": True,
        "path": "Daily Notes",
        "naming": {"pattern": "yyyy-mm-dd ddd.md", "folder": "Daily Notes/"},
        "frontmatter": {
            "type": "living/daily-note",
            "required": ["type", "tags"],
            "status_enum": None,
            "terminal_statuses": None,
        },
        "taxonomy_file": "_Config/Taxonomy/Living/daily-notes.md",
        "template_file": None, "trigger": None,
    }

    # Logs — temporal, configured, yyyymmdd-log.md
    logs_art = {
        "folder": "Logs", "type": "temporal/log", "key": "logs",
        "classification": "temporal", "configured": True,
        "path": os.path.join("_Temporal", "Logs"),
        "naming": {"pattern": "yyyymmdd-log.md", "folder": "_Temporal/Logs/yyyy-mm/"},
        "frontmatter": {
            "type": "temporal/log",
            "required": ["type", "tags"],
            "status_enum": None,
            "terminal_statuses": None,
        },
        "taxonomy_file": "_Config/Taxonomy/Temporal/logs.md",
        "template_file": None, "trigger": None,
    }

    # Plans — temporal, configured, yyyymmdd-plan~{Title}.md
    plans_art = {
        "folder": "Plans", "type": "temporal/plan", "key": "plans",
        "classification": "temporal", "configured": True,
        "path": os.path.join("_Temporal", "Plans"),
        "naming": {"pattern": "yyyymmdd-plan~{Title}.md", "folder": "_Temporal/Plans/yyyy-mm/"},
        "frontmatter": {
            "type": "temporal/plan",
            "required": ["type", "tags", "status"],
            "status_enum": ["draft", "approved", "completed"],
            "terminal_statuses": None,
        },
        "taxonomy_file": "_Config/Taxonomy/Temporal/plans.md",
        "template_file": None, "trigger": None,
    }

    # Shaping Transcripts — temporal, configured
    shaping_art = {
        "folder": "Shaping Transcripts", "type": "temporal/shaping-transcript",
        "key": "shaping-transcripts",
        "classification": "temporal", "configured": True,
        "path": os.path.join("_Temporal", "Shaping Transcripts"),
        "naming": {"pattern": "yyyymmdd-shaping-transcript~{Title}.md",
                    "folder": "_Temporal/Shaping Transcripts/yyyy-mm/"},
        "frontmatter": {
            "type": "temporal/shaping-transcript",
            "required": ["type", "tags"],
            "status_enum": None,
            "terminal_statuses": None,
        },
        "taxonomy_file": None, "template_file": None, "trigger": None,
    }

    # Cookies — temporal, configured
    cookies_art = {
        "folder": "Cookies", "type": "temporal/cookie", "key": "cookies",
        "classification": "temporal", "configured": True,
        "path": os.path.join("_Temporal", "Cookies"),
        "naming": {"pattern": "yyyymmdd-cookie~{Title}.md",
                    "folder": "_Temporal/Cookies/yyyy-mm/"},
        "frontmatter": {
            "type": "temporal/cookie",
            "required": ["type", "tags"],
            "status_enum": None,
            "terminal_statuses": None,
        },
        "taxonomy_file": None, "template_file": None, "trigger": None,
    }

    # Projects — unconfigured
    projects_art = {
        "folder": "Projects", "type": "living/projects", "key": "projects",
        "classification": "living", "configured": False,
        "path": "Projects",
        "naming": None, "frontmatter": None,
        "taxonomy_file": None, "template_file": None, "trigger": None,
    }

    artefacts = [wiki_art, designs_art, notes_art, daily_art, logs_art,
                 plans_art, shaping_art, cookies_art, projects_art]

    router = make_router(artefacts)

    # Write compiled router
    brain_local = tmp_path / ".brain" / "local"
    brain_local.mkdir(parents=True, exist_ok=True)
    (brain_local / "compiled-router.json").write_text(
        json.dumps(router, indent=2) + "\n"
    )

    # Create type folders
    (tmp_path / "Wiki").mkdir()
    (tmp_path / "Designs").mkdir()
    (tmp_path / "Notes").mkdir()
    (tmp_path / "Daily Notes").mkdir()
    (tmp_path / "Projects").mkdir()
    (tmp_path / "_Temporal" / "Logs" / "2026-03").mkdir(parents=True)
    (tmp_path / "_Temporal" / "Plans" / "2026-03").mkdir(parents=True)
    (tmp_path / "_Temporal" / "Shaping Transcripts" / "2026-03").mkdir(parents=True)
    (tmp_path / "_Temporal" / "Cookies" / "2026-03").mkdir(parents=True)

    # --- Good files ---
    write_md(tmp_path / "Wiki" / "rust-lifetimes.md",
             {"type": "living/wiki", "tags": ["rust"]}, "# Rust Lifetimes")
    write_md(tmp_path / "Designs" / "auth-redesign.md",
             {"type": "living/design", "tags": ["design"], "status": "shaping"},
             "# Auth Redesign")
    write_md(tmp_path / "Notes" / "20260315 - Rust Lifetimes.md",
             {"type": "living/note", "tags": ["rust"]}, "# Rust Lifetimes")
    write_md(tmp_path / "Daily Notes" / "2026-03-15 Sat.md",
             {"type": "living/daily-note", "tags": ["daily-note"]}, "# Saturday")
    write_md(tmp_path / "_Temporal" / "Logs" / "2026-03" / "20260315-log.md",
             {"type": "temporal/log", "tags": ["log"]}, "09:00 Started work.")
    write_md(tmp_path / "_Temporal" / "Shaping Transcripts" / "2026-03" /
             "20260315-shaping-transcript~Auth.md",
             {"type": "temporal/shaping-transcript", "tags": ["transcript"]}, "Q. What?")
    write_md(tmp_path / "_Temporal" / "Cookies" / "2026-03" /
             "20260315-cookie~Great Refactor.md",
             {"type": "temporal/cookie", "tags": ["cookie"]}, "# Cookie")

    return tmp_path, router


# ---------------------------------------------------------------------------
# TestNamingPatternToRegex
# ---------------------------------------------------------------------------

class TestNamingPatternToRegex:
    def test_slug_pattern(self):
        r = check.naming_pattern_to_regex("{slug}.md")
        assert r.match("rust-lifetimes.md")
        assert r.match("api.md")
        assert r.match("gap-assessment--brain-inbox.md")
        # Generous filenames now accepted
        assert r.match("My Page.md")
        assert r.match("UPPER.md")

    def test_yyyymmdd_slug_pattern(self):
        r = check.naming_pattern_to_regex("yyyymmdd-{slug}.md")
        assert r.match("20260315-auth-redesign.md")
        assert not r.match("auth-redesign.md")
        assert not r.match("2026-03-15-auth.md")

    def test_log_pattern(self):
        r = check.naming_pattern_to_regex("yyyymmdd-log.md")
        assert r.match("20260315-log.md")
        assert not r.match("log--2026-03-15.md")
        assert not r.match("log-20260315.md")

    def test_title_pattern(self):
        r = check.naming_pattern_to_regex("yyyymmdd - {Title}.md")
        assert r.match("20260315 - Rust Lifetimes.md")
        assert r.match("20260315 - API Design Notes.md")
        assert not r.match("Rust Lifetimes.md")

    def test_daily_note_pattern(self):
        r = check.naming_pattern_to_regex("yyyy-mm-dd ddd.md")
        assert r.match("2026-03-15 Sat.md")
        assert r.match("2026-01-01 Wed.md")
        assert not r.match("2026-03-15 Saturday.md")
        assert not r.match("20260315 Sat.md")

    def test_shaping_transcript_pattern(self):
        r = check.naming_pattern_to_regex("yyyymmdd-shaping-transcript~{Title}.md")
        assert r.match("20260315-shaping-transcript~Auth.md")
        assert r.match("20260315-shaping-transcript~My Thing.md")
        assert not r.match("20260315-design-transcript~Auth.md")

    def test_cookie_pattern(self):
        r = check.naming_pattern_to_regex("yyyymmdd-cookie~{Title}.md")
        assert r.match("20260315-cookie~Great Refactor.md")
        assert not r.match("20260315-cookie-Great Refactor.md")

    def test_idea_log_pattern(self):
        r = check.naming_pattern_to_regex("yyyymmdd-idea-log~{Title}.md")
        assert r.match("20260315-idea-log~Shared Validation.md")

    def test_temporal_title_pattern(self):
        """Temporal artefacts with ~ separator and human-readable titles."""
        r = check.naming_pattern_to_regex("yyyymmdd-plan~{Title}.md")
        assert r.match("20260317-plan~API Refactor.md")
        assert r.match("20260317-plan~Simple.md")
        assert not r.match("20260317-plan~.md")  # missing space + title

    def test_name_pattern(self):
        """Living artefacts use {name}.md — freeform names with spaces or hyphens."""
        r = check.naming_pattern_to_regex("{name}.md")
        assert r.match("rust-lifetimes.md")
        assert r.match("Underware Writing Guide.md")
        assert r.match("Pinchtab x402 service.md")
        assert r.match("API.md")
        assert not r.match(".md")  # empty name

    def test_none_pattern(self):
        assert check.naming_pattern_to_regex(None) is None


# ---------------------------------------------------------------------------
# TestCheckRootFiles
# ---------------------------------------------------------------------------

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
        (tmp_path / "Agents.md").write_text("# Agents\n")
        (tmp_path / "CLAUDE.md").write_text("# Claude\n")
        (tmp_path / "agents.local.md").write_text("# Local\n")
        (tmp_path / ".mcp.json").write_text("{}")
        findings = check.check_root_files(str(tmp_path), router)
        assert len(findings) == 0


# ---------------------------------------------------------------------------
# TestCheckNaming
# ---------------------------------------------------------------------------

class TestCheckNaming:
    def test_good_slug_passes(self, vault):
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
# TestCheckArchiveMetadata
# ---------------------------------------------------------------------------

class TestCheckArchiveMetadata:
    def test_complete_archive_passes(self, vault):
        tmp_path, router = vault
        archive = tmp_path / "Designs" / "_Archive"
        archive.mkdir()
        write_md(archive / "20260315-old-design.md",
                 {"type": "living/design", "tags": ["design"],
                  "status": "implemented", "archiveddate": "2026-03-15"})
        findings = check.check_archive_metadata(str(tmp_path), router)
        assert len(findings) == 0

    def test_missing_archiveddate(self, vault):
        tmp_path, router = vault
        archive = tmp_path / "Designs" / "_Archive"
        archive.mkdir()
        write_md(archive / "20260315-old.md",
                 {"type": "living/design", "tags": ["design"],
                  "status": "implemented"})
        findings = check.check_archive_metadata(str(tmp_path), router)
        assert any("archiveddate" in f["message"] for f in findings)

    def test_missing_prefix(self, vault):
        tmp_path, router = vault
        archive = tmp_path / "Designs" / "_Archive"
        archive.mkdir()
        write_md(archive / "old-design.md",
                 {"type": "living/design", "tags": ["design"],
                  "status": "implemented", "archiveddate": "2026-03-15"})
        findings = check.check_archive_metadata(str(tmp_path), router)
        assert any("prefix" in f["message"] for f in findings)

    def test_wrong_status(self, vault):
        tmp_path, router = vault
        archive = tmp_path / "Designs" / "_Archive"
        archive.mkdir()
        write_md(archive / "20260315-bad-status.md",
                 {"type": "living/design", "tags": ["design"],
                  "status": "shaping", "archiveddate": "2026-03-15"})
        findings = check.check_archive_metadata(str(tmp_path), router)
        assert any("terminal" in f["message"].lower() for f in findings)

    def test_no_terminal_statuses_skips_status_check(self, vault):
        tmp_path, router = vault
        # Wiki has no terminal_statuses — status sub-check should be skipped
        archive = tmp_path / "Wiki" / "_Archive"
        archive.mkdir()
        write_md(archive / "20260315-old-page.md",
                 {"type": "living/wiki", "tags": ["test"],
                  "status": "whatever", "archiveddate": "2026-03-15"})
        findings = check.check_archive_metadata(str(tmp_path), router)
        assert not any("terminal" in f.get("message", "").lower() for f in findings)

    def test_project_subfolder_archive_passes(self, vault):
        """Archives in project subfolders are validated too."""
        tmp_path, router = vault
        archive = tmp_path / "Designs" / "Brain" / "_Archive"
        archive.mkdir(parents=True)
        write_md(archive / "20260317-old-sub.md",
                 {"type": "living/design", "tags": ["design"],
                  "status": "implemented", "archiveddate": "2026-03-17"})
        findings = check.check_archive_metadata(str(tmp_path), router)
        assert len(findings) == 0

    def test_project_subfolder_archive_missing_archiveddate(self, vault):
        """Findings generated for bad metadata in project subfolder archives."""
        tmp_path, router = vault
        archive = tmp_path / "Designs" / "Brain" / "_Archive"
        archive.mkdir(parents=True)
        write_md(archive / "20260317-bad.md",
                 {"type": "living/design", "tags": ["design"],
                  "status": "implemented"})
        findings = check.check_archive_metadata(str(tmp_path), router)
        assert any("archiveddate" in f["message"] for f in findings)
        assert any("Brain" in f["file"] for f in findings)

    def test_top_level_archive_valid(self, vault):
        """check_archive_metadata finds files in top-level _Archive/ structure."""
        tmp_path, router = vault
        archive = tmp_path / "_Archive" / "Designs"
        archive.mkdir(parents=True)
        write_md(archive / "20260101-good.md",
                 {"type": "living/design", "tags": ["design"],
                  "status": "implemented", "archiveddate": "2026-01-01"})
        findings = check.check_archive_metadata(str(tmp_path), router)
        good_findings = [f for f in findings if "20260101-good" in f.get("file", "")]
        assert len(good_findings) == 0  # valid, no warnings

    def test_top_level_archive_missing_date(self, vault):
        """check_archive_metadata flags missing archiveddate in top-level _Archive/."""
        tmp_path, router = vault
        archive = tmp_path / "_Archive" / "Designs"
        archive.mkdir(parents=True)
        write_md(archive / "20260101-bad.md",
                 {"type": "living/design", "tags": ["design"],
                  "status": "implemented"})
        findings = check.check_archive_metadata(str(tmp_path), router)
        assert any("archiveddate" in f["message"] for f in findings)

    def test_top_level_archive_project_subfolder(self, vault):
        """check_archive_metadata scans project subfolders within top-level _Archive/."""
        tmp_path, router = vault
        archive = tmp_path / "_Archive" / "Designs" / "Brain"
        archive.mkdir(parents=True)
        write_md(archive / "20260101-proj.md",
                 {"type": "living/design", "tags": ["design"],
                  "status": "implemented", "archiveddate": "2026-01-01"})
        findings = check.check_archive_metadata(str(tmp_path), router)
        proj_findings = [f for f in findings if "20260101-proj" in f.get("file", "")]
        assert len(proj_findings) == 0  # valid


# ---------------------------------------------------------------------------
# TestCheckStatusValues
# ---------------------------------------------------------------------------

class TestCheckStatusValues:
    def test_valid_status_passes(self, vault):
        tmp_path, router = vault
        findings = check.check_status_values(str(tmp_path), router)
        design_findings = [f for f in findings if "auth-redesign" in f.get("file", "")]
        assert len(design_findings) == 0

    def test_invalid_status_flagged(self, vault):
        tmp_path, router = vault
        write_md(tmp_path / "Designs" / "baz.md",
                 {"type": "living/design", "tags": ["design"], "status": "wip"})
        findings = check.check_status_values(str(tmp_path), router)
        bad = [f for f in findings if "baz.md" in f.get("file", "")]
        assert len(bad) == 1
        assert "wip" in bad[0]["message"]

    def test_null_enum_skips(self, vault):
        tmp_path, router = vault
        # Wiki has no status_enum — any status value should be fine
        write_md(tmp_path / "Wiki" / "with-status.md",
                 {"type": "living/wiki", "tags": ["test"], "status": "anything"})
        findings = check.check_status_values(str(tmp_path), router)
        assert not any("with-status" in f.get("file", "") for f in findings)

    def test_no_status_field_skips(self, vault):
        tmp_path, router = vault
        # File without status field in a type that has enum — should skip, not error
        write_md(tmp_path / "Designs" / "no-status.md",
                 {"type": "living/design", "tags": ["design"]})
        findings = check.check_status_values(str(tmp_path), router)
        assert not any("no-status" in f.get("file", "") for f in findings)

    def test_archive_files_excluded(self, vault):
        tmp_path, router = vault
        archive = tmp_path / "Designs" / "_Archive"
        archive.mkdir()
        write_md(archive / "20260315-archived.md",
                 {"type": "living/design", "tags": ["design"], "status": "implemented"})
        findings = check.check_status_values(str(tmp_path), router)
        assert not any("_Archive" in f.get("file", "") for f in findings)


# ---------------------------------------------------------------------------
# TestCheckUnconfiguredType
# ---------------------------------------------------------------------------

class TestCheckUnconfiguredType:
    def test_unconfigured_emits_info(self, vault):
        tmp_path, router = vault
        findings = check.check_unconfigured_type(str(tmp_path), router)
        assert len(findings) == 1
        assert findings[0]["severity"] == "info"
        assert "Projects" in findings[0]["message"]

    def test_configured_does_not_emit(self, vault):
        tmp_path, router = vault
        findings = check.check_unconfigured_type(str(tmp_path), router)
        assert not any("Wiki" in f["message"] for f in findings)


# ---------------------------------------------------------------------------
# TestRunChecks
# ---------------------------------------------------------------------------

class TestRunChecks:
    def test_full_orchestration(self, vault):
        tmp_path, router = vault
        result = check.run_checks(str(tmp_path), router)
        assert result["brain_core_version"] == "0.9.11"
        assert "checked_at" in result
        assert "summary" in result
        assert "findings" in result
        # With the clean fixtures, should have 1 info (unconfigured Projects)
        assert result["summary"]["info"] >= 1

    def test_summary_counts_correct(self, vault):
        tmp_path, router = vault
        # Add violations
        (tmp_path / "readme.md").write_text("orphan\n")
        write_md(tmp_path / "_Temporal" / "Logs" / "2026-03" / "BAD NAME.md",
                 {"type": "temporal/log", "tags": ["log"]})
        result = check.run_checks(str(tmp_path), router)
        assert result["summary"]["errors"] >= 1   # root_files
        assert result["summary"]["warnings"] >= 1  # naming

    def test_missing_router_returns_error(self, tmp_path):
        (tmp_path / ".brain-core").mkdir()
        (tmp_path / ".brain-core" / "VERSION").write_text("0.9.11\n")
        result = check.run_checks(str(tmp_path))
        assert result["summary"]["errors"] == 1
        assert "router" in result["findings"][0]["check"]

    def test_with_loaded_router(self, vault):
        tmp_path, router = vault
        # Pass router directly — should not need to load from file
        result = check.run_checks(str(tmp_path), router)
        assert result["brain_core_version"] == "0.9.11"


# ---------------------------------------------------------------------------
# TestParseArgs
# ---------------------------------------------------------------------------

class TestParseArgs:
    def test_defaults(self):
        json_mode, actionable, severity, vault = check.parse_args(["check.py"])
        assert not json_mode
        assert not actionable
        assert severity is None
        assert vault is None

    def test_json_flag(self):
        json_mode, _, _, _ = check.parse_args(["check.py", "--json"])
        assert json_mode

    def test_actionable_flag(self):
        _, actionable, _, _ = check.parse_args(["check.py", "--actionable"])
        assert actionable

    def test_severity_filter(self):
        _, _, severity, _ = check.parse_args(["check.py", "--severity", "warning"])
        assert severity == "warning"

    def test_vault_flag(self):
        _, _, _, vault = check.parse_args(["check.py", "--vault", "/path/to/vault"])
        assert vault == "/path/to/vault"

    def test_combined_flags(self):
        json_mode, actionable, severity, vault = check.parse_args(
            ["check.py", "--json", "--actionable", "--severity", "error", "--vault", "/tmp/v"])
        assert json_mode
        assert actionable
        assert severity == "error"
        assert vault == "/tmp/v"


# ---------------------------------------------------------------------------
# TestOutput
# ---------------------------------------------------------------------------

class TestOutput:
    def test_json_output_valid(self, vault):
        tmp_path, router = vault
        result = check.run_checks(str(tmp_path), router)
        json_str = json.dumps(result, indent=2, ensure_ascii=False)
        parsed = json.loads(json_str)
        assert parsed["brain_core_version"] == "0.9.11"

    def test_findings_have_expected_keys(self, vault):
        tmp_path, router = vault
        (tmp_path / "readme.md").write_text("orphan\n")
        result = check.run_checks(str(tmp_path), router)
        for f in result["findings"]:
            assert "check" in f
            assert "severity" in f
            assert "message" in f
            # file can be None for folder-level checks


# ---------------------------------------------------------------------------
# TestCheckBrokenWikilinks
# ---------------------------------------------------------------------------

class TestCheckBrokenWikilinks:
    def test_valid_wikilinks_no_findings(self, vault):
        tmp_path, router = vault
        # rust-lifetimes.md already exists in the vault fixture
        write_md(tmp_path / "Wiki" / "linking-page.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "See [[rust-lifetimes]] for details.")
        findings = check.check_broken_wikilinks(str(tmp_path), router)
        broken = [f for f in findings if f["check"] == "broken_wikilinks"]
        assert broken == []

    def test_broken_link_detected(self, vault):
        tmp_path, router = vault
        write_md(tmp_path / "Wiki" / "has-broken-link.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "See [[nonexistent-page]] for details.")
        findings = check.check_broken_wikilinks(str(tmp_path), router)
        broken = [f for f in findings if f["check"] == "broken_wikilinks"]
        assert len(broken) >= 1
        assert any("nonexistent-page" in f["message"] for f in broken)

    def test_anchor_only_skipped(self, vault):
        tmp_path, router = vault
        write_md(tmp_path / "Wiki" / "self-ref.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "See [[#heading]] above.")
        findings = check.check_broken_wikilinks(str(tmp_path), router)
        broken = [f for f in findings if f["check"] == "broken_wikilinks"
                  and "self-ref.md" in f.get("file", "")]
        assert broken == []

    def test_link_with_anchor_resolves(self, vault):
        tmp_path, router = vault
        write_md(tmp_path / "Wiki" / "linking-anchor.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "See [[rust-lifetimes#section]] for details.")
        findings = check.check_broken_wikilinks(str(tmp_path), router)
        broken = [f for f in findings if f["check"] == "broken_wikilinks"
                  and "rust-lifetimes" in f["message"]]
        assert broken == []

    def test_link_with_alias_resolves(self, vault):
        tmp_path, router = vault
        write_md(tmp_path / "Wiki" / "linking-alias.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "See [[rust-lifetimes|Rust Ownership]] for details.")
        findings = check.check_broken_wikilinks(str(tmp_path), router)
        broken = [f for f in findings if f["check"] == "broken_wikilinks"
                  and "rust-lifetimes" in f["message"]]
        assert broken == []

    def test_embed_resolves(self, vault):
        tmp_path, router = vault
        assets = tmp_path / "_Assets"
        assets.mkdir(exist_ok=True)
        (assets / "photo.png").write_bytes(b"\x89PNG")
        write_md(tmp_path / "Wiki" / "with-image.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "Image: ![[photo.png]]")
        findings = check.check_broken_wikilinks(str(tmp_path), router)
        broken = [f for f in findings if f["check"] == "broken_wikilinks"
                  and "photo.png" in f["message"]]
        assert broken == []

    def test_embed_broken(self, vault):
        tmp_path, router = vault
        write_md(tmp_path / "Wiki" / "missing-image.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "Image: ![[missing.png]]")
        findings = check.check_broken_wikilinks(str(tmp_path), router)
        broken = [f for f in findings if f["check"] == "broken_wikilinks"
                  and "missing.png" in f["message"]]
        assert len(broken) == 1

    def test_template_placeholder_skipped(self, vault):
        tmp_path, router = vault
        write_md(tmp_path / "Wiki" / "with-template.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "Yesterday: [[{{yesterday}}]]")
        findings = check.check_broken_wikilinks(str(tmp_path), router)
        broken = [f for f in findings if f["check"] == "broken_wikilinks"
                  and "with-template.md" in f.get("file", "")]
        assert broken == []

    def test_case_insensitive(self, vault):
        tmp_path, router = vault
        write_md(tmp_path / "Wiki" / "case-test.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "See [[Rust-Lifetimes]] for details.")
        findings = check.check_broken_wikilinks(str(tmp_path), router)
        broken = [f for f in findings if f["check"] == "broken_wikilinks"
                  and "Rust-Lifetimes" in f["message"]]
        assert broken == []

    def test_path_qualified_resolves(self, vault):
        tmp_path, router = vault
        write_md(tmp_path / "Wiki" / "path-link.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "See [[Wiki/rust-lifetimes]] for details.")
        findings = check.check_broken_wikilinks(str(tmp_path), router)
        broken = [f for f in findings if f["check"] == "broken_wikilinks"
                  and "rust-lifetimes" in f["message"]]
        assert broken == []

    def test_code_block_ignored(self, vault):
        tmp_path, router = vault
        body = "Before\n\n```\n[[nonexistent-in-code]]\n```\n\nAfter"
        write_md(tmp_path / "Wiki" / "with-code.md",
                 {"type": "living/wiki", "tags": ["test"]}, body)
        findings = check.check_broken_wikilinks(str(tmp_path), router)
        broken = [f for f in findings if f["check"] == "broken_wikilinks"
                  and "nonexistent-in-code" in f["message"]]
        assert broken == []

    def test_ambiguous_link_flagged(self, vault):
        tmp_path, router = vault
        # Create a second file with same basename in different type folder
        write_md(tmp_path / "Designs" / "rust-lifetimes.md",
                 {"type": "living/design", "tags": ["design"], "status": "shaping"},
                 "# Rust Lifetimes Design")
        # Link using basename only
        write_md(tmp_path / "Wiki" / "ambig-link.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "See [[rust-lifetimes]] for details.")
        findings = check.check_broken_wikilinks(str(tmp_path), router)
        ambiguous = [f for f in findings if f["check"] == "ambiguous_wikilinks"
                     and "rust-lifetimes" in f["message"]]
        assert len(ambiguous) >= 1

    def test_ambiguous_path_qualified_not_flagged(self, vault):
        tmp_path, router = vault
        # Create duplicate basename
        write_md(tmp_path / "Designs" / "rust-lifetimes.md",
                 {"type": "living/design", "tags": ["design"], "status": "shaping"},
                 "# Rust Lifetimes Design")
        # Link using path-qualified form — unambiguous
        write_md(tmp_path / "Wiki" / "precise-link.md",
                 {"type": "living/wiki", "tags": ["test"]},
                 "See [[Wiki/rust-lifetimes]] for details.")
        findings = check.check_broken_wikilinks(str(tmp_path), router)
        ambiguous = [f for f in findings if f["check"] == "ambiguous_wikilinks"
                     and "precise-link.md" in f.get("file", "")]
        assert ambiguous == []

    def test_broken_wikilinks_skips_archive(self, vault):
        """Broken links inside _Archive/ files are not reported."""
        tmp_path, router = vault
        archive = tmp_path / "Wiki" / "_Archive"
        archive.mkdir(parents=True, exist_ok=True)
        write_md(archive / "20260101-old-page.md",
                 {"type": "living/wiki", "tags": [], "archiveddate": "2026-01-01"},
                 "See [[totally-nonexistent-target]] here.")
        findings = check.check_broken_wikilinks(str(tmp_path), router)
        broken = [f for f in findings if f["check"] == "broken_wikilinks"
                  and "totally-nonexistent-target" in f["message"]]
        assert broken == []


# ---------------------------------------------------------------------------
# TestCheckTaxonomyTypeConsistency
# ---------------------------------------------------------------------------

@pytest.fixture
def vault_cr(tmp_path):
    """Minimal vault with router.md and taxonomy files for compile_router tests."""
    # .brain-core/VERSION
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.19.4\n")
    (bc / "session-core.md").write_text("# Session Core\n")

    # _Config/router.md
    config = tmp_path / "_Config"
    config.mkdir()
    (config / "router.md").write_text(
        "Prefer MCP tools.\n\n"
        "Always:\n"
        "- Every artefact belongs in a typed folder.\n\n"
        "Conditional:\n"
        "- After meaningful work → [[_Config/Taxonomy/Temporal/logs]]\n"
    )

    # Living type: Ideas (plural key ending in 's', with singular type defined)
    (tmp_path / "Ideas").mkdir()
    tax_living = config / "Taxonomy" / "Living"
    tax_living.mkdir(parents=True)
    (tax_living / "ideas.md").write_text(
        "# Ideas\n\n"
        "## Naming\n\n"
        "`{name}.md` in `Ideas/`.\n\n"
        "## Frontmatter\n\n"
        "```yaml\n---\ntype: living/idea\ntags:\n  - idea\n---\n```\n\n"
    )

    # Temporal type: Logs
    temporal = tmp_path / "_Temporal"
    temporal.mkdir()
    (temporal / "Logs").mkdir()
    tax_temporal = config / "Taxonomy" / "Temporal"
    tax_temporal.mkdir(parents=True)
    (tax_temporal / "logs.md").write_text(
        "# Logs\n\n"
        "## Naming\n\n"
        "`yyyymmdd-log.md` in `_Temporal/Logs/yyyy-mm/`.\n\n"
        "## Frontmatter\n\n"
        "```yaml\n---\ntype: temporal/log\ntags:\n  - log\n---\n```\n\n"
    )

    return tmp_path


class TestCheckTaxonomyTypeConsistency:
    def test_no_finding_when_singular_differs(self, vault_cr):
        """Normal case: frontmatter_type is singular, type is plural — no finding."""
        router = cr.compile(vault_cr)
        findings = check.check_taxonomy_type_consistency(str(vault_cr), router)
        type_consistency = [f for f in findings if f["check"] == "taxonomy_type_consistency"]
        assert len(type_consistency) == 0

    def test_flags_when_frontmatter_type_equals_folder_type(self, vault_cr):
        """When taxonomy omits type: field, frontmatter_type falls back to plural — flag it."""
        (vault_cr / "Notes").mkdir()
        tax = vault_cr / "_Config" / "Taxonomy" / "Living"
        (tax / "Notes.md").write_text(
            "# Notes\n\n## Naming\n\n`{title}.md` in `Notes/`.\n\n"
            "## Frontmatter\n\n```yaml\n---\ntags:\n  - note\n---\n```\n"
        )
        router = cr.compile(vault_cr)
        findings = check.check_taxonomy_type_consistency(str(vault_cr), router)
        type_consistency = [f for f in findings if f["check"] == "taxonomy_type_consistency"]
        assert len(type_consistency) == 1
        assert "notes" in type_consistency[0]["message"]

    def test_no_finding_for_unconfigured(self, vault_cr):
        """Unconfigured artefacts (no taxonomy) should not be flagged."""
        (vault_cr / "Projects").mkdir()
        router = cr.compile(vault_cr)
        findings = check.check_taxonomy_type_consistency(str(vault_cr), router)
        type_consistency = [f for f in findings if f["check"] == "taxonomy_type_consistency"]
        projects_findings = [f for f in type_consistency if "projects" in f["message"]]
        assert len(projects_findings) == 0
