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


# ---------------------------------------------------------------------------
# Vault fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path):
    """Create a vault fixture with configured types and content."""
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.10.3\n")
    (bc / "session-core.md").write_text("# Session Core\n")

    config = tmp_path / "_Config"
    config.mkdir()
    (config / "router.md").write_text(
        "Prefer MCP tools.\n\nAlways:\n- Every artefact belongs in a typed folder.\n"
    )

    # Living type: Wiki
    wiki = tmp_path / "Wiki"
    wiki.mkdir()
    (wiki / "test-page.md").write_text(
        "---\ntype: living/wiki\ntags:\n  - brain-core\nstatus: active\n---\n\n"
        "# Test Page\n\nOriginal body.\n"
    )

    # Living type: Designs
    designs = tmp_path / "Designs"
    designs.mkdir()

    # Living type: Ideas
    ideas = tmp_path / "Ideas"
    ideas.mkdir()

    # Living type: Releases
    releases = tmp_path / "Releases"
    releases.mkdir()

    projects = tmp_path / "Projects"
    projects.mkdir()
    (projects / "Brain.md").write_text(
        "---\n"
        "type: living/project\n"
        "tags:\n"
        "  - project/brain\n"
        "key: brain\n"
        "---\n\n"
        "# Brain\n"
    )

    # Temporal type: Logs
    temporal = tmp_path / "_Temporal"
    temporal.mkdir()
    (temporal / "Logs").mkdir()
    (temporal / "Reports").mkdir()

    # Taxonomy: Wiki
    tax_living = config / "Taxonomy" / "Living"
    tax_living.mkdir(parents=True)
    (tax_living / "wiki.md").write_text(
        "# Wiki\n\n"
        "## Naming\n\n`{Title}.md` in `Wiki/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/wiki\ntags:\n  - topic-tag\n---\n```\n\n"
        "## Template\n\n[[_Config/Templates/Living/Wiki]]\n"
    )

    # Taxonomy: Designs (with multiple terminal statuses)
    (tax_living / "designs.md").write_text(
        "# Designs\n\n"
        "## Naming\n\n`{Title}.md` in `Designs/`.\n\n"
        "## Lifecycle\n\n"
        "| Status | Meaning |\n|---|---|\n"
        "| `shaping` | Being explored. |\n"
        "| `implemented` | Fully built. |\n"
        "| `superseded` | Replaced by a different approach. |\n"
        "| `rejected` | Declined. |\n\n"
        "## Terminal Status\n\n"
        "- set `status: implemented`, move to `Designs/+Implemented/`\n"
        "- set `status: superseded`, move to `Designs/+Superseded/`\n"
        "- set `status: rejected`, move to `Designs/+Rejected/`\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/designs\ntags:\n  - design-tag\n"
        "status: shaping             # shaping | implemented | superseded | rejected\n---\n```\n\n"
        "## Template\n\n[[_Config/Templates/Living/Designs]]\n"
    )

    # Taxonomy: Ideas (with terminal status)
    (tax_living / "ideas.md").write_text(
        "# Ideas\n\n"
        "## Naming\n\n`{Title}.md` in `Ideas/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/ideas\ntags: []\nstatus: seed\n---\n```\n\n"
        "Status values: `seed`, `shaping`, `adopted`.\n\n"
        "## Terminal Status\n\nWhen an idea reaches `adopted` status, it moves to `+Adopted/`.\n\n"
        "## Template\n\n[[_Config/Templates/Living/Ideas]]\n"
    )

    # Taxonomy: Releases (with two terminal statuses)
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
        "When a release reaches `shipped` status, move to `+Shipped/` within its current ownership context.\n"
        "Set `status: shipped` before the move.\n"
        "When a release reaches `cancelled` status, move to `+Cancelled/` within its current ownership context.\n"
        "Set `status: cancelled` before the move.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/release\ntags:\n  - release\n"
        "status: planned\nversion:\ntag:\ncommit:\nshipped:\n---\n```\n\n"
        "## Template\n\n[[_Config/Templates/Living/Releases]]\n"
    )

    (tax_living / "projects.md").write_text(
        "# Projects\n\n"
        "## Naming\n\n`{Title}.md` in `Projects/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/project\ntags:\n  - project\nkey:\n---\n```\n\n"
        "## Template\n\n[[_Config/Templates/Living/Projects]]\n"
    )

    # Taxonomy: Logs
    tax_temporal = config / "Taxonomy" / "Temporal"
    tax_temporal.mkdir(parents=True)
    (tax_temporal / "logs.md").write_text(
        "# Logs\n\n"
        "## Naming\n\n`log-{Title}.md` in `_Temporal/Logs/yyyy-mm/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: temporal/logs\ntags:\n  - session\n---\n```\n"
    )
    (tax_temporal / "reports.md").write_text(
        "# Reports\n\n"
        "## Naming\n\n`yyyymmdd-report~{Title}.md` in `_Temporal/Reports/yyyy-mm/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: temporal/reports\ntags:\n  - report\n---\n```\n"
    )

    # Taxonomy: Research
    (temporal / "Research").mkdir(exist_ok=True)
    (tax_temporal / "research.md").write_text(
        "# Research\n\n"
        "## Naming\n\n`yyyymmdd-research~{Title}.md` in `_Temporal/Research/yyyy-mm/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: temporal/research\ntags:\n  - research\n---\n```\n"
    )

    # Taxonomy: Reports
    (temporal / "Reports").mkdir(exist_ok=True)
    (tax_temporal / "reports.md").write_text(
        "# Reports\n\n"
        "## Naming\n\n`yyyymmdd-report~{Title}.md` in `_Temporal/Reports/yyyy-mm/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: temporal/reports\ntags:\n  - report\n---\n```\n"
    )

    # Templates
    templates_living = config / "Templates" / "Living"
    templates_living.mkdir(parents=True)
    (templates_living / "Wiki.md").write_text(
        "---\ntype: living/wiki\ntags: []\n---\n\n# {{title}}\n\n"
    )
    (templates_living / "Designs.md").write_text(
        "---\ntype: living/designs\ntags: []\nstatus: shaping\n---\n\n# {{title}}\n\n"
    )
    (templates_living / "Releases.md").write_text(
        "---\ntype: living/release\ntags:\n  - release\nstatus: planned\nversion:\ntag:\ncommit:\nshipped:\n---\n\n"
        "## Goal\n\n"
        "## Acceptance Criteria\n\n| Criterion | Status |\n|---|---|\n|  | pending |\n\n"
        "## Designs In Scope\n\n- \n\n"
        "## Release Notes\n\n"
        "## Sources\n\n- \n"
    )
    (templates_living / "Projects.md").write_text(
        "---\ntype: living/project\ntags: []\nkey:\n---\n\n# {{title}}\n\n"
    )

    return tmp_path


@pytest.fixture
def router(vault):
    """Compile the router for the vault fixture."""
    import compile_router
    return compile_router.compile(str(vault))


# ---------------------------------------------------------------------------
# Path validation tests
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Convert tests
# ---------------------------------------------------------------------------

