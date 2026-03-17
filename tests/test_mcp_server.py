"""Tests for Brain MCP server — unit tests with a minimal vault fixture."""

import json
import os
import sys
import time
from unittest.mock import patch

import pytest

# Add mcp server and scripts dirs to path
MCP_DIR = os.path.join(os.path.dirname(__file__), "..", "src", "brain-core", "mcp")
SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "src", "brain-core", "scripts")
sys.path.insert(0, os.path.abspath(MCP_DIR))
sys.path.insert(0, os.path.abspath(SCRIPTS_DIR))

import server
import obsidian_cli


# ---------------------------------------------------------------------------
# Vault fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path):
    """Create a minimal vault fixture with types, taxonomy, and content."""
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
        "- Every artefact belongs in a typed folder.\n"
        "- Keep instruction files lean.\n\n"
        "Conditional:\n"
        "- After meaningful work → [[_Config/Taxonomy/Temporal/logs]]\n"
    )

    # Living type: Wiki
    wiki_dir = tmp_path / "Wiki"
    wiki_dir.mkdir()
    (wiki_dir / "brain-overview-abc123.md").write_text(
        "---\ntype: living/wiki\ntags: [brain-core, overview]\nstatus: active\n---\n\n"
        "# Brain Overview\n\n"
        "The Brain is a personal knowledge management system.\n"
    )
    (wiki_dir / "python-guide-def456.md").write_text(
        "---\ntype: living/wiki\ntags: [python, guide]\nstatus: draft\n---\n\n"
        "# Python Guide\n\n"
        "Python is a versatile programming language used for scripting.\n"
    )

    # Temporal type: Logs
    temporal = tmp_path / "_Temporal"
    temporal.mkdir()
    logs_dir = temporal / "Logs"
    logs_dir.mkdir()
    month_dir = logs_dir / "2026-03"
    month_dir.mkdir()
    (month_dir / "log-test-ghi789.md").write_text(
        "---\ntype: temporal/logs\ntags: [session]\n---\n\n"
        "# Test Log\n\n"
        "Tested the MCP server implementation.\n"
    )

    # Taxonomy
    tax_living = config / "Taxonomy" / "Living"
    tax_living.mkdir(parents=True)
    (tax_living / "wiki.md").write_text(
        "# Wiki\n\n"
        "## Naming\n\n`{slug}.md` in `Wiki/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/wiki\ntags:\n  - topic-tag\n---\n```\n\n"
        "## Template\n\n[[_Config/Templates/Living/Wiki]]\n"
    )

    tax_temporal = config / "Taxonomy" / "Temporal"
    tax_temporal.mkdir(parents=True)
    (tax_temporal / "logs.md").write_text(
        "# Logs\n\n"
        "## Naming\n\n`log-{slug}.md` in `_Temporal/Logs/yyyy-mm/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: temporal/logs\ntags:\n  - session\n---\n```\n\n"
        "## Trigger\n\nAfter meaningful work, write a log entry.\n"
    )

    # Skills
    skills_dir = config / "Skills" / "Vault Maintenance"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text("# Vault Maintenance\n\nKeep the vault tidy.\n")

    # Styles
    styles_dir = config / "Styles"
    styles_dir.mkdir(parents=True)
    (styles_dir / "concise.md").write_text("# Concise\n\nBe brief and direct.\n")

    # Templates
    templates_dir = config / "Templates" / "Living"
    templates_dir.mkdir(parents=True)
    (templates_dir / "Wiki.md").write_text(
        "---\ntype: living/wiki\ntags: []\n---\n\n# {{title}}\n\n"
    )

    # Plugins
    plugins_dir = tmp_path / "_Plugins" / "Undertask"
    plugins_dir.mkdir(parents=True)
    (plugins_dir / "SKILL.md").write_text("# Undertask\n\nTask management plugin.\n")

    return tmp_path


@pytest.fixture
def initialized(vault):
    """Return vault root after initializing the server against the vault fixture."""
    server.startup(vault_root=str(vault))
    return vault


# ---------------------------------------------------------------------------
# Startup tests
# ---------------------------------------------------------------------------

