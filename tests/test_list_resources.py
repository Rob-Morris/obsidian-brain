"""Tests for list_resources — non-artefact resource listing."""

import json
import os
import sys

import pytest

SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "src", "brain-core", "scripts")
sys.path.insert(0, os.path.abspath(SCRIPTS_DIR))

import list_artefacts as la


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path):
    """Create a minimal vault and compile the router."""
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.7.0\n")

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

    # Temporal type
    temporal = tmp_path / "_Temporal"
    temporal.mkdir()
    (temporal / "Logs").mkdir()

    tax_living = config / "Taxonomy" / "Living"
    tax_living.mkdir(parents=True)
    (tax_living / "wiki.md").write_text(
        "# Wiki\n\n## Naming\n\n`{slug}.md` in `Wiki/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/wiki\ntags:\n  - topic-tag\n---\n```\n\n"
        "## Template\n\n[[_Config/Templates/Living/Wiki]]\n"
    )

    tax_temporal = config / "Taxonomy" / "Temporal"
    tax_temporal.mkdir(parents=True)
    (tax_temporal / "logs.md").write_text("# Logs\n\n## Naming\n\n`log-{slug}.md`.\n")

    # Skills
    skills_dir = config / "Skills" / "Test Skill"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text("# Test Skill\n\nDoes testing.\n")

    core_skills = bc / "skills" / "vault-maintenance"
    core_skills.mkdir(parents=True)
    (core_skills / "SKILL.md").write_text(
        "---\nname: vault-maintenance\n---\n\n# Vault Maintenance\n\nMaintains vault.\n"
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
    (memories / "test-memory.md").write_text(
        "---\ntriggers: [test topic, memory test]\n---\n\n"
        "# Test Memory\n\nRemember this.\n"
    )

    # Archive
    archive = tmp_path / "_Archive" / "Wiki"
    archive.mkdir(parents=True)
    (archive / "20260101-old-page.md").write_text(
        "---\ntype: living/wiki\nstatus: archived\narchiveddate: 2026-01-01\n---\n\nOld.\n"
    )

    # Compile the router
    from compile_router import compile as compile_router
    compiled = compile_router(str(tmp_path))

    # Build the index
    from build_index import build_index
    index = build_index(str(tmp_path))

    return tmp_path, compiled, index


# ---------------------------------------------------------------------------
# list_resources — non-artefact collections
# ---------------------------------------------------------------------------

class TestListResources:
    def test_artefact_delegates_to_list_artefacts(self, vault):
        tmp_path, router, index = vault
        results = la.list_resources(index, router, str(tmp_path), resource="artefact")
        assert isinstance(results, list)
        assert any(r["path"].endswith("test-page.md") for r in results)

    def test_skill_list(self, vault):
        tmp_path, router, index = vault
        results = la.list_resources(index, router, str(tmp_path), resource="skill")
        assert isinstance(results, list)
        names = [s["name"] for s in results]
        assert "Test Skill" in names

    def test_skill_query_filter(self, vault):
        tmp_path, router, index = vault
        results = la.list_resources(index, router, str(tmp_path), resource="skill", query="vault")
        names = [s["name"] for s in results]
        assert "vault-maintenance" in names
        assert "Test Skill" not in names

    def test_trigger_list(self, vault):
        tmp_path, router, index = vault
        results = la.list_resources(index, router, str(tmp_path), resource="trigger")
        assert isinstance(results, list)
        assert len(results) >= 1

    def test_style_list(self, vault):
        tmp_path, router, index = vault
        results = la.list_resources(index, router, str(tmp_path), resource="style")
        assert isinstance(results, list)
        names = [s["name"] for s in results]
        assert "concise" in names

    def test_plugin_list(self, vault):
        tmp_path, router, index = vault
        results = la.list_resources(index, router, str(tmp_path), resource="plugin")
        assert isinstance(results, list)
        names = [p["name"] for p in results]
        assert "TestPlugin" in names

    def test_memory_list(self, vault):
        tmp_path, router, index = vault
        results = la.list_resources(index, router, str(tmp_path), resource="memory")
        assert isinstance(results, list)
        names = [m["name"] for m in results]
        assert "test-memory" in names

    def test_memory_query_filter(self, vault):
        tmp_path, router, index = vault
        results = la.list_resources(index, router, str(tmp_path), resource="memory", query="test")
        assert len(results) >= 1
        results_none = la.list_resources(index, router, str(tmp_path), resource="memory", query="zzzzz")
        assert len(results_none) == 0

    def test_type_list(self, vault):
        tmp_path, router, index = vault
        results = la.list_resources(index, router, str(tmp_path), resource="type")
        assert isinstance(results, list)
        keys = [a["key"] for a in results]
        assert "wiki" in keys

    def test_template_list(self, vault):
        tmp_path, router, index = vault
        results = la.list_resources(index, router, str(tmp_path), resource="template")
        assert isinstance(results, list)
        names = [t["name"] for t in results]
        assert "wiki" in names

    def test_archive_list(self, vault):
        tmp_path, router, index = vault
        results = la.list_resources(index, router, str(tmp_path), resource="archive")
        assert isinstance(results, list)
        assert any("old-page" in r["title"] for r in results)

    def test_invalid_resource_raises(self, vault):
        tmp_path, router, index = vault
        with pytest.raises(ValueError, match="not listable"):
            la.list_resources(index, router, str(tmp_path), resource="environment")

    def test_query_filter_on_type(self, vault):
        tmp_path, router, index = vault
        results = la.list_resources(index, router, str(tmp_path), resource="type", query="wiki")
        assert len(results) >= 1
        results_none = la.list_resources(index, router, str(tmp_path), resource="type", query="zzzzz")
        assert len(results_none) == 0
