"""Tests for edit.py — artefact editing, appending, and conversion."""

import os
import re
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

import edit
from _common import find_section, parse_frontmatter, validate_artefact_folder


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
        "## Naming\n\n`{slug}.md` in `Wiki/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/wiki\ntags:\n  - topic-tag\n---\n```\n\n"
        "## Template\n\n[[_Config/Templates/Living/Wiki]]\n"
    )

    # Taxonomy: Designs (with multiple terminal statuses)
    (tax_living / "designs.md").write_text(
        "# Designs\n\n"
        "## Naming\n\n`{slug}.md` in `Designs/`.\n\n"
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
        "## Naming\n\n`{slug}.md` in `Ideas/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/ideas\ntags: []\nstatus: seed\n---\n```\n\n"
        "Status values: `seed`, `shaping`, `adopted`.\n\n"
        "## Terminal Status\n\nWhen an idea reaches `adopted` status, it moves to `+Adopted/`.\n\n"
        "## Template\n\n[[_Config/Templates/Living/Ideas]]\n"
    )

    # Taxonomy: Releases (with two terminal statuses)
    (tax_living / "releases.md").write_text(
        "# Releases\n\n"
        "## Naming\n\n"
        "Primary folder: `Releases/{Project}/`.\n\n"
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
        "When a release reaches `shipped` status, move to `Releases/{Project}/+Shipped/`.\n"
        "Set `status: shipped` before the move.\n"
        "When a release reaches `cancelled` status, move to `Releases/{Project}/+Cancelled/`.\n"
        "Set `status: cancelled` before the move.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/release\ntags:\n  - release\n"
        "status: planned\nversion:\ntag:\ncommit:\nshipped:\n---\n```\n\n"
        "## Template\n\n[[_Config/Templates/Living/Releases]]\n"
    )

    # Taxonomy: Logs
    tax_temporal = config / "Taxonomy" / "Temporal"
    tax_temporal.mkdir(parents=True)
    (tax_temporal / "logs.md").write_text(
        "# Logs\n\n"
        "## Naming\n\n`log-{slug}.md` in `_Temporal/Logs/yyyy-mm/`.\n\n"
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
        edit.edit_artefact(str(vault), router, "Wiki/test-page.md", "# New Body\n\nReplaced.\n")
        content = (vault / "Wiki" / "test-page.md").read_text()
        assert "Replaced." in content
        assert "Original body." not in content

    def test_edit_preserves_frontmatter(self, vault, router):
        edit.edit_artefact(str(vault), router, "Wiki/test-page.md", "# New\n")
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, _ = parse_frontmatter(content)
        assert fields["type"] == "living/wiki"
        assert fields["status"] == "active"
        assert "brain-core" in fields["tags"]

    def test_edit_merges_frontmatter_changes(self, vault, router):
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md", "# New\n",
            frontmatter_changes={"status": "archived"}
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

    def test_edit_target_entire_body_clears_content(self, vault, router):
        """target=':entire_body' with empty string should clear the body."""
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md", "",
            frontmatter_changes={"status": "archived"},
            target=":entire_body",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, body = parse_frontmatter(content)
        assert fields["status"] == "archived"
        assert body.strip() == ""  # body intentionally cleared

    def test_edit_target_entire_body_replaces_content(self, vault, router):
        """target=':entire_body' with content should replace the entire body."""
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md", "# New Content\n\nReplaced.",
            target=":entire_body",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "New Content" in body
        assert "Original body." not in body

    def test_edit_target_body_preamble_replaces_only_leading_body(self, vault, router):
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
            target=":body_preamble",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert body.startswith("Updated intro.\n\n> [!note] Status")
        assert "Intro text." not in body
        assert "Status content." in body
        assert "Notes content." in body

    def test_edit_target_body_preamble_inserts_before_heading_first_doc(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Notes\n\nNotes content.\n"
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md", "Lead text.\n",
            target=":body_preamble",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert body.startswith("Lead text.\n\n## Notes")
        assert body.count("## Notes") == 1

    def test_edit_target_body_preamble_inserts_before_callout_first_doc(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "> [!note] Status\n"
            "> Status content.\n"
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md", "Lead text.\n",
            target=":body_preamble",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert body.startswith("Lead text.\n\n> [!note] Status")
        assert "Status content." in body

    def test_edit_target_body_preamble_replaces_whole_body_without_targetable_sections(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\nOnly body.\n"
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md", "Replacement body.\n",
            target=":body_preamble",
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

    def test_edit_target_body_rejected(self, vault, router):
        with pytest.raises(ValueError, match="target=':body' is no longer valid"):
            edit.edit_artefact(
                str(vault), router, "Wiki/test-page.md", "Replacement.\n",
                target=":body",
            )

    def test_edit_file_not_found(self, vault, router):
        with pytest.raises(FileNotFoundError):
            edit.edit_artefact(str(vault), router, "Wiki/nonexistent.md", "body")

    def test_edit_basename_fallback(self, vault, router):
        edit.edit_artefact(str(vault), router, "test-page", "# Resolved Body\n")
        content = (vault / "Wiki" / "test-page.md").read_text()
        assert "Resolved Body" in content
        assert "Original body." not in content

    def test_edit_full_path_without_md_extension(self, vault, router):
        """Agents should not need to pass the .md extension on full paths."""
        edit.edit_artefact(str(vault), router, "Wiki/test-page", "# No Extension\n")
        content = (vault / "Wiki" / "test-page.md").read_text()
        assert "No Extension" in content


# ---------------------------------------------------------------------------
# Append tests
# ---------------------------------------------------------------------------

class TestAppendToArtefact:
    def test_append_adds_content(self, vault, router):
        edit.append_to_artefact(str(vault), router, "Wiki/test-page.md", "\n\nAppended text.\n")
        content = (vault / "Wiki" / "test-page.md").read_text()
        assert "Original body." in content
        assert "Appended text." in content

    def test_append_target_entire_body_appends_to_whole_body(self, vault, router):
        edit.append_to_artefact(
            str(vault), router, "Wiki/test-page.md", "\n\nAppended text.\n",
            target=":entire_body",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        assert "Original body." in content
        assert "Appended text." in content

    def test_append_file_not_found(self, vault, router):
        with pytest.raises(FileNotFoundError):
            edit.append_to_artefact(str(vault), router, "Wiki/nonexistent.md", "text")

    def test_append_target_body_rejected(self, vault, router):
        with pytest.raises(ValueError, match="target=':body' is no longer valid"):
            edit.append_to_artefact(
                str(vault), router, "Wiki/test-page.md", "Extra.\n",
                target=":body",
            )

    def test_append_body_preamble_rejected(self, vault, router):
        with pytest.raises(ValueError, match="only supported for operation='edit'"):
            edit.append_to_artefact(
                str(vault), router, "Wiki/test-page.md", "Extra.\n",
                target=":body_preamble",
            )

    def test_append_basename_fallback(self, vault, router):
        edit.append_to_artefact(str(vault), router, "test-page", "\n\nAppended via basename.\n")
        content = (vault / "Wiki" / "test-page.md").read_text()
        assert "Original body." in content
        assert "Appended via basename." in content

    def test_append_full_path_without_md_extension(self, vault, router):
        """Agents should not need to pass the .md extension on full paths."""
        edit.append_to_artefact(str(vault), router, "Wiki/test-page", "\n\nNo ext append.\n")
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
        """Source in Ideas/Brain/ should produce Designs/Brain/ after convert."""
        hub_dir = vault / "Ideas" / "Brain"
        hub_dir.mkdir(parents=True)
        (hub_dir / "my-idea.md").write_text(
            "---\ntype: living/ideas\ntags: []\n---\n\n# My Idea\n\nBody.\n"
        )
        result = edit.convert_artefact(str(vault), router, "Ideas/Brain/my-idea.md", "designs")
        assert result["new_path"].startswith("Designs/Brain/")
        assert not (hub_dir / "my-idea.md").exists()
        assert os.path.isfile(os.path.join(str(vault), result["new_path"]))

    def test_convert_flat_source_no_parent(self, vault, router):
        """Source directly in Ideas/ (no subfolder) should produce Designs/ with no subfolder."""
        (vault / "Ideas" / "flat-idea.md").write_text(
            "---\ntype: living/ideas\ntags: []\n---\n\n# Flat Idea\n\nBody.\n"
        )
        result = edit.convert_artefact(str(vault), router, "Ideas/flat-idea.md", "designs")
        assert result["new_path"].startswith("Designs/")
        assert "/" not in result["new_path"][len("Designs/"):]

    def test_convert_explicit_parent_override(self, vault, router):
        """Explicit parent kwarg takes precedence over auto-detected subfolder."""
        hub_dir = vault / "Ideas" / "Brain"
        hub_dir.mkdir(parents=True)
        (hub_dir / "overridden.md").write_text(
            "---\ntype: living/ideas\ntags: []\n---\n\n# Overridden\n\nBody.\n"
        )
        result = edit.convert_artefact(
            str(vault), router, "Ideas/Brain/overridden.md", "designs", parent="Custom"
        )
        assert result["new_path"].startswith("Designs/Custom/")

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
        first_month = vault / "_Temporal" / "Logs" / "2026-03"
        second_month = vault / "_Temporal" / "Logs" / "2026-04"
        first_month.mkdir(parents=True)
        second_month.mkdir(parents=True)
        (first_month / "20260301-log-foo.md").write_text(
            "---\n"
            "type: temporal/logs\n"
            "title: Foo\n"
            "tags:\n"
            "  - report-source\n"
            "---\n\n"
            "First body.\n"
        )
        (second_month / "20260410-log-foo.md").write_text(
            "---\n"
            "type: temporal/logs\n"
            "title: Foo\n"
            "tags:\n"
            "  - report-source\n"
            "---\n\n"
            "Second body.\n"
        )

        first = edit.convert_artefact(
            str(vault), router, "_Temporal/Logs/2026-03/20260301-log-foo.md", "reports"
        )
        second = edit.convert_artefact(
            str(vault), router, "_Temporal/Logs/2026-04/20260410-log-foo.md", "reports"
        )

        assert first["new_path"] != second["new_path"]
        assert re.search(r" [a-z0-9]{3}\.md$", second["new_path"])
        assert "First body." in (vault / first["new_path"]).read_text()
        assert "Second body." in (vault / second["new_path"]).read_text()


# ---------------------------------------------------------------------------
# find_section tests
# ---------------------------------------------------------------------------

class TestFindSection:
    def test_finds_section_boundaries(self):
        body = "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n"
        start, end = find_section(body, "Alpha")
        section = body[start:end]
        assert "Alpha content." in section
        assert "Beta content." not in section

    def test_section_at_end_of_file(self):
        body = "## First\n\nFirst content.\n\n## Last\n\nLast content.\n"
        start, end = find_section(body, "Last")
        section = body[start:end]
        assert "Last content." in section
        assert end == len(body)

    def test_case_insensitive(self):
        body = "## Notes\n\nSome notes.\n\n## Other\n\nOther stuff.\n"
        start, end = find_section(body, "notes")
        assert "Some notes." in body[start:end]

    def test_sub_headings_included(self):
        body = "## Parent\n\nIntro.\n\n### Child\n\nChild content.\n\n## Sibling\n\nSibling.\n"
        start, end = find_section(body, "Parent")
        section = body[start:end]
        assert "Intro." in section
        assert "### Child" in section
        assert "Child content." in section
        assert "Sibling." not in section

    def test_missing_section_raises(self):
        body = "## Alpha\n\nContent.\n"
        with pytest.raises(ValueError, match="not found"):
            find_section(body, "Nonexistent")

    def test_skips_headings_in_fenced_code_blocks(self):
        body = (
            "## Real\n\nContent.\n\n"
            "```markdown\n## Fake\n\nFake content.\n```\n\n"
            "More content.\n\n## Next\n\nNext stuff.\n"
        )
        start, end = find_section(body, "Real")
        section = body[start:end]
        assert "Content." in section
        assert "## Fake" in section  # fence is part of Real's section
        assert "More content." in section
        assert "Next stuff." not in section

    def test_heading_in_fence_not_found_as_section(self):
        body = "## Real\n\n```\n## OnlyInFence\n```\n"
        with pytest.raises(ValueError, match="not found"):
            find_section(body, "OnlyInFence")

    def test_level_specific_match(self):
        body = "## Notes\n\nTop.\n\n### Notes\n\nSub.\n\n## Other\n\nOther.\n"
        # "### Notes" matches the h3 specifically
        start, end = find_section(body, "### Notes")
        section = body[start:end]
        assert "Sub." in section
        assert "Top." not in section

    def test_plain_match_gets_first(self):
        body = "## Notes\n\nTop.\n\n### Notes\n\nSub.\n\n## Other\n\nOther.\n"
        # "Notes" without markers matches the first (## Notes)
        start, end = find_section(body, "Notes")
        section = body[start:end]
        assert "Top." in section
        assert "Sub." in section  # sub-heading is part of parent section

    def test_callout_section_boundaries(self):
        body = (
            "Some intro.\n\n"
            "> [!note] Implementation status\n"
            "> This is the note content.\n"
            "> More note content.\n"
            "\n"
            "After the callout.\n"
        )
        start, end = find_section(body, "[!note] Implementation status")
        section = body[start:end]
        assert "This is the note content." in section
        assert "More note content." in section
        assert "After the callout." not in section
        assert "Some intro." not in section

    def test_callout_at_end_of_file(self):
        body = (
            "## Heading\n\nContent.\n\n"
            "> [!warning] Deprecation\n"
            "> This API is deprecated.\n"
        )
        start, end = find_section(body, "[!warning] Deprecation")
        section = body[start:end]
        assert "This API is deprecated." in section
        assert end == len(body)

    def test_callout_case_insensitive(self):
        body = "> [!note] My Note\n> Content here.\n"
        start, end = find_section(body, "[!note] my note")
        assert "Content here." in body[start:end]

    def test_callout_missing_raises(self):
        body = "> [!note] Exists\n> Content.\n"
        with pytest.raises(ValueError, match="not found"):
            find_section(body, "[!tip] Nonexistent")

    def test_callout_between_headings(self):
        body = (
            "## Alpha\n\nAlpha text.\n\n"
            "> [!note] Status\n"
            "> Status content.\n"
            "\n"
            "## Beta\n\nBeta text.\n"
        )
        start, end = find_section(body, "[!note] Status")
        section = body[start:end]
        assert "Status content." in section
        assert "Alpha text." not in section
        assert "Beta text." not in section

    def test_callout_inside_fenced_code_block_ignored(self):
        body = (
            "```markdown\n"
            "> [!note] Fake\n"
            "> Not a real callout.\n"
            "```\n"
            "\n"
            "> [!note] Real\n"
            "> Real content.\n"
        )
        start, end = find_section(body, "[!note] Real")
        section = body[start:end]
        assert "Real content." in section
        # The fenced one should not be findable
        with pytest.raises(ValueError, match="not found"):
            find_section(body, "[!note] Fake")

    def test_callout_multiline_with_blank_quoted_lines(self):
        body = (
            "> [!note] Complex\n"
            "> First paragraph.\n"
            ">\n"
            "> Second paragraph.\n"
            "\n"
            "Outside.\n"
        )
        start, end = find_section(body, "[!note] Complex")
        section = body[start:end]
        assert "First paragraph." in section
        assert "Second paragraph." in section
        assert "Outside." not in section


# ---------------------------------------------------------------------------
# find_section include_heading tests
# ---------------------------------------------------------------------------

class TestFindSectionIncludeHeading:
    def test_include_heading_returns_heading_start(self):
        body = "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n"
        start, end = find_section(body, "Beta", include_heading=True)
        assert body[start:start + 7] == "## Beta"

    def test_include_heading_first_section(self):
        body = "## First\n\nContent.\n\n## Second\n\nMore.\n"
        start, end = find_section(body, "First", include_heading=True)
        assert start == 0
        assert body[start:start + 8] == "## First"

    def test_include_heading_callout(self):
        body = (
            "## Section\n\n"
            "> [!note] Status\n"
            "> Content here.\n"
            "\n"
            "After.\n"
        )
        start, end = find_section(body, "[!note] Status", include_heading=True)
        assert body[start] == ">"
        assert body[start:].startswith("> [!note] Status")

    def test_include_heading_callout_end_includes_continuation(self):
        """include_heading=True must still include continuation lines in the range."""
        body = (
            "> [!note] Status\n"
            "> Line 2.\n"
            "> Line 3.\n"
            "\n"
            "After.\n"
        )
        start, end = find_section(body, "[!note] Status", include_heading=True)
        section = body[start:end]
        assert "> [!note] Status" in section
        assert "> Line 2." in section
        assert "> Line 3." in section
        assert "After." not in section

    def test_include_heading_false_unchanged(self):
        """Default include_heading=False returns same as before."""
        body = "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n"
        start_default, end_default = find_section(body, "Beta")
        start_explicit, end_explicit = find_section(body, "Beta", include_heading=False)
        assert start_default == start_explicit
        assert end_default == end_explicit
        assert "Beta content." in body[start_default:end_default]


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
            "Appended to alpha.\n", target="Alpha",
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
            "More stuff.\n", target="Only",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert body.endswith("More stuff.\n")

    def test_append_section_not_found(self, vault, router):
        with pytest.raises(ValueError, match="not found"):
            edit.append_to_artefact(
                str(vault), router, "Wiki/test-page.md",
                "text", target="Nonexistent",
            )

    def test_append_without_section_unchanged(self, vault, router):
        edit.append_to_artefact(
            str(vault), router, "Wiki/test-page.md", "\nAppended.\n",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        assert content.endswith("\nAppended.\n")

    def test_append_to_sub_heading(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Parent\n\nIntro.\n\n### Child\n\n- Item 1\n\n"
            "### Sibling\n\nSibling content.\n\n## Other\n\nOther.\n"
        )
        edit.append_to_artefact(
            str(vault), router, "Wiki/test-page.md",
            "- Item 2\n", target="### Child",
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
            "> Appended line.\n", target="[!note] Status",
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
    def test_prepend_adds_content_before_body(self, vault, router):
        edit.prepend_to_artefact(
            str(vault), router, "Wiki/test-page.md", "Prepended text.\n",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert body.startswith("Prepended text.\n")
        assert "Original body." in body

    def test_prepend_target_entire_body_prepends_to_whole_body(self, vault, router):
        edit.prepend_to_artefact(
            str(vault), router, "Wiki/test-page.md", "Prepended text.\n",
            target=":entire_body",
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
            "## New Section\n\nInserted.\n", target="Beta",
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
            "## Zeroth\n\nBefore everything.\n", target="First",
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
            "## Inserted\n\nBefore last.\n", target="Last",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert body.index("## Alpha") < body.index("## Inserted") < body.index("## Last")
        assert "Last content." in body

    def test_prepend_file_not_found(self, vault, router):
        with pytest.raises(FileNotFoundError):
            edit.prepend_to_artefact(str(vault), router, "Wiki/nonexistent.md", "text")

    def test_prepend_target_body_rejected(self, vault, router):
        with pytest.raises(ValueError, match="target=':body' is no longer valid"):
            edit.prepend_to_artefact(
                str(vault), router, "Wiki/test-page.md", "Extra.\n",
                target=":body",
            )

    def test_prepend_body_preamble_rejected(self, vault, router):
        with pytest.raises(ValueError, match="only supported for operation='edit'"):
            edit.prepend_to_artefact(
                str(vault), router, "Wiki/test-page.md", "Extra.\n",
                target=":body_preamble",
            )

    def test_prepend_section_not_found(self, vault, router):
        with pytest.raises(ValueError, match="not found"):
            edit.prepend_to_artefact(
                str(vault), router, "Wiki/test-page.md",
                "text", target="Nonexistent",
            )

    def test_prepend_basename_fallback(self, vault, router):
        edit.prepend_to_artefact(str(vault), router, "test-page", "Prepended.\n")
        content = (vault / "Wiki" / "test-page.md").read_text()
        assert "Prepended." in content

    def test_prepend_preserves_surrounding_sections(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n\n## Gamma\n\nGamma content.\n"
        )
        edit.prepend_to_artefact(
            str(vault), router, "Wiki/test-page.md",
            "## Inserted\n\nNew.\n", target="Beta",
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
            "\nReplaced alpha.\n\n", target="Alpha",
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
            "\nNew last.\n", target="Last",
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
                "text", target="Nonexistent",
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
            "New alpha content.", target="Alpha",
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
            "\nNew child content.\n\n", target="### Child",
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
        edit.edit_artefact(str(vault), router, "Wiki/test-page.md", "", target="Alpha")
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
        edit.edit_artefact(str(vault), router, "Wiki/test-page.md", "", target="Beta")
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
            "> New status content.\n", target="[!note] Implementation status",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "> New status content." in body
        assert "Old status content." not in body
        assert "After callout." in body
        assert "> [!note] Implementation status" in body

    def test_edit_section_strips_exact_heading_wrapper(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n"
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md",
            "## Alpha\n\nUpdated alpha.\n", target="## Alpha",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert body.count("## Alpha") == 1
        assert "Updated alpha." in body
        assert "Alpha content." not in body

    def test_edit_section_rejects_different_heading_wrapper(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n"
        )
        with pytest.raises(ValueError, match=":section:## Alpha"):
            edit.edit_artefact(
                str(vault), router, "Wiki/test-page.md",
                "# Alpha\n\nUpdated alpha.\n", target="## Alpha",
            )

    def test_edit_section_allows_nested_heading_content(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n"
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md",
            "### Overview\n\nUpdated alpha.\n", target="## Alpha",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "## Alpha" in body
        assert "### Overview" in body
        assert "Updated alpha." in body

    def test_edit_section_rejects_same_or_higher_level_heading_content(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n"
        )
        with pytest.raises(ValueError, match=":section:## Alpha"):
            edit.edit_artefact(
                str(vault), router, "Wiki/test-page.md",
                "# Overview\n\nUpdated alpha.\n", target="## Alpha",
            )

    def test_edit_callout_strips_exact_wrapper(self, vault, router):
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
            "> [!note] Implementation status\n>\n> Updated status.\n",
            target="[!note] Implementation status",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert body.count("[!note] Implementation status") == 1
        assert "Updated status." in body
        assert "Old status content." not in body

    def test_edit_section_allows_callout_content(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n"
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md",
            "> [!note] Fresh note\n> Updated alpha.\n",
            target="## Alpha",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "## Alpha" in body
        assert "[!note] Fresh note" in body
        assert "Updated alpha." in body

    def test_edit_section_mode_replaces_heading(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n"
        )
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md",
            "# Renamed Alpha\n\nUpdated alpha.\n", target=":section:## Alpha",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "## Alpha" not in body
        assert "# Renamed Alpha" in body
        assert "Updated alpha." in body
        assert "## Beta" in body

    def test_edit_section_mode_requires_structural_anchor(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n"
        )
        with pytest.raises(ValueError, match="must begin with a heading or callout title line"):
            edit.edit_artefact(
                str(vault), router, "Wiki/test-page.md",
                "Updated alpha.\n", target=":section:## Alpha",
            )

    def test_append_section_prefix_is_redundant_alias(self, vault, router):
        (vault / "Wiki" / "test-page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n"
        )
        edit.append_to_artefact(
            str(vault), router, "Wiki/test-page.md",
            "Appended.\n", target=":section:## Alpha",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "Alpha content." in body
        assert "Appended." in body


# ---------------------------------------------------------------------------
# Timestamp tests
# ---------------------------------------------------------------------------

class TestEditTimestamps:
    FIXED_DT = datetime(2026, 4, 2, 10, 0, 0, tzinfo=timezone(timedelta(hours=11)))
    FIXED_ISO = "2026-04-02T10:00:00+11:00"

    def test_edit_updates_modified(self, vault, router):
        with patch("_common._templates.datetime") as mock_dt:
            mock_dt.now.return_value = self.FIXED_DT
            edit.edit_artefact(str(vault), router, "Wiki/test-page.md", "New body\n")
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, _ = parse_frontmatter(content)
        assert fields["modified"] == self.FIXED_ISO

    def test_edit_does_not_change_created(self, vault, router):
        original = (vault / "Wiki" / "test-page.md").read_text()
        original_fields, _ = parse_frontmatter(original)
        original_created = original_fields.get("created", "__absent__")

        with patch("_common._templates.datetime") as mock_dt:
            mock_dt.now.return_value = self.FIXED_DT
            edit.edit_artefact(str(vault), router, "Wiki/test-page.md", "Changed body\n")
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, _ = parse_frontmatter(content)
        assert fields.get("created", "__absent__") == original_created

    def test_append_updates_modified(self, vault, router):
        with patch("_common._templates.datetime") as mock_dt:
            mock_dt.now.return_value = self.FIXED_DT
            edit.append_to_artefact(str(vault), router, "Wiki/test-page.md", "\nAppended\n")
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, _ = parse_frontmatter(content)
        assert fields["modified"] == self.FIXED_ISO

    def test_prepend_updates_modified(self, vault, router):
        with patch("_common._templates.datetime") as mock_dt:
            mock_dt.now.return_value = self.FIXED_DT
            edit.prepend_to_artefact(str(vault), router, "Wiki/test-page.md", "Prepended\n")
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

    def _make_release(self, vault, path, status="active", version="v0.28.6", body="## Goal\n\nShip it.\n"):
        """Helper to create a release file at the given relative path."""
        abs_path = vault / path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(
            "---\n"
            "type: living/release\n"
            "tags:\n"
            "  - release\n"
            "  - project/brain\n"
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
        )
        assert result["path"] == "Ideas/body-only.md"
        assert (vault / "Ideas" / "body-only.md").is_file()

    def test_append_terminal_status_moves(self, vault, router):
        self._make_idea(vault, "Ideas/append-idea.md")
        result = edit.append_to_artefact(
            str(vault), router, "Ideas/append-idea.md", "\nExtra.\n",
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
        self._make_release(vault, "Releases/Brain/Search Hardening.md", version="v0.28.6")
        result = edit.edit_artefact(
            str(vault),
            router,
            "Releases/Brain/Search Hardening.md",
            "",
            frontmatter_changes={"status": "shipped", "shipped": "2026-04-16"},
        )
        assert result["path"] == "Releases/Brain/+Shipped/v0.28.6 - Search Hardening.md"
        assert (vault / "Releases" / "Brain" / "+Shipped" / "v0.28.6 - Search Hardening.md").is_file()

    def test_release_cancelled_moves_to_project_status_folder(self, vault, router):
        # Cancelled releases stay title-led — no version in the filename.
        self._make_release(vault, "Releases/Brain/Experimental Cut.md")
        result = edit.edit_artefact(
            str(vault),
            router,
            "Releases/Brain/Experimental Cut.md",
            "",
            frontmatter_changes={"status": "cancelled"},
        )
        assert result["path"] == "Releases/Brain/+Cancelled/Experimental Cut.md"
        assert (vault / "Releases" / "Brain" / "+Cancelled" / "Experimental Cut.md").is_file()

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
        )
        assert result["path"] == "Ideas/_Archive/20260101-old-idea.md"
        content = (vault / "Ideas" / "_Archive" / "20260101-old-idea.md").read_text()
        assert "Fixed body." in content

    def test_append_archived_skips_status_move(self, vault, router):
        """Append with terminal status on archived file doesn't move."""
        self._make_archived_idea(vault)
        result = edit.append_to_artefact(
            str(vault), router, "Ideas/_Archive/20260101-old-idea.md", "\nExtra.\n",
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
    """Tests for brain_action('archive') — archive_artefact()."""

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
    """Tests for brain_action('unarchive') — unarchive_artefact()."""

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
        with pytest.raises(ValueError, match="target=':body' is no longer valid"):
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
        )
        assert result["operation"] == "edit"
        assert "Wiki/test-page.md" in result["path"]

    def test_edit_skill_body(self, vault, router):
        self._create_skill(vault)
        result = edit.edit_resource(
            str(vault), router, resource="skill",
            operation="edit", name="test-skill",
            body="# Updated Skill\n\nNew skill content.\n",
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
            target=":entire_body",
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
        )
        assert result["path"] == "_Config/Memories/test-memory.md"
        assert result["operation"] == "append"
        content = (vault / "_Config" / "Memories" / "test-memory.md").read_text()
        assert "Original memory body." in content
        assert "Appended content." in content

    def test_memory_target_body_rejected(self, vault, router):
        self._create_memory(vault)
        with pytest.raises(ValueError, match="target=':body' is no longer valid"):
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
            target="# Test Skill",
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


class TestEditWikilinkWarnings:
    def test_clean_edit_no_warnings_key(self, vault, router):
        result = edit.edit_resource(
            str(vault), router, resource="artefact", operation="edit",
            path="Wiki/test-page.md", body="No links.\n",
            target=":entire_body",
        )
        assert "wikilink_warnings" not in result

    def test_broken_link_returns_warnings(self, vault, router):
        result = edit.edit_resource(
            str(vault), router, resource="artefact", operation="edit",
            path="Wiki/test-page.md", body="See [[missing-target]].\n",
            target=":entire_body",
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
        )
        assert "wikilink_warnings" in result

    def test_prepend_with_broken_link(self, vault, router):
        result = edit.edit_resource(
            str(vault), router, resource="artefact", operation="prepend",
            path="Wiki/test-page.md", body="Top [[top-missing]]\n\n",
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
            target=":entire_body", fix_links=True,
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
            target=":entire_body", fix_links=True,
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
            target=":entire_body",
        )
        assert "wikilink_fixes" not in result
        content = (vault / "Wiki" / "test-page.md").read_text()
        assert "[[real-target]]" in content
