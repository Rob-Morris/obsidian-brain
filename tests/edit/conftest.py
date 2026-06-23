"""Shared fixtures for the edit test suite."""

import os
import re
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

import edit
from _common import file_index_from_documents, parse_frontmatter, validate_artefact_folder


@pytest.fixture
def vault(tmp_path):
    """Create a vault fixture with configured types and content."""
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.10.3\n")
    (bc / "session-core.md").write_text("# Session Core\n")

    config = tmp_path / "_Config"
    config.mkdir()
    (config / "router.md").write_text(
        "Prefer MCP tools.\n\nAlways:\n- Every artefact belongs in a typed folder.\n"
    )

    # Living type: Wiki
    wiki = tmp_path / "Wiki"
    wiki.mkdir()
    (wiki / "test-page.md").write_text(
        "---\ntype: living/wiki\ntags:\n  - brain-core\nstatus: active\n---\n\n"
        "# Test Page\n\nOriginal body.\n"
    )

    # Living type: Designs
    designs = tmp_path / "Designs"
    designs.mkdir()

    # Living type: Ideas
    ideas = tmp_path / "Ideas"
    ideas.mkdir()

    # Living type: Releases
    releases = tmp_path / "Releases"
    releases.mkdir()

    projects = tmp_path / "Projects"
    projects.mkdir()
    (projects / "Brain.md").write_text(
        "---\n"
        "type: living/project\n"
        "tags:\n"
        "  - project/brain\n"
        "key: brain\n"
        "---\n\n"
        "# Brain\n"
    )

    # Temporal type: Logs
    temporal = tmp_path / "_Temporal"
    temporal.mkdir()
    (temporal / "Logs").mkdir()
    (temporal / "Reports").mkdir()

    # Taxonomy: Wiki
    tax_living = config / "Taxonomy" / "Living"
    tax_living.mkdir(parents=True)
    (tax_living / "wiki.md").write_text(
        "# Wiki\n\n"
        "## Naming\n\n`{Title}.md` in `Wiki/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/wiki\ntags:\n  - topic-tag\n---\n```\n\n"
        "## Template\n\n[[_Config/Templates/Living/Wiki]]\n"
    )

    # Taxonomy: Designs (with multiple terminal statuses)
    (tax_living / "designs.md").write_text(
        "# Designs\n\n"
        "## Naming\n\n`{Title}.md` in `Designs/`.\n\n"
        "## Lifecycle\n\n"
        "| Status | Meaning |\n|---|---|\n"
        "| `shaping` | Being explored. |\n"
        "| `implemented` | Fully built. |\n"
        "| `superseded` | Replaced by a different approach. |\n"
        "| `rejected` | Declined. |\n\n"
        "## Terminal Status\n\n"
        "- set `status: implemented`, move to `Designs/+Implemented/`\n"
        "- set `status: superseded`, move to `Designs/+Superseded/`\n"
        "- set `status: rejected`, move to `Designs/+Rejected/`\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/designs\ntags:\n  - design-tag\n"
        "status: shaping             # shaping | implemented | superseded | rejected\n---\n```\n\n"
        "## Template\n\n[[_Config/Templates/Living/Designs]]\n"
    )

    # Taxonomy: Ideas (with terminal status)
    (tax_living / "ideas.md").write_text(
        "# Ideas\n\n"
        "## Naming\n\n`{Title}.md` in `Ideas/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/ideas\ntags: []\nstatus: seed\n---\n```\n\n"
        "Status values: `seed`, `shaping`, `adopted`.\n\n"
        "## Terminal Status\n\nWhen an idea reaches `adopted` status, it moves to `+Adopted/`.\n\n"
        "## Template\n\n[[_Config/Templates/Living/Ideas]]\n"
    )

    # Taxonomy: Releases (with two terminal statuses)
    (tax_living / "releases.md").write_text(
        "# Releases\n\n"
        "## Naming\n\n"
        "Primary folder: `Releases/{scope}/`.\n\n"
        "### Rules\n\n"
        "| Match field | Match values | Pattern |\n"
        "|---|---|---|\n"
        "| `status` | `planned`, `active`, `cancelled` | `{Title}.md` |\n"
        "| `status` | `shipped` | `{Version} - {Title}.md` |\n\n"
        "### Placeholders\n\n"
        "| Placeholder | Field | Required when field | Required values | Regex |\n"
        "|---|---|---|---|---|\n"
        "| `Version` | `version` | `status` | `shipped` | `^v?\\d+\\.\\d+\\.\\d+$` |\n\n"
        "## Lifecycle\n\n"
        "| Status | Meaning |\n|---|---|\n"
        "| `planned` | Scoped. |\n"
        "| `active` | In progress. |\n"
        "| `shipped` | Released. |\n"
        "| `cancelled` | Stopped. |\n\n"
        "## Terminal Status\n\n"
        "When a release reaches `shipped` status, move to `+Shipped/` within its current ownership context.\n"
        "Set `status: shipped` before the move.\n"
        "When a release reaches `cancelled` status, move to `+Cancelled/` within its current ownership context.\n"
        "Set `status: cancelled` before the move.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/release\ntags:\n  - release\n"
        "status: planned\nversion:\ntag:\ncommit:\nshipped:\n---\n```\n\n"
        "## Template\n\n[[_Config/Templates/Living/Releases]]\n"
    )

    (tax_living / "projects.md").write_text(
        "# Projects\n\n"
        "## Naming\n\n`{Title}.md` in `Projects/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/project\ntags:\n  - project\nkey:\n---\n```\n\n"
        "## Template\n\n[[_Config/Templates/Living/Projects]]\n"
    )

    # Taxonomy: Logs
    tax_temporal = config / "Taxonomy" / "Temporal"
    tax_temporal.mkdir(parents=True)
    (tax_temporal / "logs.md").write_text(
        "# Logs\n\n"
        "## Naming\n\n`log-{Title}.md` in `_Temporal/Logs/yyyy-mm/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: temporal/logs\ntags:\n  - session\n---\n```\n"
    )
    (tax_temporal / "reports.md").write_text(
        "# Reports\n\n"
        "## Naming\n\n`yyyymmdd-report~{Title}.md` in `_Temporal/Reports/yyyy-mm/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: temporal/reports\ntags:\n  - report\n---\n```\n"
    )

    # Taxonomy: Research
    (temporal / "Research").mkdir(exist_ok=True)
    (tax_temporal / "research.md").write_text(
        "# Research\n\n"
        "## Naming\n\n`yyyymmdd-research~{Title}.md` in `_Temporal/Research/yyyy-mm/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: temporal/research\ntags:\n  - research\n---\n```\n"
    )

    # Taxonomy: Reports
    (temporal / "Reports").mkdir(exist_ok=True)
    (tax_temporal / "reports.md").write_text(
        "# Reports\n\n"
        "## Naming\n\n`yyyymmdd-report~{Title}.md` in `_Temporal/Reports/yyyy-mm/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: temporal/reports\ntags:\n  - report\n---\n```\n"
    )

    # Templates
    templates_living = config / "Templates" / "Living"
    templates_living.mkdir(parents=True)
    (templates_living / "Wiki.md").write_text(
        "---\ntype: living/wiki\ntags: []\n---\n\n# {{title}}\n\n"
    )
    (templates_living / "Designs.md").write_text(
        "---\ntype: living/designs\ntags: []\nstatus: shaping\n---\n\n# {{title}}\n\n"
    )
    (templates_living / "Releases.md").write_text(
        "---\ntype: living/release\ntags:\n  - release\nstatus: planned\nversion:\ntag:\ncommit:\nshipped:\n---\n\n"
        "## Goal\n\n"
        "## Acceptance Criteria\n\n| Criterion | Status |\n|---|---|\n|  | pending |\n\n"
        "## Designs In Scope\n\n- \n\n"
        "## Release Notes\n\n"
        "## Sources\n\n- \n"
    )
    (templates_living / "Projects.md").write_text(
        "---\ntype: living/project\ntags: []\nkey:\n---\n\n# {{title}}\n\n"
    )

    return tmp_path


@pytest.fixture
def router(vault):
    """Compile the router for the vault fixture."""
    import compile_router
    return compile_router.compile(str(vault))

