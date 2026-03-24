"""Tests for edit.py — artefact editing, appending, and conversion."""

import os

import pytest

import edit
from _common import parse_frontmatter


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
        with pytest.raises(ValueError, match="does not match expected pattern"):
            edit.validate_artefact_path(str(vault), router, "Wiki/Bad Name With Spaces.md")


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

    def test_edit_file_not_found(self, vault, router):
        with pytest.raises(FileNotFoundError):
            edit.edit_artefact(str(vault), router, "Wiki/nonexistent.md", "body")


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
