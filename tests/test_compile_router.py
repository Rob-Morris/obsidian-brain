"""Tests for compile_router.py — runs against the template vault fixture."""

import json
import os
import tempfile
import shutil

import pytest

import compile_router as cr

TEMPLATE_VAULT = os.path.join(
    os.path.dirname(__file__), "..", "template-vault"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path):
    """Create a minimal vault fixture in a temp directory."""
    # .brain-core/VERSION
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("1.2.3\n")

    # _Config/router.md
    config = tmp_path / "_Config"
    config.mkdir()
    (config / "router.md").write_text(
        "Prefer MCP tools.\n\n"
        "Always:\n"
        "- Every artefact belongs in a typed folder.\n"
        "- Keep instruction files lean.\n\n"
        "Conditional:\n"
        "- After meaningful work → [[_Config/Taxonomy/Temporal/logs]]\n"
    )

    # Living type: Wiki
    (tmp_path / "Wiki").mkdir()

    # Taxonomy for Wiki (using exact folder name)
    tax_living = config / "Taxonomy" / "Living"
    tax_living.mkdir(parents=True)
    (tax_living / "wiki.md").write_text(
        "# Wiki\n\n"
        "## Naming\n\n"
        "`{slug}.md` in `Wiki/`.\n\n"
        "## Frontmatter\n\n"
        "```yaml\n---\ntype: living/wiki\ntags:\n  - topic-tag\n---\n```\n\n"
        "## Template\n\n"
        "[[_Config/Templates/Living/Wiki]]\n"
    )

    # Temporal types
    temporal = tmp_path / "_Temporal"
    temporal.mkdir()
    (temporal / "Logs").mkdir()
    (temporal / ".hidden").mkdir()   # should be skipped
    (temporal / "_internal").mkdir()  # should be skipped

    # Taxonomy for Logs
    tax_temporal = config / "Taxonomy" / "Temporal"
    tax_temporal.mkdir(parents=True)
    (tax_temporal / "logs.md").write_text(
        "# Logs\n\n"
        "## Naming\n\n"
        "`yyyymmdd-log.md` in `_Temporal/Logs/yyyy-mm/`.\n\n"
        "## Frontmatter\n\n"
        "```yaml\n---\ntype: temporal/log\ntags:\n  - log\n---\n```\n\n"
        "## Trigger\n\n"
        "After completing meaningful work, append a timestamped entry.\n\n"
        "## Template\n\n"
        "[[_Config/Templates/Temporal/Logs]]\n"
    )

    # Skills
    skills = config / "Skills" / "vault-maintenance"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text("# Vault Maintenance\n")

    # Styles
    styles = config / "Styles"
    styles.mkdir()
    (styles / "obsidian.md").write_text("# Obsidian Style\n")

    # Plugins
    plugins = tmp_path / "_Plugins" / "example"
    plugins.mkdir(parents=True)
    (plugins / "SKILL.md").write_text("# Example Plugin\n")

    # Core skills
    core_skills_dir = bc / "skills" / "test-skill"
    core_skills_dir.mkdir(parents=True)
    (core_skills_dir / "SKILL.md").write_text(
        "---\nname: test-skill\n---\n\n"
        "# Test Skill (Core)\n\nA test core skill.\n"
    )

    # System dirs that should be excluded from living types
    (tmp_path / "_Config").exists()  # already exists
    (tmp_path / "_Assets").mkdir()
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".git").mkdir()

    return tmp_path


@pytest.fixture
def template_vault():
    """Use the real template vault (read-only)."""
    path = os.path.abspath(TEMPLATE_VAULT)
    if not os.path.isdir(path):
        pytest.skip("template-vault not found")
    if not os.path.isdir(os.path.join(path, ".brain-core")):
        pytest.skip(".brain-core not linked — run 'make dev-link'")
    return path


# ---------------------------------------------------------------------------
# is_system_dir
# ---------------------------------------------------------------------------

