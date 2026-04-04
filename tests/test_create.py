"""Tests for create.py — artefact creation."""

import json
import os
from unittest.mock import patch
from datetime import datetime, timezone, timedelta

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
        "## Naming\n\n`{Title}.md` in `Wiki/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/wiki\ntags:\n  - topic-tag\n---\n```\n\n"
        "## Template\n\n[[_Config/Templates/Living/Wiki]]\n"
    )

    # Taxonomy: Ideas
    (tax_living / "ideas.md").write_text(
        "# Ideas\n\n"
        "## Naming\n\n`{Title}.md` in `Ideas/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/ideas\ntags:\n  - idea-tag\nstatus: shaping\n---\n```\n\n"
        "## Template\n\n[[_Config/Templates/Living/Ideas]]\n"
    )

    # Taxonomy: Logs
    tax_temporal = config / "Taxonomy" / "Temporal"
    tax_temporal.mkdir(parents=True)
    (tax_temporal / "logs.md").write_text(
        "# Logs\n\n"
        "## Naming\n\n`log~{Title}.md` in `_Temporal/Logs/yyyy-mm/`.\n\n"
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
        assert result["path"] == os.path.join("Wiki", "My Test Page.md")
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
            frontmatter_overrides={"status": "shaping"}
        )
        abs_path = os.path.join(str(vault), result["path"])
        with open(abs_path) as f:
            content = f.read()
        fields, _ = parse_frontmatter(content)
        assert fields["status"] == "shaping"
        # Type should still be forced from artefact definition
        assert fields["type"] == "living/ideas"

    def test_filename_generation(self, vault, router):
        result = create.create_artefact(str(vault), router, "wiki", "Hello World! (2026)")
        assert result["path"] == os.path.join("Wiki", "Hello World! (2026).md")

    def test_resolve_by_key(self, vault, router):
        result = create.create_artefact(str(vault), router, "wiki", "By Key")
        assert result["type"] == "living/wiki"

    def test_resolve_by_full_type(self, vault, router):
        result = create.create_artefact(str(vault), router, "living/wiki", "By Full Type")
        assert result["type"] == "living/wiki"

    def test_resolve_singular_form(self, vault, router):
        """Agents often pass singular type keys like 'log' instead of 'logs'."""
        result = create.create_artefact(str(vault), router, "log", "Singular Test")
        assert result["type"] == "temporal/logs"

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

    def test_same_folder_collision_appends_suffix(self, vault, router):
        create.create_artefact(str(vault), router, "wiki", "Duplicate")
        result = create.create_artefact(str(vault), router, "wiki", "Duplicate")
        # Gets a random 3-char suffix: "Wiki/Duplicate xxx.md"
        assert result["path"].startswith("Wiki/Duplicate ")
        assert result["path"] != "Wiki/Duplicate.md"
        assert result["path"].endswith(".md")
        assert os.path.isfile(os.path.join(str(vault), result["path"]))

    def test_same_folder_collision_suffix_is_3_chars(self, vault, router):
        create.create_artefact(str(vault), router, "wiki", "Check")
        result = create.create_artefact(str(vault), router, "wiki", "Check")
        # Extract suffix: "Check xxx.md" → "xxx"
        stem = os.path.splitext(os.path.basename(result["path"]))[0]
        suffix = stem.removeprefix("Check ")
        assert len(suffix) == 3

    def test_create_with_parent_subfolder(self, vault, router):
        """Parent parameter places living artefact in a project subfolder."""
        result = create.create_artefact(str(vault), router, "ideas", "Sub Idea", parent="Brain")
        assert result["path"] == os.path.join("Ideas", "Brain", "Sub Idea.md")
        abs_path = os.path.join(str(vault), result["path"])
        assert os.path.isfile(abs_path)

    def test_parent_creates_directory(self, vault, router):
        """Parent subfolder is created automatically if it doesn't exist."""
        result = create.create_artefact(str(vault), router, "wiki", "New Sub", parent="Project")
        assert os.path.isdir(os.path.join(str(vault), "Wiki", "Project"))

    def test_parent_ignored_for_temporal(self, vault, router):
        """Temporal types always use yyyy-mm/ regardless of parent."""
        result = create.create_artefact(str(vault), router, "logs", "Session", parent="Brain")
        assert "_Temporal/Logs/" in result["path"]
        assert "Brain" not in result["path"]

    def test_temporal_folder_matches_frontmatter_timestamp(self, vault, router):
        """The yyyy-mm folder and the created timestamp must agree."""
        fixed = datetime(2026, 6, 15, 9, 0, 0, tzinfo=timezone(timedelta(hours=10)))
        with patch("create.datetime") as mock_dt:
            mock_dt.now.return_value = fixed
            result = create.create_artefact(str(vault), router, "log", "June Entry")
        assert "_Temporal/Logs/2026-06/" in result["path"]
        content = open(os.path.join(str(vault), result["path"])).read()
        fields, _ = parse_frontmatter(content)
        assert fields["created"].startswith("2026-06-15")