class TestStartup:
    def test_startup_compiles_router(self, vault):
        """Startup should compile the router when none exists."""
        router_path = vault / "_Config" / ".compiled-router.json"
        assert not router_path.exists()
        server.startup(vault_root=str(vault))
        assert router_path.exists()

    def test_startup_builds_index(self, vault):
        """Startup should build the index when none exists."""
        index_path = vault / "_Config" / ".retrieval-index.json"
        assert not index_path.exists()
        server.startup(vault_root=str(vault))
        assert index_path.exists()

    def test_startup_loads_router_into_memory(self, vault):
        server.startup(vault_root=str(vault))
        assert server._router is not None
        assert "artefacts" in server._router
        assert "meta" in server._router

    def test_startup_loads_index_into_memory(self, vault):
        server.startup(vault_root=str(vault))
        assert server._index is not None
        assert "documents" in server._index
        assert "corpus_stats" in server._index


# ---------------------------------------------------------------------------
# Staleness detection tests
# ---------------------------------------------------------------------------

class TestStaleness:
    def test_router_stale_when_missing(self, vault):
        stale, data = server._check_router(str(vault))
        assert stale is True
        assert data is None

    def test_router_not_stale_after_compile(self, vault):
        server._compile_and_save(str(vault))
        stale, data = server._check_router(str(vault))
        assert stale is False
        assert data is not None
        assert "artefacts" in data

    def test_router_stale_after_source_change(self, vault):
        server._compile_and_save(str(vault))
        # Touch a source file to make it newer
        time.sleep(0.1)
        router_md = vault / "_Config" / "router.md"
        router_md.write_text(router_md.read_text() + "\n- New rule.\n")
        stale, _ = server._check_router(str(vault))
        assert stale is True

    def test_index_stale_when_missing(self, vault):
        stale, data = server._check_index(str(vault))
        assert stale is True
        assert data is None

    def test_index_not_stale_after_build(self, vault):
        server._build_index_and_save(str(vault))
        stale, data = server._check_index(str(vault))
        assert stale is False
        assert data is not None
        assert "documents" in data

    def test_index_stale_after_md_change(self, vault):
        server._build_index_and_save(str(vault))
        time.sleep(0.1)
        # Add a new .md file
        (vault / "Wiki" / "new-file-zzz999.md").write_text(
            "---\ntype: living/wiki\n---\n\n# New File\n\nContent.\n"
        )
        stale, _ = server._check_index(str(vault))
        assert stale is True


# ---------------------------------------------------------------------------
# brain_read tests
# ---------------------------------------------------------------------------

class TestBrainRead:
    def test_read_artefact_list(self, initialized):
        result = json.loads(server.brain_read("artefact"))
        assert isinstance(result, list)
        keys = [a["key"] for a in result]
        assert "wiki" in keys

    def test_read_artefact_by_name(self, initialized):
        result = json.loads(server.brain_read("artefact", name="wiki"))
        assert len(result) == 1
        assert result[0]["key"] == "wiki"

    def test_read_artefact_by_type(self, initialized):
        result = json.loads(server.brain_read("artefact", name="living/wiki"))
        assert len(result) == 1
        assert result[0]["type"] == "living/wiki"

    def test_read_artefact_not_found(self, initialized):
        result = json.loads(server.brain_read("artefact", name="nonexistent"))
        assert "error" in result

    def test_read_trigger(self, initialized):
        result = json.loads(server.brain_read("trigger"))
        assert isinstance(result, list)
        assert len(result) >= 1
        assert result[0]["category"] == "after"

    def test_read_style_list(self, initialized):
        result = json.loads(server.brain_read("style"))
        assert isinstance(result, list)
        names = [s["name"] for s in result]
        assert "concise" in names

    def test_read_style_content(self, initialized):
        result = server.brain_read("style", name="concise")
        assert "Be brief and direct." in result

    def test_read_style_not_found(self, initialized):
        result = json.loads(server.brain_read("style", name="nonexistent"))
        assert "error" in result

    def test_read_template(self, initialized):
        result = server.brain_read("template", name="wiki")
        assert "{{title}}" in result

    def test_read_template_requires_name(self, initialized):
        result = json.loads(server.brain_read("template"))
        assert "error" in result

    def test_read_skill_list(self, initialized):
        result = json.loads(server.brain_read("skill"))
        assert isinstance(result, list)
        names = [s["name"] for s in result]
        assert "Vault Maintenance" in names

    def test_read_skill_content(self, initialized):
        result = server.brain_read("skill", name="Vault Maintenance")
        assert "Keep the vault tidy." in result

    def test_read_plugin_list(self, initialized):
        result = json.loads(server.brain_read("plugin"))
        assert isinstance(result, list)
        names = [p["name"] for p in result]
        assert "Undertask" in names

    def test_read_plugin_content(self, initialized):
        result = server.brain_read("plugin", name="Undertask")
        assert "Task management plugin." in result

    def test_read_environment(self, initialized):
        result = json.loads(server.brain_read("environment"))
        assert "vault_root" in result
        assert "platform" in result
        assert "obsidian_cli_available" in result

    def test_read_environment_includes_cli_status(self, initialized):
        """Environment response should reflect CLI availability."""
        assert json.loads(server.brain_read("environment"))["obsidian_cli_available"] is False

    def test_read_router(self, initialized):
        result = json.loads(server.brain_read("router"))
        assert "always_rules" in result
        assert "meta" in result
        assert len(result["always_rules"]) >= 1

    def test_read_unknown_resource(self, initialized):
        result = json.loads(server.brain_read("bogus"))
        assert "error" in result
        assert "Unknown resource" in result["error"]


