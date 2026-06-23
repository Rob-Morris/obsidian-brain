"""Tests for edit.py — artefact editing, appending, and conversion."""

import os
import re
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

import edit
from _common import file_index_from_documents, parse_frontmatter, validate_artefact_folder


class TestAppendWithSection:
    def test_append_to_middle_section(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n"
        )
        edit.append_to_artefact(
            str(vault), router, "Wiki/test-page.md",
            "Appended to alpha.\n", target="Alpha", scope="section",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        # Appended content should appear before ## Beta
        alpha_end = body.index("## Beta")
        assert "Appended to alpha.\n" in body[:alpha_end]
        assert "Beta content." in body

    def test_append_to_last_section(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Only\n\nOnly content.\n"
        )
        edit.append_to_artefact(
            str(vault), router, "Wiki/test-page.md",
            "More stuff.\n", target="Only", scope="section",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert body.endswith("More stuff.\n")

    def test_append_section_not_found(self, vault, router):
        with pytest.raises(ValueError, match="not found"):
            edit.append_to_artefact(
                str(vault), router, "Wiki/test-page.md",
                "text", target="Nonexistent", scope="section",
            )

    def test_append_without_target_is_rejected(self, vault, router):
        with pytest.raises(ValueError, match="explicit target and scope"):
            edit.append_to_artefact(
                str(vault), router, "Wiki/test-page.md", "\nAppended.\n",
            )

    def test_append_to_body_intro(self, vault, router):
        edit.append_to_artefact(
            str(vault), router, "Wiki/test-page.md", "Appended.\n",
            target=":body", scope="section",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        assert content.endswith("Appended.\n")

    def test_append_to_sub_heading(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Parent\n\nIntro.\n\n### Child\n\n- Item 1\n\n"
            "### Sibling\n\nSibling content.\n\n## Other\n\nOther.\n"
        )
        edit.append_to_artefact(
            str(vault), router, "Wiki/test-page.md",
            "- Item 2\n", target="### Child", scope="section",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        # New item should be in Child section, before Sibling
        child_end = body.index("### Sibling")
        assert "- Item 2" in body[:child_end]
        assert "Sibling content." in body
        assert "Other." in body

    def test_append_to_callout(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Section\n\n"
            "> [!note] Status\n"
            "> First line.\n"
            "\n"
            "After callout.\n"
        )
        edit.append_to_artefact(
            str(vault), router, "Wiki/test-page.md",
            "> Appended line.\n", target="[!note] Status", scope="body",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "> Appended line." in body
        assert "After callout." in body
        # Appended line should be before "After callout"
        assert body.index("> Appended line.") < body.index("After callout.")


# ---------------------------------------------------------------------------
# Prepend tests
# ---------------------------------------------------------------------------

class TestPrependToArtefact:
    def test_prepend_adds_content_before_explicit_body(self, vault, router):
        edit.prepend_to_artefact(
            str(vault), router, "Wiki/test-page.md", "Prepended text.\n",
            target=":body", scope="section",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert body.startswith("Prepended text.\n")
        assert "Original body." in body

    def test_prepend_target_body_section_prepends_to_whole_body(self, vault, router):
        edit.prepend_to_artefact(
            str(vault), router, "Wiki/test-page.md", "Prepended text.\n",
            target=":body", scope="section",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert body.startswith("Prepended text.\n")
        assert "Original body." in body

    def test_prepend_before_middle_section(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n\n## Gamma\n\nGamma content.\n"
        )
        edit.prepend_to_artefact(
            str(vault), router, "Wiki/test-page.md",
            "## New Section\n\nInserted.\n", target="Beta", scope="section",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        # New section should appear between Alpha and Beta
        assert body.index("## New Section") < body.index("## Beta")
        assert body.index("## Alpha") < body.index("## New Section")
        # All original content preserved
        assert "Alpha content." in body
        assert "Beta content." in body
        assert "Gamma content." in body

    def test_prepend_before_first_section(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## First\n\nFirst content.\n\n## Second\n\nSecond content.\n"
        )
        edit.prepend_to_artefact(
            str(vault), router, "Wiki/test-page.md",
            "## Zeroth\n\nBefore everything.\n", target="First", scope="section",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert body.index("## Zeroth") < body.index("## First")
        assert "First content." in body

    def test_prepend_before_last_section(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n\n## Last\n\nLast content.\n"
        )
        edit.prepend_to_artefact(
            str(vault), router, "Wiki/test-page.md",
            "## Inserted\n\nBefore last.\n", target="Last", scope="section",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert body.index("## Alpha") < body.index("## Inserted") < body.index("## Last")
        assert "Last content." in body

    def test_prepend_file_not_found(self, vault, router):
        with pytest.raises(FileNotFoundError):
            edit.prepend_to_artefact(
                str(vault), router, "Wiki/nonexistent.md", "text",
                target=":body", scope="section",
            )

    def test_prepend_target_body_requires_scope(self, vault, router):
        with pytest.raises(ValueError, match="requires scope"):
            edit.prepend_to_artefact(
                str(vault), router, "Wiki/test-page.md", "Extra.\n",
                target=":body",
            )

    def test_prepend_body_intro_is_valid(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "Lead.\n\n## Notes\n\nBody.\n"
        )
        edit.prepend_to_artefact(
            str(vault), router, "Wiki/test-page.md", "Before lead.\n",
            target=":body", scope="intro",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert body.startswith("Before lead.\nLead.\n")

    def test_prepend_section_not_found(self, vault, router):
        with pytest.raises(ValueError, match="not found"):
            edit.prepend_to_artefact(
                str(vault), router, "Wiki/test-page.md",
                "text", target="Nonexistent", scope="section",
            )

    def test_prepend_basename_fallback(self, vault, router):
        edit.prepend_to_artefact(
            str(vault), router, "test-page", "Prepended.\n",
            target=":body", scope="section",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        assert "Prepended." in content

    def test_prepend_preserves_surrounding_sections(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n\n## Gamma\n\nGamma content.\n"
        )
        edit.prepend_to_artefact(
            str(vault), router, "Wiki/test-page.md",
            "## Inserted\n\nNew.\n", target="Beta", scope="section",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "Alpha content." in body
        assert "Gamma content." in body


# ---------------------------------------------------------------------------
# Edit with section tests
# ---------------------------------------------------------------------------

class TestEditWithSection:
    def test_edit_replaces_section_only(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n"
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md",
            "\nReplaced alpha.\n\n", target="Alpha", scope="body",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "Replaced alpha." in body
        assert "Alpha content." not in body
        assert "Beta content." in body

    def test_edit_replaces_last_section(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## First\n\nFirst content.\n\n## Last\n\nLast content.\n"
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md",
            "\nNew last.\n", target="Last", scope="body",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "New last." in body
        assert "Last content." not in body
        assert "First content." in body

    def test_edit_section_not_found(self, vault, router):
        with pytest.raises(ValueError, match="not found"):
            edit.edit_artefact(
                str(vault), router, "Wiki/test-page.md",
                "text", target="Nonexistent", scope="body",
            )

    def test_edit_section_preserves_following_heading(self, vault, router):
        """Body without trailing newline must not corrupt the next section heading."""
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n"
        )
        # Body with NO trailing newline — the bug concatenated it with ## Beta
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md",
            "New alpha content.", target="Alpha", scope="body",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "New alpha content." in body
        assert "## Beta" in body
        assert "Beta content." in body
        # The heading must be on its own line, not glued to body
        assert "content.## Beta" not in body

    def test_edit_without_section_replaces_all(self, vault, router):
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md", "# Whole new body.\n",
            target=":body", scope="section",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert body == "# Whole new body.\n"

    def test_edit_sub_heading_only(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Parent\n\nIntro.\n\n### Child\n\nOld child content.\n\n"
            "### Sibling\n\nSibling content.\n\n## Other\n\nOther.\n"
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md",
            "\nNew child content.\n\n", target="### Child", scope="body",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "New child content." in body
        assert "Old child content." not in body
        assert "Intro." in body
        assert "Sibling content." in body
        assert "Other." in body

    def test_edit_empty_body_clears_section_content_keeps_heading(self, vault, router):
        """body="" with a section target clears content but keeps the heading."""
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n"
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md", "", target="Alpha", scope="body",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "## Alpha" in body              # heading preserved
        assert "Alpha content." not in body    # content cleared
        assert "## Beta" in body               # following section intact
        # No double-blank before ## Beta
        assert "\n\n\n" not in body
        # Exactly one blank line between cleared heading and following section
        assert "## Alpha\n\n## Beta" in body

    def test_edit_empty_body_clears_last_section_keeps_heading(self, vault, router):
        """body="" on the last section leaves just the heading with no trailing garbage."""
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n"
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md", "", target="Beta", scope="body",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "## Beta" in body
        assert "Beta content." not in body
        # No trailing double-newlines after the heading
        assert body.endswith("## Beta\n")

    def test_edit_callout_content(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Section\n\n"
            "> [!note] Implementation status\n"
            "> Old status content.\n"
            "\n"
            "After callout.\n"
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md",
            "> New status content.\n",
            target="[!note] Implementation status", scope="body",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "> New status content." in body
        assert "Old status content." not in body
        assert "After callout." in body
        assert "> [!note] Implementation status" in body

    def test_edit_heading_body_scope_replaces_content_without_heading(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n"
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md",
            "Updated alpha.\n", target="## Alpha", scope="body",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert body.count("## Alpha") == 1
        assert "Updated alpha." in body
        assert "Alpha content." not in body

    def test_edit_heading_body_scope_rejects_heading_line_replacement(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n"
        )
        with pytest.raises(ValueError, match="Use scope='section'"):
            edit.edit_artefact(
                str(vault), router, "Wiki/test-page.md",
                "# Alpha\n\nUpdated alpha.\n", target="## Alpha", scope="body",
            )

    def test_edit_heading_body_scope_allows_nested_heading_content(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n"
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md",
            "### Overview\n\nUpdated alpha.\n", target="## Alpha", scope="body",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "## Alpha" in body
        assert "### Overview" in body
        assert "Updated alpha." in body

    def test_edit_heading_body_scope_rejects_same_or_higher_level_heading_content(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n"
        )
        with pytest.raises(ValueError, match="Use scope='section'"):
            edit.edit_artefact(
                str(vault), router, "Wiki/test-page.md",
                "# Overview\n\nUpdated alpha.\n", target="## Alpha", scope="body",
            )

    def test_edit_callout_header_scope_replaces_only_header(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Section\n\n"
            "> [!note] Implementation status\n"
            "> Old status content.\n"
            "\n"
            "After callout.\n"
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md",
            "> [!warning] Updated status\n",
            target="[!note] Implementation status", scope="header",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "[!note] Implementation status" not in body
        assert "[!warning] Updated status" in body
        assert "Old status content." in body

    def test_edit_heading_body_scope_allows_callout_content(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n"
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md",
            "> [!note] Fresh note\n> Updated alpha.\n",
            target="## Alpha", scope="body",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "## Alpha" in body
        assert "[!note] Fresh note" in body
        assert "Updated alpha." in body

    def test_edit_section_scope_replaces_heading(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n"
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md",
            "# Renamed Alpha\n\nUpdated alpha.\n", target="## Alpha", scope="section",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "## Alpha" not in body
        assert "# Renamed Alpha" in body
        assert "Updated alpha." in body
        assert "## Beta" in body

    def test_edit_section_scope_requires_structural_anchor(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n"
        )
        with pytest.raises(ValueError, match="must begin with a heading line"):
            edit.edit_artefact(
                str(vault), router, "Wiki/test-page.md",
                "Updated alpha.\n", target="## Alpha", scope="section",
            )

    def test_edit_heading_intro_scope_replaces_only_intro(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nIntro.\n\n### Child\n\nChild content.\n\n## Beta\n\nBeta content.\n"
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md",
            "Updated intro.\n", target="## Alpha", scope="intro",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "## Alpha\nUpdated intro.\n### Child" in body
        assert "Intro." not in body
        assert "Child content." in body
        assert "## Beta" in body

    def test_append_heading_intro_scope_inserts_before_first_child_heading(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nIntro.\n\n### Child\n\nChild content.\n"
        )
        edit.append_to_artefact(
            str(vault), router, "Wiki/test-page.md",
            "More intro.\n", target="## Alpha", scope="intro",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "Intro.\n\nMore intro.\n### Child" in body

    def test_prepend_heading_intro_scope_inserts_after_heading_line(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nIntro.\n\n### Child\n\nChild content.\n"
        )
        edit.prepend_to_artefact(
            str(vault), router, "Wiki/test-page.md",
            "Before intro.\n", target="## Alpha", scope="intro",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "## Alpha\nBefore intro.\n\nIntro." in body

    def test_edit_callout_section_scope_replaces_whole_block(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Section\n\n"
            "> [!note] Implementation status\n"
            "> Old status content.\n"
            "\n"
            "After callout.\n"
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md",
            "> [!warning] Updated status\n> New body.\n",
            target="[!note] Implementation status", scope="section",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "[!note] Implementation status" not in body
        assert "[!warning] Updated status" in body
        assert "New body." in body
        assert "After callout." in body

    def test_append_callout_section_scope_inserts_after_whole_block(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Section\n\n"
            "> [!note] Implementation status\n"
            "> Old status content.\n"
            "\n"
            "After callout.\n"
        )
        edit.append_to_artefact(
            str(vault), router, "Wiki/test-page.md",
            "Inserted after.\n", target="[!note] Implementation status", scope="section",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert body.index("Inserted after.") > body.index("Old status content.")
        assert body.index("Inserted after.") < body.index("After callout.")

    def test_prepend_callout_section_scope_inserts_before_whole_block(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Section\n\n"
            "> [!note] Implementation status\n"
            "> Old status content.\n"
            "\n"
            "After callout.\n"
        )
        edit.prepend_to_artefact(
            str(vault), router, "Wiki/test-page.md",
            "Inserted before.\n", target="[!note] Implementation status", scope="section",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert body.index("Inserted before.") < body.index("[!note] Implementation status")

    def test_edit_callout_body_rejects_unquoted_blank_lines(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Section\n\n"
            "> [!note] Implementation status\n"
            "> Old status content.\n"
        )
        with pytest.raises(ValueError, match="expects raw quoted markdown lines"):
            edit.edit_artefact(
                str(vault), router, "Wiki/test-page.md",
                "> First.\n\n> Second.\n",
                target="[!note] Implementation status", scope="body",
            )

    def test_edit_selector_disambiguates_duplicate_heading(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "# API\n\n"
            "## Notes\n\nFirst.\n\n"
            "# API\n\n"
            "## Notes\n\nSecond.\n"
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md",
            "Selected.\n",
            target="## Notes",
            selector={"within": [{"target": "# API", "occurrence": 2}]},
            scope="body",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "First." in body
        assert "Selected." in body
        assert "Second." not in body

    def test_edit_selector_ambiguity_errors(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "# API\n\n"
            "## Notes\n\nFirst.\n\n"
            "# API\n\n"
            "## Notes\n\nSecond.\n"
        )
        with pytest.raises(ValueError, match="Ambiguous target '## Notes'"):
            edit.edit_artefact(
                str(vault), router, "Wiki/test-page.md",
                "Selected.\n", target="## Notes", scope="body",
            )

    def test_append_selector_disambiguates_duplicate_heading(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "# API\n\n"
            "## Notes\n\nFirst.\n\n"
            "# API\n\n"
            "## Notes\n\nSecond.\n"
        )
        edit.append_to_artefact(
            str(vault), router, "Wiki/test-page.md",
            "Added.\n",
            target="## Notes",
            selector={"within": [{"target": "# API", "occurrence": 2}]},
            scope="body",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "## Notes\n\nFirst.\n" in body
        assert "## Notes\n\nSecond.\nAdded.\n" in body

    def test_prepend_selector_disambiguates_duplicate_heading(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "# API\n\n"
            "## Notes\n\nFirst.\n\n"
            "# API\n\n"
            "## Notes\n\nSecond.\n"
        )
        edit.prepend_to_artefact(
            str(vault), router, "Wiki/test-page.md",
            "Added first.\n",
            target="## Notes",
            selector={"within": [{"target": "# API", "occurrence": 2}]},
            scope="body",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "## Notes\n\nFirst.\n" in body
        assert "## Notes\nAdded first.\n\nSecond.\n" in body

    def test_legacy_section_target_is_rejected(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n"
        )
        with pytest.raises(ValueError, match="Use target='## Alpha' with scope='section'"):
            edit.append_to_artefact(
                str(vault), router, "Wiki/test-page.md",
                "Appended.\n", target=":section:## Alpha",
            )


# ---------------------------------------------------------------------------
# Trailing-newline invariant — bodies must end with \n after any mutation
# ---------------------------------------------------------------------------

class TestBodyTrailingNewline:
    """Pin the invariant that ``_apply_body_operation`` always returns a body
    ending in ``\\n`` when non-empty.

    The pre-target-scope refactor enforced this via ``_normalize_range_replacement``;
    after the refactor, EOF replacements via ``_prepare_boundary_safe_text`` could
    silently drop the trailing newline. Existing tests missed this because every
    fixture body ends in ``\\n`` and only checked substring containment.
    """

    @pytest.mark.parametrize(
        "label,body,operation,content,kwargs",
        [
            (
                "edit body of section at EOF",
                "# A\nbody1\n## B\ncontent\n",
                "edit",
                "newcontent",
                {"target": "## B", "scope": "body"},
            ),
            (
                "append body of section at EOF",
                "# A\nfoo\n",
                "append",
                "bar",
                {"target": "# A", "scope": "body"},
            ),
            (
                "edit :body section (whole replacement)",
                "# A\nbody1\n## B\ncontent\n",
                "edit",
                "totalreplace",
                {"target": ":body", "scope": "section"},
            ),
            (
                "edit body of heading with following section",
                "# A\nbody1\n## B\ncontent\n",
                "edit",
                "mid",
                {"target": "# A", "scope": "body"},
            ),
            (
                "edit heading line at EOF",
                "# A\nfoo\n## B\nbar\n",
                "edit",
                "## Renamed",
                {"target": "## B", "scope": "heading"},
            ),
            (
                "prepend to body of unique heading",
                "# A\nfoo\n",
                "prepend",
                "bar",
                {"target": "# A", "scope": "body"},
            ),
            (
                "append where content already ends with newline",
                "# A\nfoo\n",
                "append",
                "bar\n",
                {"target": "# A", "scope": "body"},
            ),
        ],
    )
    def test_result_ends_with_single_newline(self, label, body, operation, content, kwargs):
        result, _ = edit._apply_body_operation(body, operation, content, **kwargs)
        assert result.endswith("\n"), f"{label}: result did not end with newline: {result!r}"
        assert not result.endswith("\n\n"), f"{label}: result ends with double newline: {result!r}"

    def test_disk_file_ends_with_newline_after_eof_edit(self, vault, router):
        """End-to-end check: written file preserves trailing newline after EOF edit."""
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "# A\nbody1\n## B\ncontent\n"
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md",
            "newcontent", target="## B", scope="body",
        )
        on_disk = (vault / "Wiki" / "test-page.md").read_text()
        assert on_disk.endswith("\n"), f"on-disk file lost trailing newline: {on_disk!r}"


# ---------------------------------------------------------------------------
# Heading-body payload validation — symmetric across edit / append / prepend
# ---------------------------------------------------------------------------

class TestHeadingBodyPayloadValidation:
    """A heading-body or heading-intro payload that begins with a same-level or
    higher-level heading would silently restructure the document. ``edit``
    rejects this; ``append`` and ``prepend`` must reject it on the same terms.
    """

    @pytest.mark.parametrize("operation", ["edit", "append", "prepend"])
    @pytest.mark.parametrize("scope", ["body", "intro"])
    def test_same_level_heading_payload_rejected(self, operation, scope):
        body = "## Notes\nx\n## Other\ny\n"
        with pytest.raises(ValueError, match="only replaces the content below the heading"):
            edit._apply_body_operation(
                body, operation, "## Splice\nzz\n",
                target="## Notes", scope=scope,
            )

    @pytest.mark.parametrize("operation", ["edit", "append", "prepend"])
    @pytest.mark.parametrize("scope", ["body", "intro"])
    def test_higher_level_heading_payload_rejected(self, operation, scope):
        body = "## Notes\nx\n### Sub\ny\n"
        with pytest.raises(ValueError, match="only replaces the content below the heading"):
            edit._apply_body_operation(
                body, operation, "# H1 splice\n",
                target="## Notes", scope=scope,
            )

    @pytest.mark.parametrize("operation", ["append", "prepend"])
    @pytest.mark.parametrize("scope", ["body", "intro"])
    def test_deeper_level_heading_payload_allowed(self, operation, scope):
        """A deeper heading is content within the section — must be permitted."""
        body = "## Notes\nx\n## Other\ny\n"
        result, _ = edit._apply_body_operation(
            body, operation, "### Sub\nzz\n",
            target="## Notes", scope=scope,
        )
        assert "### Sub" in result

    @pytest.mark.parametrize("operation", ["append", "prepend"])
    def test_same_level_heading_payload_allowed_for_section_scope(self, operation):
        """scope='section' on a heading append/prepend is "act as a sibling" —
        a same-level heading payload is the user's intent, not a bug."""
        body = "## Notes\nx\n## Other\ny\n"
        result, _ = edit._apply_body_operation(
            body, operation, "## Splice\nzz\n",
            target="## Notes", scope="section",
        )
        assert "## Splice" in result

