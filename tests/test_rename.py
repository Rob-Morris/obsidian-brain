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
    (bc / "session-core.md").write_text("# Session Core\n")

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


# ---------------------------------------------------------------------------
# Filename-only wikilinks
# ---------------------------------------------------------------------------

@pytest.fixture
def vault_with_filename_links(tmp_path):
    """Vault where wikilinks use filename-only format (Obsidian default)."""
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.7.0\n")
    (bc / "session-core.md").write_text("# Session Core\n")

    wiki = tmp_path / "Wiki"
    wiki.mkdir()
    (wiki / "topic-a.md").write_text(
        "---\ntype: living/wiki\ntags: []\n---\n\n# Topic A\n"
    )
    (wiki / "topic-b.md").write_text(
        "---\ntype: living/wiki\ntags: []\n---\n\n# Topic B\n\n"
        "Related to [[topic-a|Topic A]] and [[topic-a]].\n"
    )

    temporal = tmp_path / "_Temporal" / "Logs" / "2026-03"
    temporal.mkdir(parents=True)
    (temporal / "20260324-log.md").write_text(
        "---\ntype: temporal/logs\ntags: []\n---\n\nWorked on [[topic-a]] today.\n"
    )

    return tmp_path


class TestFilenameOnlyRename:
    def test_updates_filename_only_wikilinks(self, vault_with_filename_links):
        vault = vault_with_filename_links
        rename.rename_and_update_links(str(vault), "Wiki/topic-a.md", "Wiki/new-name.md")
        content = (vault / "Wiki" / "topic-b.md").read_text()
        assert "[[new-name]]" in content

    def test_preserves_filename_only_format(self, vault_with_filename_links):
        vault = vault_with_filename_links
        rename.rename_and_update_links(str(vault), "Wiki/topic-a.md", "Wiki/new-name.md")
        content = (vault / "Wiki" / "topic-b.md").read_text()
        # Should NOT upgrade to full-path format
        assert "[[Wiki/new-name]]" not in content
        assert "[[new-name|Topic A]]" in content
        assert "[[new-name]]" in content

    def test_updates_both_full_path_and_filename_links(self, vault_with_filename_links):
        vault = vault_with_filename_links
        # Add a full-path link alongside the existing filename-only links
        (vault / "_Temporal" / "Logs" / "2026-03" / "20260324-log.md").write_text(
            "---\ntype: temporal/logs\ntags: []\n---\n\n"
            "Full: [[Wiki/topic-a]]. Short: [[topic-a]].\n"
        )
        rename.rename_and_update_links(str(vault), "Wiki/topic-a.md", "Wiki/new-name.md")
        content = (vault / "_Temporal" / "Logs" / "2026-03" / "20260324-log.md").read_text()
        assert "[[Wiki/new-name]]" in content
        assert "[[new-name]]" in content

    def test_returns_correct_count_with_filename_links(self, vault_with_filename_links):
        vault = vault_with_filename_links
        count = rename.rename_and_update_links(
            str(vault), "Wiki/topic-a.md", "Wiki/new-name.md"
        )
        # topic-b has 2 links ([[topic-a|Topic A]] and [[topic-a]]), log has 1
        assert count == 3

    def test_skips_filename_match_when_ambiguous(self, vault_with_filename_links):
        vault = vault_with_filename_links
        # Create a second file with the same basename in a different folder
        other = vault / "Notes"
        other.mkdir()
        (other / "topic-a.md").write_text("---\ntype: living/note\n---\n\n# Other\n")
        count = rename.rename_and_update_links(
            str(vault), "Wiki/topic-a.md", "Wiki/new-name.md"
        )
        # Ambiguous: only full-path links matched; filename-only links are skipped
        assert count == 0
        content = (vault / "Wiki" / "topic-b.md").read_text()
        assert "[[topic-a|Topic A]]" in content  # unchanged
        assert "[[topic-a]]" in content  # unchanged


class TestFilenameOnlyDelete:
    def test_cleans_filename_only_wikilinks(self, vault_with_filename_links):
        vault = vault_with_filename_links
        rename.delete_and_clean_links(str(vault), "Wiki/topic-a.md")
        content = (vault / "Wiki" / "topic-b.md").read_text()
        assert "~~topic-a~~" in content
        assert "[[topic-a]]" not in content

    def test_cleans_filename_only_with_alias(self, vault_with_filename_links):
        vault = vault_with_filename_links
        rename.delete_and_clean_links(str(vault), "Wiki/topic-a.md")
        content = (vault / "Wiki" / "topic-b.md").read_text()
        assert "~~Topic A~~" in content
        assert "[[topic-a|Topic A]]" not in content