class TestIsSystemDir:
    def test_dot_prefixed_dirs_are_system(self):
        assert cr.is_system_dir(".obsidian")
        assert cr.is_system_dir(".git")
        assert cr.is_system_dir(".trash")
        assert cr.is_system_dir(".brain-core")

    def test_underscore_prefixed_dirs_are_system(self):
        assert cr.is_system_dir("_Config")
        assert cr.is_system_dir("_Plugins")
        assert cr.is_system_dir("_Assets")
        assert cr.is_system_dir("_AnyNewSystemDir")

    def test_temporal_is_system(self):
        # _Temporal starts with _ so is excluded from living type scan;
        # it gets its own dedicated temporal scan instead
        assert cr.is_system_dir("_Temporal")

    def test_normal_dirs_are_not_system(self):
        assert not cr.is_system_dir("Wiki")
        assert not cr.is_system_dir("Projects")
        assert not cr.is_system_dir("Daily Notes")


# ---------------------------------------------------------------------------
# Filesystem scanning
# ---------------------------------------------------------------------------

class TestScanLivingTypes:
    def test_finds_wiki(self, vault):
        types = cr.scan_living_types(str(vault))
        folders = [t["folder"] for t in types]
        assert "Wiki" in folders

    def test_excludes_system_dirs(self, vault):
        types = cr.scan_living_types(str(vault))
        folders = [t["folder"] for t in types]
        assert "_Config" not in folders
        assert "_Assets" not in folders
        assert ".obsidian" not in folders
        assert ".git" not in folders
        assert "_Plugins" not in folders

    def test_temporal_not_in_living(self, vault):
        types = cr.scan_living_types(str(vault))
        folders = [t["folder"] for t in types]
        assert "_Temporal" not in folders

    def test_uses_relative_paths(self, vault):
        types = cr.scan_living_types(str(vault))
        wiki = [t for t in types if t["folder"] == "Wiki"][0]
        assert wiki["path"] == "Wiki"
        assert "absolute" not in str(wiki)

    def test_key_derivation(self, vault):
        # Add a folder with spaces
        (vault / "Daily Notes").mkdir()
        types = cr.scan_living_types(str(vault))
        dn = [t for t in types if t["folder"] == "Daily Notes"][0]
        assert dn["key"] == "daily-notes"
        assert dn["type"] == "living/daily-notes"


class TestScanTemporalTypes:
    def test_finds_logs(self, vault):
        types = cr.scan_temporal_types(str(vault))
        folders = [t["folder"] for t in types]
        assert "Logs" in folders

    def test_excludes_hidden_dirs(self, vault):
        types = cr.scan_temporal_types(str(vault))
        folders = [t["folder"] for t in types]
        assert ".hidden" not in folders

    def test_excludes_underscore_dirs(self, vault):
        types = cr.scan_temporal_types(str(vault))
        folders = [t["folder"] for t in types]
        assert "_internal" not in folders

    def test_relative_paths(self, vault):
        types = cr.scan_temporal_types(str(vault))
        logs = [t for t in types if t["folder"] == "Logs"][0]
        assert logs["path"] == os.path.join("_Temporal", "Logs")

    def test_no_temporal_dir(self, tmp_path):
        types = cr.scan_temporal_types(str(tmp_path))
        assert types == []


# ---------------------------------------------------------------------------
# Taxonomy parsing
# ---------------------------------------------------------------------------

class TestParseTaxonomyFile:
    def test_parses_wiki_taxonomy(self, vault):
        path = str(vault / "_Config" / "Taxonomy" / "Living" / "wiki.md")
        result = cr.parse_taxonomy_file(path)
        assert result["naming"]["pattern"] == "{slug}.md"
        assert result["naming"]["folder"] == "Wiki/"
        assert result["frontmatter"]["type"] == "living/wiki"
        assert "type" in result["frontmatter"]["required"]
        assert result["template_file"] == "_Config/Templates/Living/Wiki"

    def test_parses_trigger(self, vault):
        path = str(vault / "_Config" / "Taxonomy" / "Temporal" / "logs.md")
        result = cr.parse_taxonomy_file(path)
        assert result["trigger"] is not None
        assert result["trigger"]["category"] == "after"
        assert "meaningful work" in result["trigger"]["condition"].lower()


# ---------------------------------------------------------------------------
# Status enum extraction
# ---------------------------------------------------------------------------

