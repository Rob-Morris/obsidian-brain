"""Tests for Brain MCP server — unit tests with a minimal vault fixture."""

import json
import os
import time
from unittest.mock import patch

import pytest

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

    # Living type: Ideas
    ideas_dir = tmp_path / "Ideas"
    ideas_dir.mkdir()

    # Taxonomy: Ideas
    (tax_living / "ideas.md").write_text(
        "# Ideas\n\n"
        "## Naming\n\n`{slug}.md` in `Ideas/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/ideas\ntags:\n  - idea-tag\nstatus: shaping\n---\n```\n\n"
        "## Template\n\n[[_Config/Templates/Living/Ideas]]\n"
    )

    # Templates
    templates_dir = config / "Templates" / "Living"
    templates_dir.mkdir(parents=True)
    (templates_dir / "Wiki.md").write_text(
        "---\ntype: living/wiki\ntags: []\n---\n\n# {{title}}\n\n"
    )
    (templates_dir / "Ideas.md").write_text(
        "---\ntype: living/ideas\ntags: []\nstatus: shaping\n---\n\n# {{title}}\n\nWhat if...\n"
    )

    # Core skills
    core_skills_dir = bc / "skills" / "brain-remote"
    core_skills_dir.mkdir(parents=True)
    (core_skills_dir / "SKILL.md").write_text(
        "---\nname: brain-remote\n---\n\n"
        "# Brain Remote\n\nUse brain MCP tools from external projects.\n"
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
        assert "brain-remote" in names
        assert "Vault Maintenance" in names

    def test_read_core_skill_content(self, initialized):
        result = server.brain_read("skill", name="brain-remote")
        assert "Brain Remote" in result

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


class TestBrainReadMemory:
    @pytest.fixture(autouse=True)
    def setup_memories(self, initialized):
        """Add a memories directory with a test memory to the vault fixture."""
        self.vault = initialized
        memories_dir = initialized / "_Config" / "Memories"
        memories_dir.mkdir(parents=True, exist_ok=True)
        (memories_dir / "README.md").write_text("# Memories\n\nDispatch doc.\n")
        (memories_dir / "brain-core-reference.md").write_text(
            "---\ntriggers: [brain core, obsidian-brain, vault system]\n---\n\n"
            "# Brain Core Reference\n\nBrain-core is the system.\n"
        )
        (memories_dir / "python-setup.md").write_text(
            "---\ntriggers: [python, dev environment]\n---\n\n"
            "# Python Setup\n\nUse Python 3.12.\n"
        )
        # Recompile to pick up memories
        server.brain_action("compile")

    def test_list_memories(self):
        result = json.loads(server.brain_read("memory"))
        assert isinstance(result, list)
        assert len(result) == 2
        names = [m["name"] for m in result]
        assert "brain-core-reference" in names
        assert "python-setup" in names

    def test_read_by_trigger(self):
        result = server.brain_read("memory", name="brain core")
        assert "Brain-core is the system." in result

    def test_trigger_case_insensitive(self):
        result = server.brain_read("memory", name="BRAIN CORE")
        assert "Brain-core is the system." in result

    def test_trigger_substring(self):
        result = server.brain_read("memory", name="brain")
        # "brain" is a substring of "brain core" and "obsidian-brain"
        # Should match brain-core-reference — single match returns content
        assert "Brain-core is the system." in result

    def test_fallback_to_name(self):
        result = server.brain_read("memory", name="python-setup")
        assert "Use Python 3.12." in result

    def test_not_found(self):
        result = json.loads(server.brain_read("memory", name="nonexistent-thing"))
        assert "error" in result

    def test_compile_summary_includes_memories(self):
        result = json.loads(server.brain_action("compile"))
        assert "memories" in result["summary"]


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


# ---------------------------------------------------------------------------
# Version drift detection
# ---------------------------------------------------------------------------

class TestVersionCheck:
    def test_startup_records_loaded_version(self, vault):
        server.startup(vault_root=str(vault))
        assert server._loaded_version == "0.7.0"

    def test_no_reload_when_version_matches(self, initialized):
        """_check_and_reload should be a no-op when version is unchanged."""
        old_version = server._loaded_version
        server._check_and_reload()
        assert server._loaded_version == old_version

    def test_reloads_when_version_changes(self, initialized):
        """_check_and_reload should update _loaded_version when on-disk version differs."""
        version_path = initialized / ".brain-core" / "VERSION"
        version_path.write_text("99.0.0\n")
        server._check_and_reload()
        assert server._loaded_version == "99.0.0"

    def test_no_reload_when_version_file_missing(self, initialized):
        """_check_and_reload should be a no-op if VERSION file is deleted."""
        version_path = initialized / ".brain-core" / "VERSION"
        version_path.unlink()
        old_version = server._loaded_version
        server._check_and_reload()
        assert server._loaded_version == old_version

    def test_brain_read_survives_version_drift(self, initialized):
        """brain_read should succeed after version drift (reload, not exit)."""
        version_path = initialized / ".brain-core" / "VERSION"
        version_path.write_text("99.0.0\n")
        result = json.loads(server.brain_read("artefact"))
        assert isinstance(result, list)
        assert server._loaded_version == "99.0.0"

    def test_brain_search_survives_version_drift(self, initialized):
        """brain_search should succeed after version drift."""
        version_path = initialized / ".brain-core" / "VERSION"
        version_path.write_text("99.0.0\n")
        resp = json.loads(server.brain_search("brain"))
        assert "results" in resp
        assert server._loaded_version == "99.0.0"

    def test_brain_action_survives_version_drift(self, initialized):
        """brain_action should succeed after version drift."""
        version_path = initialized / ".brain-core" / "VERSION"
        version_path.write_text("99.0.0\n")
        result = json.loads(server.brain_action("compile"))
        assert result["status"] == "ok"
        assert server._loaded_version == "99.0.0"

    def test_brain_create_survives_version_drift(self, initialized):
        """brain_create should succeed after version drift."""
        version_path = initialized / ".brain-core" / "VERSION"
        version_path.write_text("99.0.0\n")
        result = json.loads(server.brain_create(type="wiki", title="drift test"))
        assert "path" in result
        assert server._loaded_version == "99.0.0"

    def test_brain_edit_survives_version_drift(self, initialized):
        """brain_edit should succeed after version drift."""
        version_path = initialized / ".brain-core" / "VERSION"
        version_path.write_text("99.0.0\n")
        result = json.loads(server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            body="# Drifted\n"
        ))
        assert result["operation"] == "edit"
        assert server._loaded_version == "99.0.0"