# ---------------------------------------------------------------------------
# brain_search tests
# ---------------------------------------------------------------------------

class TestBrainSearch:
    def test_search_returns_results(self, initialized):
        resp = json.loads(server.brain_search("brain knowledge"))
        assert resp["source"] == "bm25"
        assert isinstance(resp["results"], list)
        assert len(resp["results"]) >= 1

    def test_search_result_shape(self, initialized):
        resp = json.loads(server.brain_search("brain"))
        results = resp["results"]
        assert len(results) >= 1
        r = results[0]
        assert "path" in r
        assert "title" in r
        assert "type" in r
        assert "status" in r
        assert "score" in r
        assert "snippet" in r

    def test_search_ranked_by_score(self, initialized):
        results = json.loads(server.brain_search("brain"))["results"]
        if len(results) >= 2:
            assert results[0]["score"] >= results[1]["score"]

    def test_search_type_filter(self, initialized):
        results = json.loads(server.brain_search("test", type="temporal/logs"))["results"]
        for r in results:
            assert r["type"] == "temporal/logs"

    def test_search_tag_filter(self, initialized):
        results = json.loads(server.brain_search("brain", tag="brain-core"))["results"]
        assert len(results) >= 1
        unfiltered = json.loads(server.brain_search("brain"))["results"]
        assert len(unfiltered) >= len(results)

    def test_search_top_k(self, initialized):
        results = json.loads(server.brain_search("the", top_k=1))["results"]
        assert len(results) <= 1

    def test_search_empty_query(self, initialized):
        resp = json.loads(server.brain_search(""))
        assert resp["results"] == []

    def test_search_status_filter(self, initialized):
        results = json.loads(server.brain_search("brain", status="active"))["results"]
        assert len(results) >= 1
        for r in results:
            assert r["status"] == "active"

    def test_search_no_matches(self, initialized):
        resp = json.loads(server.brain_search("xyzzyplugh"))
        assert resp["results"] == []

    def test_search_uses_bm25_when_cli_unavailable(self, initialized):
        """Verify BM25 is used when CLI is not available (default state)."""
        assert server._cli_available is False
        resp = json.loads(server.brain_search("brain"))
        assert resp["source"] == "bm25"

    def test_search_with_mocked_cli(self, initialized):
        """Verify CLI results are transformed to match schema."""
        cli_results = [
            {"filename": "Wiki/brain-overview-abc123.md", "score": 2.0,
             "matches": [{"content": "The Brain is a system"}]},
        ]
        with patch.object(obsidian_cli, "search", return_value=cli_results):
            server._cli_available = True
            server._vault_name = "test"
            try:
                resp = json.loads(server.brain_search("brain"))
                assert resp["source"] == "obsidian_cli"
                assert len(resp["results"]) >= 1
                r = resp["results"][0]
                assert r["path"] == "Wiki/brain-overview-abc123.md"
                assert "title" in r
                assert "type" in r
                assert "score" in r
            finally:
                server._cli_available = False

    def test_search_cli_failure_falls_back_to_bm25(self, initialized):
        """Verify fallback to BM25 when CLI search returns None."""
        with patch.object(obsidian_cli, "search", return_value=None):
            server._cli_available = True
            server._vault_name = "test"
            try:
                resp = json.loads(server.brain_search("brain"))
                assert resp["source"] == "bm25"
                assert len(resp["results"]) >= 1
            finally:
                server._cli_available = False