class TestParseStatusEnum:
    def test_inline_yaml_comment(self, tmp_path):
        """Pattern 1: status: default  # val1 | val2 | val3"""
        f = tmp_path / "tax.md"
        f.write_text(
            "# Designs\n\n"
            "## Frontmatter\n\n"
            "```yaml\n---\ntype: living/design\n"
            "status: shaping             # shaping | ready | active | implemented | parked\n"
            "---\n```\n"
        )
        result = cr.parse_status_enum(f.read_text())
        assert result == ["shaping", "ready", "active", "implemented", "parked"]

    def test_lifecycle_table(self, tmp_path):
        """Pattern 2: | `value` | description | rows"""
        f = tmp_path / "tax.md"
        f.write_text(
            "# Writing\n\n"
            "## Lifecycle\n\n"
            "| Status | Meaning |\n"
            "|---|---|\n"
            "| `draft` | Work in progress. |\n"
            "| `editing` | Refining. |\n"
            "| `published` | Released. |\n"
            "| `parked` | Set aside. |\n\n"
            "## Frontmatter\n\n"
            "```yaml\n---\ntype: living/writing\nstatus: draft\n---\n```\n"
        )
        result = cr.parse_status_enum(f.read_text())
        assert result == ["draft", "editing", "published", "parked"]

    def test_prose_line(self, tmp_path):
        """Pattern 3: Status values: `val1`, `val2`, `val3`."""
        f = tmp_path / "tax.md"
        f.write_text(
            "# Plans\n\n"
            "## Frontmatter\n\n"
            "```yaml\n---\ntype: temporal/plan\nstatus: draft\n---\n```\n\n"
            "Status values: `draft`, `approved`, `completed`.\n"
        )
        result = cr.parse_status_enum(f.read_text())
        assert result == ["draft", "approved", "completed"]

    def test_no_status_returns_none(self, tmp_path):
        """Types without status fields return None."""
        f = tmp_path / "tax.md"
        f.write_text(
            "# Wiki\n\n"
            "## Frontmatter\n\n"
            "```yaml\n---\ntype: living/wiki\ntags:\n  - topic\n---\n```\n"
        )
        result = cr.parse_status_enum(f.read_text())
        assert result is None

    def test_inline_comment_takes_priority_over_table(self, tmp_path):
        """When both inline comment and table exist, inline comment wins."""
        f = tmp_path / "tax.md"
        f.write_text(
            "# Test\n\n"
            "## Lifecycle\n\n"
            "| Status | Meaning |\n|---|---|\n"
            "| `alpha` | First. |\n| `beta` | Second. |\n\n"
            "## Frontmatter\n\n"
            "```yaml\n---\nstatus: alpha  # alpha | beta | gamma\n---\n```\n"
        )
        result = cr.parse_status_enum(f.read_text())
        assert result == ["alpha", "beta", "gamma"]


# ---------------------------------------------------------------------------
# Terminal status extraction
# ---------------------------------------------------------------------------

