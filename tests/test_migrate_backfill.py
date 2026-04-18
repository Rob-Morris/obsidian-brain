"""Tests for migrations/migrate_to_0_29_0.py — v0.29.0 backfill.

Covers the core behaviours from the plan:
    - `created` / `modified` backfilled from filename prefix / mtime
    - type-specific `date_source` backfilled (daily-notes `date`, writing `publisheddate`)
    - writing `status` inferred from `publisheddate` presence
    - temporal artefact relocated when `created` month differs from folder
    - filename renamed when the rule selected for the reconciled state renders a new name
    - idempotent — second run is a no-op
"""

from __future__ import annotations

import os

import pytest

import compile_router
import migrate_to_0_29_0


# ---------------------------------------------------------------------------
# Seeded vault fixture — taxonomies exercise the three living types with
# date_source plus a simple temporal type.
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path):
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.28.8\n")
    (bc / "session-core.md").write_text("# Session Core\n")

    config = tmp_path / "_Config"
    config.mkdir()
    (config / "router.md").write_text(
        "Prefer MCP tools.\n\nAlways:\n- Every artefact belongs in a typed folder.\n"
    )

    # Living: daily-notes — `date` field, `yyyy-mm-dd ddd.md`
    daily_dir = tmp_path / "Daily Notes"
    daily_dir.mkdir()
    (daily_dir / "2026-03-15 Sun.md").write_text(
        "---\ntype: living/daily-note\ntags:\n  - daily-note\n---\n\n# Sunday\n"
    )

    # Living: writing — published piece without status field
    writing_dir = tmp_path / "Writing"
    writing_dir.mkdir()
    (writing_dir / "+Published").mkdir()
    (writing_dir / "+Published" / "20260310-On Tool Use.md").write_text(
        "---\ntype: living/writing\ntags:\n  - writing\n"
        "publisheddate: 2026-03-10\n---\n\n# On Tool Use\n"
    )
    # Living: writing — draft (no publisheddate, no status)
    (writing_dir / "Another Draft.md").write_text(
        "---\ntype: living/writing\ntags:\n  - writing\n---\n\n# Another Draft\n"
    )

    # Temporal: logs — file in wrong month folder relative to its filename date
    temporal = tmp_path / "_Temporal"
    temporal.mkdir()
    wrong_month = temporal / "Logs" / "2026-02"
    wrong_month.mkdir(parents=True)
    (wrong_month / "20260310-log.md").write_text(
        "---\ntype: temporal/logs\ntags:\n  - log\n---\n\n# Log\n"
    )

    # Temporal: log in correct month folder (should end already_clean)
    correct_month = temporal / "Logs" / "2026-03"
    correct_month.mkdir(parents=True)
    (correct_month / "20260305-log.md").write_text(
        "---\ntype: temporal/logs\ntags:\n  - log\n"
        "created: 2026-03-05T09:00:00+11:00\n"
        "modified: 2026-03-05T09:00:00+11:00\n---\n\n# Log\n"
    )

    # --- Taxonomies ---
    tax_living = config / "Taxonomy" / "Living"
    tax_living.mkdir(parents=True)

    (tax_living / "daily-notes.md").write_text(
        "# Daily Notes\n\n"
        "## Naming\n\n`yyyy-mm-dd ddd.md` in `Daily Notes/`, date source `date`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/daily-note\ntags:\n  - daily-note\ndate:\n---\n```\n"
    )

    (tax_living / "writing.md").write_text(
        "# Writing\n\n"
        "## Lifecycle\n\n"
        "| Status | Meaning |\n"
        "|---|---|\n"
        "| `draft` | wip |\n"
        "| `published` | out |\n\n"
        "## Naming\n\n"
        "Primary folder: `Writing/`.\n\n"
        "### Rules\n\n"
        "| Match field | Match values | Pattern | Date source |\n"
        "|---|---|---|---|\n"
        "| `status` | `draft` | `{Title}.md` |  |\n"
        "| `status` | `published` | `yyyymmdd-{Title}.md` | `publisheddate` |\n\n"
        "## On Status Change\n\n"
        "When `status` transitions to `published`, set `publisheddate` to today (if not already present).\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/writing\ntags:\n  - writing\nstatus: draft\n---\n```\n"
    )

    tax_temporal = config / "Taxonomy" / "Temporal"
    tax_temporal.mkdir(parents=True)
    (tax_temporal / "logs.md").write_text(
        "# Logs\n\n"
        "## Naming\n\n`yyyymmdd-log.md` in `_Temporal/Logs/yyyy-mm/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: temporal/logs\ntags:\n  - log\n---\n```\n"
    )

    return tmp_path


