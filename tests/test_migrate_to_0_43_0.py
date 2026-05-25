"""Tests for migrations/migrate_to_0_43_0.py — unified closure-status vocabulary."""

from __future__ import annotations

import pytest

import compile_router
import migrate_to_0_43_0
from _common import parse_frontmatter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path, fields, body=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for k, v in fields.items():
        if isinstance(v, list):
            if v:
                lines.append(f"{k}:")
                for item in v:
                    lines.append(f"  - {item}")
            else:
                lines.append(f"{k}: []")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    path.write_text("\n".join(lines) + "\n")


def _taxonomy(root, classification, folder, frontmatter_type, status_enum, terminal_statuses):
    subdir = "Living" if classification == "living" else "Temporal"
    path = root / "_Config" / "Taxonomy" / subdir / f"{folder.lower()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    terminal_line = (
        f"\n## Terminal Status\n\nWhen a {folder.rstrip('s').lower()} reaches a terminal status "
        f"({', '.join(f'`{s}`' for s in terminal_statuses)}), move it to the corresponding `+Status` folder.\n"
        if terminal_statuses else ""
    )
    enum_line = " | ".join(f"`{s}`" for s in status_enum)
    path.write_text(
        f"# {folder}\n\n"
        f"## Naming\n\n`{{Title}}.md` in `{folder}/`.\n\n"
        f"## Lifecycle\n\n"
        f"| Status | Meaning |\n|---|---|\n"
        + "".join(f"| `{s}` | {s} |\n" for s in status_enum) +
        f"\nStatus values: {enum_line}.\n"
        + terminal_line +
        f"\n## Frontmatter\n\n"
        f"```yaml\n---\ntype: {frontmatter_type}\ntags: []\nstatus: {status_enum[0]}\n---\n```\n"
    )


# ---------------------------------------------------------------------------
# Vault fixture with new-model taxonomies
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path):
    """Minimal vault with new-model taxonomies for designs, plans, releases, tasks."""
    (tmp_path / ".brain-core").mkdir()
    (tmp_path / ".brain-core" / "VERSION").write_text("0.43.0\n")
    (tmp_path / ".brain-core" / "session-core.md").write_text("# Session Core\n")

    (tmp_path / "_Config").mkdir()
    (tmp_path / "_Config" / "router.md").write_text(
        "Prefer MCP tools.\n\nAlways:\n- Every artefact belongs in a typed folder.\n"
    )

    (tmp_path / "Designs").mkdir()
    (tmp_path / "Releases").mkdir()
    (tmp_path / "Tasks").mkdir()
    (tmp_path / "_Temporal" / "Plans").mkdir(parents=True)

    _taxonomy(tmp_path, "living", "Designs", "living/design",
              ["proposed", "shaping", "ready", "active", "implemented", "deprecated", "parked"],
              ["implemented", "deprecated"])
    _taxonomy(tmp_path, "temporal", "Plans", "temporal/plan",
              ["draft", "shaping", "approved", "implementing", "completed", "deprecated", "parked"],
              [])
    _taxonomy(tmp_path, "living", "Releases", "living/release",
              ["planned", "active", "shipped", "deprecated"],
              ["shipped", "deprecated"])
    _taxonomy(tmp_path, "living", "Tasks", "living/task",
              ["open", "shaping", "in-progress", "done", "parked", "deprecated"],
              ["done", "deprecated"])

    return tmp_path


@pytest.fixture
def router(vault):
    return compile_router.compile(str(vault))


def _read_status(path):
    fields, _ = parse_frontmatter(path.read_text())
    return fields.get("status")


# ---------------------------------------------------------------------------
# Status rewrite tests
# ---------------------------------------------------------------------------

