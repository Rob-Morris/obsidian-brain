"""Tests for migrations/migrate_to_0_34_0.py — release artefact alignment."""

from __future__ import annotations

import json

import pytest

import compile_router
import migrate_to_0_34_0
from _common import parse_frontmatter


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


@pytest.fixture
def vault(tmp_path):
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.33.0\n")
    (bc / "session-core.md").write_text("# Session Core\n")

    config = tmp_path / "_Config"
    config.mkdir()
    (config / "router.md").write_text(
        "Prefer MCP tools.\n\nAlways:\n- Every artefact belongs in a typed folder.\n"
    )

    (tmp_path / "Projects").mkdir()
    (tmp_path / "Releases").mkdir()

    tax_living = config / "Taxonomy" / "Living"
    tax_living.mkdir(parents=True)
    (tax_living / "projects.md").write_text(
        "# Projects\n\n"
        "## Naming\n\n`{Title}.md` in `Projects/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/project\nkey:\ntags:\n  - project/{key}\n---\n```\n"
    )
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
        "When a release reaches `shipped` status, move it to `+Shipped/` within its current ownership context.\n"
        "When a release reaches `cancelled` status, move it to `+Cancelled/` within its current ownership context.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/release\ntags:\n  - release\nstatus: planned\nversion:\ntag:\ncommit:\nshipped:\n---\n```\n"
    )

    project = (
        "---\n"
        "type: living/project\n"
        "tags:\n"
        "  - project/brain\n"
        "key: brain\n"
        "---\n\n"
        "# Brain\n"
    )
    _write(tmp_path / "Projects" / "Brain.md", project)

    legacy_folder_release = (
        "---\n"
        "type: living/release\n"
        "tags:\n"
        "  - release\n"
        "  - project/brain\n"
        "key: operational-maturity\n"
        "parent: project/brain\n"
        "status: active\n"
        "version: v0.28.6\n"
        "tag:\n"
        "commit:\n"
        "shipped:\n"
        "---\n\n"
        "**Project:** [[PROJECT]]\n\n"
        "## Goal\n\nShip the milestone.\n\n"
        "## Gates\n\n"
        "| Gate | Status | Implicated Designs |\n"
        "|---|---|---|\n"
        "| Search is stable | pending | [[Brain Search]] |\n"
        "| Docs refreshed | met | [[Brain Search]], [[Docs Refresh]] |\n\n"
        "## Changelog\n\n"
        "### Changed\n\n"
        "- Search pipeline stabilised.\n\n"
        "## Sources\n\n"
        "- [[Brain Search]]\n"
    )
    _write(
        tmp_path / "Releases" / "project~brain" / "v0.28.6 - Operational Maturity.md",
        legacy_folder_release,
    )

    missing_parent_tag_release = (
        "---\n"
        "type: living/release\n"
        "tags:\n"
        "  - release\n"
        "key: missing-parent-tag\n"
        "parent: project/brain\n"
        "status: planned\n"
        "version:\n"
        "tag:\n"
        "commit:\n"
        "shipped:\n"
        "---\n\n"
        "## Goal\n\nAlready parented.\n\n"
        "## Acceptance Criteria\n\n"
        "| Criterion | Status |\n"
        "|---|---|\n"
        "| Keep parent tag in sync | pending |\n\n"
        "## Designs In Scope\n\n"
        "- [[Brain Search]]\n\n"
        "## Release Notes\n\n"
        "- None yet.\n\n"
        "## Sources\n\n"
        "- [[Brain Search]]\n"
    )
    _write(
        tmp_path / "Releases" / "project~brain" / "Missing Parent Tag.md",
        missing_parent_tag_release,
    )

    local = tmp_path / ".brain" / "local"
    local.mkdir(parents=True, exist_ok=True)
    (local / "compiled-router.json").write_text(
        json.dumps(compile_router.compile(str(tmp_path)), indent=2) + "\n"
    )

    return tmp_path


def test_normalises_parented_release_structure_and_filename(vault):
    router = compile_router.compile(str(vault))
    result = migrate_to_0_34_0.backfill_vault(str(vault), router=router, dry_run=False)

    assert result["status"] == "ok"
    path = vault / "Releases" / "project~brain" / "Operational Maturity.md"
    fields, body = parse_frontmatter(path.read_text())

    assert fields["parent"] == "project/brain"
    assert "**Project:** [[PROJECT]]" not in body
    assert "## Acceptance Criteria" in body
    assert "## Designs In Scope" in body
    assert "## Release Notes" in body
    assert "## Gates" not in body
    assert "## Changelog" not in body
    assert "| Criterion | Status |" in body
    assert '- [[Brain Search]] — _todo: release role_ (legacy criteria: "Search is stable"; "Docs refreshed")' in body
    assert '- [[Docs Refresh]] — _todo: release role_ (legacy criterion: "Docs refreshed")' in body
    assert "### Changed" in body
    assert not (vault / "Releases" / "project~brain" / "v0.28.6 - Operational Maturity.md").exists()


def test_halts_when_release_is_unparented(vault):
    unparented = (
        "---\n"
        "type: living/release\n"
        "tags:\n"
        "  - release\n"
        "key: tagged-release\n"
        "status: planned\n"
        "version: v0.10.0\n"
        "---\n\n"
        "## Goal\n\nNo owner set.\n"
    )
    _write(vault / "Releases" / "v0.10.0 - Tagged Release.md", unparented)

    router = compile_router.compile(str(vault))
    with pytest.raises(ValueError, match=r"no `parent:` set"):
        migrate_to_0_34_0.backfill_vault(str(vault), router=router, dry_run=False)
    assert (vault / "Releases" / "v0.10.0 - Tagged Release.md").is_file()


def test_backfills_missing_parent_tag_when_parent_already_set(vault):
    router = compile_router.compile(str(vault))
    result = migrate_to_0_34_0.backfill_vault(str(vault), router=router, dry_run=False)

    assert result["status"] == "ok"
    path = vault / "Releases" / "project~brain" / "Missing Parent Tag.md"
    fields, _body = parse_frontmatter(path.read_text())
    assert "project/brain" in fields["tags"]


def test_idempotent(vault):
    router = compile_router.compile(str(vault))
    migrate_to_0_34_0.backfill_vault(str(vault), router=router, dry_run=False)

    second_router = compile_router.compile(str(vault))
    second = migrate_to_0_34_0.backfill_vault(str(vault), router=second_router, dry_run=False)

    assert second["status"] == "skipped"
    assert second["updated"] == 0