class TestParseTerminalStatuses:
    def test_explicit_status_reference(self, tmp_path):
        """Detects `implemented` from 'reaches `implemented` status'."""
        f = tmp_path / "tax.md"
        f.write_text(
            "# Designs\n\n"
            "## Terminal Status\n\n"
            "When a design reaches `implemented` status, move to +Implemented/.\n\n"
            "1. Set `status: implemented` in frontmatter\n"
            "2. Move to `Designs/+Implemented/`\n\n"
            "## Naming\n"
        )
        enum = ["shaping", "ready", "active", "implemented", "parked"]
        result = cr.parse_terminal_statuses(f.read_text(), enum)
        assert result == ["implemented"]

    def test_cross_reference_capitalised_enum(self, tmp_path):
        """Detects 'adopted' from 'Adopted ideas remain searchable'."""
        f = tmp_path / "tax.md"
        f.write_text(
            "# Ideas\n\n"
            "## Terminal Status\n\n"
            "Adopted ideas remain searchable in the +Adopted/ folder.\n\n"
            "## Naming\n"
        )
        enum = ["new", "adopted", "parked"]
        result = cr.parse_terminal_statuses(f.read_text(), enum)
        assert result == ["adopted"]

    def test_no_false_positive_on_incidental_word(self, tmp_path):
        """Does not match 'active' from 'active folder clean'."""
        f = tmp_path / "tax.md"
        f.write_text(
            "# Designs\n\n"
            "## Terminal Status\n\n"
            "When a design reaches `implemented` status, move the design "
            "to keep the active folder clean.\n\n"
            "1. Set `status: implemented`\n\n"
            "## Naming\n"
        )
        enum = ["shaping", "ready", "active", "implemented", "parked"]
        result = cr.parse_terminal_statuses(f.read_text(), enum)
        assert "active" not in result
        assert "implemented" in result

    def test_no_terminal_or_archiving_section_returns_none(self, tmp_path):
        """Types without ## Terminal Status or ## Archiving return None."""
        f = tmp_path / "tax.md"
        f.write_text("# Wiki\n\n## Naming\n\nSome naming rules.\n")
        result = cr.parse_terminal_statuses(f.read_text(), ["active"])
        assert result is None

    def test_archiving_section_with_no_statuses(self, tmp_path):
        """Archiving section that doesn't reference any status values."""
        f = tmp_path / "tax.md"
        f.write_text(
            "# Writing\n\n"
            "## Archiving\n\n"
            "Superseded writing can be archived.\n\n"
            "## Naming\n"
        )
        result = cr.parse_terminal_statuses(f.read_text(), ["draft", "editing"])
        assert result is None

    def test_published_cross_reference(self, tmp_path):
        """Detects 'published' from 'Published writing that has been superseded'."""
        f = tmp_path / "tax.md"
        f.write_text(
            "# Writing\n\n"
            "## Archiving\n\n"
            "Published writing that has been superseded can be archived.\n\n"
            "## Naming\n"
        )
        enum = ["draft", "editing", "review", "published", "parked"]
        result = cr.parse_terminal_statuses(f.read_text(), enum)
        assert result == ["published"]

    def test_no_enum_still_finds_explicit_references(self, tmp_path):
        """Even without an enum, explicit `status: value` patterns are found."""
        f = tmp_path / "tax.md"
        f.write_text(
            "# Test\n\n"
            "## Terminal Status\n\n"
            "Set `status: done` and move to +Done/.\n\n"
            "## Naming\n"
        )
        result = cr.parse_terminal_statuses(f.read_text(), None)
        assert result == ["done"]

    def test_archiving_heading_still_works(self, tmp_path):
        """## Archiving heading is still matched (e.g. writing taxonomy)."""
        f = tmp_path / "tax.md"
        f.write_text(
            "# Writing\n\n"
            "## Archiving\n\n"
            "Set `status: published` and move to +Published/.\n\n"
            "## Naming\n"
        )
        result = cr.parse_terminal_statuses(f.read_text(), None)
        assert result == ["published"]


# ---------------------------------------------------------------------------
# Frontmatter integration — status_enum and terminal_statuses in parsed output
# ---------------------------------------------------------------------------

