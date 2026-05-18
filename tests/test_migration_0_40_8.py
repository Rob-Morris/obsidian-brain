"""Tests for migrations/migrate_to_0_40_8.py — duplicate frontmatter repair."""

from __future__ import annotations

import _lifecycle.frontmatter_repairs as frontmatter_repairs
import migrate_to_0_40_8


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_backfill_repairs_duplicate_frontmatter(tmp_path, monkeypatch):
    (tmp_path / ".brain-core").mkdir()
    (tmp_path / ".brain-core" / "VERSION").write_text("0.40.7\n")
    (tmp_path / ".brain-core" / "session-core.md").write_text("# Session Core\n")

    target = tmp_path / "Wiki" / "Broken.md"
    _write(
        target,
        "---\n"
        "type: living/wiki\n"
        "tags:\n"
        "  - wiki\n"
        "key: broken\n"
        "status: active\n"
        "---\n\n"
        "---\n"
        "status: shaping\n"
        "tags:\n"
        "  - repaired\n"
        "---\n"
        "# Broken\n",
    )

    monkeypatch.setattr(frontmatter_repairs, "now_iso", lambda: "2026-05-19T09:30:00+10:00")
    result = migrate_to_0_40_8.backfill_vault(str(tmp_path), dry_run=False)

    assert result["status"] == "ok"
    assert result["updated"] == 1
    assert target.read_text() == (
        "---\n"
        "type: living/wiki\n"
        "tags:\n"
        "  - wiki\n"
        "  - repaired\n"
        "key: broken\n"
        "status: active\n"
        "modified: 2026-05-19T09:30:00+10:00\n"
        "---\n"
        "# Broken\n"
    )


def test_backfill_dry_run_reports_without_mutating(tmp_path):
    (tmp_path / ".brain-core").mkdir()
    (tmp_path / ".brain-core" / "VERSION").write_text("0.40.7\n")
    (tmp_path / ".brain-core" / "session-core.md").write_text("# Session Core\n")

    target = tmp_path / "_Temporal" / "Bug Logs" / "2026-05" / "20260518-bug~Broken.md"
    original = (
        "---\n"
        "type: temporal/bug-log\n"
        "tags:\n"
        "  - bug\n"
        "---\n"
        "---\n"
        "status: open\n"
        "---\n"
        "# Broken\n"
    )
    _write(target, original)

    result = migrate_to_0_40_8.backfill_vault(str(tmp_path), dry_run=True)

    assert result["status"] == "ok"
    assert result["dry_run"] is True
    assert result["updated"] == 1
    assert target.read_text() == original


def test_backfill_is_idempotent(tmp_path):
    (tmp_path / ".brain-core").mkdir()
    (tmp_path / ".brain-core" / "VERSION").write_text("0.40.7\n")
    (tmp_path / ".brain-core" / "session-core.md").write_text("# Session Core\n")

    target = tmp_path / "Wiki" / "Clean.md"
    _write(
        target,
        "---\n"
        "type: living/wiki\n"
        "tags:\n"
        "  - wiki\n"
        "key: clean\n"
        "---\n\n"
        "# Clean\n",
    )

    result = migrate_to_0_40_8.backfill_vault(str(tmp_path), dry_run=False)

    assert result["status"] == "skipped"
    assert result["updated"] == 0