# ---------------------------------------------------------------------------
# Heading anchors, block refs, and embeds
# ---------------------------------------------------------------------------

@pytest.fixture
def vault_with_anchors(tmp_path):
    """Vault with heading anchors, block refs, and embeds."""
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.7.0\n")
    (bc / "session-core.md").write_text("# Session Core\n")

    wiki = tmp_path / "Wiki"
    wiki.mkdir()
    (wiki / "topic-a.md").write_text(
        "---\ntype: living/wiki\ntags: []\n---\n\n# Topic A\n\n"
        "Some content. ^block123\n"
    )
    (wiki / "topic-b.md").write_text(
        "---\ntype: living/wiki\ntags: []\n---\n\n# Topic B\n\n"
        "Anchor: [[Wiki/topic-a#Overview]].\n"
        "Anchor alias: [[Wiki/topic-a#Overview|see overview]].\n"
        "Block ref: [[Wiki/topic-a#^block123]].\n"
        "Embed: ![[Wiki/topic-a]].\n"
        "Embed anchor: ![[Wiki/topic-a#Overview]].\n"
        "Filename anchor: [[topic-a#Details]].\n"
        "Plain: [[Wiki/topic-a]].\n"
    )

    return tmp_path


class TestAnchorAndEmbedRename:
    def test_preserves_heading_anchor(self, vault_with_anchors):
        vault = vault_with_anchors
        rename.rename_and_update_links(str(vault), "Wiki/topic-a.md", "Wiki/new-name.md")
        content = (vault / "Wiki" / "topic-b.md").read_text()
        assert "[[Wiki/new-name#Overview]]" in content

    def test_preserves_anchor_with_alias(self, vault_with_anchors):
        vault = vault_with_anchors
        rename.rename_and_update_links(str(vault), "Wiki/topic-a.md", "Wiki/new-name.md")
        content = (vault / "Wiki" / "topic-b.md").read_text()
        assert "[[Wiki/new-name#Overview|see overview]]" in content

    def test_preserves_block_ref(self, vault_with_anchors):
        vault = vault_with_anchors
        rename.rename_and_update_links(str(vault), "Wiki/topic-a.md", "Wiki/new-name.md")
        content = (vault / "Wiki" / "topic-b.md").read_text()
        assert "[[Wiki/new-name#^block123]]" in content

    def test_preserves_embed_prefix(self, vault_with_anchors):
        vault = vault_with_anchors
        rename.rename_and_update_links(str(vault), "Wiki/topic-a.md", "Wiki/new-name.md")
        content = (vault / "Wiki" / "topic-b.md").read_text()
        assert "![[Wiki/new-name]]" in content

    def test_preserves_embed_with_anchor(self, vault_with_anchors):
        vault = vault_with_anchors
        rename.rename_and_update_links(str(vault), "Wiki/topic-a.md", "Wiki/new-name.md")
        content = (vault / "Wiki" / "topic-b.md").read_text()
        assert "![[Wiki/new-name#Overview]]" in content

    def test_filename_only_with_anchor(self, vault_with_anchors):
        vault = vault_with_anchors
        rename.rename_and_update_links(str(vault), "Wiki/topic-a.md", "Wiki/new-name.md")
        content = (vault / "Wiki" / "topic-b.md").read_text()
        assert "[[new-name#Details]]" in content

    def test_plain_link_still_works(self, vault_with_anchors):
        vault = vault_with_anchors
        rename.rename_and_update_links(str(vault), "Wiki/topic-a.md", "Wiki/new-name.md")
        content = (vault / "Wiki" / "topic-b.md").read_text()
        assert "[[Wiki/new-name]]" in content

    def test_returns_correct_count(self, vault_with_anchors):
        vault = vault_with_anchors
        count = rename.rename_and_update_links(
            str(vault), "Wiki/topic-a.md", "Wiki/new-name.md"
        )
        # 7 links in topic-b: anchor, anchor+alias, blockref, embed, embed+anchor, filename+anchor, plain
        assert count == 7


class TestAnchorAndEmbedDelete:
    def test_delete_anchor_link(self, vault_with_anchors):
        vault = vault_with_anchors
        rename.delete_and_clean_links(str(vault), "Wiki/topic-a.md")
        content = (vault / "Wiki" / "topic-b.md").read_text()
        assert "~~topic-a~~" in content
        assert "[[Wiki/topic-a#Overview]]" not in content

    def test_delete_anchor_alias_uses_alias(self, vault_with_anchors):
        vault = vault_with_anchors
        rename.delete_and_clean_links(str(vault), "Wiki/topic-a.md")
        content = (vault / "Wiki" / "topic-b.md").read_text()
        assert "~~see overview~~" in content

    def test_delete_embed(self, vault_with_anchors):
        vault = vault_with_anchors
        rename.delete_and_clean_links(str(vault), "Wiki/topic-a.md")
        content = (vault / "Wiki" / "topic-b.md").read_text()
        assert "![[Wiki/topic-a]]" not in content


