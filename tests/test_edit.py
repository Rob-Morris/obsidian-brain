"""Tests for edit.py — artefact editing, appending, and conversion."""

import os

import pytest

import edit
from _common import find_section, parse_frontmatter


# ---------------------------------------------------------------------------
# Vault fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path):
    """Create a vault fixture with configured types and content."""
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.10.3\n")

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

    # Temporal type: Logs
    temporal = tmp_path / "_Temporal"
    temporal.mkdir()
    (temporal / "Logs").mkdir()

    # Taxonomy: Wiki
    tax_living = config / "Taxonomy" / "Living"
    tax_living.mkdir(parents=True)
    (tax_living / "wiki.md").write_text(
        "# Wiki\n\n"
        "## Naming\n\n`{slug}.md` in `Wiki/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/wiki\ntags:\n  - topic-tag\n---\n```\n\n"
        "## Template\n\n[[_Config/Templates/Living/Wiki]]\n"
    )

    # Taxonomy: Designs
    (tax_living / "designs.md").write_text(
        "# Designs\n\n"
        "## Naming\n\n`{slug}.md` in `Designs/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/designs\ntags:\n  - design-tag\nstatus: shaping\n---\n```\n\n"
        "## Template\n\n[[_Config/Templates/Living/Designs]]\n"
    )

    # Taxonomy: Logs
    tax_temporal = config / "Taxonomy" / "Temporal"
    tax_temporal.mkdir(parents=True)
    (tax_temporal / "logs.md").write_text(
        "# Logs\n\n"
        "## Naming\n\n`log-{slug}.md` in `_Temporal/Logs/yyyy-mm/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: temporal/logs\ntags:\n  - session\n---\n```\n"
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

    return tmp_path


@pytest.fixture
def router(vault):
    """Compile the router for the vault fixture."""
    import compile_router
    return compile_router.compile(str(vault))


# ---------------------------------------------------------------------------
# Path validation tests
# ---------------------------------------------------------------------------

class TestValidateArtefactPath:
    def test_valid_path(self, vault, router):
        art = edit.validate_artefact_path(str(vault), router, "Wiki/test-page.md")
        assert art["key"] == "wiki"

    def test_invalid_folder(self, vault, router):
        with pytest.raises(ValueError, match="does not belong"):
            edit.validate_artefact_path(str(vault), router, "Unknown/file.md")

    def test_wrong_naming_pattern(self, vault, router):
        # Temporal log pattern requires yyyymmdd prefix — a bare name should fail
        month = vault / "_Temporal" / "Logs" / "2026-03"
        month.mkdir(parents=True)
        (month / "bad-name.md").write_text("---\ntype: temporal/logs\n---\n")
        with pytest.raises(ValueError, match="does not match expected pattern"):
            edit.validate_artefact_path(str(vault), router, "_Temporal/Logs/2026-03/bad-name.md")


class TestValidateArtefactFolder:
    def test_valid_folder_returns_artefact(self, vault, router):
        art = edit.validate_artefact_folder(str(vault), router, "Wiki/test-page.md")
        assert art["key"] == "wiki"

    def test_invalid_folder_raises(self, vault, router):
        with pytest.raises(ValueError, match="does not belong"):
            edit.validate_artefact_folder(str(vault), router, "Unknown/file.md")

    def test_ignores_naming_pattern(self, vault, router):
        """File with non-conforming name in valid folder succeeds."""
        month = vault / "_Temporal" / "Logs" / "2026-03"
        month.mkdir(parents=True)
        (month / "bad-name.md").write_text("---\ntype: temporal/logs\n---\n")
        art = edit.validate_artefact_folder(str(vault), router, "_Temporal/Logs/2026-03/bad-name.md")
        assert art["key"] == "logs"


class TestValidateArtefactNaming:
    def test_rejects_bad_name(self, vault, router):
        art = edit.validate_artefact_folder(str(vault), router, "_Temporal/Logs/2026-03/bad-name.md")
        with pytest.raises(ValueError, match="does not match expected pattern"):
            edit.validate_artefact_naming(art, "_Temporal/Logs/2026-03/bad-name.md")

    def test_accepts_conforming_name(self, vault, router):
        art = edit.validate_artefact_folder(str(vault), router, "Wiki/test-page.md")
        # Should not raise
        edit.validate_artefact_naming(art, "Wiki/test-page.md")


class TestNonConformingNameOperations:
    """Edit/append succeed on existing files with non-conforming names."""

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
        content = (month / "legacy-log.md").read_text()
        assert "Replaced." in content

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
        content = (month / "legacy-log.md").read_text()
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

    def test_edit_target_body_clears_content(self, vault, router):
        """target=':body' with empty string should clear the body."""
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md", "",
            frontmatter_changes={"status": "archived"},
            target=":body",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        fields, body = parse_frontmatter(content)
        assert fields["status"] == "archived"
        assert body.strip() == ""  # body intentionally cleared

    def test_edit_target_body_replaces_content(self, vault, router):
        """target=':body' with content should replace the entire body."""
        edit.edit_artefact(
            str(vault), router, "Wiki/test-page.md", "# New Content\n\nReplaced.",
            target=":body",
        )
        content = (vault / "Wiki" / "test-page.md").read_text()
        _, body = parse_frontmatter(content)
        assert "New Content" in body
        assert "Original body." not in body

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

    def test_append_file_not_found(self, vault, router):
        with pytest.raises(FileNotFoundError):
            edit.append_to_artefact(str(vault), router, "Wiki/nonexistent.md", "text")

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
