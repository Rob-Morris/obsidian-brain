"""Tests for create.py — artefact creation."""

import json
import os
from unittest.mock import patch
from datetime import datetime, timezone

import pytest

import create
from _common import parse_frontmatter


# ---------------------------------------------------------------------------
# Vault fixture (with taxonomy + templates)
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path):
    """Create a vault fixture with configured types and templates."""
    # .brain-core/VERSION
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.10.3\n")

    # _Config
    config = tmp_path / "_Config"
    config.mkdir()
    (config / "router.md").write_text(
        "Prefer MCP tools.\n\nAlways:\n- Every artefact belongs in a typed folder.\n"
    )

    # Living type: Wiki
    (tmp_path / "Wiki").mkdir()

    # Living type: Ideas
    (tmp_path / "Ideas").mkdir()

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

    # Taxonomy: Ideas
    (tax_living / "ideas.md").write_text(
        "# Ideas\n\n"
        "## Naming\n\n`{slug}.md` in `Ideas/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/ideas\ntags:\n  - idea-tag\nstatus: shaping\n---\n```\n\n"
        "## Template\n\n[[_Config/Templates/Living/Ideas]]\n"
    )

    # Taxonomy: Logs
    tax_temporal = config / "Taxonomy" / "Temporal"
    tax_temporal.mkdir(parents=True)
    (tax_temporal / "logs.md").write_text(
        "# Logs\n\n"
        "## Naming\n\n`log-{slug}.md` in `_Temporal/Logs/yyyy-mm/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: temporal/logs\ntags:\n  - session\n---\n```\n\n"
        "## Template\n\n[[_Config/Templates/Temporal/Logs]]\n"
    )

    # Templates
    templates_living = config / "Templates" / "Living"
    templates_living.mkdir(parents=True)
    (templates_living / "Wiki.md").write_text(
        "---\ntype: living/wiki\ntags: []\n---\n\n# {{title}}\n\n"
    )
    (templates_living / "Ideas.md").write_text(
        "---\ntype: living/ideas\ntags: []\nstatus: shaping\n---\n\n# {{title}}\n\nWhat if...\n"
    )

    templates_temporal = config / "Templates" / "Temporal"
    templates_temporal.mkdir(parents=True)
    (templates_temporal / "Logs.md").write_text(
        "---\ntype: temporal/logs\ntags:\n  - session\n---\n\n# Log\n\n"
    )

    return tmp_path


@pytest.fixture
def router(vault):
    """Compile the router for the vault fixture."""
    import compile_router
    return compile_router.compile(str(vault))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCreateArtefact:
    def test_create_living_type(self, vault, router):
        result = create.create_artefact(str(vault), router, "wiki", "My Test Page")
        assert result["type"] == "living/wiki"
        assert result["title"] == "My Test Page"
        assert result["path"] == os.path.join("Wiki", "my-test-page.md")
        # File exists on disk
        abs_path = os.path.join(str(vault), result["path"])
        assert os.path.isfile(abs_path)

    def test_created_file_has_correct_frontmatter(self, vault, router):
        result = create.create_artefact(str(vault), router, "wiki", "Test FM")
        abs_path = os.path.join(str(vault), result["path"])
        with open(abs_path) as f:
            content = f.read()
        fields, _ = parse_frontmatter(content)
        assert fields["type"] == "living/wiki"

    def test_create_temporal_type(self, vault, router):
        result = create.create_artefact(str(vault), router, "logs", "My Session")
        assert result["type"] == "temporal/logs"
        # Path should include yyyy-mm subfolder
        assert "_Temporal/Logs/" in result["path"]
        parts = result["path"].split(os.sep)
        # Should have _Temporal/Logs/yyyy-mm/filename
        assert len(parts) == 4
        # The month folder should match yyyy-mm pattern
        import re
        assert re.match(r"\d{4}-\d{2}", parts[2])

    def test_body_override(self, vault, router):
        result = create.create_artefact(
            str(vault), router, "wiki", "Custom Body",
            body="# Custom\n\nMy custom content.\n"
        )
        abs_path = os.path.join(str(vault), result["path"])
        with open(abs_path) as f:
            content = f.read()
        assert "My custom content." in content

    def test_template_body_when_no_body(self, vault, router):
        result = create.create_artefact(str(vault), router, "ideas", "Some Idea")
        abs_path = os.path.join(str(vault), result["path"])
        with open(abs_path) as f:
            content = f.read()
        assert "What if..." in content

    def test_frontmatter_overrides(self, vault, router):
        result = create.create_artefact(
            str(vault), router, "ideas", "Override Test",
            frontmatter_overrides={"status": "developing"}
        )
        abs_path = os.path.join(str(vault), result["path"])
        with open(abs_path) as f:
            content = f.read()
        fields, _ = parse_frontmatter(content)
        assert fields["status"] == "developing"
        # Type should still be forced from artefact definition
        assert fields["type"] == "living/ideas"

    def test_slug_generation(self, vault, router):
        result = create.create_artefact(str(vault), router, "wiki", "Hello World! (2026)")
        assert result["path"] == os.path.join("Wiki", "hello-world-2026.md")

    def test_resolve_by_key(self, vault, router):
        result = create.create_artefact(str(vault), router, "wiki", "By Key")
        assert result["type"] == "living/wiki"

    def test_resolve_by_full_type(self, vault, router):
        result = create.create_artefact(str(vault), router, "living/wiki", "By Full Type")
        assert result["type"] == "living/wiki"

    def test_unknown_type_error(self, vault, router):
        with pytest.raises(ValueError, match="Unknown artefact type"):
            create.create_artefact(str(vault), router, "nonexistent", "Title")

    def test_unconfigured_type_error(self, vault, router):
        # Add an unconfigured folder (no taxonomy)
        (vault / "Scratch").mkdir()
        import compile_router
        router = compile_router.compile(str(vault))
        with pytest.raises(ValueError, match="not configured"):
            create.create_artefact(str(vault), router, "scratch", "Title")

    def test_collision_error(self, vault, router):
        # Create a file first
        create.create_artefact(str(vault), router, "wiki", "Duplicate")
        # Try to create the same file
        with pytest.raises(ValueError, match="already exists"):
            create.create_artefact(str(vault), router, "wiki", "Duplicate")


class TestResolveNamingPattern:
    def test_slug_pattern(self):
        assert create.resolve_naming_pattern("{slug}.md", "My Title") == "my-title.md"

    def test_name_pattern(self):
        assert create.resolve_naming_pattern("{name}.md", "My Title") == "my-title.md"

    def test_title_pattern(self):
        assert create.resolve_naming_pattern("{Title}.md", "My Title") == "My Title.md"

    def test_prefixed_slug(self):
        result = create.resolve_naming_pattern("log-{slug}.md", "My Session")
        assert result == "log-my-session.md"
