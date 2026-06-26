"""Shared fixtures for the check test suite."""

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


@pytest.fixture
def vault(tmp_path):
    """Vault fixture with compiled router and test files."""
    # .brain-core/VERSION
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.9.11\n")
    (bc / "session-core.md").write_text("# Session Core\n")

    # Wiki — living, configured, {Title}.md
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

    # Releases — living, configured, requires a canonical parent (any owner type)
    releases_art = {
        "folder": "Releases", "type": "living/releases", "key": "releases",
        "classification": "living", "configured": True,
        "path": "Releases",
        "naming": {"pattern": "{name}.md", "folder": "Releases/"},
        "frontmatter": {
            "type": "living/release",
            "required": ["type", "tags", "status", "parent"],
            "status_enum": ["planned", "active", "shipped", "cancelled"],
            "terminal_statuses": ["shipped", "cancelled"],
        },
        "taxonomy_file": "_Config/Taxonomy/Living/releases.md",
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
            "status_enum": ["draft", "shaping", "approved", "implementing", "completed", "superseded", "parked", "rejected"],
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

    artefacts = [wiki_art, designs_art, notes_art, daily_art, releases_art, logs_art,
                 plans_art, shaping_art, cookies_art, projects_art]

    router = make_router(artefacts)
    router["artefact_index"] = {
        "wiki/rust-lifetimes": {
            "path": "Wiki/rust-lifetimes.md",
            "type": "living/wiki",
            "type_key": "wiki",
            "type_prefix": "wiki",
            "key": "rust-lifetimes",
            "parent": None,
            "children_count": 0,
        },
        "design/auth-redesign": {
            "path": "Designs/auth-redesign.md",
            "type": "living/design",
            "type_key": "designs",
            "type_prefix": "design",
            "key": "auth-redesign",
            "parent": None,
            "children_count": 0,
        },
        "note/rust-lifetimes": {
            "path": "Notes/20260315 - Rust Lifetimes.md",
            "type": "living/note",
            "type_key": "notes",
            "type_prefix": "note",
            "key": "rust-lifetimes",
            "parent": None,
            "children_count": 0,
        },
        "daily-note/2026-03-15-sat": {
            "path": "Daily Notes/2026-03-15 Sat.md",
            "type": "living/daily-note",
            "type_key": "daily-notes",
            "type_prefix": "daily-note",
            "key": "2026-03-15-sat",
            "parent": None,
            "children_count": 0,
        },
        "project/brain": {
            "path": "Projects/Brain.md",
            "type": "living/project",
            "type_key": "projects",
            "type_prefix": "project",
            "key": "brain",
            "parent": None,
            "children_count": 1,
        },
    }

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
    (tmp_path / "Releases").mkdir()
    (tmp_path / "_Temporal" / "Logs" / "2026-03").mkdir(parents=True)
    (tmp_path / "_Temporal" / "Plans" / "2026-03").mkdir(parents=True)
    (tmp_path / "_Temporal" / "Shaping Transcripts" / "2026-03").mkdir(parents=True)
    (tmp_path / "_Temporal" / "Cookies" / "2026-03").mkdir(parents=True)

    # --- Good files ---
    write_md(tmp_path / "Wiki" / "rust-lifetimes.md",
             {"type": "living/wiki", "tags": ["rust"], "key": "rust-lifetimes"}, "# Rust Lifetimes")
    write_md(tmp_path / "Designs" / "auth-redesign.md",
             {"type": "living/design", "tags": ["design"], "status": "shaping", "key": "auth-redesign"},
             "# Auth Redesign")
    write_md(tmp_path / "Notes" / "20260315 - Rust Lifetimes.md",
             {"type": "living/note", "tags": ["rust"], "key": "rust-lifetimes"}, "# Rust Lifetimes")
    write_md(tmp_path / "Daily Notes" / "2026-03-15 Sat.md",
             {"type": "living/daily-note", "tags": ["daily-note"], "key": "2026-03-15-sat"}, "# Saturday")
    write_md(tmp_path / "Projects" / "Brain.md",
             {"type": "living/project", "tags": ["project/brain"], "key": "brain"}, "# Brain")
    write_md(tmp_path / "_Temporal" / "Logs" / "2026-03" / "20260315-log.md",
             {"type": "temporal/log", "tags": ["log"]}, "09:00 Started work.")
    write_md(tmp_path / "_Temporal" / "Shaping Transcripts" / "2026-03" /
             "20260315-shaping-transcript~Auth.md",
             {"type": "temporal/shaping-transcript", "tags": ["transcript"]}, "Q. What?")
    write_md(tmp_path / "_Temporal" / "Cookies" / "2026-03" /
             "20260315-cookie~Great Refactor.md",
             {"type": "temporal/cookie", "tags": ["cookie"]}, "# Cookie")

    return tmp_path, router


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
