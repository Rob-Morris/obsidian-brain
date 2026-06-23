"""Tests for edit.py — artefact editing, appending, and conversion."""

import os
import re
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

import edit
from _common import file_index_from_documents, parse_frontmatter, validate_artefact_folder


def test_scope_meanings_cover_valid_scopes():
    """Every public scope value should have matching help text."""
    for kind, operations in edit._VALID_SCOPES.items():
        all_scopes = set().union(*operations.values()) if operations else set()
        meanings = set(edit._SCOPE_MEANINGS.get(kind, {}).keys())
        missing = all_scopes - meanings
        assert not missing, (
            f"_SCOPE_MEANINGS[{kind!r}] missing entries for {sorted(missing)!r}; "
            "add the meaning(s) alongside the _VALID_SCOPES update."
        )


def test_scope_required_error_detailed_message_lists_meanings():
    err = edit.ScopeRequiredError("append", ":body", "body", ["intro", "section"])
    assert isinstance(err, edit.ScopeValidationError)
    assert err.detailed_message() == (
        "append with target=':body' requires scope. Valid scopes for body targets:\n"
        "  scope='intro' -> the lead paragraph(s) before the first heading\n"
        "  scope='section' -> the entire markdown body after frontmatter"
    )


def test_invalid_scope_error_detailed_message_lists_meanings():
    err = edit.InvalidScopeError("append", "header", "callout", ["body", "section"])
    assert isinstance(err, edit.ScopeValidationError)
    assert err.detailed_message() == (
        "scope='header' is not valid for append on callout targets. Valid scopes:\n"
        "  scope='body' -> the callout body (excludes the header line)\n"
        "  scope='section' -> the whole callout (header line plus body)"
    )


class TestValidateArtefactFolder:
    def test_valid_folder_returns_artefact(self, vault, router):
        art = validate_artefact_folder(str(vault), router, "Wiki/test-page.md")
        assert art["key"] == "wiki"

    def test_invalid_folder_raises(self, vault, router):
        with pytest.raises(ValueError, match="does not belong"):
            validate_artefact_folder(str(vault), router, "Unknown/file.md")

    def test_ignores_naming_pattern(self, vault, router):
        """File with non-conforming name in valid folder succeeds."""
        month = vault / "_Temporal" / "Logs" / "2026-03"
        month.mkdir(parents=True)
        (month / "bad-name.md").write_text("---\ntype: temporal/logs\n---\n")
        art = validate_artefact_folder(str(vault), router, "_Temporal/Logs/2026-03/bad-name.md")
        assert art["key"] == "logs"


class TestNonConformingNameOperations:
    """Edit/append renames non-conforming names to match the naming contract."""

    def test_edit_existing_file_with_nonconforming_name(self, vault, router):
        month = vault / "_Temporal" / "Logs" / "2026-03"
        month.mkdir(parents=True)
        (month / "legacy-log.md").write_text(
            "---\ntype: temporal/logs\ntags:\n  - log\n---\n\n# Old Log\n\nOld content.\n"
        )
        result = edit.edit_artefact(
            str(vault), router, "_Temporal/Logs/2026-03/legacy-log.md",
            "# New Log\n\nReplaced.\n",
            target=":body", scope="section",
        )
        assert result["operation"] == "edit"
        # Edit re-renders the filename from the current frontmatter state.
        # The legacy nonconforming name gets rewritten to the canonical form.
        renamed = month / "log-legacy-log.md"
        assert renamed.is_file()
        assert "Replaced." in renamed.read_text()

    def test_append_existing_file_with_nonconforming_name(self, vault, router):
        month = vault / "_Temporal" / "Logs" / "2026-03"
        month.mkdir(parents=True)
        (month / "legacy-log.md").write_text(
            "---\ntype: temporal/logs\ntags:\n  - log\n---\n\n# Old Log\n\nExisting.\n"
        )
        result = edit.append_to_artefact(
            str(vault), router, "_Temporal/Logs/2026-03/legacy-log.md",
            "\nAppended.\n",
            target=":body", scope="section",
        )
        assert result["operation"] == "append"
        renamed = month / "log-legacy-log.md"
        assert renamed.is_file()
        content = renamed.read_text()
        assert "Existing." in content
        assert "Appended." in content


# ---------------------------------------------------------------------------
# Edit tests
# ---------------------------------------------------------------------------