class TestResolveNamingPattern:
    def test_slug_pattern(self):
        assert create.resolve_naming_pattern("{slug}.md", "My Title") == "My Title.md"

    def test_name_pattern(self):
        assert create.resolve_naming_pattern("{name}.md", "My Title") == "My Title.md"

    def test_title_pattern(self):
        assert create.resolve_naming_pattern("{Title}.md", "My Title") == "My Title.md"

    def test_prefixed_title(self):
        result = create.resolve_naming_pattern("log~{Title}.md", "My Session")
        assert result == "log~My Session.md"

    def test_unsafe_chars_stripped(self):
        result = create.resolve_naming_pattern("{Title}.md", "Q3 / Q4 Review")
        assert result == "Q3 Q4 Review.md"

    def test_date_pattern_uses_injected_now(self):
        fixed = datetime(2026, 1, 15, 0, 0, 0, tzinfo=timezone(timedelta(hours=11)))
        result = create.resolve_naming_pattern("yyyymmdd-log~{slug}.md", "My Entry", _now=fixed)
        assert result == "20260115-log~My Entry.md"

    def test_mmdd_pattern_uses_injected_now(self):
        fixed = datetime(2026, 3, 7, 0, 0, 0, tzinfo=timezone(timedelta(hours=11)))
        result = create.resolve_naming_pattern("yyyy-mm-dd~{slug}.md", "Entry", _now=fixed)
        assert result == "2026-03-07~Entry.md"


# ---------------------------------------------------------------------------
# Basename disambiguation
# ---------------------------------------------------------------------------

class TestBasenameDisambiguation:
    def test_no_suffix_when_unique(self, vault, router):
        result = create.create_artefact(str(vault), router, "wiki", "Unique Page")
        assert result["path"] == "Wiki/Unique Page.md"

    def test_appends_type_on_collision(self, vault, router):
        # Create a wiki page first
        create.create_artefact(str(vault), router, "wiki", "JWT Refresh")
        # Create an idea with the same title — should get (ideas) suffix
        result = create.create_artefact(str(vault), router, "ideas", "JWT Refresh")
        assert result["path"] == "Ideas/JWT Refresh (ideas).md"
        assert os.path.isfile(os.path.join(str(vault), result["path"]))

    def test_disambiguated_file_has_correct_content(self, vault, router):
        create.create_artefact(str(vault), router, "wiki", "Overlap")
        result = create.create_artefact(str(vault), router, "ideas", "Overlap", body="# My Idea\n")
        content = (vault / "Ideas" / "Overlap (ideas).md").read_text()
        assert "# My Idea" in content


class TestCreateArtefactTimestamps:
    FIXED_DT = datetime(2026, 4, 2, 9, 0, 0, tzinfo=timezone(timedelta(hours=11)))
    FIXED_ISO = "2026-04-02T09:00:00+11:00"

    def test_created_injected(self, vault, router):
        with patch("create.datetime") as mock_dt:
            mock_dt.now.return_value = self.FIXED_DT
            result = create.create_artefact(str(vault), router, "wiki", "TS Test")
        content = open(os.path.join(str(vault), result["path"])).read()
        fields, _ = parse_frontmatter(content)
        assert fields["created"] == self.FIXED_ISO

    def test_modified_injected(self, vault, router):
        with patch("create.datetime") as mock_dt:
            mock_dt.now.return_value = self.FIXED_DT
            result = create.create_artefact(str(vault), router, "wiki", "TS Test 2")
        content = open(os.path.join(str(vault), result["path"])).read()
        fields, _ = parse_frontmatter(content)
        assert fields["modified"] == self.FIXED_ISO

    def test_created_respects_override(self, vault, router):
        custom = "2025-01-01T00:00:00+00:00"
        with patch("create.datetime") as mock_dt:
            mock_dt.now.return_value = self.FIXED_DT
            result = create.create_artefact(
                str(vault), router, "wiki", "TS Override",
                frontmatter_overrides={"created": custom}
            )
        content = open(os.path.join(str(vault), result["path"])).read()
        fields, _ = parse_frontmatter(content)
        assert fields["created"] == custom

    def test_modified_respects_override(self, vault, router):
        custom = "2025-06-15T12:00:00+00:00"
        with patch("create.datetime") as mock_dt:
            mock_dt.now.return_value = self.FIXED_DT
            result = create.create_artefact(
                str(vault), router, "wiki", "TS Mod Override",
                frontmatter_overrides={"modified": custom}
            )
        content = open(os.path.join(str(vault), result["path"])).read()
        fields, _ = parse_frontmatter(content)
        assert fields["modified"] == custom