class TestConvertArtefact:
    def test_convert_between_living_types(self, vault, router):
        result = edit.convert_artefact(str(vault), router, "Wiki/test-page.md", "designs")
        assert result["type"] == "living/designs"
        assert result["new_path"].startswith("Designs/")
        # Old file removed
        assert not (vault / "Wiki" / "test-page.md").exists()
        # New file exists
        assert os.path.isfile(os.path.join(str(vault), result["new_path"]))

    def test_convert_updates_frontmatter_type(self, vault, router):
        result = edit.convert_artefact(str(vault), router, "Wiki/test-page.md", "designs")
        abs_new = os.path.join(str(vault), result["new_path"])
        with open(abs_new) as f:
            content = f.read()
        fields, _ = parse_frontmatter(content)
        assert fields["type"] == "living/designs"

    def test_convert_preserves_body(self, vault, router):
        result = edit.convert_artefact(str(vault), router, "Wiki/test-page.md", "designs")
        abs_new = os.path.join(str(vault), result["new_path"])
        with open(abs_new) as f:
            content = f.read()
        assert "Original body." in content

    def test_convert_updates_wikilinks(self, vault, router):
        # Create a file that links to the source
        (vault / "Wiki" / "linker.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\nSee [[Wiki/test-page]].\n"
        )
        result = edit.convert_artefact(str(vault), router, "Wiki/test-page.md", "designs")
        content = (vault / "Wiki" / "linker.md").read_text()
        new_stem = result["new_path"][:-3]  # strip .md
        assert f"[[{new_stem}]]" in content
        assert "[[Wiki/test-page]]" not in content

    def test_convert_unknown_target_error(self, vault, router):
        with pytest.raises(ValueError, match="Unknown artefact type"):
            edit.convert_artefact(str(vault), router, "Wiki/test-page.md", "nonexistent")

    def test_convert_file_not_found(self, vault, router):
        with pytest.raises(FileNotFoundError):
            edit.convert_artefact(str(vault), router, "Wiki/gone.md", "designs")

    def test_convert_preserves_parent_subfolder(self, vault, router):
        """Canonical parent ownership survives convert across living types."""
        hub_dir = vault / "Ideas" / "project~brain"
        hub_dir.mkdir(parents=True)
        (hub_dir / "my-idea.md").write_text(
            "---\n"
            "type: living/ideas\n"
            "tags:\n"
            "  - project/brain\n"
            "key: my-idea\n"
            "parent: project/brain\n"
            "---\n\n"
            "# My Idea\n\nBody.\n"
        )
        result = edit.convert_artefact(
            str(vault), router, "Ideas/project~brain/my-idea.md", "designs"
        )
        assert result["new_path"].startswith("Designs/project~brain/")
        assert not (hub_dir / "my-idea.md").exists()
        assert os.path.isfile(os.path.join(str(vault), result["new_path"]))
        fields, _ = parse_frontmatter((vault / result["new_path"]).read_text())
        assert fields["parent"] == "project/brain"
        assert fields["key"] == "my-idea"

    def test_convert_flat_source_no_parent(self, vault, router):
        """Source directly in Ideas/ (no subfolder) should produce Designs/ with no subfolder."""
        (vault / "Ideas" / "flat-idea.md").write_text(
            "---\ntype: living/ideas\ntags: []\nkey: flat-idea\n---\n\n# Flat Idea\n\nBody.\n"
        )
        result = edit.convert_artefact(str(vault), router, "Ideas/flat-idea.md", "designs")
        assert result["new_path"].startswith("Designs/")
        assert "/" not in result["new_path"][len("Designs/"):]
        fields, _ = parse_frontmatter((vault / result["new_path"]).read_text())
        assert fields["key"] == "flat-idea"
        assert "parent" not in fields

    def test_convert_explicit_parent_override(self, vault, router):
        """Explicit canonical parent takes precedence over the source parent."""
        (vault / "Projects" / "Custom.md").write_text(
            "---\n"
            "type: living/project\n"
            "tags:\n"
            "  - project/custom\n"
            "key: custom\n"
            "---\n\n"
            "# Custom\n"
        )
        import compile_router
        router = compile_router.compile(str(vault))
        hub_dir = vault / "Ideas" / "project~brain"
        hub_dir.mkdir(parents=True)
        (hub_dir / "overridden.md").write_text(
            "---\n"
            "type: living/ideas\n"
            "tags:\n"
            "  - project/brain\n"
            "key: overridden\n"
            "parent: project/brain\n"
            "---\n\n"
            "# Overridden\n\nBody.\n"
        )
        result = edit.convert_artefact(
            str(vault), router, "Ideas/project~brain/overridden.md", "designs", parent="project/custom"
        )
        assert result["new_path"].startswith("Designs/project~custom/")
        fields, _ = parse_frontmatter((vault / result["new_path"]).read_text())
        assert fields["parent"] == "project/custom"

    def test_convert_rejects_target_type_key_collision(self, vault, router):
        (vault / "Ideas" / "collision.md").write_text(
            "---\ntype: living/ideas\ntags: []\nkey: shared\n---\n\n# Shared Idea\n"
        )
        (vault / "Designs" / "shared.md").write_text(
            "---\ntype: living/designs\ntags: []\nkey: shared\nstatus: shaping\n---\n\n# Existing Design\n"
        )
        import compile_router
        router = compile_router.compile(str(vault))
        with pytest.raises(ValueError, match="KEY_TAKEN"):
            edit.convert_artefact(str(vault), router, "Ideas/collision.md", "designs")

    def test_convert_living_child_to_temporal_preserves_parent_metadata(self, vault, router):
        child_dir = vault / "Ideas" / "project~brain"
        child_dir.mkdir(parents=True, exist_ok=True)
        (child_dir / "child-idea.md").write_text(
            "---\n"
            "type: living/ideas\n"
            "tags:\n"
            "  - project/brain\n"
            "key: child-idea\n"
            "parent: project/brain\n"
            "status: shaping\n"
            "---\n\n"
            "# Child Idea\n\nBody.\n"
        )

        result = edit.convert_artefact(
            str(vault), router, "Ideas/project~brain/child-idea.md", "reports"
        )

        assert result["new_path"].startswith("_Temporal/Reports/")
        assert "project~brain" not in result["new_path"]
        fields, _ = parse_frontmatter((vault / result["new_path"]).read_text())
        assert fields["parent"] == "project/brain"
        assert "project/brain" in fields["tags"]

    def test_convert_owner_to_temporal_removes_child_owner_reference_cleanly(self, vault, router):
        child_dir = vault / "Ideas" / "project~brain"
        child_dir.mkdir(parents=True, exist_ok=True)
        (child_dir / "child-idea.md").write_text(
            "---\n"
            "type: living/ideas\n"
            "tags:\n"
            "  - project/brain\n"
            "key: child-idea\n"
            "parent: project/brain\n"
            "status: shaping\n"
            "---\n\n"
            "# Child Idea\n\nBody.\n"
        )
        import compile_router
        router = compile_router.compile(str(vault))

        result = edit.convert_artefact(
            str(vault), router, "Projects/Brain.md", "reports"
        )

        assert result["new_path"].startswith("_Temporal/Reports/")
        relocated = vault / "Ideas" / "child-idea.md"
        assert relocated.is_file()
        fields, _ = parse_frontmatter(relocated.read_text())
        assert "parent" not in fields
        assert fields["tags"] == []

    def test_convert_owner_to_temporal_keeps_tag_only_reference_in_place(self, vault, router):
        (vault / "Wiki" / "tagged.md").write_text(
            "---\n"
            "type: living/wiki\n"
            "tags:\n"
            "  - note\n"
            "  - project/brain\n"
            "key: tagged\n"
            "---\n\n"
            "# Tagged\n\nBody.\n"
        )
        import compile_router
        router = compile_router.compile(str(vault))

        edit.convert_artefact(str(vault), router, "Projects/Brain.md", "reports")

        tagged = vault / "Wiki" / "tagged.md"
        assert tagged.is_file()
        fields, _ = parse_frontmatter(tagged.read_text())
        assert "parent" not in fields
        assert fields["tags"] == ["note"]

    def test_convert_temporal_to_temporal_strips_old_prefix(self, vault, router):
        """Converting research → reports must not nest the old prefix in the new filename.

        Regression test for the bug where the filename stem (including the old
        type's naming prefix) was used as the title when no title frontmatter was
        present, producing '20260413-report~20260413-research~Foo.md'.
        """
        import re
        research_dir = vault / "_Temporal" / "Research" / "2026-04"
        research_dir.mkdir(parents=True, exist_ok=True)
        src_name = "20260413-research~Sample Title.md"
        (research_dir / src_name).write_text(
            "---\ntype: temporal/research\ntags:\n  - research\n---\n\nBody.\n"
        )

        result = edit.convert_artefact(
            str(vault), router,
            f"_Temporal/Research/2026-04/{src_name}",
            "reports",
        )

        new_basename = os.path.basename(result["new_path"])
        assert "research~" not in new_basename, (
            f"Old prefix leaked into new filename: {new_basename}"
        )
        assert re.match(r"^\d{8}-report~Sample Title\.md$", new_basename), (
            f"Unexpected filename shape: {new_basename}"
        )

    def test_convert_collision_uses_standard_suffix_in_target_folder(self, vault, router):
        # Same created date → both converts target the same report folder and filename.
        month = vault / "_Temporal" / "Logs" / "2026-03"
        month.mkdir(parents=True)
        (month / "20260301-log-foo-a.md").write_text(
            "---\n"
            "type: temporal/logs\n"
            "title: Foo\n"
            "tags:\n"
            "  - report-source\n"
            "---\n\n"
            "First body.\n"
        )
        (month / "20260301-log-foo-b.md").write_text(
            "---\n"
            "type: temporal/logs\n"
            "title: Foo\n"
            "tags:\n"
            "  - report-source\n"
            "---\n\n"
            "Second body.\n"
        )

        first = edit.convert_artefact(
            str(vault), router, "_Temporal/Logs/2026-03/20260301-log-foo-a.md", "reports"
        )
        second = edit.convert_artefact(
            str(vault), router, "_Temporal/Logs/2026-03/20260301-log-foo-b.md", "reports"
        )

        assert first["new_path"] != second["new_path"]
        assert re.search(r" [a-z0-9]{3}\.md$", second["new_path"])
        assert "First body." in (vault / first["new_path"]).read_text()
        assert "Second body." in (vault / second["new_path"]).read_text()


# ---------------------------------------------------------------------------
# Append with section tests
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Timestamp tests
# ---------------------------------------------------------------------------

