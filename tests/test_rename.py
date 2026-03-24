"""Tests for rename.py — wikilink-aware file renaming."""

import os

import pytest

import rename


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path):
    """Create a minimal vault with linked files."""
    # .brain-core/VERSION (needed for find_vault_root)
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.7.0\n")

    # Living type: Wiki
    wiki = tmp_path / "Wiki"
    wiki.mkdir()
    (wiki / "topic-a.md").write_text(
        "---\ntype: living/wiki\ntags: []\n---\n\n# Topic A\n\nSee also [[Wiki/topic-b]].\n"
    )
    (wiki / "topic-b.md").write_text(
        "---\ntype: living/wiki\ntags: []\n---\n\n# Topic B\n\nRelated to [[Wiki/topic-a|Topic A]].\n"
    )

    # Temporal type
    temporal = tmp_path / "_Temporal"
    temporal.mkdir()
    logs = temporal / "Logs" / "2026-03"
    logs.mkdir(parents=True)
    (logs / "20260324-log.md").write_text(
        "---\ntype: temporal/logs\ntags: []\n---\n\nWorked on [[Wiki/topic-a]] today.\n"
    )

    return tmp_path


# ---------------------------------------------------------------------------
# Core rename tests
# ---------------------------------------------------------------------------

class TestRenameAndUpdateLinks:
    def test_renames_file(self, vault):
        rename.rename_and_update_links(str(vault), "Wiki/topic-a.md", "Wiki/topic-a-renamed.md")
        assert not (vault / "Wiki" / "topic-a.md").exists()
        assert (vault / "Wiki" / "topic-a-renamed.md").exists()

    def test_updates_wikilinks_in_other_files(self, vault):
        rename.rename_and_update_links(str(vault), "Wiki/topic-a.md", "Wiki/topic-a-renamed.md")
        content_b = (vault / "Wiki" / "topic-b.md").read_text()
        assert "[[Wiki/topic-a-renamed|Topic A]]" in content_b

    def test_updates_wikilinks_in_temporal_files(self, vault):
        rename.rename_and_update_links(str(vault), "Wiki/topic-a.md", "Wiki/topic-a-renamed.md")
        content_log = (vault / "_Temporal" / "Logs" / "2026-03" / "20260324-log.md").read_text()
        assert "[[Wiki/topic-a-renamed]]" in content_log

    def test_returns_links_updated_count(self, vault):
        count = rename.rename_and_update_links(str(vault), "Wiki/topic-a.md", "Wiki/topic-a-renamed.md")
        # topic-b has one link, log has one link = 2
        assert count == 2

    def test_preserves_alias_in_wikilink(self, vault):
        rename.rename_and_update_links(str(vault), "Wiki/topic-a.md", "Wiki/new-name.md")
        content = (vault / "Wiki" / "topic-b.md").read_text()
        assert "[[Wiki/new-name|Topic A]]" in content

    def test_raises_on_missing_source(self, vault):
        with pytest.raises(FileNotFoundError, match="Source file not found"):
            rename.rename_and_update_links(str(vault), "Wiki/nonexistent.md", "Wiki/dest.md")

    def test_creates_destination_directory(self, vault):
        rename.rename_and_update_links(str(vault), "Wiki/topic-a.md", "Wiki/sub/topic-a.md")
        assert (vault / "Wiki" / "sub" / "topic-a.md").exists()

    def test_no_links_updated_when_no_references(self, vault):
        # topic-b is referenced only by topic-a with an alias
        # Remove all references first
        (vault / "Wiki" / "topic-a.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n# Topic A\n\nNo links here.\n"
        )
        (vault / "_Temporal" / "Logs" / "2026-03" / "20260324-log.md").write_text(
            "---\ntype: temporal/logs\ntags: []\n---\n\nNothing linked.\n"
        )
        count = rename.rename_and_update_links(str(vault), "Wiki/topic-b.md", "Wiki/topic-b-new.md")
        assert count == 0

    def test_does_not_update_links_in_system_dirs(self, vault):
        """System directories (other than _Temporal) should be skipped."""
        config = vault / "_Config"
        config.mkdir(exist_ok=True)
        (config / "notes.md").write_text("See [[Wiki/topic-a]].\n")

        rename.rename_and_update_links(str(vault), "Wiki/topic-a.md", "Wiki/renamed.md")
        # _Config file should NOT be updated
        assert "[[Wiki/topic-a]]" in (config / "notes.md").read_text()


# ---------------------------------------------------------------------------
# Delete and clean links tests
# ---------------------------------------------------------------------------

class TestDeleteAndCleanLinks:
    def test_delete_removes_file(self, vault):
        rename.delete_and_clean_links(str(vault), "Wiki/topic-a.md")
        assert not (vault / "Wiki" / "topic-a.md").exists()

    def test_delete_replaces_wikilinks_with_strikethrough(self, vault):
        rename.delete_and_clean_links(str(vault), "Wiki/topic-a.md")
        # topic-b has [[Wiki/topic-a|Topic A]] → should become ~~Topic A~~
        content_b = (vault / "Wiki" / "topic-b.md").read_text()
        assert "~~Topic A~~" in content_b
        assert "[[Wiki/topic-a" not in content_b

    def test_delete_replaces_plain_wikilinks(self, vault):
        rename.delete_and_clean_links(str(vault), "Wiki/topic-a.md")
        # log has [[Wiki/topic-a]] (no alias) → should become ~~topic-a~~
        content_log = (vault / "_Temporal" / "Logs" / "2026-03" / "20260324-log.md").read_text()
        assert "~~topic-a~~" in content_log
        assert "[[Wiki/topic-a]]" not in content_log

    def test_delete_returns_links_replaced_count(self, vault):
        count = rename.delete_and_clean_links(str(vault), "Wiki/topic-a.md")
        # topic-b has one link, log has one link = 2
        assert count == 2

    def test_delete_raises_on_missing_file(self, vault):
        with pytest.raises(FileNotFoundError, match="File not found"):
            rename.delete_and_clean_links(str(vault), "Wiki/nonexistent.md")

    def test_delete_no_references(self, vault):
        # Remove all references to topic-b first
        (vault / "Wiki" / "topic-a.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n# Topic A\n\nNo links.\n"
        )
        (vault / "_Temporal" / "Logs" / "2026-03" / "20260324-log.md").write_text(
            "---\ntype: temporal/logs\ntags: []\n---\n\nNothing linked.\n"
        )
        count = rename.delete_and_clean_links(str(vault), "Wiki/topic-b.md")
        assert count == 0
        assert not (vault / "Wiki" / "topic-b.md").exists()