class TestParseTaxonomyStatusIntegration:
    def test_status_enum_in_frontmatter(self, tmp_path):
        """parse_taxonomy_file includes status_enum in frontmatter."""
        f = tmp_path / "designs.md"
        f.write_text(
            "# Designs\n\n"
            "## Frontmatter\n\n"
            "```yaml\n---\ntype: living/design\n"
            "status: shaping  # shaping | ready | active | implemented | parked\n"
            "---\n```\n\n"
            "## Terminal Status\n\n"
            "When `implemented` status is reached, move to +Implemented/.\n"
        )
        result = cr.parse_taxonomy_file(str(f))
        assert result["frontmatter"]["status_enum"] == ["shaping", "ready", "active", "implemented", "parked"]
        assert result["frontmatter"]["terminal_statuses"] == ["implemented"]

    def test_no_status_fields_are_null(self, tmp_path):
        """Types without status have null for both new fields."""
        f = tmp_path / "wiki.md"
        f.write_text(
            "# Wiki\n\n"
            "## Frontmatter\n\n"
            "```yaml\n---\ntype: living/wiki\ntags:\n  - topic\n---\n```\n"
        )
        result = cr.parse_taxonomy_file(str(f))
        assert result["frontmatter"]["status_enum"] is None
        assert result["frontmatter"]["terminal_statuses"] is None

    def test_full_compile_includes_status_fields(self, vault):
        """Compiled output includes status_enum and terminal_statuses."""
        # Add a type with status enum to the fixture
        (vault / "Designs").mkdir()
        tax = vault / "_Config" / "Taxonomy" / "Living"
        (tax / "designs.md").write_text(
            "# Designs\n\n"
            "## Naming\n\n`{slug}.md` in `Designs/`.\n\n"
            "## Frontmatter\n\n"
            "```yaml\n---\ntype: living/design\n"
            "status: shaping  # shaping | ready | active | implemented | parked\n"
            "---\n```\n\n"
            "## Terminal Status\n\n"
            "When a design reaches `implemented` status, move to +Implemented/.\n"
            "1. Set `status: implemented`\n\n"
            "## Template\n\n[[_Config/Templates/Living/Design]]\n"
        )
        result = cr.compile(vault)
        designs = [a for a in result["artefacts"] if a["folder"] == "Designs"][0]
        assert designs["frontmatter"]["status_enum"] == ["shaping", "ready", "active", "implemented", "parked"]
        assert designs["frontmatter"]["terminal_statuses"] == ["implemented"]

        # Wiki should have null for both
        wiki = [a for a in result["artefacts"] if a["folder"] == "Wiki"][0]
        assert wiki["frontmatter"]["status_enum"] is None
        assert wiki["frontmatter"]["terminal_statuses"] is None


class TestInferTriggerCategory:
    def test_after(self):
        assert cr.infer_trigger_category("After meaningful work") == "after"

    def test_before(self):
        assert cr.infer_trigger_category("Before complex work") == "before"

    def test_ongoing(self):
        assert cr.infer_trigger_category("When creating a new page") == "ongoing"


# ---------------------------------------------------------------------------
# Router parsing
# ---------------------------------------------------------------------------

class TestParseRouter:
    def test_parses_always_rules(self, vault):
        path = str(vault / "_Config" / "router.md")
        always, _ = cr.parse_router(path)
        assert len(always) == 2
        assert "typed folder" in always[0].lower()

    def test_parses_conditionals(self, vault):
        path = str(vault / "_Config" / "router.md")
        _, conds = cr.parse_router(path)
        assert len(conds) == 1
        assert conds[0]["target"] == "_Config/Taxonomy/Temporal/logs"

    def test_warns_on_empty_router(self, vault, capsys):
        empty = vault / "_Config" / "empty-router.md"
        empty.write_text("Nothing here.\n")
        cr.parse_router(str(empty))
        captured = capsys.readouterr()
        assert "Warning" in captured.err

    def test_no_warning_on_valid_router(self, vault, capsys):
        path = str(vault / "_Config" / "router.md")
        cr.parse_router(path)
        captured = capsys.readouterr()
        assert "Warning" not in captured.err


# ---------------------------------------------------------------------------
# Version reading
# ---------------------------------------------------------------------------

class TestReadVersion:
    def test_reads_version(self, vault):
        version = cr.read_version(str(vault))
        assert version == "1.2.3"

    def test_strips_whitespace(self, vault):
        (vault / ".brain-core" / "VERSION").write_text("  2.0.0  \n")
        version = cr.read_version(str(vault))
        assert version == "2.0.0"


# ---------------------------------------------------------------------------
# Enrichment discovery
# ---------------------------------------------------------------------------

class TestDiscoverSkills:
    def test_finds_skills(self, vault):
        skills = cr.discover_skills(str(vault))
        assert len(skills) == 1
        assert skills[0]["name"] == "vault-maintenance"
        assert "SKILL.md" in skills[0]["skill_doc"]

    def test_no_skills_dir(self, tmp_path):
        assert cr.discover_skills(str(tmp_path)) == []