# ---------------------------------------------------------------------------
# Auto-recompile on taxonomy changes
# ---------------------------------------------------------------------------

class TestAutoRecompile:
    def test_new_type_triggers_recompile(self, initialized):
        """Installing a new taxonomy file should trigger recompile via _ensure_router_fresh."""
        old_count = len(server._router["artefacts"])
        # Add a new living type taxonomy
        tax_living = initialized / "_Config" / "Taxonomy" / "Living"
        (tax_living / "glossary.md").write_text(
            "# Glossary\n\n"
            "## Naming\n\n`{slug}.md` in `Glossary/`.\n\n"
            "## Frontmatter\n\n```yaml\n---\ntype: living/glossary\ntags:\n  - term\n---\n```\n"
        )
        (initialized / "Glossary").mkdir()
        server._ensure_router_fresh()
        new_count = len(server._router["artefacts"])
        assert new_count == old_count + 1
        keys = [a["key"] for a in server._router["artefacts"]]
        assert "glossary" in keys

    def test_no_recompile_when_types_unchanged(self, initialized):
        """_ensure_router_fresh should not recompile when nothing changed."""
        compiled_at = server._router["meta"]["compiled_at"]
        server._ensure_router_fresh()
        assert server._router["meta"]["compiled_at"] == compiled_at

    def test_modified_taxonomy_triggers_recompile(self, initialized):
        """Modifying an existing taxonomy file's mtime should trigger recompile."""
        compiled_at = server._router["meta"]["compiled_at"]
        time.sleep(0.1)
        # Touch a taxonomy source file to make it newer than compiled_at
        tax_file = initialized / "_Config" / "Taxonomy" / "Living" / "wiki.md"
        tax_file.write_text(tax_file.read_text() + "\n")
        server._ensure_router_fresh()
        assert server._router["meta"]["compiled_at"] != compiled_at