class TestEditArtefact:
    def test_edit_replaces_body(self, vault, router):
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md", "# New Body\n\nReplaced.\n",
            target=":body", scope="section",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        assert "Replaced." in content
        assert "Original body." not in content

    def test_edit_preserves_frontmatter(self, vault, router):
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md", "# New\n",
            target=":body", scope="section",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, _ = parse_frontmatter(content)
        assert fields["type"] == "living/wiki"
        assert fields["status"] == "active"
        assert "brain-core" in fields["tags"]

    def test_edit_rejects_body_with_frontmatter_block(self, vault, router):
        with pytest.raises(ValueError, match="must not start with a frontmatter block"):
            edit.edit_artefact(
                str(vault), router, "Wiki/test-page.md",
                "---\nstatus: shaping\n---\n\n# Body\n",
                target=":body", scope="section",
            )

    def test_edit_merges_frontmatter_changes(self, vault, router):
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md", "# New\n",
            frontmatter_changes={"status": "archived"},
            target=":body", scope="section",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, _ = parse_frontmatter(content)
        assert fields["status"] == "archived"
        assert fields["type"] == "living/wiki"  # preserved

    def test_edit_frontmatter_null_deletes_field(self, vault, router):
        """Setting a frontmatter field to None removes it."""
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md", "",
            frontmatter_changes={"status": None}
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, _ = parse_frontmatter(content)
        assert "status" not in fields
        assert "statusdate" not in fields  # no orphaned statusdate
        assert fields["type"] == "living/wiki"  # other fields preserved

    def test_edit_frontmatter_only_preserves_body(self, vault, router):
        """Editing only frontmatter (no body) must preserve the existing body."""
        original = (vault / "Wiki" / "test-page.md").read_text()
        _, original_body = parse_frontmatter(original)

        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md", "",
            frontmatter_changes={"status": "archived"}
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, body = parse_frontmatter(content)
        assert fields["status"] == "archived"
        assert body == original_body  # body preserved, not wiped

    def test_edit_target_body_section_clears_content(self, vault, router):
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md", "",
            frontmatter_changes={"status": "archived"},
            target=":body", scope="section",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, body = parse_frontmatter(content)
        assert fields["status"] == "archived"
        assert body.strip() == ""  # body intentionally cleared

    def test_edit_target_body_section_replaces_content(self, vault, router):
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md", "# New Content\n\nReplaced.",
            target=":body", scope="section",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "New Content" in body
        assert "Original body." not in body

    def test_edit_target_body_intro_replaces_only_leading_body(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "Intro text.\n\n"
            "> [!note] Status\n"
            "> Status content.\n"
            "\n"
            "## Notes\n\nNotes content.\n"
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md", "Updated intro.\n",
            target=":body", scope="intro",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert body.startswith("Updated intro.\n## Notes")
        assert "Intro text." not in body
        assert "Status content." not in body
        assert "Notes content." in body

    def test_edit_target_body_intro_inserts_before_heading_first_doc(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Notes\n\nNotes content.\n"
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md", "Lead text.\n",
            target=":body", scope="intro",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert body.startswith("Lead text.\n## Notes")
        assert body.count("## Notes") == 1

    def test_edit_target_body_intro_inserts_before_callout_first_doc(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "> [!note] Status\n"
            "> Status content.\n"
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md", "Lead text.\n",
            target=":body", scope="intro",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert body == "Lead text.\n"

    def test_edit_target_body_intro_replaces_whole_body_without_headings(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\nOnly body.\n"
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md", "Replacement body.\n",
            target=":body", scope="intro",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert body == "Replacement body.\n"

    def test_edit_target_body_before_first_heading_rejected(self, vault, router):
        with pytest.raises(ValueError, match="target=':body_before_first_heading' is no longer valid"):
            edit.edit_artefact(
                str(vault), router, "Wiki/test-page.md", "Replacement.\n",
                target=":body_before_first_heading",
            )

    def test_edit_target_body_requires_scope(self, vault, router):
        with pytest.raises(ValueError, match="requires scope"):
            edit.edit_artefact(
                str(vault), router, "Wiki/test-page.md", "Replacement.\n",
                target=":body",
            )

    def test_edit_file_not_found(self, vault, router):
        with pytest.raises(FileNotFoundError):
            edit.edit_artefact(
                str(vault), router, "Wiki/nonexistent.md", "body",
                target=":body", scope="section",
            )

    def test_edit_basename_fallback(self, vault, router):
        edit.edit_artefact(
            str(vault), router, "test-page", "# Resolved Body\n",
            target=":body", scope="section",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        assert "Resolved Body" in content
        assert "Original body." not in content

    def test_edit_full_path_without_md_extension(self, vault, router):
        """Agents should not need to pass the .md extension on full paths."""
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page", "# No Extension\n",
            target=":body", scope="section",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        assert "No Extension" in content


# ---------------------------------------------------------------------------
# Append tests
# ---------------------------------------------------------------------------

class TestAppendToArtefact:
    def test_append_adds_content_to_explicit_body_section(self, vault, router):
        edit.append_to_artefact(
            str(vault), router, "Wiki/test-page.md", "\n\nAppended text.\n",
            target=":body", scope="section",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        assert "Original body." in content
        assert "Appended text." in content

    def test_append_target_body_section_appends_to_whole_body(self, vault, router):
        edit.append_to_artefact(
            str(vault), router, "Wiki/test-page.md", "\n\nAppended text.\n",
            target=":body", scope="section",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        assert "Original body." in content
        assert "Appended text." in content

    def test_append_file_not_found(self, vault, router):
        with pytest.raises(FileNotFoundError):
            edit.append_to_artefact(
                str(vault), router, "Wiki/nonexistent.md", "text",
                target=":body", scope="section",
            )

    def test_append_target_body_requires_scope(self, vault, router):
        with pytest.raises(ValueError, match="requires scope"):
            edit.append_to_artefact(
                str(vault), router, "Wiki/test-page.md", "Extra.\n",
                target=":body",
            )

    def test_append_body_intro_is_valid(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "Lead.\n\n## Notes\n\nBody.\n"
        )
        edit.append_to_artefact(
            str(vault), router, "Wiki/test-page.md", "More lead.\n",
            target=":body", scope="intro",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "Lead.\n\nMore lead.\n## Notes" in body

    def test_append_basename_fallback(self, vault, router):
        edit.append_to_artefact(
            str(vault), router, "test-page", "\n\nAppended via basename.\n",
            target=":body", scope="section",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        assert "Original body." in content
        assert "Appended via basename." in content

    def test_append_full_path_without_md_extension(self, vault, router):
        """Agents should not need to pass the .md extension on full paths."""
        edit.append_to_artefact(
            str(vault), router, "Wiki/test-page", "\n\nNo ext append.\n",
            target=":body", scope="section",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        assert "No ext append." in content