class TestStatusRewrites:
    def test_design_superseded_to_deprecated(self, vault, router):
        src = vault / "Designs" / "+Superseded" / "Old Design.md"
        _write(src, {"type": "living/design", "tags": ["design"], "status": "superseded"},
               body="> [!info] Superseded\n> This design was superseded by [[New Design]]. See that design for the current approach.\n\n## Goal\n\nOriginal text.\n")
        result = migrate_to_0_43_0.backfill_vault(str(vault), router=router)
        assert result["status"] == "ok"
        moved = vault / "Designs" / "+Deprecated" / "Old Design.md"
        assert moved.exists()
        assert not src.exists()
        assert _read_status(moved) == "deprecated"
        content = moved.read_text()
        assert "> [!info] Deprecated — superseded by [[New Design]]" in content
        assert "Superseded" not in content.split("##")[0].split("Deprecated")[0]

    def test_design_rejected_to_deprecated(self, vault, router):
        src = vault / "Designs" / "+Rejected" / "Bad Design.md"
        _write(src, {"type": "living/design", "tags": ["design"], "status": "rejected"},
               body="## Goal\n\nDescription of declined design.\n")
        migrate_to_0_43_0.backfill_vault(str(vault), router=router)
        moved = vault / "Designs" / "+Deprecated" / "Bad Design.md"
        assert moved.exists()
        assert _read_status(moved) == "deprecated"
        assert "> [!info] Deprecated — rejected" in moved.read_text()

    def test_plan_superseded_to_deprecated(self, vault, router):
        src = vault / "_Temporal" / "Plans" / "20260101-plan~Old Plan.md"
        _write(src, {"type": "temporal/plan", "tags": ["plan"], "status": "superseded"},
               body="**Origin:** Replaced by another plan.\n\n## Goal\n\nText.\n")
        migrate_to_0_43_0.backfill_vault(str(vault), router=router)
        assert _read_status(src) == "deprecated"
        assert "> [!info] Deprecated — superseded" in src.read_text()

    def test_plan_rejected_to_deprecated(self, vault, router):
        src = vault / "_Temporal" / "Plans" / "20260102-plan~Bad Plan.md"
        _write(src, {"type": "temporal/plan", "tags": ["plan"], "status": "rejected"},
               body="## Goal\n\nTried this, didn't fit.\n")
        migrate_to_0_43_0.backfill_vault(str(vault), router=router)
        assert _read_status(src) == "deprecated"
        assert "> [!info] Deprecated — rejected" in src.read_text()

    def test_release_cancelled_to_deprecated(self, vault, router):
        src = vault / "Releases" / "+Cancelled" / "Aborted Release.md"
        _write(src, {"type": "living/release", "tags": ["release"], "status": "cancelled", "parent": "project/x"},
               body="## Goal\n\nMilestone that got cut.\n")
        migrate_to_0_43_0.backfill_vault(str(vault), router=router)
        moved = vault / "Releases" / "+Deprecated" / "Aborted Release.md"
        assert moved.exists()
        assert _read_status(moved) == "deprecated"
        assert "> [!info] Deprecated — cancelled" in moved.read_text()

    def test_task_blocked_to_parked_no_callout(self, vault, router):
        src = vault / "Tasks" / "Stuck Task.md"
        _write(src, {"type": "living/task", "tags": ["task"], "status": "blocked"},
               body="Waiting on the platform team to ship X.\n")
        migrate_to_0_43_0.backfill_vault(str(vault), router=router)
        assert _read_status(src) == "parked"
        # No deprecation callout — parked is non-terminal.
        assert "Deprecated" not in src.read_text()


# ---------------------------------------------------------------------------
# Folder move tests
# ---------------------------------------------------------------------------

class TestFolderMoves:
    def test_superseded_folder_collapsed_into_deprecated(self, vault, router):
        src = vault / "Designs" / "+Superseded" / "A.md"
        _write(src, {"type": "living/design", "tags": ["design"], "status": "superseded"},
               body="## Body\n\n")
        migrate_to_0_43_0.backfill_vault(str(vault), router=router)
        assert (vault / "Designs" / "+Deprecated" / "A.md").exists()
        # Old folder removed.
        assert not (vault / "Designs" / "+Superseded").exists()

    def test_rejected_folder_collapsed_into_deprecated(self, vault, router):
        src = vault / "Designs" / "+Rejected" / "B.md"
        _write(src, {"type": "living/design", "tags": ["design"], "status": "rejected"},
               body="## Body\n\n")
        migrate_to_0_43_0.backfill_vault(str(vault), router=router)
        assert (vault / "Designs" / "+Deprecated" / "B.md").exists()
        assert not (vault / "Designs" / "+Rejected").exists()

    def test_cancelled_folder_collapsed_into_deprecated(self, vault, router):
        src = vault / "Releases" / "+Cancelled" / "C.md"
        _write(src, {"type": "living/release", "tags": ["release"], "status": "cancelled", "parent": "project/x"},
               body="## Body\n\n")
        migrate_to_0_43_0.backfill_vault(str(vault), router=router)
        assert (vault / "Releases" / "+Deprecated" / "C.md").exists()
        assert not (vault / "Releases" / "+Cancelled").exists()


# ---------------------------------------------------------------------------
# Callout preservation
# ---------------------------------------------------------------------------

class TestCalloutPreservation:
    def test_existing_superseded_link_preserved(self, vault, router):
        src = vault / "Designs" / "+Superseded" / "Old.md"
        _write(src, {"type": "living/design", "tags": ["design"], "status": "superseded"},
               body="> [!info] Superseded\n> This design was superseded by [[New|Successor]]. Details follow.\n\n## Goal\n\nx\n")
        migrate_to_0_43_0.backfill_vault(str(vault), router=router)
        moved = vault / "Designs" / "+Deprecated" / "Old.md"
        content = moved.read_text()
        assert "Deprecated — superseded by [[New|Successor]]" in content

    def test_single_line_superseded_callout_preserved(self, vault, router):
        src = vault / "Designs" / "+Superseded" / "Old2.md"
        _write(src, {"type": "living/design", "tags": ["design"], "status": "superseded"},
               body="> [!info] Superseded by [[Successor]]\n\n## Goal\n\nx\n")
        migrate_to_0_43_0.backfill_vault(str(vault), router=router)
        moved = vault / "Designs" / "+Deprecated" / "Old2.md"
        content = moved.read_text()
        assert "Deprecated — superseded by [[Successor]]" in content