# ---------------------------------------------------------------------------
# brain_create tests
# ---------------------------------------------------------------------------

class TestBrainCreate:
    def test_create_returns_path(self, initialized):
        result = json.loads(server.brain_create(type="wiki", title="New Page"))
        assert "path" in result
        assert result["type"] == "living/wiki"
        assert result["title"] == "New Page"

    def test_create_file_on_disk(self, initialized):
        result = json.loads(server.brain_create(type="wiki", title="Disk Test"))
        abs_path = os.path.join(str(initialized), result["path"])
        assert os.path.isfile(abs_path)

    def test_create_correct_frontmatter(self, initialized):
        result = json.loads(server.brain_create(type="wiki", title="FM Test"))
        abs_path = os.path.join(str(initialized), result["path"])
        with open(abs_path) as f:
            content = f.read()
        from _common import parse_frontmatter
        fields, _ = parse_frontmatter(content)
        assert fields["type"] == "living/wiki"

    def test_create_unknown_type_error(self, initialized):
        result = json.loads(server.brain_create(type="nonexistent", title="Test"))
        assert "error" in result

    def test_create_temporal_subfolder(self, initialized):
        result = json.loads(server.brain_create(type="logs", title="My Session"))
        assert "_Temporal/Logs/" in result["path"]
        import re
        # Path should contain yyyy-mm subfolder
        assert re.search(r"\d{4}-\d{2}", result["path"])

    def test_create_body_override(self, initialized):
        result = json.loads(server.brain_create(
            type="wiki", title="Custom Body", body="# Custom\n\nMy content.\n"
        ))
        abs_path = os.path.join(str(initialized), result["path"])
        with open(abs_path) as f:
            content = f.read()
        assert "My content." in content

    def test_create_frontmatter_override(self, initialized):
        result = json.loads(server.brain_create(
            type="ideas", title="Override Test",
            frontmatter={"status": "developing"}
        ))
        abs_path = os.path.join(str(initialized), result["path"])
        with open(abs_path) as f:
            content = f.read()
        from _common import parse_frontmatter
        fields, _ = parse_frontmatter(content)
        assert fields["status"] == "developing"


# ---------------------------------------------------------------------------
# brain_edit tests
# ---------------------------------------------------------------------------

class TestBrainEdit:
    def test_edit_replaces_body(self, initialized):
        result = json.loads(server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            body="# New Body\n\nReplaced.\n"
        ))
        assert result["operation"] == "edit"
        content = (initialized / "Wiki" / "brain-overview-abc123.md").read_text()
        assert "Replaced." in content

    def test_edit_preserves_frontmatter(self, initialized):
        server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            body="# New\n"
        )
        content = (initialized / "Wiki" / "brain-overview-abc123.md").read_text()
        from _common import parse_frontmatter
        fields, _ = parse_frontmatter(content)
        assert fields["type"] == "living/wiki"

    def test_edit_merges_frontmatter(self, initialized):
        server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            body="# New\n",
            frontmatter={"status": "archived"}
        )
        content = (initialized / "Wiki" / "brain-overview-abc123.md").read_text()
        from _common import parse_frontmatter
        fields, _ = parse_frontmatter(content)
        assert fields["status"] == "archived"

    def test_append_works(self, initialized):
        result = json.loads(server.brain_edit(
            operation="append",
            path="Wiki/brain-overview-abc123.md",
            body="\n\nAppended text.\n"
        ))
        assert result["operation"] == "append"
        content = (initialized / "Wiki" / "brain-overview-abc123.md").read_text()
        assert "Appended text." in content
        assert "Brain Overview" in content  # original preserved

    def test_invalid_path_rejected(self, initialized):
        result = json.loads(server.brain_edit(
            operation="edit",
            path="Unknown/file.md",
            body="test"
        ))
        assert "error" in result

    def test_file_not_found(self, initialized):
        result = json.loads(server.brain_edit(
            operation="edit",
            path="Wiki/nonexistent.md",
            body="test"
        ))
        assert "error" in result

    def test_unknown_operation(self, initialized):
        result = json.loads(server.brain_edit(
            operation="bogus",
            path="Wiki/brain-overview-abc123.md",
            body="test"
        ))
        assert "error" in result