class TestDiscoverPlugins:
    def test_finds_plugins(self, vault):
        plugins = cr.discover_plugins(str(vault))
        assert len(plugins) == 1
        assert plugins[0]["name"] == "example"

    def test_no_plugins_dir(self, tmp_path):
        assert cr.discover_plugins(str(tmp_path)) == []


class TestDiscoverStyles:
    def test_finds_styles(self, vault):
        styles = cr.discover_styles(str(vault))
        assert len(styles) == 1
        assert styles[0]["name"] == "obsidian"
        assert "style_doc" in styles[0]

    def test_returns_list(self, vault):
        styles = cr.discover_styles(str(vault))
        assert isinstance(styles, list)

    def test_no_styles_dir(self, tmp_path):
        assert cr.discover_styles(str(tmp_path)) == []


# ---------------------------------------------------------------------------
# Full compilation
# ---------------------------------------------------------------------------

class TestCompile:
    def test_full_compile(self, vault):
        result = cr.compile(vault)
        assert result["meta"]["brain_core_version"] == "1.2.3"
        assert result["meta"]["source_hash"].startswith("sha256:")
        assert len(result["meta"]["sources"]) > 0
        assert len(result["always_rules"]) == 2
        assert len(result["artefacts"]) >= 2  # Wiki + Logs at minimum
        assert len(result["triggers"]) == 1
        assert len(result["skills"]) == 2
        assert len(result["plugins"]) == 1
        assert isinstance(result["styles"], list)

    def test_artefact_paths_are_relative(self, vault):
        result = cr.compile(vault)
        for art in result["artefacts"]:
            assert not os.path.isabs(art["path"])

    def test_configured_vs_unconfigured(self, vault):
        # Add a folder with no taxonomy
        (vault / "Projects").mkdir()
        result = cr.compile(vault)
        projects = [a for a in result["artefacts"] if a["folder"] == "Projects"]
        assert len(projects) == 1
        assert projects[0]["configured"] is False
        assert projects[0]["naming"] is None

    def test_taxonomy_exact_name_fallback(self, vault):
        """Taxonomy lookup tries exact folder name first, then key."""
        # Create a folder with a capitalised taxonomy file
        (vault / "Recipes").mkdir()
        tax = vault / "_Config" / "Taxonomy" / "Living"
        (tax / "Recipes.md").write_text(
            "# Recipes\n\n## Naming\n\n`{slug}.md` in `Recipes/`.\n"
        )
        result = cr.compile(vault)
        recipes = [a for a in result["artefacts"] if a["folder"] == "Recipes"]
        assert len(recipes) == 1
        assert recipes[0]["configured"] is True
        assert "Recipes.md" in recipes[0]["taxonomy_file"]

    def test_trigger_merging(self, vault):
        result = cr.compile(vault)
        triggers = result["triggers"]
        assert len(triggers) == 1
        assert triggers[0]["category"] == "after"
        assert "meaningful work" in triggers[0]["condition"].lower()

    def test_core_skills_before_user_skills(self, vault):
        result = cr.compile(vault)
        sources = [s["source"] for s in result["skills"]]
        assert sources == ["core", "user"]

    def test_skills_have_source_tag(self, vault):
        result = cr.compile(vault)
        for s in result["skills"]:
            assert "source" in s
            assert s["source"] in ("core", "user")

    def test_version_from_file(self, vault):
        """Version comes from VERSION file, not a hardcoded constant."""
        (vault / ".brain-core" / "VERSION").write_text("9.9.9\n")
        result = cr.compile(vault)
        assert result["meta"]["brain_core_version"] == "9.9.9"


# ---------------------------------------------------------------------------
# Template vault integration test
# ---------------------------------------------------------------------------

class TestDiscoverCoreSkills:
    def test_finds_core_skills(self, vault):
        skills = cr.discover_core_skills(str(vault))
        assert len(skills) == 1
        assert skills[0]["name"] == "test-skill"
        assert skills[0]["source"] == "core"
        assert "SKILL.md" in skills[0]["skill_doc"]

    def test_no_core_skills_dir(self, tmp_path):
        assert cr.discover_core_skills(str(tmp_path)) == []

    def test_core_skills_path_is_relative(self, vault):
        skills = cr.discover_core_skills(str(vault))
        for s in skills:
            assert not os.path.isabs(s["skill_doc"])