# ---------------------------------------------------------------------------
# Idempotency + no-op
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_second_run_is_noop(self, vault, router):
        src = vault / "Designs" / "+Superseded" / "X.md"
        _write(src, {"type": "living/design", "tags": ["design"], "status": "superseded"},
               body="## Goal\n\nx\n")
        first = migrate_to_0_43_0.backfill_vault(str(vault), router=router)
        assert first["updated"] >= 1
        second = migrate_to_0_43_0.backfill_vault(str(vault), router=router)
        assert second["updated"] == 0
        assert second["status"] == "skipped"

    def test_empty_vault_noop(self, vault, router):
        result = migrate_to_0_43_0.backfill_vault(str(vault), router=router)
        assert result["updated"] == 0
        assert result["status"] == "skipped"

    def test_post_migration_artefacts_untouched(self, vault, router):
        # Artefacts already on the new vocab — must not be touched.
        already_deprecated = vault / "Designs" / "+Deprecated" / "Already.md"
        _write(already_deprecated, {"type": "living/design", "tags": ["design"], "status": "deprecated"},
               body="> [!info] Deprecated — rejected\n\n## Goal\n\nx\n")
        parked = vault / "Designs" / "Parked.md"
        _write(parked, {"type": "living/design", "tags": ["design"], "status": "parked"},
               body="## Goal\n\nx\n")

        result = migrate_to_0_43_0.backfill_vault(str(vault), router=router)
        assert result["updated"] == 0
        assert _read_status(already_deprecated) == "deprecated"
        assert _read_status(parked) == "parked"

    def test_live_statuses_untouched(self, vault, router):
        # Active / in-flight statuses must be left alone — regression guard
        # against the migration accidentally widening its rewrite set.
        active_design = vault / "Designs" / "Active.md"
        _write(active_design, {"type": "living/design", "tags": ["design"], "status": "active"},
               body="## Goal\n\nWork in progress.\n")
        implemented = vault / "Designs" / "+Implemented" / "Done.md"
        _write(implemented, {"type": "living/design", "tags": ["design"], "status": "implemented"},
               body="## Goal\n\nShipped.\n")
        in_progress_task = vault / "Tasks" / "Working.md"
        _write(in_progress_task, {"type": "living/task", "tags": ["task"], "status": "in-progress"},
               body="Someone is on it.\n")
        shipped_release = vault / "Releases" / "+Shipped" / "v1.0 - First.md"
        _write(shipped_release, {"type": "living/release", "tags": ["release"], "status": "shipped", "parent": "project/x"},
               body="## Release Notes\n\nx\n")

        result = migrate_to_0_43_0.backfill_vault(str(vault), router=router)
        assert result["updated"] == 0
        assert _read_status(active_design) == "active"
        assert _read_status(implemented) == "implemented"
        assert _read_status(in_progress_task) == "in-progress"
        assert _read_status(shipped_release) == "shipped"

    def test_destination_collision_warns_and_skips_move(self, vault, router):
        # An existing file at the destination path must not be overwritten;
        # the status change still applies in place, the move is skipped, and
        # a warning surfaces.
        src = vault / "Designs" / "+Superseded" / "Conflict.md"
        _write(src, {"type": "living/design", "tags": ["design"], "status": "superseded"},
               body="## Goal\n\noriginal\n")
        # Pre-existing file at the would-be destination.
        existing = vault / "Designs" / "+Deprecated" / "Conflict.md"
        _write(existing, {"type": "living/design", "tags": ["design"], "status": "deprecated"},
               body="## Goal\n\npre-existing\n")

        result = migrate_to_0_43_0.backfill_vault(str(vault), router=router)
        assert any("destination already exists" in w for w in result["warnings"])
        # Source file still exists — move was skipped.
        assert src.exists()
        # Status was still rewritten in place.
        assert _read_status(src) == "deprecated"
        # Pre-existing destination untouched.
        assert "pre-existing" in existing.read_text()


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_does_not_write(self, vault, router):
        src = vault / "Designs" / "+Superseded" / "DryRun.md"
        _write(src, {"type": "living/design", "tags": ["design"], "status": "superseded"},
               body="## Goal\n\nx\n")
        result = migrate_to_0_43_0.backfill_vault(str(vault), router=router, dry_run=True)
        assert result["dry_run"] is True
        assert result["updated"] >= 1
        # File unchanged in dry run.
        assert src.exists()
        assert _read_status(src) == "superseded"