# ---------------------------------------------------------------------------
# brain_action delete/convert tests
# ---------------------------------------------------------------------------

class TestBrainActionDelete:
    def test_delete_removes_file(self, initialized):
        result = json.loads(server.brain_action("delete", {"path": "Wiki/python-guide-def456.md"}))
        assert result["status"] == "ok"
        assert not (initialized / "Wiki" / "python-guide-def456.md").exists()

    def test_delete_cleans_links(self, initialized):
        # Add a link to the target file
        (initialized / "Wiki" / "linker-aaa000.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\nSee [[Wiki/python-guide-def456|Python]].\n"
        )
        result = json.loads(server.brain_action("delete", {"path": "Wiki/python-guide-def456.md"}))
        assert result["links_replaced"] >= 1
        content = (initialized / "Wiki" / "linker-aaa000.md").read_text()
        assert "~~Python~~" in content

    def test_delete_missing_params(self, initialized):
        result = json.loads(server.brain_action("delete"))
        assert "error" in result

    def test_delete_not_found(self, initialized):
        result = json.loads(server.brain_action("delete", {"path": "Wiki/gone.md"}))
        assert "error" in result


class TestBrainActionConvert:
    def test_convert_changes_type_and_path(self, initialized):
        result = json.loads(server.brain_action("convert", {
            "path": "Wiki/brain-overview-abc123.md",
            "target_type": "ideas",
        }))
        assert result["status"] == "ok"
        assert result["type"] == "living/ideas"
        assert result["new_path"].startswith("Ideas/")
        assert not (initialized / "Wiki" / "brain-overview-abc123.md").exists()
        assert os.path.isfile(os.path.join(str(initialized), result["new_path"]))

    def test_convert_updates_links(self, initialized):
        (initialized / "Wiki" / "linker-bbb000.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\nSee [[Wiki/brain-overview-abc123]].\n"
        )
        result = json.loads(server.brain_action("convert", {
            "path": "Wiki/brain-overview-abc123.md",
            "target_type": "ideas",
        }))
        assert result["links_updated"] >= 1
        content = (initialized / "Wiki" / "linker-bbb000.md").read_text()
        new_stem = result["new_path"][:-3]
        assert f"[[{new_stem}]]" in content

    def test_convert_missing_params(self, initialized):
        result = json.loads(server.brain_action("convert"))
        assert "error" in result

    def test_convert_unknown_target(self, initialized):
        result = json.loads(server.brain_action("convert", {
            "path": "Wiki/brain-overview-abc123.md",
            "target_type": "nonexistent",
        }))
        assert "error" in result


# ---------------------------------------------------------------------------
# brain_action shape-presentation tests
# ---------------------------------------------------------------------------