class TestDiscoverMemories:
    def test_finds_memories(self, vault):
        memories_dir = vault / "_Config" / "Memories"
        memories_dir.mkdir()
        (memories_dir / "test-memory.md").write_text(
            "---\ntriggers: [brain core, vault system]\n---\n\n# Test Memory\n\nSome context.\n"
        )
        memories = cr.discover_memories(str(vault))
        assert len(memories) == 1
        assert memories[0]["name"] == "test-memory"
        assert memories[0]["triggers"] == ["brain core", "vault system"]
        assert "memory_doc" in memories[0]

    def test_excludes_readme(self, vault):
        memories_dir = vault / "_Config" / "Memories"
        memories_dir.mkdir()
        (memories_dir / "README.md").write_text("# Memories\n\nHow to use.\n")
        (memories_dir / "actual-memory.md").write_text(
            "---\ntriggers: [test]\n---\n\nContent.\n"
        )
        memories = cr.discover_memories(str(vault))
        assert len(memories) == 1
        assert memories[0]["name"] == "actual-memory"

    def test_no_memories_dir(self, tmp_path):
        assert cr.discover_memories(str(tmp_path)) == []

    def test_yaml_list_triggers(self, vault):
        memories_dir = vault / "_Config" / "Memories"
        memories_dir.mkdir()
        (memories_dir / "listed.md").write_text(
            "---\ntriggers:\n  - alpha\n  - beta\n---\n\nContent.\n"
        )
        memories = cr.discover_memories(str(vault))
        assert memories[0]["triggers"] == ["alpha", "beta"]

    def test_empty_triggers(self, vault):
        memories_dir = vault / "_Config" / "Memories"
        memories_dir.mkdir()
        (memories_dir / "empty.md").write_text(
            "---\ntriggers: []\n---\n\nContent.\n"
        )
        memories = cr.discover_memories(str(vault))
        assert memories[0]["triggers"] == []

    def test_memories_in_full_compile(self, vault):
        memories_dir = vault / "_Config" / "Memories"
        memories_dir.mkdir()
        (memories_dir / "test-memory.md").write_text(
            "---\ntriggers: [brain core]\n---\n\n# Test\n"
        )
        result = cr.compile(vault)
        assert "memories" in result
        assert len(result["memories"]) == 1
        assert result["memories"][0]["name"] == "test-memory"

    def test_memories_tracked_in_sources(self, vault):
        memories_dir = vault / "_Config" / "Memories"
        memories_dir.mkdir()
        (memories_dir / "tracked.md").write_text(
            "---\ntriggers: [test]\n---\n\nContent.\n"
        )
        result = cr.compile(vault)
        memory_path = os.path.join("_Config", "Memories", "tracked.md")
        assert memory_path in result["meta"]["sources"]


class TestTemplateVault:
    def test_compiles_template_vault(self, template_vault):
        result = cr.compile(template_vault)

        # Should have the real version
        assert result["meta"]["brain_core_version"]

        # Should find Notes (living) and Logs/Plans/Transcripts (temporal)
        folders = [a["folder"] for a in result["artefacts"]]
        assert "Notes" in folders

        temporal = [a for a in result["artefacts"] if a["classification"] == "temporal"]
        temporal_folders = [a["folder"] for a in temporal]
        assert "Logs" in temporal_folders
        assert "Plans" in temporal_folders
        assert "Transcripts" in temporal_folders

        # All paths should be relative
        for art in result["artefacts"]:
            assert not os.path.isabs(art["path"])

        # Should have triggers
        assert len(result["triggers"]) >= 1

        # JSON roundtrip
        json_str = json.dumps(result, indent=2)
        roundtripped = json.loads(json_str)
        assert roundtripped["meta"]["brain_core_version"] == result["meta"]["brain_core_version"]


# ---------------------------------------------------------------------------
# Hash invalidation
# ---------------------------------------------------------------------------