# ---------------------------------------------------------------------------
# brain_action tests
# ---------------------------------------------------------------------------

class TestBrainAction:
    def test_action_compile(self, initialized):
        result = json.loads(server.brain_action("compile"))
        assert result["status"] == "ok"
        assert "Compiled" in result["summary"]
        assert "compiled_at" in result

    def test_action_compile_updates_memory(self, initialized):
        old_compiled_at = server._router["meta"]["compiled_at"]
        time.sleep(0.1)
        server.brain_action("compile")
        assert server._router["meta"]["compiled_at"] != old_compiled_at

    def test_action_build_index(self, initialized):
        result = json.loads(server.brain_action("build_index"))
        assert result["status"] == "ok"
        assert "Built index" in result["summary"]
        assert "built_at" in result

    def test_action_build_index_updates_memory(self, initialized):
        old_built_at = server._index["meta"]["built_at"]
        time.sleep(0.1)
        server.brain_action("build_index")
        assert server._index["meta"]["built_at"] != old_built_at

    def test_action_unknown(self, initialized):
        result = json.loads(server.brain_action("bogus"))
        assert "error" in result
        assert "Unknown action" in result["error"]

    def test_action_rename_without_cli(self, initialized):
        """Rename via grep-and-replace when CLI is unavailable."""
        vault = initialized
        # Create a file that links to the source
        (vault / "Wiki" / "linker-xyz000.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "# Linker\n\nSee [[Wiki/brain-overview-abc123]].\n"
        )
        result = json.loads(server.brain_action("rename", {
            "source": "Wiki/brain-overview-abc123.md",
            "dest": "Wiki/brain-intro-abc123.md",
        }))
        assert result["status"] == "ok"
        assert result["method"] == "grep_replace"
        assert result["links_updated"] >= 1
        # Verify file was renamed
        assert not (vault / "Wiki" / "brain-overview-abc123.md").exists()
        assert (vault / "Wiki" / "brain-intro-abc123.md").exists()
        # Verify wikilink was updated
        content = (vault / "Wiki" / "linker-xyz000.md").read_text()
        assert "[[Wiki/brain-intro-abc123]]" in content

    def test_action_rename_with_mocked_cli(self, initialized):
        """Rename via CLI when available."""
        with patch.object(obsidian_cli, "move", return_value={"status": "ok", "links_updated": 5}):
            server._cli_available = True
            server._vault_name = "test"
            try:
                result = json.loads(server.brain_action("rename", {
                    "source": "Wiki/old.md",
                    "dest": "Wiki/new.md",
                }))
                assert result["status"] == "ok"
                assert result["method"] == "obsidian_cli"
                assert result["links_updated"] == 5
            finally:
                server._cli_available = False

    def test_action_rename_missing_params(self, initialized):
        result = json.loads(server.brain_action("rename"))
        assert "error" in result

    def test_action_rename_source_not_found(self, initialized):
        result = json.loads(server.brain_action("rename", {
            "source": "Wiki/nonexistent.md",
            "dest": "Wiki/other.md",
        }))
        assert "error" in result


# ---------------------------------------------------------------------------
# Integration: startup skips rebuild when fresh
# ---------------------------------------------------------------------------

class TestStartupCaching:
    def test_startup_reuses_fresh_router(self, vault):
        """Second startup should load from disk, not recompile."""
        server.startup(vault_root=str(vault))
        compiled_at_1 = server._router["meta"]["compiled_at"]

        # Second startup — files haven't changed
        server.startup(vault_root=str(vault))
        compiled_at_2 = server._router["meta"]["compiled_at"]
        assert compiled_at_1 == compiled_at_2

    def test_startup_reuses_fresh_index(self, vault):
        """Second startup should load from disk, not rebuild."""
        server.startup(vault_root=str(vault))
        built_at_1 = server._index["meta"]["built_at"]

        server.startup(vault_root=str(vault))
        built_at_2 = server._index["meta"]["built_at"]
        assert built_at_1 == built_at_2