class TestEditTimestamps:
    FIXED_DT = datetime(2026, 4, 2, 10, 0, 0, tzinfo=timezone(timedelta(hours=11)))
    FIXED_ISO = "2026-04-02T10:00:00+11:00"

    def test_edit_updates_modified(self, vault, router):
        with patch("_common._templates.datetime") as mock_dt:
            mock_dt.now.return_value = self.FIXED_DT
            edit.edit_artefact(
                str(vault), router, "Wiki/test-page.md", "New body\n",
                target=":body", scope="section",
            )
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, _ = parse_frontmatter(content)
        assert fields["modified"] == self.FIXED_ISO

    def test_edit_does_not_change_created(self, vault, router):
        # Seed a stable ``created`` so this test exercises preservation rather
        # than reconciliation-on-absence (which is covered by reconcile tests).
        seed = (
            "---\ntype: living/wiki\ntags:\n  - brain-core\nstatus: active\n"
            "created: 2026-03-01T09:00:00+11:00\n---\n\n# Test Page\n\nOriginal body.\n"
        )
        (vault / "Wiki" / "test-page.md").write_text(seed)
        original_fields, _ = parse_frontmatter(seed)
        original_created = original_fields["created"]

        with patch("_common._templates.datetime") as mock_dt:
            mock_dt.now.return_value = self.FIXED_DT
            edit.edit_artefact(
                str(vault), router, "Wiki/test-page.md", "Changed body\n",
                target=":body", scope="section",
            )
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, _ = parse_frontmatter(content)
        assert fields["created"] == original_created

    def test_append_updates_modified(self, vault, router):
        with patch("_common._templates.datetime") as mock_dt:
            mock_dt.now.return_value = self.FIXED_DT
            edit.append_to_artefact(
                str(vault), router, "Wiki/test-page.md", "\nAppended\n",
                target=":body", scope="section",
            )
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, _ = parse_frontmatter(content)
        assert fields["modified"] == self.FIXED_ISO

    def test_prepend_updates_modified(self, vault, router):
        with patch("_common._templates.datetime") as mock_dt:
            mock_dt.now.return_value = self.FIXED_DT
            edit.prepend_to_artefact(
                str(vault), router, "Wiki/test-page.md", "Prepended\n",
                target=":body", scope="section",
            )
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, _ = parse_frontmatter(content)
        assert fields["modified"] == self.FIXED_ISO


# ---------------------------------------------------------------------------
# Frontmatter merge tests
# ---------------------------------------------------------------------------