class TestBrainActionShapePresentation:
    @pytest.fixture(autouse=True)
    def setup_presentation_files(self, initialized):
        """Add presentation template and theme to the vault fixture."""
        self.vault = initialized
        # Template
        templates_dir = initialized / "_Config" / "Templates" / "Temporal"
        templates_dir.mkdir(parents=True, exist_ok=True)
        (templates_dir / "Presentations.md").write_text(
            "---\ntype: temporal/presentation\ntags:\n  - presentation\n"
            "marp: true\ntheme: brain\npaginate: true\n---\n\n"
            "<!-- _class: title -->\n\n# PRESENTATION TITLE\n\n"
            "**{{date:YYYY-MM-DD}}**\n\n"
            "**Origin:** [[source-artefact|Source document]]\n\n---\n\n## Slide 1\n"
        )
        # Theme
        skills_dir = initialized / "_Config" / "Skills" / "presentations"
        skills_dir.mkdir(parents=True, exist_ok=True)
        (skills_dir / "theme.css").write_text("/* @theme brain */\n")

    def test_missing_params_returns_error(self, initialized):
        result = json.loads(server.brain_action("shape-presentation"))
        assert "error" in result

    def test_missing_source_returns_error(self, initialized):
        result = json.loads(server.brain_action("shape-presentation", {"slug": "test"}))
        assert "error" in result

    def test_missing_slug_returns_error(self, initialized):
        result = json.loads(server.brain_action("shape-presentation", {"source": "Wiki/brain-overview-abc123.md"}))
        assert "error" in result

    def test_source_not_found_returns_error(self, initialized):
        result = json.loads(server.brain_action("shape-presentation", {
            "source": "Wiki/nonexistent.md",
            "slug": "test",
        }))
        assert "error" in result

    @patch("shape_presentation.subprocess.Popen")
    def test_creates_file_and_returns_status(self, mock_popen, initialized):
        mock_popen.return_value.pid = 12345
        result = json.loads(server.brain_action("shape-presentation", {
            "source": "Wiki/brain-overview-abc123.md",
            "slug": "test-deck",
        }))
        assert result["status"] == "ok"
        assert "presentation" in result["path"]
        assert "test-deck" in result["path"]
        assert result["created"] is True
        # Verify file was created on disk
        abs_path = os.path.join(str(initialized), result["path"])
        assert os.path.isfile(abs_path)

    @patch("shape_presentation.subprocess.Popen")
    def test_does_not_recreate_existing_file(self, mock_popen, initialized):
        mock_popen.return_value.pid = 12345
        # First call creates
        result1 = json.loads(server.brain_action("shape-presentation", {
            "source": "Wiki/brain-overview-abc123.md",
            "slug": "existing-deck",
        }))
        assert result1["created"] is True
        # Second call reuses
        result2 = json.loads(server.brain_action("shape-presentation", {
            "source": "Wiki/brain-overview-abc123.md",
            "slug": "existing-deck",
        }))
        assert result2["status"] == "ok"
        assert result2["created"] is False

    @patch("shape_presentation.subprocess.Popen", side_effect=FileNotFoundError)
    def test_works_without_marp_installed(self, mock_popen, initialized):
        """Should succeed even if marp CLI is not installed (no preview)."""
        result = json.loads(server.brain_action("shape-presentation", {
            "source": "Wiki/brain-overview-abc123.md",
            "slug": "no-marp",
        }))
        assert result["status"] == "ok"
        assert "preview_pid" not in result


# ---------------------------------------------------------------------------
# brain_session tests
# ---------------------------------------------------------------------------

