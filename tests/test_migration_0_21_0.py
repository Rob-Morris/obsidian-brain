"""Tests for migrations/migrate_to_0_21_0.py — Move per-type _Archive/ to top-level."""

import os
import sys

import pytest

# Add scripts and migrations dirs to path
SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "src", "brain-core", "scripts")
MIGRATIONS_DIR = os.path.join(SCRIPTS_DIR, "migrations")
sys.path.insert(0, os.path.abspath(SCRIPTS_DIR))
sys.path.insert(0, os.path.abspath(MIGRATIONS_DIR))

from migrate_to_0_21_0 import migrate


# ---------------------------------------------------------------------------
# Vault fixture helpers
# ---------------------------------------------------------------------------

def make_vault(tmp_path):
    """Create a minimal vault structure."""
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.20.1\n")
    (bc / "session-core.md").write_text("# Session Core\n")
    (tmp_path / "Designs").mkdir()
    (tmp_path / "Wiki").mkdir()
    return tmp_path


def write_md(path, fields_str):
    """Write a markdown file with frontmatter string."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{fields_str}\n---\n\nBody.\n")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_no_archives_skipped(tmp_path):
    """No _Archive/ folders → returns skipped."""
    vault = make_vault(tmp_path)
    result = migrate(str(vault))
    assert result["status"] == "skipped"


def test_moves_type_root_archive(tmp_path):
    """Designs/_Archive/20260315-old.md → _Archive/Designs/20260315-old.md"""
    vault = make_vault(tmp_path)
    archive = vault / "Designs" / "_Archive"
    archive.mkdir()
    write_md(archive / "20260315-old.md",
             "type: living/design\ntags: [design]\nstatus: implemented\narchiveddate: 2026-03-15")

    result = migrate(str(vault))
    assert result["status"] == "ok"
    assert result["moved"] == 1

    # File moved to top-level
    assert (vault / "_Archive" / "Designs" / "20260315-old.md").exists()
    # Old location cleaned up
    assert not (vault / "Designs" / "_Archive" / "20260315-old.md").exists()


def test_moves_project_subfolder_archive(tmp_path):
    """Designs/Brain/_Archive/20260317-old.md → _Archive/Designs/Brain/20260317-old.md"""
    vault = make_vault(tmp_path)
    archive = vault / "Designs" / "Brain" / "_Archive"
    archive.mkdir(parents=True)
    write_md(archive / "20260317-old.md",
             "type: living/design\ntags: [design]\nstatus: implemented\narchiveddate: 2026-03-17")

    result = migrate(str(vault))
    assert result["status"] == "ok"
    assert result["moved"] == 1
    assert (vault / "_Archive" / "Designs" / "Brain" / "20260317-old.md").exists()
    assert not (vault / "Designs" / "Brain" / "_Archive" / "20260317-old.md").exists()


def test_updates_wikilinks(tmp_path):
    """Wikilinks pointing to moved file are updated."""
    vault = make_vault(tmp_path)
    archive = vault / "Designs" / "_Archive"
    archive.mkdir()
    write_md(archive / "20260315-old.md",
             "type: living/design\ntags: [design]\nstatus: implemented\narchiveddate: 2026-03-15")

    # A file with a wikilink to the archived file
    (vault / "Designs" / "linker.md").write_text(
        "---\ntype: living/design\ntags: [design]\nstatus: shaping\n---\n\n"
        "See [[20260315-old]].\n"
    )

    result = migrate(str(vault))
    assert result["moved"] == 1

    content = (vault / "Designs" / "linker.md").read_text()
    assert "20260315-old" in content


def test_idempotent(tmp_path):
    """Running migration twice doesn't fail or duplicate files."""
    vault = make_vault(tmp_path)
    archive = vault / "Designs" / "_Archive"
    archive.mkdir()
    write_md(archive / "20260315-old.md",
             "type: living/design\ntags: [design]\nstatus: implemented\narchiveddate: 2026-03-15")

    result1 = migrate(str(vault))
    assert result1["status"] == "ok"
    assert result1["moved"] == 1

    # Second run: source is gone, nothing to do
    result2 = migrate(str(vault))
    assert result2["status"] == "skipped"


def test_removes_empty_archive_dirs(tmp_path):
    """Empty per-type _Archive/ directories are cleaned up after migration."""
    vault = make_vault(tmp_path)
    archive = vault / "Designs" / "_Archive"
    archive.mkdir()
    write_md(archive / "20260315-old.md",
             "type: living/design\ntags: [design]\nstatus: implemented\narchiveddate: 2026-03-15")

    migrate(str(vault))
    assert not (vault / "Designs" / "_Archive").exists()


def test_multiple_files(tmp_path):
    """Multiple files across different archive locations are all migrated."""
    vault = make_vault(tmp_path)

    # Type-root archive
    (vault / "Designs" / "_Archive").mkdir()
    write_md(vault / "Designs" / "_Archive" / "20260315-a.md",
             "type: living/design\ntags: [design]\nstatus: implemented\narchiveddate: 2026-03-15")

    # Project-subfolder archive
    (vault / "Designs" / "Brain" / "_Archive").mkdir(parents=True)
    write_md(vault / "Designs" / "Brain" / "_Archive" / "20260317-b.md",
             "type: living/design\ntags: [design]\nstatus: implemented\narchiveddate: 2026-03-17")

    result = migrate(str(vault))
    assert result["status"] == "ok"
    assert result["moved"] == 2
    assert (vault / "_Archive" / "Designs" / "20260315-a.md").exists()
    assert (vault / "_Archive" / "Designs" / "Brain" / "20260317-b.md").exists()