class TestFrontmatterMerge:
    def test_edit_overwrites_list_field(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags:\n  - existing-1\n  - existing-2\n---\n\nBody.\n"
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md", "Body.\n",
            frontmatter_changes={"tags": ["new"]},
            target=":body", scope="section",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, _ = parse_frontmatter(content)
        assert fields["tags"] == ["new"]

    def test_append_extends_list_field(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags:\n  - existing-1\n  - existing-2\n---\n\nBody.\n"
        )
        edit.append_to_artefact(
            str(vault), router, "Wiki/test-page.md",
            frontmatter_changes={"tags": ["new-tag"]},
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, _ = parse_frontmatter(content)
        assert fields["tags"] == ["existing-1", "existing-2", "new-tag"]

    def test_prepend_extends_list_field(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags:\n  - existing-1\n  - existing-2\n---\n\nBody.\n"
        )
        edit.prepend_to_artefact(
            str(vault), router, "Wiki/test-page.md",
            frontmatter_changes={"tags": ["new-tag"]},
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, _ = parse_frontmatter(content)
        assert fields["tags"] == ["existing-1", "existing-2", "new-tag"]

    def test_append_deduplicates(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags:\n  - existing\n---\n\nBody.\n"
        )
        edit.append_to_artefact(
            str(vault), router, "Wiki/test-page.md",
            frontmatter_changes={"tags": ["existing", "new"]},
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, _ = parse_frontmatter(content)
        assert fields["tags"] == ["existing", "new"]

    def test_append_overwrites_scalar(self, vault, router):
        edit.append_to_artefact(
            str(vault), router, "Wiki/test-page.md",
            frontmatter_changes={"status": "archived"},
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, _ = parse_frontmatter(content)
        assert fields["status"] == "archived"

    def test_append_frontmatter_only(self, vault, router):
        """Empty body + frontmatter changes = frontmatter-only mutation."""
        original = (vault / "Wiki" / "test-page.md").read_text()
        _, original_body = parse_frontmatter(original)

        edit.append_to_artefact(
            str(vault), router, "Wiki/test-page.md",
            frontmatter_changes={"status": "archived"},
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, body = parse_frontmatter(content)
        assert fields["status"] == "archived"
        assert body == original_body  # body unchanged

    def test_prepend_frontmatter_only(self, vault, router):
        """Empty body + frontmatter changes = frontmatter-only mutation."""
        original = (vault / "Wiki" / "test-page.md").read_text()
        _, original_body = parse_frontmatter(original)

        edit.prepend_to_artefact(
            str(vault), router, "Wiki/test-page.md",
            frontmatter_changes={"status": "archived"},
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, body = parse_frontmatter(content)
        assert fields["status"] == "archived"
        assert body == original_body  # body unchanged

    def test_targeted_append_frontmatter_only_omits_structural_target(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nBody.\n"
        )
        result = edit.append_to_artefact(
            str(vault), router, "Wiki/test-page.md",
            frontmatter_changes={"status": "archived"},
            target="## Alpha", scope="body",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, body = parse_frontmatter(content)
        assert fields["status"] == "archived"
        assert body == "## Alpha\n\nBody.\n"
        assert "structural_target" not in result

    def test_targeted_prepend_frontmatter_only_omits_structural_target(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nBody.\n"
        )
        result = edit.prepend_to_artefact(
            str(vault), router, "Wiki/test-page.md",
            frontmatter_changes={"status": "archived"},
            target="## Alpha", scope="body",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, body = parse_frontmatter(content)
        assert fields["status"] == "archived"
        assert body == "## Alpha\n\nBody.\n"
        assert "structural_target" not in result


# ---------------------------------------------------------------------------
# Ownership path regression tests
# ---------------------------------------------------------------------------

class TestOwnershipEditPaths:
    def test_temporal_parent_edit_persists_without_rehoming(self, vault, router):
        month = vault / "_Temporal" / "Research" / "2026-04"
        month.mkdir(parents=True, exist_ok=True)
        path = month / "20260413-research~Sample Title.md"
        path.write_text(
            "---\n"
            "type: temporal/research\n"
            "tags:\n"
            "  - research\n"
            "created: 2026-04-13T09:00:00+10:00\n"
            "---\n\n"
            "Body.\n"
        )

        result = edit.edit_artefact(
            str(vault),
            router,
            "_Temporal/Research/2026-04/20260413-research~Sample Title.md",
            "",
            frontmatter_changes={"parent": "project/brain"},
        )

        assert result["path"] == "_Temporal/Research/2026-04/20260413-research~Sample Title.md"
        fields, _ = parse_frontmatter(path.read_text())
        assert fields["parent"] == "project/brain"
        assert "project/brain" in fields["tags"]

    def test_parent_key_change_rehomes_children_using_canonical_owner_folder(self, vault, router):
        child_dir = vault / "Ideas" / "project~brain"
        child_dir.mkdir(parents=True, exist_ok=True)
        (child_dir / "child-idea.md").write_text(
            "---\n"
            "type: living/ideas\n"
            "tags:\n"
            "  - project/brain\n"
            "key: child-idea\n"
            "parent: project/brain\n"
            "status: shaping\n"
            "---\n\n"
            "# Child Idea\n\nBody.\n"
        )

        result = edit.edit_artefact(
            str(vault),
            router,
            "Projects/Brain.md",
            "",
            frontmatter_changes={"key": "brain2"},
        )

        assert result["path"] == "Projects/Brain.md"
        relocated = vault / "Ideas" / "project~brain2" / "child-idea.md"
        assert relocated.is_file()
        assert not (vault / "Ideas" / "project" / "brain2" / "child-idea.md").exists()
        fields, _ = parse_frontmatter(relocated.read_text())
        assert fields["parent"] == "project/brain2"
        assert "project/brain2" in fields["tags"]

    def test_parent_key_change_updates_temporal_children_without_rehoming(self, vault, router):
        month = vault / "_Temporal" / "Research" / "2026-04"
        month.mkdir(parents=True, exist_ok=True)
        path = month / "20260413-research~Sample Title.md"
        path.write_text(
            "---\n"
            "type: temporal/research\n"
            "tags:\n"
            "  - research\n"
            "  - project/brain\n"
            "parent: project/brain\n"
            "created: 2026-04-13T09:00:00+10:00\n"
            "---\n\n"
            "Body.\n"
        )

        result = edit.edit_artefact(
            str(vault),
            router,
            "Projects/Brain.md",
            "",
            frontmatter_changes={"key": "brain2"},
        )

        assert result["path"] == "Projects/Brain.md"
        assert path.is_file()
        fields, _ = parse_frontmatter(path.read_text())
        assert fields["parent"] == "project/brain2"
        assert "project/brain2" in fields["tags"]
        assert "project/brain" not in fields["tags"]

    def test_parent_edit_keeps_existing_terminal_status_folder(self, vault, router):
        (vault / "Projects" / "Custom.md").write_text(
            "---\n"
            "type: living/project\n"
            "tags:\n"
            "  - project/custom\n"
            "key: custom\n"
            "---\n\n"
            "# Custom\n"
        )
        import compile_router
        router = compile_router.compile(str(vault))

        adopted_dir = vault / "Ideas" / "project~brain" / "+Adopted"
        adopted_dir.mkdir(parents=True, exist_ok=True)
        (adopted_dir / "adopted-idea.md").write_text(
            "---\n"
            "type: living/ideas\n"
            "tags:\n"
            "  - project/brain\n"
            "key: adopted-idea\n"
            "parent: project/brain\n"
            "status: adopted\n"
            "---\n\n"
            "# Adopted Idea\n\nBody.\n"
        )

        result = edit.edit_artefact(
            str(vault),
            router,
            "Ideas/project~brain/+Adopted/adopted-idea.md",
            "",
            frontmatter_changes={"parent": "project/custom"},
        )

        assert result["path"] == "Ideas/project~custom/+Adopted/adopted-idea.md"
        relocated = vault / "Ideas" / "project~custom" / "+Adopted" / "adopted-idea.md"
        assert relocated.is_file()
        assert not (vault / "Ideas" / "project~custom" / "adopted-idea.md").exists()
        fields, _ = parse_frontmatter(relocated.read_text())
        assert fields["parent"] == "project/custom"


# ---------------------------------------------------------------------------
# Terminal status auto-move tests
# ---------------------------------------------------------------------------

class TestTerminalStatusMove:
    """Tests for automatic file movement on terminal status changes."""

    def _make_idea(self, vault, path, status="seed", body="# Idea\n\nBody.\n"):
        """Helper to create an idea file at the given relative path."""
        abs_path = vault / path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(
            f"---\ntype: living/ideas\ntags: []\nstatus: {status}\n---\n\n{body}"
        )

    def _make_release(
        self,
        vault,
        path,
        status="active",
        version="v0.28.6",
        body=(
            "## Goal\n\nShip it.\n\n"
            "## Acceptance Criteria\n\n| Criterion | Status |\n|---|---|\n| Ship it | pending |\n\n"
            "## Designs In Scope\n\n- [[Brain Master Design]]\n\n"
            "## Release Notes\n\n"
            "## Sources\n\n- [[Brain Master Design]]\n"
        ),
    ):
        """Helper to create a release file at the given relative path."""
        abs_path = vault / path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(
            "---\n"
            "type: living/release\n"
            "tags:\n"
            "  - release\n"
            "  - project/brain\n"
            "parent: project/brain\n"
            f"status: {status}\n"
            f"version: {version}\n"
            "tag:\n"
            "commit:\n"
            "shipped:\n"
            "---\n\n"
            f"{body}"
        )

    def test_edit_terminal_status_moves_to_plus_folder(self, vault, router):
        self._make_idea(vault, "Ideas/my-idea.md")
        result = edit.edit_artefact(
            str(vault), router, "Ideas/my-idea.md", "",
            frontmatter_changes={"status": "adopted"},
        )
        assert result["path"] == "Ideas/+Adopted/my-idea.md"
        assert (vault / "Ideas" / "+Adopted" / "my-idea.md").is_file()
        assert not (vault / "Ideas" / "my-idea.md").exists()

    def test_edit_terminal_status_creates_folder(self, vault, router):
        self._make_idea(vault, "Ideas/new-idea.md")
        assert not (vault / "Ideas" / "+Adopted").exists()
        edit.edit_artefact(
            str(vault), router, "Ideas/new-idea.md", "",
            frontmatter_changes={"status": "adopted"},
        )
        assert (vault / "Ideas" / "+Adopted").is_dir()
        assert (vault / "Ideas" / "+Adopted" / "new-idea.md").is_file()

    def test_edit_terminal_status_updates_wikilinks(self, vault, router):
        self._make_idea(vault, "Ideas/linked-idea.md")
        (vault / "Wiki" / "linker.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\nSee [[Ideas/linked-idea]].\n"
        )
        edit.edit_artefact(
            str(vault), router, "Ideas/linked-idea.md", "",
            frontmatter_changes={"status": "adopted"},
        )
        content = (vault / "Wiki" / "linker.md").read_text()
        assert "[[Ideas/+Adopted/linked-idea]]" in content
        assert "[[Ideas/linked-idea]]" not in content

    def test_edit_non_terminal_status_no_move(self, vault, router):
        self._make_idea(vault, "Ideas/staying.md")
        result = edit.edit_artefact(
            str(vault), router, "Ideas/staying.md", "",
            frontmatter_changes={"status": "shaping"},
        )
        assert result["path"] == "Ideas/staying.md"
        assert (vault / "Ideas" / "staying.md").is_file()

    def test_edit_already_in_plus_folder_no_move(self, vault, router):
        self._make_idea(vault, "Ideas/+Adopted/already.md", status="adopted")
        result = edit.edit_artefact(
            str(vault), router, "Ideas/+Adopted/already.md", "",
            frontmatter_changes={"status": "adopted"},
        )
        assert result["path"] == "Ideas/+Adopted/already.md"
        assert (vault / "Ideas" / "+Adopted" / "already.md").is_file()

    def test_edit_no_status_change_no_move(self, vault, router):
        self._make_idea(vault, "Ideas/body-only.md")
        result = edit.edit_artefact(
            str(vault), router, "Ideas/body-only.md", "# Updated\n\nNew body.\n",
            target=":body", scope="section",
        )
        assert result["path"] == "Ideas/body-only.md"
        assert (vault / "Ideas" / "body-only.md").is_file()

    def test_append_terminal_status_moves(self, vault, router):
        self._make_idea(vault, "Ideas/append-idea.md")
        result = edit.append_to_artefact(
            str(vault), router, "Ideas/append-idea.md", "\nExtra.\n",
            target=":body", scope="section",
            frontmatter_changes={"status": "adopted"},
        )
        assert result["path"] == "Ideas/+Adopted/append-idea.md"
        assert (vault / "Ideas" / "+Adopted" / "append-idea.md").is_file()

    def test_edit_terminal_status_with_subfolder(self, vault, router):
        self._make_idea(vault, "Ideas/Brain/project-idea.md")
        result = edit.edit_artefact(
            str(vault), router, "Ideas/Brain/project-idea.md", "",
            frontmatter_changes={"status": "adopted"},
        )
        assert result["path"] == "Ideas/Brain/+Adopted/project-idea.md"
        assert (vault / "Ideas" / "Brain" / "+Adopted" / "project-idea.md").is_file()
        assert not (vault / "Ideas" / "Brain" / "project-idea.md").exists()

    def test_release_shipped_moves_to_project_status_folder(self, vault, router):
        # Pre-ship releases use title-led filenames; shipping renames to version-led.
        self._make_release(vault, "Releases/project~brain/Search Hardening.md", version="v0.28.6")
        result = edit.edit_artefact(
            str(vault),
            router,
            "Releases/project~brain/Search Hardening.md",
            "",
            frontmatter_changes={"status": "shipped", "shipped": "2026-04-16"},
        )
        assert result["path"] == "Releases/project~brain/+Shipped/v0.28.6 - Search Hardening.md"
        assert (vault / "Releases" / "project~brain" / "+Shipped" / "v0.28.6 - Search Hardening.md").is_file()

    def test_release_cancelled_moves_to_project_status_folder(self, vault, router):
        # Cancelled releases stay title-led — no version in the filename.
        self._make_release(vault, "Releases/project~brain/Experimental Cut.md")
        result = edit.edit_artefact(
            str(vault),
            router,
            "Releases/project~brain/Experimental Cut.md",
            "",
            frontmatter_changes={"status": "cancelled"},
        )
        assert result["path"] == "Releases/project~brain/+Cancelled/Experimental Cut.md"
        assert (vault / "Releases" / "project~brain" / "+Cancelled" / "Experimental Cut.md").is_file()

    def test_edit_no_terminal_defined(self, vault, router):
        """Type with no terminal_statuses doesn't move on status change."""
        result = edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md", "",
            frontmatter_changes={"status": "archived"},
        )
        assert result["path"] == "Wiki/test-page.md"
        assert (vault / "Wiki" / "test-page.md").is_file()

    def test_edit_revive_from_terminal(self, vault, router):
        """Non-terminal status on file in +Status/ folder moves it back out."""
        self._make_idea(vault, "Ideas/+Adopted/revived.md", status="adopted")
        result = edit.edit_artefact(
            str(vault), router, "Ideas/+Adopted/revived.md", "",
            frontmatter_changes={"status": "shaping"},
        )
        assert result["path"] == "Ideas/revived.md"
        assert (vault / "Ideas" / "revived.md").is_file()
        assert not (vault / "Ideas" / "+Adopted" / "revived.md").exists()

    def test_edit_revive_from_subfolder(self, vault, router):
        """Revive from project subfolder +Status/ moves up one level."""
        self._make_idea(vault, "Ideas/Brain/+Adopted/sub-revive.md", status="adopted")
        result = edit.edit_artefact(
            str(vault), router, "Ideas/Brain/+Adopted/sub-revive.md", "",
            frontmatter_changes={"status": "shaping"},
        )
        assert result["path"] == "Ideas/Brain/sub-revive.md"
        assert (vault / "Ideas" / "Brain" / "sub-revive.md").is_file()
        assert not (vault / "Ideas" / "Brain" / "+Adopted" / "sub-revive.md").exists()

    def test_edit_revive_cleans_empty_folder(self, vault, router):
        """Reviving last file from +Adopted/ removes the empty folder."""
        self._make_idea(vault, "Ideas/+Adopted/last-one.md", status="adopted")
        edit.edit_artefact(
            str(vault), router, "Ideas/+Adopted/last-one.md", "",
            frontmatter_changes={"status": "shaping"},
        )
        assert not (vault / "Ideas" / "+Adopted").exists()

    def test_edit_revive_keeps_nonempty_folder(self, vault, router):
        """Reviving one file from +Adopted/ when others remain keeps the folder."""
        self._make_idea(vault, "Ideas/+Adopted/leaving.md", status="adopted")
        self._make_idea(vault, "Ideas/+Adopted/staying.md", status="adopted")
        edit.edit_artefact(
            str(vault), router, "Ideas/+Adopted/leaving.md", "",
            frontmatter_changes={"status": "shaping"},
        )
        assert (vault / "Ideas" / "+Adopted").is_dir()
        assert (vault / "Ideas" / "+Adopted" / "staying.md").is_file()
        assert (vault / "Ideas" / "leaving.md").is_file()

    def test_edit_terminal_to_different_terminal_no_nesting(self, vault, router):
        """Changing terminal status on a file already in +Status/ moves to sibling folder, not nested."""
        # Designs have multiple terminal statuses: implemented, superseded, rejected
        abs_path = vault / "Designs" / "+Implemented" / "my-design.md"
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(
            "---\ntype: living/design\ntags: [design]\nstatus: implemented\n---\n\n# Design\n"
        )
        result = edit.edit_artefact(
            str(vault), router, "Designs/+Implemented/my-design.md", "",
            frontmatter_changes={"status": "superseded"},
        )
        assert result["path"] == "Designs/+Superseded/my-design.md"
        assert (vault / "Designs" / "+Superseded" / "my-design.md").is_file()
        assert not (vault / "Designs" / "+Implemented" / "+Superseded" / "my-design.md").exists()
        assert not (vault / "Designs" / "+Implemented" / "my-design.md").exists()


# ---------------------------------------------------------------------------
# Statusdate auto-set tests
# ---------------------------------------------------------------------------

class TestStatusDate:
    """Tests that statusdate is auto-set on status transitions."""

    def _make_idea(self, vault, path, status="seed", body="# Idea\n\nBody.\n",
                   extra_fm=""):
        """Helper to create an idea file at the given relative path."""
        abs_path = vault / path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(
            f"---\ntype: living/ideas\ntags: []\nstatus: {status}\n{extra_fm}---\n\n{body}"
        )

    def _read_fields(self, vault, path):
        """Read back frontmatter fields from a file."""
        content = (vault / path).read_text()
        fields, _ = parse_frontmatter(content)
        return fields

    def test_status_change_sets_statusdate(self, vault, router):
        """Changing status from seed to shaping sets statusdate."""
        self._make_idea(vault, "Ideas/sd-idea.md", status="seed")
        edit.edit_artefact(
            str(vault), router, "Ideas/sd-idea.md", "",
            frontmatter_changes={"status": "shaping"},
        )
        fields = self._read_fields(vault, "Ideas/sd-idea.md")
        assert "statusdate" in fields
        assert len(fields["statusdate"]) == 10  # YYYY-MM-DD

    def test_no_status_change_no_statusdate(self, vault, router):
        """Body-only edit does not add statusdate."""
        self._make_idea(vault, "Ideas/no-sd.md", status="seed")
        edit.edit_artefact(
            str(vault), router, "Ideas/no-sd.md", "Updated body.",
            target=":body", scope="section",
        )
        fields = self._read_fields(vault, "Ideas/no-sd.md")
        assert "statusdate" not in fields

    def test_same_status_no_update(self, vault, router):
        """Setting status to its current value preserves existing statusdate."""
        self._make_idea(vault, "Ideas/same-sd.md", status="shaping",
                        extra_fm="statusdate: '2020-01-01'\n")
        edit.edit_artefact(
            str(vault), router, "Ideas/same-sd.md", "",
            frontmatter_changes={"status": "shaping"},
        )
        fields = self._read_fields(vault, "Ideas/same-sd.md")
        assert fields["statusdate"] == "2020-01-01"

    def test_terminal_status_sets_statusdate(self, vault, router):
        """Adopting an idea sets statusdate on the moved file."""
        self._make_idea(vault, "Ideas/term-sd.md", status="seed")
        edit.edit_artefact(
            str(vault), router, "Ideas/term-sd.md", "",
            frontmatter_changes={"status": "adopted"},
        )
        fields = self._read_fields(vault, "Ideas/+Adopted/term-sd.md")
        assert "statusdate" in fields
        assert len(fields["statusdate"]) == 10

    def test_revive_updates_statusdate(self, vault, router):
        """Reviving from +Adopted/ updates statusdate."""
        self._make_idea(vault, "Ideas/+Adopted/revive-sd.md", status="adopted",
                        extra_fm="statusdate: '2020-01-01'\n")
        edit.edit_artefact(
            str(vault), router, "Ideas/+Adopted/revive-sd.md", "",
            frontmatter_changes={"status": "shaping"},
        )
        fields = self._read_fields(vault, "Ideas/revive-sd.md")
        assert "statusdate" in fields
        assert fields["statusdate"] != "2020-01-01"

    def test_append_status_change_sets_statusdate(self, vault, router):
        """Append operation with status change sets statusdate."""
        self._make_idea(vault, "Ideas/app-sd.md", status="seed")
        edit.append_to_artefact(
            str(vault), router, "Ideas/app-sd.md", "Extra content.",
            target=":body", scope="section",
            frontmatter_changes={"status": "shaping"},
        )
        fields = self._read_fields(vault, "Ideas/app-sd.md")
        assert "statusdate" in fields
        assert len(fields["statusdate"]) == 10


class TestArchiveGuards:
    """Tests that _Archive/ files are immune to auto-move and convert."""

    def _make_archived_idea(self, vault, path="Ideas/_Archive/20260101-old-idea.md"):
        abs_path = vault / path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(
            "---\ntype: living/ideas\ntags: []\nstatus: adopted\n"
            "archiveddate: 2026-01-01\n---\n\nOld idea.\n"
        )

    def test_status_move_skipped_for_archived_file(self, vault, router):
        """Terminal status change on archived file does NOT create +Status/ inside _Archive/."""
        self._make_archived_idea(vault)
        result = edit.edit_artefact(
            str(vault), router, "Ideas/_Archive/20260101-old-idea.md", "",
            frontmatter_changes={"status": "adopted"},
        )
        assert result["path"] == "Ideas/_Archive/20260101-old-idea.md"
        assert not (vault / "Ideas" / "_Archive" / "+Adopted").exists()

    def test_nonterminal_status_on_archived_stays(self, vault, router):
        """Non-terminal status on archived file stays in _Archive/, frontmatter updated."""
        self._make_archived_idea(vault)
        result = edit.edit_artefact(
            str(vault), router, "Ideas/_Archive/20260101-old-idea.md", "",
            frontmatter_changes={"status": "shaping"},
        )
        assert result["path"] == "Ideas/_Archive/20260101-old-idea.md"
        content = (vault / "Ideas" / "_Archive" / "20260101-old-idea.md").read_text()
        fields, _ = parse_frontmatter(content)
        assert fields["status"] == "shaping"

    def test_edit_archived_file_body_succeeds(self, vault, router):
        """Body edits on archived files work normally."""
        self._make_archived_idea(vault)
        result = edit.edit_artefact(
            str(vault), router, "Ideas/_Archive/20260101-old-idea.md",
            "# Updated\n\nFixed body.\n",
            target=":body", scope="section",
        )
        assert result["path"] == "Ideas/_Archive/20260101-old-idea.md"
        content = (vault / "Ideas" / "_Archive" / "20260101-old-idea.md").read_text()
        assert "Fixed body." in content

    def test_append_archived_skips_status_move(self, vault, router):
        """Append with terminal status on archived file doesn't move."""
        self._make_archived_idea(vault)
        result = edit.append_to_artefact(
            str(vault), router, "Ideas/_Archive/20260101-old-idea.md", "\nExtra.\n",
            target=":body", scope="section",
            frontmatter_changes={"status": "adopted"},
        )
        assert result["path"] == "Ideas/_Archive/20260101-old-idea.md"
        assert not (vault / "Ideas" / "_Archive" / "+Adopted").exists()

    def test_convert_archived_file_raises(self, vault, router):
        """Converting an archived file raises ValueError."""
        self._make_archived_idea(vault)
        with pytest.raises(ValueError, match="Cannot convert archived file"):
            edit.convert_artefact(
                str(vault), router, "Ideas/_Archive/20260101-old-idea.md", "designs"
            )


class TestArchiveArtefact:
    """Tests for brain_move(op='archive') — archive_artefact()."""

    def _make_idea(self, vault, name="my-idea.md", status="adopted", project=None):
        if project:
            folder = vault / "Ideas" / project
        else:
            folder = vault / "Ideas"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        path.write_text(
            f"---\ntype: living/ideas\ntags: []\nstatus: {status}\n---\n\nIdea body.\n"
        )
        if project:
            return f"Ideas/{project}/{name}"
        return f"Ideas/{name}"

    def test_archive_moves_to_top_level(self, vault, router):
        rel = self._make_idea(vault)
        result = edit.archive_artefact(str(vault), router, rel)
        assert result["new_path"].startswith("_Archive/Ideas/")
        assert not (vault / rel).exists()
        assert (vault / result["new_path"]).exists()

    def test_archive_adds_date_prefix(self, vault, router):
        rel = self._make_idea(vault)
        result = edit.archive_artefact(str(vault), router, rel)
        filename = os.path.basename(result["new_path"])
        assert filename[8] == "-"  # yyyymmdd-
        assert filename[9:] == "my-idea.md"

    def test_archive_adds_archiveddate(self, vault, router):
        rel = self._make_idea(vault)
        result = edit.archive_artefact(str(vault), router, rel)
        content = (vault / result["new_path"]).read_text()
        fields, _ = parse_frontmatter(content)
        assert "archiveddate" in fields

    def test_archive_preserves_project_structure(self, vault, router):
        rel = self._make_idea(vault, project="Brain")
        result = edit.archive_artefact(str(vault), router, rel)
        assert "_Archive/Ideas/Brain/" in result["new_path"]

    def test_archive_refuses_non_terminal_status(self, vault, router):
        rel = self._make_idea(vault, status="shaping")
        with pytest.raises(ValueError, match="not terminal"):
            edit.archive_artefact(str(vault), router, rel)

    def test_archive_refuses_already_archived(self, vault, router):
        archive = vault / "Ideas" / "_Archive"
        archive.mkdir(parents=True)
        (archive / "20260101-old.md").write_text(
            "---\ntype: living/ideas\ntags: []\nstatus: adopted\n"
            "archiveddate: 2026-01-01\n---\n\nOld.\n"
        )
        with pytest.raises(ValueError, match="already archived"):
            edit.archive_artefact(str(vault), router, "Ideas/_Archive/20260101-old.md")

    def test_archive_strips_status_folder(self, vault, router):
        """Archiving from +Adopted/ should not include +Adopted in archive path."""
        status_dir = vault / "Ideas" / "+Adopted"
        status_dir.mkdir(parents=True)
        (status_dir / "my-idea.md").write_text(
            "---\ntype: living/ideas\ntags: []\nstatus: adopted\n---\n\nBody.\n"
        )
        result = edit.archive_artefact(str(vault), router, "Ideas/+Adopted/my-idea.md")
        assert "+Adopted" not in result["new_path"]
        assert result["new_path"].startswith("_Archive/Ideas/")

    def test_archive_updates_wikilinks(self, vault, router):
        rel = self._make_idea(vault)
        (vault / "Wiki" / "linker.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\nSee [[my-idea]].\n"
        )
        result = edit.archive_artefact(str(vault), router, rel)
        content = (vault / "Wiki" / "linker.md").read_text()
        new_stem = os.path.splitext(os.path.basename(result["new_path"]))[0]
        assert new_stem in content


class TestUnarchiveArtefact:
    """Tests for brain_move(op='unarchive') — unarchive_artefact()."""

    def _make_archived(self, vault, rel="_Archive/Ideas/20260101-my-idea.md"):
        p = vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            "---\ntype: living/ideas\ntags: []\nstatus: adopted\n"
            "archiveddate: 2026-01-01\n---\n\nOld idea.\n"
        )
        return rel

    def test_unarchive_moves_to_type_folder(self, vault, router):
        rel = self._make_archived(vault)
        result = edit.unarchive_artefact(str(vault), router, rel)
        assert result["new_path"] == "Ideas/my-idea.md"
        assert not (vault / rel).exists()
        assert (vault / result["new_path"]).exists()

    def test_unarchive_strips_date_prefix(self, vault, router):
        rel = self._make_archived(vault)
        result = edit.unarchive_artefact(str(vault), router, rel)
        assert "20260101-" not in result["new_path"]

    def test_unarchive_removes_archiveddate(self, vault, router):
        rel = self._make_archived(vault)
        result = edit.unarchive_artefact(str(vault), router, rel)
        content = (vault / result["new_path"]).read_text()
        fields, _ = parse_frontmatter(content)
        assert "archiveddate" not in fields

    def test_unarchive_preserves_project_structure(self, vault, router):
        rel = self._make_archived(vault, "_Archive/Ideas/Brain/20260101-my-idea.md")
        result = edit.unarchive_artefact(str(vault), router, rel)
        assert result["new_path"] == "Ideas/Brain/my-idea.md"

    def test_unarchive_refuses_non_archived(self, vault, router):
        (vault / "Ideas" / "live-idea.md").write_text(
            "---\ntype: living/ideas\ntags: []\nstatus: shaping\n---\n\nLive.\n"
        )
        with pytest.raises(ValueError, match="not in _Archive"):
            edit.unarchive_artefact(str(vault), router, "Ideas/live-idea.md")

    def test_unarchive_updates_wikilinks(self, vault, router):
        rel = self._make_archived(vault)
        (vault / "Wiki" / "linker.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\nSee [[20260101-my-idea]].\n"
        )
        result = edit.unarchive_artefact(str(vault), router, rel)
        content = (vault / "Wiki" / "linker.md").read_text()
        assert "my-idea" in content


class TestDeleteSection:
    def test_delete_middle_section(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n\n## Gamma\n\nGamma content.\n"
        )
        edit.delete_section_artefact(str(vault), router, "Wiki/test-page.md", target="Beta")
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "## Alpha" in body
        assert "Alpha content." in body
        assert "## Beta" not in body
        assert "Beta content." not in body
        assert "## Gamma" in body
        assert "Gamma content." in body
        assert "\n\n\n" not in body  # no double-blank

    def test_delete_first_section(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n"
        )
        edit.delete_section_artefact(str(vault), router, "Wiki/test-page.md", target="Alpha")
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "## Alpha" not in body
        assert "Alpha content." not in body
        assert "## Beta" in body
        assert "Beta content." in body
        # Body must start directly with the remaining heading (no orphaned blank lines above it)
        assert body.startswith("## Beta\n")

    def test_delete_last_section(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n"
        )
        edit.delete_section_artefact(str(vault), router, "Wiki/test-page.md", target="Beta")
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "## Alpha" in body
        assert "Alpha content." in body
        assert "## Beta" not in body
        assert "Beta content." not in body

    def test_delete_only_section(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Only\n\nOnly content.\n"
        )
        edit.delete_section_artefact(str(vault), router, "Wiki/test-page.md", target="Only")
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "## Only" not in body
        assert "Only content." not in body

    def test_delete_section_not_found(self, vault, router):
        with pytest.raises(ValueError, match="not found"):
            edit.delete_section_artefact(
                str(vault), router, "Wiki/test-page.md", target="Nonexistent"
            )

    def test_delete_section_target_body_rejected(self, vault, router):
        with pytest.raises(ValueError, match="delete_section requires a heading or callout target"):
            edit.delete_section_artefact(
                str(vault), router, "Wiki/test-page.md", target=":body"
            )

    def test_delete_section_with_level_qualifier(self, vault, router):
        """Level-qualified target (e.g. '## Beta') matches exactly."""
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n"
        )
        edit.delete_section_artefact(str(vault), router, "Wiki/test-page.md", target="## Beta")
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "## Beta" not in body
        assert "## Alpha" in body

    def test_delete_section_updates_modified(self, vault, router):
        from datetime import datetime, timezone, timedelta
        from unittest.mock import patch
        fixed = datetime(2026, 4, 6, 10, 0, 0, tzinfo=timezone(timedelta(hours=11)))
        expected_iso = fixed.astimezone().isoformat()
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n## Alpha\n\nContent.\n"
        )
        with patch("_common._templates.datetime") as mock_dt:
            mock_dt.now.return_value = fixed
            edit.delete_section_artefact(str(vault), router, "Wiki/test-page.md", target="Alpha")
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, _ = parse_frontmatter(content)
        assert fields["modified"] == expected_iso

    def test_delete_callout_section(self, vault, router):
        """delete_section works with callout targets."""
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Context\n\n"
            "> [!note] Implementation status\n"
            "> Old status content.\n"
            "\n"
            "After callout.\n"
        )
        edit.delete_section_artefact(
            str(vault), router, "Wiki/test-page.md",
            target="[!note] Implementation status",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "[!note] Implementation status" not in body
        assert "Old status content." not in body
        assert "## Context" in body
        assert "After callout." in body

    def test_delete_section_selector_disambiguates_duplicate_heading(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "# API\n\n"
            "## Notes\n\nFirst.\n\n"
            "# API\n\n"
            "## Notes\n\nSecond.\n"
        )
        edit.delete_section_artefact(
            str(vault), router, "Wiki/test-page.md",
            target="## Notes",
            selector={"within": [{"target": "# API", "occurrence": 2}]},
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "First." in body
        assert "Second." not in body
        assert body.count("## Notes") == 1

    def test_delete_section_ambiguity_errors(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "# API\n\n"
            "## Notes\n\nFirst.\n\n"
            "# API\n\n"
            "## Notes\n\nSecond.\n"
        )
        with pytest.raises(ValueError, match="Ambiguous target '## Notes'"):
            edit.delete_section_artefact(
                str(vault), router, "Wiki/test-page.md", target="## Notes"
            )


# ---------------------------------------------------------------------------
# Resource editing (Phase 5)
# ---------------------------------------------------------------------------

class TestEditResource:
    """Tests for edit_resource() — the resource-aware dispatcher."""

    def _create_skill(self, vault):
        """Helper: create a skill file for editing tests."""
        skill_dir = vault / "_Config" / "Skills" / "test-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        path = skill_dir / "SKILL.md"
        path.write_text(
            "---\ndescription: A test skill\n---\n\n"
            "# Test Skill\n\nOriginal skill body.\n"
        )
        return "_Config/Skills/test-skill/SKILL.md"

    def _create_memory(self, vault):
        """Helper: create a memory file for editing tests."""
        mem_dir = vault / "_Config" / "Memories"
        mem_dir.mkdir(parents=True, exist_ok=True)
        path = mem_dir / "test-memory.md"
        path.write_text(
            "---\ntriggers:\n  - keyword1\n---\n\n"
            "# Test Memory\n\nOriginal memory body.\n"
        )
        return "_Config/Memories/test-memory.md"

    def _create_style(self, vault):
        """Helper: create a style file for editing tests."""
        style_dir = vault / "_Config" / "Styles"
        style_dir.mkdir(parents=True, exist_ok=True)
        path = style_dir / "test-style.md"
        path.write_text(
            "---\naudience: technical\n---\n\n"
            "# Test Style\n\nOriginal style body.\n"
        )
        return "_Config/Styles/test-style.md"

    def test_artefact_delegates_to_edit_artefact(self, vault, router):
        result = edit.edit_resource(
            str(vault), router, resource="artefact",
            operation="edit", path="Wiki/test-page.md",
            body="# Replaced\n",
            target=":body", scope="section",
        )
        assert result["operation"] == "edit"
        assert "Wiki/test-page.md" in result["path"]

    def test_edit_skill_body(self, vault, router):
        self._create_skill(vault)
        result = edit.edit_resource(
            str(vault), router, resource="skill",
            operation="edit", name="test-skill",
            body="# Updated Skill\n\nNew skill content.\n",
            target=":body", scope="section",
        )
        assert result["path"] == "_Config/Skills/test-skill/SKILL.md"
        assert result["operation"] == "edit"
        content = (vault / "_Config" / "Skills" / "test-skill" / "SKILL.md").read_text()
        assert "New skill content." in content
        assert "Original skill body." not in content

    def test_edit_memory_target_entire_body(self, vault, router):
        self._create_memory(vault)
        result = edit.edit_resource(
            str(vault), router, resource="memory",
            operation="edit", name="test-memory",
            body="Replacement memory body.\n",
            target=":body", scope="section",
        )
        assert result["path"] == "_Config/Memories/test-memory.md"
        content = (vault / "_Config" / "Memories" / "test-memory.md").read_text()
        assert "Replacement memory body." in content
        assert "Original memory body." not in content

    def test_append_to_memory(self, vault, router):
        self._create_memory(vault)
        result = edit.edit_resource(
            str(vault), router, resource="memory",
            operation="append", name="test-memory",
            body="\n## New Section\n\nAppended content.\n",
            target=":body", scope="section",
        )
        assert result["path"] == "_Config/Memories/test-memory.md"
        assert result["operation"] == "append"
        content = (vault / "_Config" / "Memories" / "test-memory.md").read_text()
        assert "Original memory body." in content
        assert "Appended content." in content

    def test_memory_target_body_requires_scope(self, vault, router):
        self._create_memory(vault)
        with pytest.raises(ValueError, match="requires scope"):
            edit.edit_resource(
                str(vault), router, resource="memory",
                operation="append", name="test-memory",
                body="Extra.\n",
                target=":body",
            )

    def test_prepend_to_style(self, vault, router):
        self._create_style(vault)
        result = edit.edit_resource(
            str(vault), router, resource="style",
            operation="prepend", name="test-style",
            body="> Important note\n\n",
            target=":body", scope="section",
        )
        assert result["path"] == "_Config/Styles/test-style.md"
        assert result["operation"] == "prepend"
        content = (vault / "_Config" / "Styles" / "test-style.md").read_text()
        assert content.index("Important note") < content.index("Original style body.")

    def test_edit_skill_frontmatter(self, vault, router):
        self._create_skill(vault)
        result = edit.edit_resource(
            str(vault), router, resource="skill",
            operation="edit", name="test-skill",
            frontmatter_changes={"description": "Updated description"},
        )
        content = (vault / "_Config" / "Skills" / "test-skill" / "SKILL.md").read_text()
        fields, body = parse_frontmatter(content)
        assert fields["description"] == "Updated description"
        assert "Original skill body." in body

    def test_edit_memory_triggers(self, vault, router):
        self._create_memory(vault)
        edit.edit_resource(
            str(vault), router, resource="memory",
            operation="append", name="test-memory",
            frontmatter_changes={"triggers": ["keyword2"]},
        )
        content = (vault / "_Config" / "Memories" / "test-memory.md").read_text()
        fields, _ = parse_frontmatter(content)
        assert "keyword1" in fields["triggers"]
        assert "keyword2" in fields["triggers"]

    def test_delete_section_from_skill(self, vault, router):
        skill_dir = vault / "_Config" / "Skills" / "section-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            "---\n---\n\n# Skill\n\nIntro.\n\n## Remove Me\n\nGone.\n\n## Keep\n\nStays.\n"
        )
        result = edit.edit_resource(
            str(vault), router, resource="skill",
            operation="delete_section", name="section-skill",
            target="## Remove Me",
        )
        assert result["operation"] == "delete_section"
        content = (skill_dir / "SKILL.md").read_text()
        assert "Remove Me" not in content
        assert "Stays." in content

    def test_not_editable_resource(self, vault, router):
        with pytest.raises(ValueError, match="not editable"):
            edit.edit_resource(
                str(vault), router, resource="workspace",
                operation="edit", name="ws", body="content",
            )

    def test_name_required_for_non_artefact(self, vault, router):
        with pytest.raises(ValueError, match="name"):
            edit.edit_resource(
                str(vault), router, resource="skill",
                operation="edit", body="content",
            )

    def test_resource_not_found(self, vault, router):
        with pytest.raises(FileNotFoundError, match="not found"):
            edit.edit_resource(
                str(vault), router, resource="skill",
                operation="edit", name="nonexistent",
                body="content",
            )

    def test_no_status_move_for_config_resources(self, vault, router):
        """Config resources should not auto-move on status changes."""
        self._create_skill(vault)
        edit.edit_resource(
            str(vault), router, resource="skill",
            operation="edit", name="test-skill",
            frontmatter_changes={"status": "implemented"},
        )
        # File should stay in place, not moved to +Implemented/
        assert (vault / "_Config" / "Skills" / "test-skill" / "SKILL.md").is_file()

    def test_edit_template(self, vault, router):
        """Templates are editable via artefact type key as name."""
        result = edit.edit_resource(
            str(vault), router, resource="template",
            operation="edit", name="wiki",
            body="# Updated Template\n\nNew template content.\n",
            target=":body", scope="section",
        )
        assert "_Config/Templates/" in result["path"]
        assert result["operation"] == "edit"

    def test_targeted_edit_on_resource(self, vault, router):
        """Target parameter works for resource editing too."""
        self._create_skill(vault)
        result = edit.edit_resource(
            str(vault), router, resource="skill",
            operation="edit", name="test-skill",
            body="Replaced section content.\n",
            target="# Test Skill", scope="body",
        )
        content = (vault / "_Config" / "Skills" / "test-skill" / "SKILL.md").read_text()
        assert "Replaced section content." in content
        assert "Original skill body." not in content


class TestTempPathFlag:
    def test_temp_path_prints_path_and_exits(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            sys.argv = ["edit.py", "--temp-path"]
            edit.main()
        assert exc_info.value.code == 0
        out = capsys.readouterr().out.strip()
        try:
            assert out.endswith(".md")
            assert os.path.exists(out)
        finally:
            if os.path.exists(out):
                os.remove(out)

    def test_temp_path_custom_suffix(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            sys.argv = ["edit.py", "--temp-path", ".txt"]
            edit.main()
        assert exc_info.value.code == 0
        out = capsys.readouterr().out.strip()
        try:
            assert out.endswith(".txt")
            assert os.path.exists(out)
        finally:
            if os.path.exists(out):
                os.remove(out)

    def test_occurrence_invalid_int_prints_error_and_exits(self, capsys):
        with patch.object(sys, "argv", ["edit.py", "edit", "--occurrence", "abc"]):
            with pytest.raises(SystemExit) as exc_info:
                edit.main()
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "Error: --occurrence expects an integer" in err

    def test_within_occurrence_invalid_int_prints_error_and_exits(self, capsys):
        with patch.object(
            sys,
            "argv",
            ["edit.py", "edit", "--within", "## Notes", "--within-occurrence", "abc"],
        ):
            with pytest.raises(SystemExit) as exc_info:
                edit.main()
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "Error: --within-occurrence expects an integer" in err


class TestEditWikilinkWarnings:
    def test_clean_edit_no_warnings_key(self, vault, router):
        result = edit.edit_resource(
            str(vault), router, resource="artefact", operation="edit",
            path="Wiki/test-page.md", body="No links.\n",
            target=":body", scope="section",
        )
        assert "wikilink_warnings" not in result

    def test_broken_link_returns_warnings(self, vault, router):
        result = edit.edit_resource(
            str(vault), router, resource="artefact", operation="edit",
            path="Wiki/test-page.md", body="See [[missing-target]].\n",
            target=":body", scope="section",
        )
        assert "wikilink_warnings" in result
        warnings = result["wikilink_warnings"]
        assert len(warnings) == 1
        assert warnings[0]["stem"] == "missing-target"
        assert warnings[0]["status"] == "broken"

    def test_append_with_broken_link(self, vault, router):
        result = edit.edit_resource(
            str(vault), router, resource="artefact", operation="append",
            path="Wiki/test-page.md", body="\nAlso [[also-missing]].\n",
            target=":body", scope="section",
        )
        assert "wikilink_warnings" in result

    def test_prepend_with_broken_link(self, vault, router):
        result = edit.edit_resource(
            str(vault), router, resource="artefact", operation="prepend",
            path="Wiki/test-page.md", body="Top [[top-missing]]\n\n",
            target=":body", scope="section",
        )
        assert "wikilink_warnings" in result


class TestEditFixLinks:
    def test_fix_links_applies_resolvable(self, vault, router):
        (vault / "Wiki" / "Real Target.md").write_text("# Real\n")
        import compile_router
        router2 = compile_router.compile(str(vault))
        result = edit.edit_resource(
            str(vault), router2, resource="artefact", operation="edit",
            path="Wiki/test-page.md", body="See [[real-target]].\n",
            target=":body", scope="section", fix_links=True,
        )
        assert "wikilink_fixes" in result
        assert result["wikilink_fixes"]["applied"] == 1
        assert "wikilink_warnings" not in result
        content = (vault / "Wiki" / "test-page.md").read_text()
        assert "[[Real Target]]" in content
        assert "[[real-target]]" not in content

    def test_fix_links_leaves_unresolvable_as_warning(self, vault, router):
        result = edit.edit_resource(
            str(vault), router, resource="artefact", operation="edit",
            path="Wiki/test-page.md", body="See [[never-existed]].\n",
            target=":body", scope="section", fix_links=True,
        )
        assert "wikilink_fixes" not in result
        assert "wikilink_warnings" in result
        assert result["wikilink_warnings"][0]["status"] == "broken"

    def test_fix_links_false_leaves_file_untouched(self, vault, router):
        (vault / "Wiki" / "Real Target.md").write_text("# Real\n")
        import compile_router
        router2 = compile_router.compile(str(vault))
        result = edit.edit_resource(
            str(vault), router2, resource="artefact", operation="edit",
            path="Wiki/test-page.md", body="See [[real-target]].\n",
            target=":body", scope="section",
        )
        assert "wikilink_fixes" not in result
        content = (vault / "Wiki" / "test-page.md").read_text()
        assert "[[real-target]]" in content


class TestEditFileIndexThreading:
    """Verify edit_resource skips the vault walk when file_index is supplied."""

    def test_supplied_file_index_skips_vault_walk(self, vault, router, monkeypatch):
        """When file_index is supplied, build_vault_file_index must not be called."""
        import fix_links as _fix_links
        called = {"count": 0}
        original = _fix_links.build_vault_file_index
        def spy(*args, **kwargs):
            called["count"] += 1
            return original(*args, **kwargs)
        monkeypatch.setattr(_fix_links, "build_vault_file_index", spy)

        # Construct a minimal file_index that resolves [[real-target]]
        (vault / "Wiki" / "Real Target.md").write_text("# Real\n")
        file_index = file_index_from_documents(
            [{"path": "Wiki/Real Target.md"}],
            vault_root=str(vault),
        )

        result = edit.edit_resource(
            str(vault), router, resource="artefact", operation="edit",
            path="Wiki/test-page.md", body="See [[real-target]].\n",
            target=":body", scope="section",
            fix_links=True, file_index=file_index,
        )
        assert called["count"] == 0, "build_vault_file_index must not be called when file_index supplied"
        # The supplied index treats the link as ambiguous-or-resolved; we don't assert wikilink_fixes here
        # because the synthetic stem doesn't exactly match. The point is the walk was skipped.

    def test_no_file_index_falls_back_to_walk(self, vault, router, monkeypatch):
        """Backward-compat: omitting file_index still triggers the walk (legacy CLI/tests)."""
        import fix_links as _fix_links
        called = {"count": 0}
        original = _fix_links.build_vault_file_index
        def spy(*args, **kwargs):
            called["count"] += 1
            return original(*args, **kwargs)
        monkeypatch.setattr(_fix_links, "build_vault_file_index", spy)

        edit.edit_resource(
            str(vault), router, resource="artefact", operation="edit",
            path="Wiki/test-page.md", body="No links.\n",
            target=":body", scope="section",
        )
        assert called["count"] == 1, "build_vault_file_index must be called when file_index is None"


class TestSectionReplaceBoundaryGuard:
    """Reject section-replace bodies that would duplicate the next boundary heading."""

    def _setup(self, vault, body_text):
        path = vault / "Wiki" / "boundary-page.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n" + body_text
        )

    def test_rejects_body_ending_with_boundary_heading(self, vault, router):
        self._setup(
            vault,
            "## Alpha\n\nAlpha body.\n\n## Beta\n\nBeta body.\n",
        )
        with pytest.raises(ValueError, match="boundary"):
            edit.edit_artefact(
                str(vault), router, "Wiki/boundary-page.md",
                "## Alpha\n\nUpdated alpha.\n\n## Beta\n",
                target="## Alpha", scope="section",
            )

    def test_accepts_split_into_two_with_interior_same_level_heading(self, vault, router):
        self._setup(
            vault,
            "## Alpha\n\nAlpha body.\n\n## Gamma\n\nGamma body.\n",
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/boundary-page.md",
            "## Alpha\n\nFirst half.\n\n## Beta\n\n| col |\n| --- |\n| val |\n",
            target="## Alpha", scope="section",
        )
        content = (vault / "Wiki" / "boundary-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert body.count("## Alpha") == 1
        assert body.count("## Beta") == 1
        assert body.count("## Gamma") == 1
        assert body.index("## Alpha") < body.index("## Beta") < body.index("## Gamma")
        assert "## Alpha\n\nFirst half.\n\n## Beta" in body

    def test_accepts_body_ending_with_deeper_subsection(self, vault, router):
        self._setup(
            vault,
            "## Alpha\n\nAlpha body.\n\n## Beta\n\nBeta body.\n",
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/boundary-page.md",
            "## Alpha\n\nUpdated.\n\n### Subsection\n\nDeeper.\n",
            target="## Alpha", scope="section",
        )
        content = (vault / "Wiki" / "boundary-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert body.count("## Beta") == 1
        assert "### Subsection" in body
        assert body.index("### Subsection") < body.index("## Beta")

    def test_accepts_when_target_has_no_next_sibling_at_level(self, vault, router):
        self._setup(
            vault,
            "## Alpha\n\nAlpha body.\n\n### Child\n\nChild body.\n",
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/boundary-page.md",
            "## Alpha\n\nUpdated.\n\n## Whatever\n",
            target="## Alpha", scope="section",
        )
        content = (vault / "Wiki" / "boundary-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert body.count("## Alpha") == 1
        assert "## Whatever" in body

    def test_rejection_error_is_actionable(self, vault, router):
        self._setup(
            vault,
            "## Alpha\n\nAlpha body.\n\n## Beta\n\nBeta body.\n",
        )
        with pytest.raises(ValueError) as exc:
            edit.edit_artefact(
                str(vault), router, "Wiki/boundary-page.md",
                "## Alpha\n\nUpdated alpha.\n\n## Beta\n",
                target="## Alpha", scope="section",
            )
        msg = str(exc.value)
        assert "## Beta" in msg
        assert ("drop" in msg.lower()) or ("widen" in msg.lower()) or ("trailing" in msg.lower())

    def test_accepts_body_with_no_internal_headings_other_than_target(self, vault, router):
        self._setup(
            vault,
            "## Alpha\n\nbody.\n\n## Beta\n\nB body.\n",
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/boundary-page.md",
            "## Alpha\n\nJust prose, no other headings.\n",
            target="## Alpha", scope="section",
        )
        content = (vault / "Wiki" / "boundary-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert body.count("## Alpha") == 1
        assert body.count("## Beta") == 1

    def test_accepts_when_body_final_heading_is_deeper_level_than_boundary(self, vault, router):
        self._setup(
            vault,
            "## Alpha\n\nbody.\n\n## Beta\n\nB body.\n",
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/boundary-page.md",
            "## Alpha\n\nUpdated.\n\n### Beta\n\nDeeper Beta.\n",
            target="## Alpha", scope="section",
        )
        content = (vault / "Wiki" / "boundary-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "## Beta" in body
        assert "### Beta" in body

    def test_rejects_when_boundary_heading_appears_twice_in_body(self, vault, router):
        self._setup(
            vault,
            "## Alpha\n\nbody.\n\n## Beta\n\nB body.\n",
        )
        with pytest.raises(ValueError, match="boundary"):
            edit.edit_artefact(
                str(vault), router, "Wiki/boundary-page.md",
                "## Alpha\n\nFirst.\n\n## Beta\n\nMid.\n\n## Beta\n",
                target="## Alpha", scope="section",
            )

    def test_rejects_split_into_two_when_interior_heading_matches_boundary_text(self, vault, router):
        self._setup(
            vault,
            "## Alpha\n\nbody.\n\n## Gamma\n\nG body.\n",
        )
        with pytest.raises(ValueError, match="boundary"):
            edit.edit_artefact(
                str(vault), router, "Wiki/boundary-page.md",
                "## Alpha\n\nFirst.\n\n## Beta\n\nMid.\n\n## Gamma\n",
                target="## Alpha", scope="section",
            )

    def test_rejects_body_ending_with_shallower_boundary_heading(self, vault, router):
        self._setup(
            vault,
            "## Alpha\n\nbody.\n\n# Omega\n\nO body.\n",
        )
        with pytest.raises(ValueError, match="boundary"):
            edit.edit_artefact(
                str(vault), router, "Wiki/boundary-page.md",
                "## Alpha\n\nUpdated.\n\n# Omega\n",
                target="## Alpha", scope="section",
            )
