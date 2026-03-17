"""Tests for compile_router.py — runs against the template vault fixture."""

import json
import os
import sys
import tempfile
import shutil

import pytest

# Add scripts dir to path so we can import the module
SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "src", "brain-core", "scripts")
sys.path.insert(0, SCRIPTS_DIR)

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
        "`log--yyyy-mm-dd.md` in `_Temporal/Logs/yyyy-mm/`.\n\n"
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

    # System dirs that should be excluded from living types
    (tmp_path / "_Config").exists()  # already exists
    (tmp_path / "_Attachments").mkdir()
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".git").mkdir()

    return tmp_path


@pytest.fixture
def template_vault():
    """Use the real template vault (read-only)."""
    path = os.path.abspath(TEMPLATE_VAULT)
    if not os.path.isdir(path):
        pytest.skip("template-vault not found")
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
        assert cr.is_system_dir("_Attachments")
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
        assert "_Attachments" not in folders
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
        assert len(result["skills"]) == 1
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

    def test_version_from_file(self, vault):
        """Version comes from VERSION file, not a hardcoded constant."""
        (vault / ".brain-core" / "VERSION").write_text("9.9.9\n")
        result = cr.compile(vault)
        assert result["meta"]["brain_core_version"] == "9.9.9"


# ---------------------------------------------------------------------------
# Template vault integration test
# ---------------------------------------------------------------------------

class TestTemplateVault:
    def test_compiles_template_vault(self, template_vault):
        result = cr.compile(template_vault)

        # Should have the real version
        assert result["meta"]["brain_core_version"]

        # Should find Wiki (living) and Logs/Plans/Transcripts (temporal)
        folders = [a["folder"] for a in result["artefacts"]]
        assert "Wiki" in folders

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