@pytest.fixture
def router(vault):
    return compile_router.compile(str(vault))


# ---------------------------------------------------------------------------
# backfill_vault behaviour
# ---------------------------------------------------------------------------

class TestBackfill:
    def test_populates_timestamps_on_legacy_file(self, vault, router):
        result = migrate_to_0_29_0.backfill_vault(str(vault), router=router, dry_run=False)
        assert result["status"] == "ok"

        daily = (vault / "Daily Notes" / "2026-03-15 Sun.md").read_text()
        assert "created:" in daily
        assert "modified:" in daily
        # date_source backfilled from filename prefix
        assert "date: 2026-03-15" in daily

    def test_infers_writing_status_from_publisheddate(self, vault, router):
        migrate_to_0_29_0.backfill_vault(str(vault), router=router, dry_run=False)
        published = (vault / "Writing" / "+Published" / "20260310-On Tool Use.md").read_text()
        assert "status: published" in published

        draft = (vault / "Writing" / "Another Draft.md").read_text()
        assert "status: draft" in draft

    def test_relocates_temporal_across_month_boundary(self, vault, router):
        migrate_to_0_29_0.backfill_vault(str(vault), router=router, dry_run=False)
        moved = vault / "_Temporal" / "Logs" / "2026-03" / "20260310-log.md"
        assert moved.is_file()
        assert not (vault / "_Temporal" / "Logs" / "2026-02" / "20260310-log.md").exists()

    def test_dry_run_leaves_files_untouched(self, vault, router):
        before = (vault / "Daily Notes" / "2026-03-15 Sun.md").read_text()
        result = migrate_to_0_29_0.backfill_vault(str(vault), router=router, dry_run=True)
        assert result["dry_run"] is True
        after = (vault / "Daily Notes" / "2026-03-15 Sun.md").read_text()
        assert before == after

    def test_already_clean_file_is_noop(self, vault, router):
        result = migrate_to_0_29_0.backfill_vault(str(vault), router=router, dry_run=False)
        counts = result["counts"]
        # The pre-seeded 2026-03/20260305-log.md had both timestamps — it should
        # count as already_clean on first run.
        assert counts["already_clean"] >= 1

    def test_idempotent(self, vault, router):
        migrate_to_0_29_0.backfill_vault(str(vault), router=router, dry_run=False)
        second = migrate_to_0_29_0.backfill_vault(str(vault), router=router, dry_run=False)
        # After the first pass everything is reconciled; a second run should
        # not touch any frontmatter or rename anything.
        assert second["counts"]["frontmatter_backfilled"] == 0
        assert second["counts"]["renamed"] == 0
        assert second["counts"]["relocated"] == 0


class TestMigrateEntry:
    def test_migrate_returns_ok_with_actions(self, vault):
        # migrate() reads the compiled router from disk, matching the real
        # upgrade.py flow where compile validation runs before migrations.
        import json
        local = vault / ".brain" / "local"
        local.mkdir(parents=True, exist_ok=True)
        (local / "compiled-router.json").write_text(
            json.dumps(compile_router.compile(str(vault)))
        )
        result = migrate_to_0_29_0.migrate(str(vault))
        assert result["status"] == "ok"
        assert result["actions"], "migrate should record at least one action"