class TestHashing:
    def test_hash_changes_on_file_change(self, vault):
        result1 = cr.compile(vault)
        # Modify a source file
        router = vault / "_Config" / "router.md"
        router.write_text(router.read_text() + "\n- New rule added.\n")
        result2 = cr.compile(vault)
        assert result1["meta"]["source_hash"] != result2["meta"]["source_hash"]

    def test_hash_stable_without_changes(self, vault):
        result1 = cr.compile(vault)
        result2 = cr.compile(vault)
        assert result1["meta"]["source_hash"] == result2["meta"]["source_hash"]


# ---------------------------------------------------------------------------
# System rules from index.md
# ---------------------------------------------------------------------------

class TestSystemRulesFromIndex:
    def test_merges_index_and_router_rules(self, vault):
        """System rules from index.md come first, vault rules from router.md after."""
        # Create index.md with system always-rules
        index = vault / ".brain-core" / "index.md"
        index.write_text(
            "# Brain Core\n\n"
            "Always:\n"
            "- System rule alpha.\n"
            "- System rule beta.\n"
        )
        result = cr.compile(vault)
        rules = result["always_rules"]
        # System rules first
        assert rules[0] == "System rule alpha."
        assert rules[1] == "System rule beta."
        # Vault rules after (from router.md fixture)
        assert "typed folder" in rules[2].lower()
        assert len(rules) == 4  # 2 system + 2 vault

    def test_no_index_uses_router_only(self, vault):
        """Without index.md, only router.md rules are used (backward compat)."""
        # The vault fixture has no index.md by default
        assert not (vault / ".brain-core" / "index.md").exists()
        result = cr.compile(vault)
        assert len(result["always_rules"]) == 2
        assert "typed folder" in result["always_rules"][0].lower()

    def test_index_tracked_as_source(self, vault):
        """index.md should appear in meta.sources when present."""
        index = vault / ".brain-core" / "index.md"
        index.write_text(
            "# Brain Core\n\n"
            "Always:\n"
            "- A system rule.\n"
        )
        result = cr.compile(vault)
        index_key = os.path.join(".brain-core", "index.md")
        assert index_key in result["meta"]["sources"]

    def test_index_without_always_section(self, vault):
        """index.md without Always: section contributes no system rules."""
        index = vault / ".brain-core" / "index.md"
        index.write_text("# Brain Core\n\nJust some text, no rules.\n")
        result = cr.compile(vault)
        # Only router.md rules
        assert len(result["always_rules"]) == 2


# ---------------------------------------------------------------------------
# frontmatter_type field
# ---------------------------------------------------------------------------

class TestFrontmatterType:
    def test_configured_artefact_has_frontmatter_type(self, vault):
        """Configured artefacts get frontmatter_type from taxonomy."""
        result = cr.compile(vault)
        wiki = next(a for a in result["artefacts"] if a["folder"] == "Wiki")
        assert wiki["frontmatter_type"] == "living/wiki"

    def test_configured_temporal_has_frontmatter_type(self, vault):
        """Configured temporal artefacts get frontmatter_type from taxonomy."""
        result = cr.compile(vault)
        logs = next(a for a in result["artefacts"] if a["folder"] == "Logs")
        assert logs["frontmatter_type"] == "temporal/log"

    def test_unconfigured_artefact_falls_back_to_type(self, vault):
        """Unconfigured artefacts (no taxonomy) fall back to folder-derived type."""
        (vault / "Projects").mkdir()
        result = cr.compile(vault)
        projects = next(a for a in result["artefacts"] if a["folder"] == "Projects")
        assert projects["frontmatter_type"] == "living/projects"

    def test_configured_without_type_field_falls_back(self, vault):
        """Configured artefact whose taxonomy has no type: field falls back."""
        (vault / "Recipes").mkdir()
        tax = vault / "_Config" / "Taxonomy" / "Living"
        (tax / "Recipes.md").write_text(
            "# Recipes\n\n## Naming\n\n`{title}.md` in `Recipes/`.\n\n"
            "## Frontmatter\n\n```yaml\n---\ntags:\n  - recipe\n---\n```\n"
        )
        result = cr.compile(vault)
        recipes = next(a for a in result["artefacts"] if a["folder"] == "Recipes")
        assert recipes["configured"] is True
        assert recipes["frontmatter_type"] == "living/recipes"
