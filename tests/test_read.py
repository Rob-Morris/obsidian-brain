"""Tests for read.py — compiled router resource queries."""

import json
import os
import sys

import pytest

# Add scripts dir to path for imports
SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "src", "brain-core", "scripts")
sys.path.insert(0, os.path.abspath(SCRIPTS_DIR))

import read


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path):
    """Create a minimal vault and compile the router."""
    # .brain-core/VERSION
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.7.0\n")

    # _Config/router.md
    config = tmp_path / "_Config"
    config.mkdir()
    (config / "router.md").write_text(
        "Prefer MCP tools.\n\n"
        "Always:\n"
        "- Every artefact belongs in a typed folder.\n\n"
        "Conditional:\n"
        "- After meaningful work → [[_Config/Taxonomy/Temporal/logs]]\n"
    )

    # Living type: Wiki
    wiki = tmp_path / "Wiki"
    wiki.mkdir()
    (wiki / "test-page.md").write_text(
        "---\ntype: living/wiki\ntags: [test]\n---\n\n# Test Page\n"
    )

    # Temporal type: Logs
    temporal = tmp_path / "_Temporal"
    temporal.mkdir()
    logs = temporal / "Logs" / "2026-03"
    logs.mkdir(parents=True)

    # Taxonomy
    tax_living = config / "Taxonomy" / "Living"
    tax_living.mkdir(parents=True)
    (tax_living / "wiki.md").write_text(
        "# Wiki\n\n## Naming\n\n`{slug}.md` in `Wiki/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/wiki\ntags:\n  - topic-tag\n---\n```\n\n"
        "## Template\n\n[[_Config/Templates/Living/Wiki]]\n"
    )

    tax_temporal = config / "Taxonomy" / "Temporal"
    tax_temporal.mkdir(parents=True)
    (tax_temporal / "logs.md").write_text(
        "# Logs\n\n## Naming\n\n`log-{slug}.md`.\n\n"
        "## Trigger\n\nAfter meaningful work, write a log entry.\n"
    )

    # Skills
    skills_dir = config / "Skills" / "Test Skill"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text("# Test Skill\n\nDoes testing.\n")

    # Core skills
    core_skills = bc / "skills" / "brain-remote"
    core_skills.mkdir(parents=True)
    (core_skills / "SKILL.md").write_text(
        "---\nname: brain-remote\n---\n\n# Brain Remote\n\nRemote workflow.\n"
    )

    # Styles
    styles = config / "Styles"
    styles.mkdir()
    (styles / "concise.md").write_text("# Concise\n\nBe brief.\n")

    # Templates
    templates = config / "Templates" / "Living"
    templates.mkdir(parents=True)
    (templates / "Wiki.md").write_text(
        "---\ntype: living/wiki\ntags: []\n---\n\n# {{title}}\n"
    )

    # Plugins
    plugins = tmp_path / "_Plugins" / "TestPlugin"
    plugins.mkdir(parents=True)
    (plugins / "SKILL.md").write_text("# TestPlugin\n\nA test plugin.\n")

    # Memories
    memories = config / "Memories"
    memories.mkdir()
    (memories / "README.md").write_text("# Memories\n")
    (memories / "test-memory.md").write_text(
        "---\ntriggers: [test topic, memory test]\n---\n\n"
        "# Test Memory\n\nRemember this.\n"
    )

    # Compile the router
    from compile_router import compile as compile_router
    compiled = compile_router(str(tmp_path))
    router_path = config / ".compiled-router.json"
    with open(router_path, "w") as f:
        json.dump(compiled, f, indent=2)

    return tmp_path, compiled


# ---------------------------------------------------------------------------
# read_resource dispatch tests
# ---------------------------------------------------------------------------

