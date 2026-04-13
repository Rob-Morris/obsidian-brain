"""Tests for migrations/migrate_to_0_21_3.py — Backfill status: active on documentation."""

from _common import parse_frontmatter
from migrate_to_0_21_3 import migrate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_vault(tmp_path):
    """Create a minimal vault structure."""
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.21.2\n")
    (bc / "session-core.md").write_text("# Session Core\n")
    (tmp_path / "Documentation").mkdir()
    return tmp_path


def write_md(path, fields_str):
    """Write a markdown file with frontmatter string."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{fields_str}\n---\n\nBody.\n")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_no_docs_dir_skipped(tmp_path):
    """No Documentation/ folder → returns skipped."""
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.21.2\n")
    (bc / "session-core.md").write_text("# Session Core\n")

    result = migrate(str(tmp_path))
    assert result["status"] == "skipped"


def test_empty_docs_dir_skipped(tmp_path):
    """Empty Documentation/ folder → returns skipped."""
    vault = make_vault(tmp_path)
    result = migrate(str(vault))
    assert result["status"] == "skipped"


def test_backfills_active(tmp_path):
    """Documentation without status gets status: active."""
    vault = make_vault(tmp_path)
    write_md(
        vault / "Documentation" / "style-guide.md",
        "type: living/documentation\ntags:\n  - documentation",
    )

    result = migrate(str(vault))
    assert result["status"] == "ok"
    assert result["updated"] == 1

    content = (vault / "Documentation" / "style-guide.md").read_text()
    fields, _ = parse_frontmatter(content)
    assert fields["status"] == "active"


def test_preserves_existing_status(tmp_path):
    """Documentation with an existing status is not overwritten."""
    vault = make_vault(tmp_path)
    write_md(
        vault / "Documentation" / "draft-guide.md",
        "type: living/documentation\ntags:\n  - documentation\nstatus: shaping",
    )

    result = migrate(str(vault))
    assert result["status"] == "skipped"

    content = (vault / "Documentation" / "draft-guide.md").read_text()
    fields, _ = parse_frontmatter(content)
    assert fields["status"] == "shaping"


def test_skips_non_documentation_type(tmp_path):
    """Files with a different type in Documentation/ are skipped."""
    vault = make_vault(tmp_path)
    write_md(
        vault / "Documentation" / "stray-wiki.md",
        "type: living/wiki\ntags:\n  - topic",
    )

    result = migrate(str(vault))
    assert result["status"] == "skipped"


def test_multiple_files(tmp_path):
    """Multiple documentation files are all backfilled."""
    vault = make_vault(tmp_path)
    write_md(
        vault / "Documentation" / "guide-a.md",
        "type: living/documentation\ntags:\n  - documentation",
    )
    write_md(
        vault / "Documentation" / "guide-b.md",
        "type: living/documentation\ntags:\n  - documentation",
    )
    write_md(
        vault / "Documentation" / "already-set.md",
        "type: living/documentation\ntags:\n  - documentation\nstatus: active",
    )

    result = migrate(str(vault))
    assert result["status"] == "ok"
    assert result["updated"] == 2


def test_idempotent(tmp_path):
    """Running migration twice doesn't duplicate changes."""
    vault = make_vault(tmp_path)
    write_md(
        vault / "Documentation" / "guide.md",
        "type: living/documentation\ntags:\n  - documentation",
    )

    result1 = migrate(str(vault))
    assert result1["status"] == "ok"
    assert result1["updated"] == 1

    result2 = migrate(str(vault))
    assert result2["status"] == "skipped"


def test_skips_archive(tmp_path):
    """Files inside Documentation/_Archive/ are not touched."""
    vault = make_vault(tmp_path)
    write_md(
        vault / "Documentation" / "_Archive" / "old-guide.md",
        "type: living/documentation\ntags:\n  - documentation",
    )

    result = migrate(str(vault))
    assert result["status"] == "skipped"