class TestBrainSession:

    def test_returns_valid_json(self, initialized):
        result = json.loads(server.brain_session())
        assert "error" not in result

    def test_payload_keys(self, initialized):
        result = json.loads(server.brain_session())
        expected_keys = {
            "version", "brain_core_version", "compiled_at",
            "always_rules", "preferences", "gotchas",
            "triggers", "artefacts", "environment",
            "memories", "skills", "plugins", "styles",
        }
        assert set(result.keys()) == expected_keys

    def test_always_rules(self, initialized):
        result = json.loads(server.brain_session())
        assert isinstance(result["always_rules"], list)
        assert len(result["always_rules"]) > 0
        assert all(isinstance(r, str) for r in result["always_rules"])

    def test_artefact_condensed(self, initialized):
        result = json.loads(server.brain_session())
        allowed_keys = {"type", "key", "path", "naming_pattern", "status_enum", "configured"}
        for a in result["artefacts"]:
            assert set(a.keys()) == allowed_keys
            # No full taxonomy/template fields leaking
            assert "taxonomy_file" not in a
            assert "template_file" not in a
            assert "frontmatter" not in a
            assert "trigger" not in a

    def test_artefact_configured_wiki(self, initialized):
        result = json.loads(server.brain_session())
        wiki = [a for a in result["artefacts"] if a["key"] == "wiki"]
        assert len(wiki) == 1
        assert wiki[0]["configured"] is True
        assert wiki[0]["naming_pattern"] is not None

    def test_triggers_present(self, initialized):
        result = json.loads(server.brain_session())
        assert isinstance(result["triggers"], list)
        assert len(result["triggers"]) > 0
        for t in result["triggers"]:
            assert "category" in t
            assert "condition" in t

    def test_environment(self, initialized):
        result = json.loads(server.brain_session())
        env = result["environment"]
        assert "vault_root" in env
        assert "platform" in env
        assert "cli_available" in env
        assert "obsidian_cli_available" in env

    def test_memories_condensed(self, initialized):
        # Add memories and recompile
        memories_dir = initialized / "_Config" / "Memories"
        memories_dir.mkdir(parents=True, exist_ok=True)
        (memories_dir / "test-mem.md").write_text(
            "---\ntriggers: [test, memory]\n---\n\n# Test Memory\n"
        )
        server.brain_action("compile")

        result = json.loads(server.brain_session())
        assert isinstance(result["memories"], list)
        assert len(result["memories"]) > 0
        for m in result["memories"]:
            assert "name" in m
            assert "triggers" in m
            assert "memory_doc" not in m

    def test_skills_condensed(self, initialized):
        result = json.loads(server.brain_session())
        assert isinstance(result["skills"], list)
        assert len(result["skills"]) > 0
        for s in result["skills"]:
            assert "name" in s
            assert "source" in s
            assert "skill_doc" not in s

    def test_plugins_condensed(self, initialized):
        result = json.loads(server.brain_session())
        assert isinstance(result["plugins"], list)
        assert len(result["plugins"]) > 0
        for p in result["plugins"]:
            assert "name" in p
            assert "skill_doc" not in p

    def test_styles_are_names(self, initialized):
        result = json.loads(server.brain_session())
        assert isinstance(result["styles"], list)
        assert len(result["styles"]) > 0
        assert all(isinstance(s, str) for s in result["styles"])
        assert "concise" in result["styles"]

    def test_preferences_present(self, initialized):
        user_dir = initialized / "_Config" / "User"
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "preferences-always.md").write_text(
            "---\ntype: user-preferences\n---\n\nBe concise. No emojis.\n"
        )
        result = json.loads(server.brain_session())
        assert "Be concise. No emojis." in result["preferences"]
        # Frontmatter should be stripped
        assert "---" not in result["preferences"]

    def test_preferences_missing(self, initialized):
        result = json.loads(server.brain_session())
        assert result["preferences"] == ""

    def test_gotchas_present(self, initialized):
        user_dir = initialized / "_Config" / "User"
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "gotchas.md").write_text(
            "---\ntype: user-gotchas\n---\n\nNever force-push to main.\n"
        )
        result = json.loads(server.brain_session())
        assert "Never force-push to main." in result["gotchas"]
        assert "---" not in result["gotchas"]

    def test_gotchas_missing(self, initialized):
        result = json.loads(server.brain_session())
        assert result["gotchas"] == ""

    def test_context_stub(self, initialized):
        result = json.loads(server.brain_session(context="mcp-spike"))
        assert "context" in result
        assert result["context"]["slug"] == "mcp-spike"
        assert result["context"]["status"] == "not_implemented"
        # General payload should still be present
        assert "always_rules" in result
        assert "artefacts" in result

    def test_not_initialized(self):
        # Save and reset server state
        saved_router = server._router
        saved_root = server._vault_root
        server._router = None
        server._vault_root = None
        try:
            result = server.brain_session()
            assert "Error" in result
        finally:
            server._router = saved_router
            server._vault_root = saved_root