class TestReadResource:
    def test_unknown_resource(self, vault):
        _, router = vault
        result = read.read_resource(router, "", "bogus")
        assert "error" in result
        assert "Unknown resource" in result["error"]

    def test_artefact_list(self, vault):
        _, router = vault
        result = read.read_resource(router, "", "artefact")
        assert isinstance(result, list)
        keys = [a["key"] for a in result]
        assert "wiki" in keys

    def test_artefact_by_key(self, vault):
        _, router = vault
        result = read.read_resource(router, "", "artefact", name="wiki")
        assert isinstance(result, list)
        assert result[0]["key"] == "wiki"

    def test_artefact_by_type(self, vault):
        _, router = vault
        result = read.read_resource(router, "", "artefact", name="living/wiki")
        assert result[0]["type"] == "living/wiki"

    def test_artefact_not_found(self, vault):
        _, router = vault
        result = read.read_resource(router, "", "artefact", name="nonexistent")
        assert "error" in result

    def test_trigger_list(self, vault):
        _, router = vault
        result = read.read_resource(router, "", "trigger")
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_style_list(self, vault):
        _, router = vault
        result = read.read_resource(router, "", "style")
        assert isinstance(result, list)
        names = [s["name"] for s in result]
        assert "concise" in names

    def test_style_content(self, vault):
        tmp_path, router = vault
        result = read.read_resource(router, str(tmp_path), "style", name="concise")
        assert isinstance(result, str)
        assert "Be brief." in result

    def test_style_not_found(self, vault):
        _, router = vault
        result = read.read_resource(router, "", "style", name="nonexistent")
        assert "error" in result

    def test_template_requires_name(self, vault):
        _, router = vault
        result = read.read_resource(router, "", "template")
        assert "error" in result

    def test_template_content(self, vault):
        tmp_path, router = vault
        result = read.read_resource(router, str(tmp_path), "template", name="wiki")
        assert isinstance(result, str)
        assert "{{title}}" in result

    def test_skill_list(self, vault):
        _, router = vault
        result = read.read_resource(router, "", "skill")
        assert isinstance(result, list)
        names = [s["name"] for s in result]
        assert "brain-remote" in names
        assert "Test Skill" in names

    def test_skill_content(self, vault):
        tmp_path, router = vault
        result = read.read_resource(router, str(tmp_path), "skill", name="Test Skill")
        assert "Does testing." in result

    def test_plugin_list(self, vault):
        _, router = vault
        result = read.read_resource(router, "", "plugin")
        assert isinstance(result, list)
        names = [p["name"] for p in result]
        assert "TestPlugin" in names

    def test_plugin_content(self, vault):
        tmp_path, router = vault
        result = read.read_resource(router, str(tmp_path), "plugin", name="TestPlugin")
        assert "A test plugin." in result

    def test_environment(self, vault):
        _, router = vault
        result = read.read_resource(router, "", "environment")
        assert isinstance(result, dict)
        assert "vault_root" in result
        assert "platform" in result

    def test_router_meta(self, vault):
        _, router = vault
        result = read.read_resource(router, "", "router")
        assert "always_rules" in result
        assert "meta" in result

    def test_memory_list(self, vault):
        _, router = vault
        result = read.read_resource(router, "", "memory")
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["name"] == "test-memory"

    def test_memory_by_trigger(self, vault):
        tmp_path, router = vault
        result = read.read_resource(router, str(tmp_path), "memory", name="test topic")
        assert isinstance(result, str)
        assert "Remember this." in result

    def test_memory_trigger_case_insensitive(self, vault):
        tmp_path, router = vault
        result = read.read_resource(router, str(tmp_path), "memory", name="TEST TOPIC")
        assert "Remember this." in result

    def test_memory_by_name_fallback(self, vault):
        tmp_path, router = vault
        result = read.read_resource(router, str(tmp_path), "memory", name="test-memory")
        assert "Remember this." in result

    def test_memory_not_found(self, vault):
        _, router = vault
        result = read.read_resource(router, "", "memory", name="nonexistent")
        assert "error" in result


# ---------------------------------------------------------------------------
# File reading helper tests
# ---------------------------------------------------------------------------

class TestReadFileContent:
    def test_reads_existing_file(self, vault):
        tmp_path, _ = vault
        result = read.read_file_content(str(tmp_path), "Wiki/test-page.md")
        assert "# Test Page" in result

    def test_resolves_wikilink_path(self, vault):
        tmp_path, _ = vault
        result = read.read_file_content(str(tmp_path), "Wiki/test-page")
        assert "# Test Page" in result

    def test_returns_error_for_missing_file(self, vault):
        tmp_path, _ = vault
        result = read.read_file_content(str(tmp_path), "Wiki/nonexistent.md")
        assert result.startswith("Error:")


# ---------------------------------------------------------------------------
# File resource tests (read artefact files by path)
# ---------------------------------------------------------------------------

class TestReadFile:
    def test_reads_file_by_path(self, vault):
        tmp_path, router = vault
        result = read.read_resource(router, str(tmp_path), "file", name="Wiki/test-page.md")
        assert isinstance(result, str)
        assert "# Test Page" in result

    def test_reads_file_wikilink_style(self, vault):
        tmp_path, router = vault
        result = read.read_resource(router, str(tmp_path), "file", name="Wiki/test-page")
        assert "# Test Page" in result

    def test_requires_name(self, vault):
        _, router = vault
        result = read.read_resource(router, "", "file")
        assert "error" in result
        assert "requires a name" in result["error"]

    def test_file_not_found(self, vault):
        tmp_path, router = vault
        result = read.read_resource(router, str(tmp_path), "file", name="Wiki/nonexistent.md")
        assert isinstance(result, str)
        assert result.startswith("Error:")

    def test_rejects_path_outside_type_folders(self, vault):
        tmp_path, router = vault
        # _Config is not an artefact type folder
        result = read.read_resource(router, str(tmp_path), "file", name="_Config/router.md")
        assert "error" in result
        assert "does not belong" in result["error"]

    def test_rejects_unconfigured_type(self, vault):
        tmp_path, router = vault
        # Create a folder that the router sees but has no taxonomy
        unknown = tmp_path / "Unconfigured"
        unknown.mkdir()
        (unknown / "test.md").write_text("# Test\n")
        # Recompile router to pick up the new folder
        from compile_router import compile as compile_router
        router = compile_router(str(tmp_path))
        result = read.read_resource(router, str(tmp_path), "file", name="Unconfigured/test.md")
        assert "error" in result
        assert "unconfigured" in result["error"].lower()


# ---------------------------------------------------------------------------
# CLI compiled router loading
# ---------------------------------------------------------------------------

class TestLoadCompiledRouter:
    def test_loads_from_disk(self, vault):
        tmp_path, expected = vault
        loaded = read.load_compiled_router(str(tmp_path))
        assert loaded["meta"]["compiled_at"] == expected["meta"]["compiled_at"]
        assert len(loaded["artefacts"]) == len(expected["artefacts"])

    def test_exits_when_missing(self, tmp_path):
        with pytest.raises(SystemExit):
            read.load_compiled_router(str(tmp_path))
