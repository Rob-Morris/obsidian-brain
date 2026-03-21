"""Tests for check.py — router-driven vault compliance checker."""

import json
import os

import pytest

import check


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def write_md(path, frontmatter_fields=None, body=""):
    """Write a markdown file with optional frontmatter."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if frontmatter_fields:
        lines.append("---")
        for k, v in frontmatter_fields.items():
            if isinstance(v, list):
                lines.append(f"{k}:")
                for item in v:
                    lines.append(f"  - {item}")
            else:
                lines.append(f"{k}: {v}")
        lines.append("---")
    lines.append(body)
    path.write_text("\n".join(lines) + "\n")


def make_router(artefacts, meta=None):
    """Build a minimal compiled router dict."""
    if meta is None:
        meta = {"brain_core_version": "0.9.11"}
    return {"meta": meta, "artefacts": artefacts}


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

    # Logs — temporal, configured, log--yyyy-mm-dd.md
    logs_art = {
        "folder": "Logs", "type": "temporal/log", "key": "logs",
        "classification": "temporal", "configured": True,
        "path": os.path.join("_Temporal", "Logs"),
        "naming": {"pattern": "log--yyyy-mm-dd.md", "folder": "_Temporal/Logs/yyyy-mm/"},
        "frontmatter": {
            "type": "temporal/log",
            "required": ["type", "tags"],
            "status_enum": None,
            "terminal_statuses": None,
        },
        "taxonomy_file": "_Config/Taxonomy/Temporal/logs.md",
        "template_file": None, "trigger": None,
    }

    # Plans — temporal, configured, yyyymmdd-{slug}.md
    plans_art = {
        "folder": "Plans", "type": "temporal/plan", "key": "plans",
        "classification": "temporal", "configured": True,
        "path": os.path.join("_Temporal", "Plans"),
        "naming": {"pattern": "yyyymmdd-{slug}.md", "folder": "_Temporal/Plans/yyyy-mm/"},
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
        "naming": {"pattern": "yyyymmdd-{sourcedoctype}-transcript--{slug}.md",
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
        "naming": {"pattern": "yyyymmdd-cookie--{slug}.md",
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
    config = tmp_path / "_Config"
    config.mkdir(parents=True, exist_ok=True)
    (config / ".compiled-router.json").write_text(
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
    write_md(tmp_path / "_Temporal" / "Logs" / "2026-03" / "log--2026-03-15.md",
             {"type": "temporal/log", "tags": ["log"]}, "09:00 Started work.")
    write_md(tmp_path / "_Temporal" / "Shaping Transcripts" / "2026-03" /
             "20260315-design-transcript--auth.md",
             {"type": "temporal/shaping-transcript", "tags": ["transcript"]}, "Q. What?")
    write_md(tmp_path / "_Temporal" / "Cookies" / "2026-03" /
             "20260315-cookie--great-refactor.md",
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
        assert not r.match("My Page.md")
        assert not r.match("UPPER.md")

    def test_yyyymmdd_slug_pattern(self):
        r = check.naming_pattern_to_regex("yyyymmdd-{slug}.md")
        assert r.match("20260315-auth-redesign.md")
        assert not r.match("auth-redesign.md")
        assert not r.match("2026-03-15-auth.md")

    def test_log_pattern(self):
        r = check.naming_pattern_to_regex("log--yyyy-mm-dd.md")
        assert r.match("log--2026-03-15.md")
        assert not r.match("log-2026-03-15.md")
        assert not r.match("log--20260315.md")

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
        r = check.naming_pattern_to_regex("yyyymmdd-{sourcedoctype}-transcript--{slug}.md")
        assert r.match("20260315-design-transcript--auth.md")
        assert r.match("20260315-project-transcript--my-thing.md")
        assert not r.match("20260315-Design-transcript--auth.md")

    def test_cookie_pattern(self):
        r = check.naming_pattern_to_regex("yyyymmdd-cookie--{slug}.md")
        assert r.match("20260315-cookie--great-refactor.md")
        assert not r.match("20260315-cookie-great-refactor.md")

    def test_idea_log_pattern(self):
        r = check.naming_pattern_to_regex("yyyymmdd-idea-log--{slug}.md")
        assert r.match("20260315-idea-log--shared-validation.md")

    def test_categorised_slug_pattern(self):
        """Slugs with -- separators (e.g. gap-assessment--brain-inbox)."""
        r = check.naming_pattern_to_regex("yyyymmdd-{slug}.md")
        assert r.match("20260317-gap-assessment--brain-inbox.md")
        assert r.match("20260317-plan--archive-convention.md")
        assert r.match("20260317-simple-slug.md")
        assert not r.match("20260317-.md")

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
        (tmp_path / "_Attachments").mkdir(exist_ok=True)
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