# ---------------------------------------------------------------------------
# Path boundary checks
# ---------------------------------------------------------------------------

class TestPathBoundary:
    """Ensure path traversal attacks are rejected."""

    def test_rename_source_traversal(self, vault):
        with pytest.raises(ValueError, match="outside allowed boundary"):
            rename.rename_and_update_links(
                str(vault), "../../etc/passwd", "Wiki/stolen.md",
            )

    def test_rename_dest_traversal(self, vault):
        with pytest.raises(ValueError, match="outside allowed boundary"):
            rename.rename_and_update_links(
                str(vault), "Wiki/topic-a.md", "../../tmp/exfil.md",
            )

    def test_delete_traversal(self, vault):
        with pytest.raises(ValueError, match="outside allowed boundary"):
            rename.delete_and_clean_links(str(vault), "../../etc/hosts")


class TestRenameRegionAwareness:
    """Rename honours literal-region skip ranges and the FM-property contract."""

    def test_literal_wikilink_in_inline_code_preserved(self, vault):
        (vault / "Wiki" / "docs.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "Use `[[Wiki/topic-a]]` as an example.\n"
            "Real link: [[Wiki/topic-a]].\n"
        )
        rename.rename_and_update_links(
            str(vault), "Wiki/topic-a.md", "Wiki/topic-a-renamed.md"
        )
        content = (vault / "Wiki" / "docs.md").read_text()
        assert "`[[Wiki/topic-a]]`" in content
        assert "Real link: [[Wiki/topic-a-renamed]]." in content

    def test_literal_wikilink_in_fence_preserved(self, vault):
        (vault / "Wiki" / "docs.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "```\n[[Wiki/topic-a]]\n```\n"
            "[[Wiki/topic-a]]\n"
        )
        rename.rename_and_update_links(
            str(vault), "Wiki/topic-a.md", "Wiki/topic-a-renamed.md"
        )
        content = (vault / "Wiki" / "docs.md").read_text()
        assert "```\n[[Wiki/topic-a]]\n```" in content
        assert "\n[[Wiki/topic-a-renamed]]\n" in content

    def test_frontmatter_property_is_rewritten(self, vault):
        """YAML property wikilinks are real links (D10) and must be renamed."""
        (vault / "Wiki" / "has-parent.md").write_text(
            "---\ntype: living/wiki\ntags: []\nparent: \"[[Wiki/topic-a]]\"\n---\n\n"
            "body\n"
        )
        rename.rename_and_update_links(
            str(vault), "Wiki/topic-a.md", "Wiki/topic-a-renamed.md"
        )
        content = (vault / "Wiki" / "has-parent.md").read_text()
        assert 'parent: "[[Wiki/topic-a-renamed]]"' in content


class TestBrainCoreProtection:
    """Ensure .brain-core/ files cannot be modified via rename/delete."""

    def test_rename_dest_into_brain_core(self, vault):
        with pytest.raises(ValueError, match="Cannot modify files inside .brain-core/"):
            rename.rename_and_update_links(
                str(vault), "Wiki/topic-a.md", ".brain-core/hijack.md",
            )

    def test_rename_source_out_of_brain_core_allowed(self, vault):
        """Moving a file OUT of .brain-core is allowed (source not checked)."""
        bc_file = vault / ".brain-core" / "movable.md"
        bc_file.write_text("---\ntype: living/wiki\n---\n\ntemp\n")
        # Should not raise — only dest is checked
        rename.rename_and_update_links(
            str(vault), ".brain-core/movable.md", "Wiki/rescued.md",
        )
        assert (vault / "Wiki" / "rescued.md").exists()
        assert not bc_file.exists()

    def test_delete_inside_brain_core(self, vault):
        with pytest.raises(ValueError, match="Cannot modify files inside .brain-core/"):
            rename.delete_and_clean_links(str(vault), ".brain-core/VERSION")

    def test_rename_dest_into_brain_core_subdir(self, vault):
        with pytest.raises(ValueError, match="Cannot modify files inside .brain-core/"):
            rename.rename_and_update_links(
                str(vault), "Wiki/topic-a.md", ".brain-core/scripts/evil.py",
            )
