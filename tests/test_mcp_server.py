"""Tests for Brain MCP server — unit tests with a minimal vault fixture."""

import importlib
import json
import os
import time
from unittest.mock import patch

import pytest

from mcp.types import CallToolResult

import server
import build_index
import obsidian_cli
import workspace_registry
import config as config_mod


def _assert_error(result, substring=None):
    """Assert result is a CallToolResult with isError flag."""
    assert isinstance(result, CallToolResult), f"Expected CallToolResult, got {type(result)}"
    assert result.isError is True
    if substring:
        assert substring in result.content[0].text


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
    # Reset staleness-check TTLs so tests can trigger checks immediately
    server._router_checked_at = 0.0
    server._index_checked_at = 0.0
    return vault


@pytest.fixture(autouse=True)
def _block_real_obsidian():
    """Prevent tests from connecting to a real Obsidian IPC socket.

    Tests that want CLI available mock the public API (check_available, search,
    move) directly — they never need the real socket.
    """
    with patch.object(obsidian_cli, "_socket_exists", return_value=False):
        yield


@pytest.fixture
def cli_available():
    """Temporarily enable CLI availability for tests that need the CLI path."""
    server._cli_available = True
    server._vault_name = "test"
    yield
    server._cli_available = False
    server._vault_name = None


# ---------------------------------------------------------------------------
# Startup tests
# ---------------------------------------------------------------------------

class TestStartup:
    def test_startup_compiles_router(self, vault):
        """Startup should compile the router when none exists."""
        router_path = vault / ".brain" / "local" / "compiled-router.json"
        assert not router_path.exists()
        server.startup(vault_root=str(vault))
        assert router_path.exists()

    def test_startup_builds_index(self, vault):
        """Startup should build the index when none exists."""
        index_path = vault / ".brain" / "local" / "retrieval-index.json"
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
    def test_read_type_list(self, initialized):
        result = server.brain_read("type")
        assert "wiki" in result
        assert "\n" in result  # multi-line

    def test_read_type_by_name(self, initialized):
        result = json.loads(server.brain_read("type", name="wiki"))
        assert len(result) == 1
        assert result[0]["key"] == "wiki"

    def test_read_type_by_type(self, initialized):
        result = json.loads(server.brain_read("type", name="living/wiki"))
        assert len(result) == 1
        assert result[0]["type"] == "living/wiki"

    def test_read_type_not_found(self, initialized):
        result = server.brain_read("type", name="nonexistent")
        _assert_error(result)

    def test_read_trigger(self, initialized):
        result = server.brain_read("trigger")
        assert "[after]" in result

    def test_read_style_list(self, initialized):
        result = server.brain_read("style")
        assert "concise" in result

    def test_read_style_content(self, initialized):
        result = server.brain_read("style", name="concise")
        assert "Be brief and direct." in result

    def test_read_style_not_found(self, initialized):
        result = server.brain_read("style", name="nonexistent")
        _assert_error(result)

    def test_read_template(self, initialized):
        result = server.brain_read("template", name="wiki")
        assert "{{title}}" in result

    def test_read_template_requires_name(self, initialized):
        result = server.brain_read("template")
        _assert_error(result)

    def test_read_skill_list(self, initialized):
        result = server.brain_read("skill")
        assert "brain-remote" in result
        assert "Vault Maintenance" in result

    def test_read_core_skill_content(self, initialized):
        result = server.brain_read("skill", name="brain-remote")
        assert "Brain Remote" in result

    def test_read_skill_content(self, initialized):
        result = server.brain_read("skill", name="Vault Maintenance")
        assert "Keep the vault tidy." in result

    def test_read_plugin_list(self, initialized):
        result = server.brain_read("plugin")
        assert "Undertask" in result

    def test_read_plugin_content(self, initialized):
        result = server.brain_read("plugin", name="Undertask")
        assert "Task management plugin." in result

    def test_read_environment(self, initialized):
        result = server.brain_read("environment")
        assert "vault_root=" in result
        assert "platform=" in result
        assert "obsidian_cli_available=" in result

    def test_read_environment_includes_cli_status(self, initialized):
        """Environment response should reflect CLI availability."""
        result = server.brain_read("environment")
        assert "obsidian_cli_available=False" in result

    def test_read_router(self, initialized):
        result = json.loads(server.brain_read("router"))
        assert "always_rules" in result
        assert "meta" in result
        assert len(result["always_rules"]) >= 1

    def test_read_unknown_resource(self, initialized):
        result = server.brain_read("bogus")
        _assert_error(result, "Unknown resource")


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
        result = server.brain_read("memory")
        assert "brain-core-reference" in result
        assert "python-setup" in result

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
        result = server.brain_read("memory", name="nonexistent-thing")
        _assert_error(result)

    def test_compile_summary_includes_memories(self):
        result = server.brain_action("compile")
        assert "memories" in result


# ---------------------------------------------------------------------------
# brain_search tests
# ---------------------------------------------------------------------------

def _search_text(response):
    """Join TextContent blocks into single string for search assertions."""
    if isinstance(response, str):
        return response
    return "\n".join(block.text for block in response)


def _search_result_lines(response):
    """Extract individual result lines from a search response (skipping meta)."""
    if isinstance(response, str):
        return []
    if len(response) < 2:
        return []
    return response[1].text.strip().split("\n")


class TestBrainSearch:
    def test_search_returns_results(self, initialized):
        resp = server.brain_search("brain knowledge")
        text = _search_text(resp)
        assert "bm25" in text
        assert "results" in text

    def test_search_result_shape(self, initialized):
        resp = server.brain_search("brain")
        lines = _search_result_lines(resp)
        assert len(lines) >= 1
        line = lines[0]
        # Each line has tab-separated: title, path, type[, status]
        assert "\t" in line

    def test_search_type_filter(self, initialized):
        resp = server.brain_search("test", type="temporal/logs")
        lines = _search_result_lines(resp)
        for line in lines:
            assert "temporal/logs" in line

    def test_search_tag_filter(self, initialized):
        resp = server.brain_search("brain", tag="brain-core")
        filtered_lines = _search_result_lines(resp)
        assert len(filtered_lines) >= 1
        unfiltered_lines = _search_result_lines(server.brain_search("brain"))
        assert len(unfiltered_lines) >= len(filtered_lines)

    def test_search_top_k(self, initialized):
        resp = server.brain_search("the", top_k=1)
        lines = _search_result_lines(resp)
        assert len(lines) <= 1

    def test_search_empty_query(self, initialized):
        resp = server.brain_search("")
        text = _search_text(resp)
        assert "0 results" in text

    def test_search_status_filter(self, initialized):
        resp = server.brain_search("brain", status="active")
        lines = _search_result_lines(resp)
        assert len(lines) >= 1
        for line in lines:
            assert "active" in line

    def test_search_no_matches(self, initialized):
        resp = server.brain_search("xyzzyplugh")
        text = _search_text(resp)
        assert "0 results" in text

    def test_search_uses_bm25_when_cli_unavailable(self, initialized):
        """Verify BM25 is used when CLI is not available (default state)."""
        assert server._cli_available is False
        text = _search_text(server.brain_search("brain"))
        assert "bm25" in text

    def test_search_with_mocked_cli(self, initialized, cli_available):
        """Verify CLI results are transformed to match schema."""
        cli_results = ["Wiki/brain-overview-abc123.md"]
        with patch.object(obsidian_cli, "search", return_value=cli_results), \
             patch.object(obsidian_cli, "check_available", return_value=True):
            server._cli_probed_at = 0.0  # force TTL expiry
            resp = server.brain_search("brain")
            text = _search_text(resp)
            assert "obsidian_cli" in text
            lines = _search_result_lines(resp)
            assert len(lines) >= 1
            assert "Wiki/brain-overview-abc123.md" in lines[0]

    def test_search_cli_failure_falls_back_to_bm25(self, initialized, cli_available):
        """Verify fallback to BM25 when CLI search returns None."""
        with patch.object(obsidian_cli, "search", return_value=None), \
             patch.object(obsidian_cli, "check_available", return_value=True):
            server._cli_probed_at = 0.0  # force TTL expiry
            text = _search_text(server.brain_search("brain"))
            assert "bm25" in text

    def test_search_finds_newly_created_artefact(self, initialized):
        """brain_search should find an artefact created via brain_create (incremental index)."""
        # Verify the unique term isn't already in the index
        text = _search_text(server.brain_search("zorblaxian"))
        assert "0 results" in text
        # Create an artefact containing a unique term
        server.brain_create(type="ideas", title="Zorblaxian Discovery", body="The zorblaxian method is novel.")
        # Search should find it without a full rebuild
        text = _search_text(server.brain_search("zorblaxian"))
        assert "1 results" in text

    def test_search_reflects_edited_artefact(self, initialized):
        """brain_search should reflect edits made via brain_edit (incremental index)."""
        # Create an artefact, then search to flush the pending queue
        result = server.brain_create(type="ideas", title="Editable Idea", body="Original content here.")
        server.brain_search("editable")
        # Extract created path from result
        created_path = result.split(": ", 1)[1]
        # Verify unique term not yet present
        text = _search_text(server.brain_search("qwertymorphic"))
        assert "0 results" in text
        # Edit the artefact to include a unique term
        edit_result = server.brain_edit(operation="edit", path=created_path, body="Now has qwertymorphic content.")
        assert "Error" not in edit_result
        # Verify the file on disk has the new content
        file_path = initialized / created_path
        assert "qwertymorphic" in file_path.read_text()
        text = _search_text(server.brain_search("qwertymorphic"))
        assert "1 results" in text


# ---------------------------------------------------------------------------
# brain_action tests
# ---------------------------------------------------------------------------

class TestBrainAction:
    def test_action_compile(self, initialized):
        result = server.brain_action("compile")
        assert result.startswith("**Compiled:**")

    def test_action_compile_updates_memory(self, initialized):
        old_compiled_at = server._router["meta"]["compiled_at"]
        time.sleep(0.1)
        server.brain_action("compile")
        assert server._router["meta"]["compiled_at"] != old_compiled_at

    def test_action_build_index(self, initialized):
        result = server.brain_action("build_index")
        assert result.startswith("**Built index:**")

    def test_action_build_index_updates_memory(self, initialized):
        old_built_at = server._index["meta"]["built_at"]
        time.sleep(0.1)
        server.brain_action("build_index")
        assert server._index["meta"]["built_at"] != old_built_at

    def test_action_unknown(self, initialized):
        result = server.brain_action("bogus")
        _assert_error(result, "Unknown action")

    def test_action_rename_without_cli(self, initialized):
        """Rename via grep-and-replace when CLI is unavailable."""
        vault = initialized
        # Create a file that links to the source
        (vault / "Wiki" / "linker-xyz000.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "# Linker\n\nSee [[Wiki/brain-overview-abc123]].\n"
        )
        result = server.brain_action("rename", {
            "source": "Wiki/brain-overview-abc123.md",
            "dest": "Wiki/brain-intro-abc123.md",
        })
        assert "grep_replace" in result
        assert "links updated" in result
        # Verify file was renamed
        assert not (vault / "Wiki" / "brain-overview-abc123.md").exists()
        assert (vault / "Wiki" / "brain-intro-abc123.md").exists()
        # Verify wikilink was updated
        content = (vault / "Wiki" / "linker-xyz000.md").read_text()
        assert "[[Wiki/brain-intro-abc123]]" in content

    def test_action_rename_with_mocked_cli(self, initialized, cli_available):
        """Rename via CLI when available."""
        with patch.object(obsidian_cli, "move", return_value=True):
            result = server.brain_action("rename", {
                "source": "Wiki/old.md",
                "dest": "Wiki/new.md",
            })
            assert "obsidian_cli" in result
            assert "wikilinks auto-updated" in result

    def test_action_rename_missing_params(self, initialized):
        result = server.brain_action("rename")
        _assert_error(result)

    def test_action_rename_source_not_found(self, initialized):
        result = server.brain_action("rename", {
            "source": "Wiki/nonexistent.md",
            "dest": "Wiki/other.md",
        })
        _assert_error(result)

    def test_action_rename_cli_error_falls_back_to_grep(self, initialized, cli_available):
        """When CLI returns an error (False), fallback to grep-replace."""
        vault = initialized
        (vault / "Wiki" / "linker-fallback.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "# Linker\n\nSee [[Wiki/brain-overview-abc123]].\n"
        )
        with patch.object(obsidian_cli, "move", return_value=False):
            result = server.brain_action("rename", {
                "source": "Wiki/brain-overview-abc123.md",
                "dest": "Wiki/brain-moved-abc123.md",
            })
            assert "grep_replace" in result
            assert not (vault / "Wiki" / "brain-overview-abc123.md").exists()
            assert (vault / "Wiki" / "brain-moved-abc123.md").exists()
            content = (vault / "Wiki" / "linker-fallback.md").read_text()
            assert "[[Wiki/brain-moved-abc123]]" in content

    def test_action_rename_cross_directory_without_cli(self, initialized):
        """Rename across directories creates destination dir (regression test)."""
        vault = initialized
        result = server.brain_action("rename", {
            "source": "Wiki/brain-overview-abc123.md",
            "dest": "Wiki/subdir/brain-overview-abc123.md",
        })
        assert "grep_replace" in result
        assert not (vault / "Wiki" / "brain-overview-abc123.md").exists()
        assert (vault / "Wiki" / "subdir" / "brain-overview-abc123.md").exists()

    def test_action_rename_cli_mkdir_before_move(self, initialized, cli_available):
        """CLI path creates destination directory before calling obsidian_cli.move."""
        with patch.object(obsidian_cli, "move", return_value=True) as mock_move, \
             patch.object(os, "makedirs") as mock_makedirs:
            server.brain_action("rename", {
                "source": "Wiki/old.md",
                "dest": "Wiki/subdir/new.md",
            })
            mock_makedirs.assert_called_once()
            mock_move.assert_called_once()
            assert mock_makedirs.call_args[0][0].endswith("Wiki/subdir")


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
        result = server.brain_read("type")
        assert "wiki" in result
        assert server._loaded_version == "99.0.0"

    def test_brain_search_survives_version_drift(self, initialized):
        """brain_search should succeed after version drift."""
        version_path = initialized / ".brain-core" / "VERSION"
        version_path.write_text("99.0.0\n")
        text = _search_text(server.brain_search("brain"))
        assert "results" in text
        assert server._loaded_version == "99.0.0"

    def test_brain_action_survives_version_drift(self, initialized):
        """brain_action should succeed after version drift."""
        version_path = initialized / ".brain-core" / "VERSION"
        version_path.write_text("99.0.0\n")
        result = server.brain_action("compile")
        assert result.startswith("**Compiled:**")
        assert server._loaded_version == "99.0.0"

    def test_brain_create_survives_version_drift(self, initialized):
        """brain_create should succeed after version drift."""
        version_path = initialized / ".brain-core" / "VERSION"
        version_path.write_text("99.0.0\n")
        result = server.brain_create(type="wiki", title="drift test")
        assert result.startswith("**Created**")
        assert server._loaded_version == "99.0.0"

    def test_brain_edit_survives_version_drift(self, initialized):
        """brain_edit should succeed after version drift."""
        version_path = initialized / ".brain-core" / "VERSION"
        version_path.write_text("99.0.0\n")
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            body="# Drifted\n"
        )
        assert result == "**Edited:** Wiki/brain-overview-abc123.md"
        assert server._loaded_version == "99.0.0"


# ---------------------------------------------------------------------------
# Atomic JSON save
# ---------------------------------------------------------------------------

class TestAtomicSave:
    def test_save_json_creates_file(self, tmp_path):
        """_save_json should create the file with correct content."""
        data = {"key": "value", "nested": [1, 2, 3]}
        server._save_json(data, str(tmp_path), "sub/data.json")
        result = json.loads((tmp_path / "sub" / "data.json").read_text())
        assert result == data

    def test_save_json_overwrites_existing(self, tmp_path):
        """_save_json should atomically replace an existing file."""
        path = tmp_path / "data.json"
        path.write_text('{"old": true}\n')
        server._save_json({"new": True}, str(tmp_path), "data.json")
        result = json.loads(path.read_text())
        assert result == {"new": True}

    def test_save_json_atomic_no_corruption_on_error(self, tmp_path):
        """If os.replace fails, the original file should be intact."""
        path = tmp_path / "data.json"
        original = {"original": True}
        server._save_json(original, str(tmp_path), "data.json")

        with patch("_common.os.replace", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                server._save_json({"corrupt": True}, str(tmp_path), "data.json")

        # Original file should be untouched
        result = json.loads(path.read_text())
        assert result == original

    def test_save_json_cleans_up_temp_on_failure(self, tmp_path):
        """No .tmp files should remain after a failed write."""
        (tmp_path / "sub").mkdir()
        server._save_json({"ok": True}, str(tmp_path), "sub/data.json")

        with patch("_common.os.replace", side_effect=OSError("replace failed")):
            with pytest.raises(OSError, match="replace failed"):
                server._save_json({"bad": True}, str(tmp_path), "sub/data.json")

        tmp_files = list((tmp_path / "sub").glob("*.tmp"))
        assert tmp_files == [], f"Temp files not cleaned up: {tmp_files}"


# ---------------------------------------------------------------------------
# Reload robustness
# ---------------------------------------------------------------------------

class TestReloadRobustness:
    def test_reload_rolls_back_on_module_failure(self, initialized):
        """If a module fails to reload, all modules should be rolled back."""
        version_path = initialized / ".brain-core" / "VERSION"
        version_path.write_text("99.0.0\n")
        old_version = server._loaded_version
        old_router = server._router

        call_count = 0
        original_reload = importlib.reload

        def fail_on_third(mod):
            nonlocal call_count
            call_count += 1
            if call_count == 3:
                raise ImportError("simulated module failure")
            return original_reload(mod)

        with patch("server.importlib.reload", side_effect=fail_on_third):
            server._check_and_reload()

        assert server._loaded_version == old_version, "Version should not update after failed reload"
        assert server._router is old_router, "Router should be unchanged after failed reload"

    def test_reload_post_reload_failure_leaves_version_unchanged(self, initialized):
        """If post-reload work (migration+compile) fails, version should not update."""
        version_path = initialized / ".brain-core" / "VERSION"
        version_path.write_text("99.0.0\n")
        old_version = server._loaded_version

        # _compile_and_save is defined in server.py (not reloaded), so this patch survives reload
        with patch.object(server, "_compile_and_save", side_effect=OSError("post-reload boom")):
            server._check_and_reload()

        assert server._loaded_version == old_version, "Version should not update after post-reload failure"

    def test_reload_compile_failure_leaves_version_unchanged(self, initialized):
        """If router recompile fails after reload, version should not update."""
        version_path = initialized / ".brain-core" / "VERSION"
        version_path.write_text("99.0.0\n")
        old_version = server._loaded_version

        with patch("server._compile_and_save", side_effect=OSError("compile boom")):
            server._check_and_reload()

        assert server._loaded_version == old_version

    def test_reload_retries_after_failure(self, initialized):
        """After a failed reload, the next call should retry (version still mismatches)."""
        version_path = initialized / ".brain-core" / "VERSION"
        version_path.write_text("99.0.0\n")

        with patch("server._compile_and_save", side_effect=OSError("boom")):
            server._check_and_reload()
        assert server._loaded_version != "99.0.0"

        # Second call should retry and succeed (no patch)
        server._check_and_reload()
        assert server._loaded_version == "99.0.0"

    def test_reload_success_updates_version_last(self, initialized):
        """On successful reload, version should be updated to disk version."""
        version_path = initialized / ".brain-core" / "VERSION"
        version_path.write_text("99.0.0\n")
        server._check_and_reload()
        assert server._loaded_version == "99.0.0"

    def test_check_and_reload_never_raises(self, initialized):
        """_check_and_reload should never raise, even on unexpected errors."""
        version_path = initialized / ".brain-core" / "VERSION"
        version_path.write_text("99.0.0\n")

        with patch("server._read_disk_version", side_effect=Exception("unexpected")):
            server._check_and_reload()  # should not raise


class TestEnsureFreshRobustness:
    def test_ensure_router_fresh_survives_compile_error(self, initialized):
        """If _compile_and_save raises, the old router should be preserved."""
        old_router = server._router
        # Force staleness by bumping a taxonomy file's mtime
        time.sleep(0.1)
        tax_file = initialized / "_Config" / "Taxonomy" / "Living" / "wiki.md"
        tax_file.write_text(tax_file.read_text() + "\n")

        with patch.object(server, "_compile_and_save", side_effect=OSError("boom")):
            server._ensure_router_fresh()

        assert server._router is old_router, "Old router should be preserved on compile failure"

    def test_ensure_index_fresh_survives_build_error(self, initialized):
        """If _build_index_and_save raises during dirty rebuild, old index should be preserved."""
        old_index = server._index
        server._mark_index_dirty()

        with patch.object(server, "_build_index_and_save", side_effect=OSError("boom")):
            server._ensure_index_fresh()

        assert server._index is old_index, "Old index should be preserved on build failure"
        assert not server._index_dirty, "Dirty flag should be cleared to prevent tight retry loop"

    def test_ensure_index_fresh_incremental_failure_marks_dirty(self, initialized):
        """If incremental update fails, index should be marked dirty for full rebuild."""
        server._index_dirty = False
        server._mark_index_pending("Wiki/brain-overview-abc123.md", "wiki")

        with patch("server.build_index.index_update", side_effect=OSError("boom")):
            server._ensure_index_fresh()

        assert server._index_dirty, "Index should be marked dirty after incremental failure"


class TestStartupRobustness:
    def test_startup_survives_migration_failure(self, vault):
        """If migrations fail, startup should still load router and index."""
        with patch("server.upgrade.run_pending_migrations", side_effect=OSError("boom")):
            server.startup(vault_root=str(vault))

        assert server._router is not None
        assert server._index is not None

    def test_startup_survives_router_compile_failure(self, vault):
        """If router compile fails, _router is None but index still loads."""
        server._router = None
        with patch.object(server, "_compile_and_save", side_effect=OSError("boom")), \
             patch.object(server, "_check_router", return_value=(True, None)):
            server.startup(vault_root=str(vault))

        assert server._router is None
        assert server._index is not None

    def test_startup_survives_index_build_failure(self, vault):
        """If index build fails, _index is None but router still loads."""
        server._index = None
        with patch.object(server, "_build_index_and_save", side_effect=OSError("boom")), \
             patch.object(server, "_check_index", return_value=(True, None)):
            server.startup(vault_root=str(vault))

        assert server._router is not None
        assert server._index is None

    def test_main_exits_on_vault_discovery_failure(self):
        """If vault root discovery fails, main should exit with code 1."""
        with patch("server.compile_router.find_vault_root", side_effect=FileNotFoundError("no vault")), \
             patch.dict(os.environ, {}, clear=True):
            with pytest.raises(SystemExit) as exc_info:
                server.main()
            assert exc_info.value.code == 1


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

    def test_new_skill_triggers_recompile(self, initialized):
        """Adding a new skill directory should trigger recompile."""
        old_count = len(server._router["skills"])
        skill_dir = initialized / "_Config" / "Skills" / "new-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: new-skill\ndescription: A new skill\n---\n\n# New Skill\n"
        )
        server._ensure_router_fresh()
        assert len(server._router["skills"]) == old_count + 1
        names = [s["name"] for s in server._router["skills"]]
        assert "new-skill" in names

    def test_new_memory_triggers_recompile(self, initialized):
        """Adding a new memory file should trigger recompile."""
        memories_dir = initialized / "_Config" / "Memories"
        memories_dir.mkdir(parents=True, exist_ok=True)
        (memories_dir / "test-memory.md").write_text(
            "---\ntriggers:\n  - testing\n---\n\n# Test Memory\n"
        )
        server._ensure_router_fresh()
        names = [m["name"] for m in server._router.get("memories", [])]
        assert "test-memory" in names

    def test_new_style_triggers_recompile(self, initialized):
        """Adding a new style file should trigger recompile."""
        old_count = len(server._router["styles"])
        styles_dir = initialized / "_Config" / "Styles"
        (styles_dir / "formal.md").write_text("# Formal\n\nWrite formally.\n")
        server._ensure_router_fresh()
        assert len(server._router["styles"]) == old_count + 1
        names = [s["name"] for s in server._router["styles"]]
        assert "formal" in names

    def test_new_plugin_triggers_recompile(self, initialized):
        """Adding a new plugin directory should trigger recompile."""
        old_count = len(server._router.get("plugins", []))
        plugin_dir = initialized / "_Plugins" / "new-plugin"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "SKILL.md").write_text("# New Plugin\n\nDoes things.\n")
        server._ensure_router_fresh()
        assert len(server._router.get("plugins", [])) == old_count + 1
        names = [p["name"] for p in server._router.get("plugins", [])]
        assert "new-plugin" in names

    def test_deleted_skill_triggers_recompile(self, initialized):
        """Removing a skill directory should trigger recompile."""
        old_count = len(server._router["skills"])
        skill_dir = initialized / "_Config" / "Skills" / "Vault Maintenance"
        (skill_dir / "SKILL.md").unlink()
        skill_dir.rmdir()
        server._ensure_router_fresh()
        assert len(server._router["skills"]) == old_count - 1

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

def _extract_create_path(result):
    """Extract path from 'Created type: path' format."""
    return result.split(": ", 1)[1]


class TestBrainCreate:
    def test_create_returns_path(self, initialized):
        result = server.brain_create(type="wiki", title="New Page")
        assert result.startswith("**Created** living/wiki: ")
        path = _extract_create_path(result)
        assert path.startswith("Wiki/")

    def test_create_file_on_disk(self, initialized):
        result = server.brain_create(type="wiki", title="Disk Test")
        path = _extract_create_path(result)
        abs_path = os.path.join(str(initialized), path)
        assert os.path.isfile(abs_path)

    def test_create_correct_frontmatter(self, initialized):
        result = server.brain_create(type="wiki", title="FM Test")
        path = _extract_create_path(result)
        abs_path = os.path.join(str(initialized), path)
        with open(abs_path) as f:
            content = f.read()
        from _common import parse_frontmatter
        fields, _ = parse_frontmatter(content)
        assert fields["type"] == "living/wiki"

    def test_create_unknown_type_error(self, initialized):
        result = server.brain_create(type="nonexistent", title="Test")
        _assert_error(result)

    def test_create_temporal_subfolder(self, initialized):
        result = server.brain_create(type="logs", title="My Session")
        path = _extract_create_path(result)
        assert "_Temporal/Logs/" in path
        import re
        # Path should contain yyyy-mm subfolder
        assert re.search(r"\d{4}-\d{2}", path)

    def test_create_body_override(self, initialized):
        result = server.brain_create(
            type="wiki", title="Custom Body", body="# Custom\n\nMy content.\n"
        )
        path = _extract_create_path(result)
        abs_path = os.path.join(str(initialized), path)
        with open(abs_path) as f:
            content = f.read()
        assert "My content." in content

    def test_create_frontmatter_override(self, initialized):
        result = server.brain_create(
            type="ideas", title="Override Test",
            frontmatter={"status": "shaping"}
        )
        path = _extract_create_path(result)
        abs_path = os.path.join(str(initialized), path)
        with open(abs_path) as f:
            content = f.read()
        from _common import parse_frontmatter
        fields, _ = parse_frontmatter(content)
        assert fields["status"] == "shaping"


# ---------------------------------------------------------------------------
# brain_edit tests
# ---------------------------------------------------------------------------

class TestBrainEdit:
    def test_edit_replaces_body(self, initialized):
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            body="# New Body\n\nReplaced.\n"
        )
        assert result == "**Edited:** Wiki/brain-overview-abc123.md"
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
        result = server.brain_edit(
            operation="append",
            path="Wiki/brain-overview-abc123.md",
            body="\n\nAppended text.\n"
        )
        assert result == "**Appended:** Wiki/brain-overview-abc123.md"
        content = (initialized / "Wiki" / "brain-overview-abc123.md").read_text()
        assert "Appended text." in content
        assert "Brain Overview" in content  # original preserved

    def test_invalid_path_rejected(self, initialized):
        result = server.brain_edit(
            operation="edit",
            path="Unknown/file.md",
            body="test"
        )
        _assert_error(result)

    def test_file_not_found(self, initialized):
        result = server.brain_edit(
            operation="edit",
            path="Wiki/nonexistent.md",
            body="test"
        )
        _assert_error(result)

    def test_unknown_operation(self, initialized):
        result = server.brain_edit(
            operation="bogus",
            path="Wiki/brain-overview-abc123.md",
            body="test"
        )
        _assert_error(result)

    def test_prepend_works(self, initialized):
        result = server.brain_edit(
            operation="prepend",
            path="Wiki/brain-overview-abc123.md",
            body="Prepended text.\n\n"
        )
        assert "**Prepended:**" in result
        content = (initialized / "Wiki" / "brain-overview-abc123.md").read_text()
        assert "Prepended text." in content
        assert "Brain Overview" in content  # original preserved

    def test_noop_edit_rejected(self, initialized):
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
        )
        _assert_error(result, "no-op")

    def test_noop_append_rejected(self, initialized):
        result = server.brain_edit(
            operation="append",
            path="Wiki/brain-overview-abc123.md",
        )
        _assert_error(result, "no-op")

    def test_noop_prepend_rejected(self, initialized):
        result = server.brain_edit(
            operation="prepend",
            path="Wiki/brain-overview-abc123.md",
        )
        _assert_error(result, "no-op")

    def test_edit_with_target_and_empty_body_allowed(self, initialized):
        """edit with target + empty body clears that section — not a no-op."""
        # Write a file with sections
        from _common import parse_frontmatter
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n"
        )
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            target="Alpha",
        )
        assert "**Edited:**" in result

    def test_frontmatter_only_append_allowed(self, initialized):
        """append with just frontmatter changes is valid, not a no-op."""
        result = server.brain_edit(
            operation="append",
            path="Wiki/brain-overview-abc123.md",
            frontmatter={"status": "archived"},
        )
        assert "**Appended:**" in result

    def test_append_frontmatter_extends_list(self, initialized):
        """append should extend list fields, not overwrite."""
        from _common import parse_frontmatter
        result = server.brain_edit(
            operation="append",
            path="Wiki/brain-overview-abc123.md",
            frontmatter={"tags": ["new-tag"]},
        )
        assert "**Appended:**" in result
        content = (initialized / "Wiki" / "brain-overview-abc123.md").read_text()
        fields, _ = parse_frontmatter(content)
        assert "new-tag" in fields["tags"]
        # Original tags should still be there
        assert len(fields["tags"]) > 1

    def test_targeted_edit_includes_context(self, initialized):
        """Targeted operations should include surrounding headings in response."""
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n\n## Gamma\n\nGamma content.\n"
        )
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            body="Replaced.\n",
            target="Beta",
        )
        assert "**Context:**" in result
        assert "Alpha" in result
        assert "Gamma" in result


# ---------------------------------------------------------------------------
# brain_action delete/convert tests
# ---------------------------------------------------------------------------

class TestBrainActionDelete:
    def test_delete_removes_file(self, initialized):
        result = server.brain_action("delete", {"path": "Wiki/python-guide-def456.md"})
        assert result.startswith("**Deleted:**")
        assert not (initialized / "Wiki" / "python-guide-def456.md").exists()

    def test_delete_cleans_links(self, initialized):
        # Add a link to the target file
        (initialized / "Wiki" / "linker-aaa000.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\nSee [[Wiki/python-guide-def456|Python]].\n"
        )
        result = server.brain_action("delete", {"path": "Wiki/python-guide-def456.md"})
        assert "links replaced" in result
        content = (initialized / "Wiki" / "linker-aaa000.md").read_text()
        assert "~~Python~~" in content

    def test_delete_missing_params(self, initialized):
        result = server.brain_action("delete")
        _assert_error(result)

    def test_delete_not_found(self, initialized):
        result = server.brain_action("delete", {"path": "Wiki/gone.md"})
        _assert_error(result)


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
        result = server.brain_action("convert")
        _assert_error(result)

    def test_convert_unknown_target(self, initialized):
        result = server.brain_action("convert", {
            "path": "Wiki/brain-overview-abc123.md",
            "target_type": "nonexistent",
        })
        _assert_error(result)


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
        result = server.brain_action("shape-presentation")
        _assert_error(result)

    def test_missing_source_returns_error(self, initialized):
        result = server.brain_action("shape-presentation", {"slug": "test"})
        _assert_error(result)

    def test_missing_slug_returns_error(self, initialized):
        result = server.brain_action("shape-presentation", {"source": "Wiki/brain-overview-abc123.md"})
        _assert_error(result)

    def test_source_not_found_returns_error(self, initialized):
        result = server.brain_action("shape-presentation", {
            "source": "Wiki/nonexistent.md",
            "slug": "test",
        })
        _assert_error(result)

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
            "config", "active_profile",
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
            _assert_error(result)
        finally:
            server._router = saved_router
            server._vault_root = saved_root


# ---------------------------------------------------------------------------
# brain_action upgrade tests
# ---------------------------------------------------------------------------

class TestBrainActionUpgrade:
    @pytest.fixture
    def source(self, tmp_path):
        """Create a source brain-core directory for upgrade tests."""
        src = tmp_path / "src-brain-core"
        src.mkdir()
        (src / "VERSION").write_text("0.8.0\n")
        (src / "index.md").write_text("# Brain Core\n\nNew version.\n")
        (src / "guide.md").write_text("# Guide\n\nUpdated guide.\n")
        scripts = src / "scripts"
        scripts.mkdir()
        (scripts / "_common.py").write_text("# common\n")
        return src

    def test_upgrade_copies_files(self, initialized, source):
        result = json.loads(server.brain_action("upgrade", {"source": str(source)}))
        assert result["status"] == "ok"
        assert (initialized / ".brain-core" / "index.md").read_text() == "# Brain Core\n\nNew version.\n"
        assert (initialized / ".brain-core" / "guide.md").read_text() == "# Guide\n\nUpdated guide.\n"

    def test_upgrade_reports_diff(self, initialized, source):
        result = json.loads(server.brain_action("upgrade", {"source": str(source)}))
        assert result["status"] == "ok"
        assert isinstance(result["files_added"], list)
        assert isinstance(result["files_modified"], list)
        assert isinstance(result["files_removed"], list)
        assert isinstance(result["files_unchanged"], int)

    def test_upgrade_updates_version(self, initialized, source):
        result = json.loads(server.brain_action("upgrade", {"source": str(source)}))
        assert result["old_version"] == "0.7.0"
        assert result["new_version"] == "0.8.0"
        assert (initialized / ".brain-core" / "VERSION").read_text().strip() == "0.8.0"

    def test_upgrade_skips_same_version(self, initialized, source):
        (source / "VERSION").write_text("0.7.0\n")
        result = json.loads(server.brain_action("upgrade", {"source": str(source)}))
        assert result["status"] == "skipped"

    def test_upgrade_skips_downgrade(self, initialized, source):
        (source / "VERSION").write_text("0.6.0\n")
        result = json.loads(server.brain_action("upgrade", {"source": str(source)}))
        assert result["status"] == "skipped"
        assert "Downgrade" in result["message"]

    def test_upgrade_force_downgrade(self, initialized, source):
        (source / "VERSION").write_text("0.6.0\n")
        result = json.loads(server.brain_action("upgrade", {
            "source": str(source), "force": True
        }))
        assert result["status"] == "ok"
        assert result["new_version"] == "0.6.0"

    def test_upgrade_dry_run(self, initialized, source):
        result = json.loads(server.brain_action("upgrade", {
            "source": str(source), "dry_run": True
        }))
        assert result["status"] == "ok"
        assert result["dry_run"] is True
        # Version should NOT have changed on disk
        assert (initialized / ".brain-core" / "VERSION").read_text().strip() == "0.7.0"

    def test_upgrade_removes_obsolete_files(self, initialized, source):
        # Add a file only in the vault's .brain-core (not in source)
        (initialized / ".brain-core" / "obsolete.md").write_text("old\n")
        result = json.loads(server.brain_action("upgrade", {"source": str(source)}))
        assert result["status"] == "ok"
        assert "obsolete.md" in result["files_removed"]
        assert not (initialized / ".brain-core" / "obsolete.md").exists()

    def test_upgrade_excludes_pycache(self, initialized, source):
        pycache = source / "__pycache__"
        pycache.mkdir()
        (pycache / "module.cpython-312.pyc").write_text("bytecode\n")
        result = json.loads(server.brain_action("upgrade", {"source": str(source)}))
        assert result["status"] == "ok"
        assert not (initialized / ".brain-core" / "__pycache__").exists()

    def test_upgrade_missing_source(self, initialized):
        result = json.loads(server.brain_action("upgrade", {"source": "/nonexistent/path"}))
        assert result["status"] == "error"

    def test_upgrade_missing_source_version(self, initialized, tmp_path):
        empty_src = tmp_path / "empty-src"
        empty_src.mkdir()
        result = json.loads(server.brain_action("upgrade", {"source": str(empty_src)}))
        assert result["status"] == "error"

    def test_upgrade_via_mcp_action(self, initialized, source):
        """End-to-end: upgrade triggers post-upgrade recompile + index rebuild."""
        result = json.loads(server.brain_action("upgrade", {"source": str(source)}))
        assert result["status"] == "ok"
        assert "post_upgrade" in result
        # Router and index should still be valid after upgrade
        assert server._router is not None
        assert server._index is not None



# ---------------------------------------------------------------------------
# Workspace registry — script tests
# ---------------------------------------------------------------------------

class TestWorkspaceRegistryScript:
    """Tests for workspace_registry.py script functions."""

    def test_load_empty_registry(self, vault):
        """No .brain/ directory → empty registry."""
        result = workspace_registry.load_registry(str(vault))
        assert result == {}

    def test_load_malformed_registry(self, vault):
        """Malformed JSON → empty registry (graceful fallback)."""
        brain_local = vault / ".brain" / "local"
        brain_local.mkdir(parents=True)
        (brain_local / "workspaces.json").write_text("not json{{{")
        result = workspace_registry.load_registry(str(vault))
        assert result == {}

    def test_save_and_load_roundtrip(self, vault):
        """Save then load returns the same data."""
        registry = {"my-project": {"path": "/tmp/my-project"}}
        workspace_registry.save_registry(str(vault), registry)
        loaded = workspace_registry.load_registry(str(vault))
        assert loaded == registry

    def test_save_creates_brain_dir(self, vault):
        """save_registry creates .brain/local/ if it doesn't exist."""
        assert not (vault / ".brain" / "local").exists()
        workspace_registry.save_registry(str(vault), {"test": {"path": "/tmp"}})
        assert (vault / ".brain" / "local" / "workspaces.json").exists()

    def test_resolve_embedded(self, vault):
        """Embedded workspace resolves via _Workspaces/{slug}/."""
        ws_dir = vault / "_Workspaces" / "my-data"
        ws_dir.mkdir(parents=True)
        result = workspace_registry.resolve_workspace(str(vault), "my-data")
        assert result["slug"] == "my-data"
        assert result["mode"] == "embedded"
        assert result["path"] == str(ws_dir)

    def test_resolve_linked(self, vault, tmp_path):
        """Linked workspace resolves via registry."""
        ext_path = str(tmp_path / "external-project")
        registry = {"ext-proj": {"path": ext_path}}
        result = workspace_registry.resolve_workspace(str(vault), "ext-proj", registry=registry)
        assert result["slug"] == "ext-proj"
        assert result["mode"] == "linked"
        assert result["path"] == ext_path

    def test_resolve_embedded_takes_precedence(self, vault, tmp_path):
        """Embedded workspace takes precedence over linked registration."""
        ws_dir = vault / "_Workspaces" / "dual"
        ws_dir.mkdir(parents=True)
        registry = {"dual": {"path": str(tmp_path / "somewhere-else")}}
        result = workspace_registry.resolve_workspace(str(vault), "dual", registry=registry)
        assert result["mode"] == "embedded"

    def test_resolve_unknown_raises(self, vault):
        """Unknown slug raises ValueError."""
        with pytest.raises(ValueError, match="Unknown workspace"):
            workspace_registry.resolve_workspace(str(vault), "nonexistent")

    def test_resolve_tilde_expansion(self, vault):
        """Linked path with ~ gets expanded."""
        registry = {"tilde-proj": {"path": "~/my-project"}}
        result = workspace_registry.resolve_workspace(str(vault), "tilde-proj", registry=registry)
        assert "~" not in result["path"]
        assert os.path.expanduser("~") in result["path"]

    def test_list_empty(self, vault):
        """No workspaces → empty list."""
        result = workspace_registry.list_workspaces(str(vault))
        assert result == []

    def test_list_embedded_only(self, vault):
        """Discovers embedded workspaces from _Workspaces/."""
        (vault / "_Workspaces" / "alpha").mkdir(parents=True)
        (vault / "_Workspaces" / "beta").mkdir(parents=True)
        result = workspace_registry.list_workspaces(str(vault))
        slugs = [w["slug"] for w in result]
        assert "alpha" in slugs
        assert "beta" in slugs
        assert all(w["mode"] == "embedded" for w in result)

    def test_list_linked_only(self, vault, tmp_path):
        """Lists linked workspaces from registry."""
        registry = {"ext": {"path": str(tmp_path / "ext")}}
        result = workspace_registry.list_workspaces(str(vault), registry=registry)
        assert len(result) == 1
        assert result[0]["slug"] == "ext"
        assert result[0]["mode"] == "linked"

    def test_list_combined(self, vault, tmp_path):
        """Lists both embedded and linked workspaces."""
        (vault / "_Workspaces" / "local").mkdir(parents=True)
        registry = {"remote": {"path": str(tmp_path / "remote")}}
        result = workspace_registry.list_workspaces(str(vault), registry=registry)
        slugs = [w["slug"] for w in result]
        assert "local" in slugs
        assert "remote" in slugs

    def test_list_skips_system_dirs(self, vault):
        """System dirs (_Archive, .hidden) in _Workspaces/ are excluded."""
        ws = vault / "_Workspaces"
        ws.mkdir(parents=True)
        (ws / "_Archive").mkdir()
        (ws / ".hidden").mkdir()
        (ws / "real-workspace").mkdir()
        result = workspace_registry.list_workspaces(str(vault))
        slugs = [w["slug"] for w in result]
        assert slugs == ["real-workspace"]

    def test_list_enriched_with_hub_metadata(self, vault):
        """Hub artefact metadata enriches the workspace listing."""
        (vault / "_Workspaces" / "taxes").mkdir(parents=True)
        hub_dir = vault / "Workspaces"
        hub_dir.mkdir(parents=True)
        (hub_dir / "taxes.md").write_text(
            "---\ntype: living/workspace\nstatus: active\n"
            "workspace_mode: embedded\ntags:\n  - workspace/taxes\n---\n\n# Taxes\n"
        )
        result = workspace_registry.list_workspaces(str(vault))
        assert len(result) == 1
        ws = result[0]
        assert ws["status"] == "active"
        assert ws["hub_path"] == "Workspaces/taxes.md"

    def test_register_creates_entry(self, vault, tmp_path):
        """register_workspace adds to .brain/local/workspaces.json."""
        ext_path = str(tmp_path / "my-project")
        result = workspace_registry.register_workspace(str(vault), "my-project", ext_path)
        assert result["status"] == "ok"
        assert result["action"] == "registered"
        # Verify on disk
        loaded = workspace_registry.load_registry(str(vault))
        assert "my-project" in loaded
        assert loaded["my-project"]["path"] == ext_path

    def test_register_updates_existing(self, vault, tmp_path):
        """Re-registering updates the path."""
        workspace_registry.register_workspace(str(vault), "proj", str(tmp_path / "v1"))
        result = workspace_registry.register_workspace(str(vault), "proj", str(tmp_path / "v2"))
        assert result["action"] == "updated"
        loaded = workspace_registry.load_registry(str(vault))
        assert loaded["proj"]["path"] == str(tmp_path / "v2")

    def test_register_rejects_embedded_conflict(self, vault, tmp_path):
        """Cannot register linked workspace when embedded exists."""
        (vault / "_Workspaces" / "conflict").mkdir(parents=True)
        with pytest.raises(ValueError, match="embedded workspace already exists"):
            workspace_registry.register_workspace(
                str(vault), "conflict", str(tmp_path / "elsewhere")
            )

    def test_unregister_removes_entry(self, vault, tmp_path):
        """unregister_workspace removes from .brain/local/workspaces.json."""
        workspace_registry.register_workspace(str(vault), "temp", str(tmp_path / "temp"))
        result = workspace_registry.unregister_workspace(str(vault), "temp")
        assert result["status"] == "ok"
        loaded = workspace_registry.load_registry(str(vault))
        assert "temp" not in loaded

    def test_unregister_unknown_raises(self, vault):
        """Cannot unregister a workspace that isn't registered."""
        with pytest.raises(ValueError, match="not registered"):
            workspace_registry.unregister_workspace(str(vault), "ghost")


# ---------------------------------------------------------------------------
# Workspace registry — MCP server integration tests
# ---------------------------------------------------------------------------

class TestWorkspaceRead:
    """Tests for brain_read(resource='workspace')."""

    @pytest.fixture(autouse=True)
    def setup_workspaces(self, initialized):
        """Add workspace fixtures to the vault."""
        self.vault = initialized
        # Embedded workspace
        (initialized / "_Workspaces" / "analysis").mkdir(parents=True)
        # Hub artefact
        (initialized / "Workspaces").mkdir(parents=True)
        (initialized / "Workspaces" / "analysis.md").write_text(
            "---\ntype: living/workspace\nstatus: active\n"
            "workspace_mode: embedded\ntags:\n  - workspace/analysis\n---\n\n# Analysis\n"
        )

    def test_list_workspaces(self):
        result = server.brain_read("workspace")
        assert "analysis" in result

    def test_list_workspace_shape(self):
        result = server.brain_read("workspace")
        assert "embedded" in result

    def test_resolve_workspace_by_slug(self):
        result = server.brain_read("workspace", name="analysis")
        assert "analysis" in result
        assert "embedded" in result

    def test_resolve_unknown_workspace(self):
        result = server.brain_read("workspace", name="nonexistent")
        _assert_error(result)


class TestWorkspaceActions:
    """Tests for brain_action register/unregister workspace."""

    def test_register_linked_workspace(self, initialized, tmp_path):
        ext_path = str(tmp_path / "my-repo")
        result = server.brain_action("register_workspace", {
            "slug": "my-repo", "path": ext_path,
        })
        assert "registered" in result
        assert "my-repo" in result
        # Verify server state was refreshed
        assert "my-repo" in server._workspace_registry

    def test_register_missing_params(self, initialized):
        result = server.brain_action("register_workspace")
        _assert_error(result)

    def test_register_missing_path(self, initialized):
        result = server.brain_action("register_workspace", {"slug": "x"})
        _assert_error(result)

    def test_unregister_workspace(self, initialized, tmp_path):
        # Register first
        server.brain_action("register_workspace", {
            "slug": "temp", "path": str(tmp_path / "temp"),
        })
        result = server.brain_action("unregister_workspace", {"slug": "temp"})
        assert "unregistered" in result
        assert "temp" not in server._workspace_registry

    def test_unregister_missing_params(self, initialized):
        result = server.brain_action("unregister_workspace")
        _assert_error(result)

    def test_unregister_unknown_slug(self, initialized):
        result = server.brain_action("unregister_workspace", {"slug": "ghost"})
        _assert_error(result)

    def test_registered_workspace_visible_in_read(self, initialized, tmp_path):
        """After registering, brain_read workspace should list it."""
        ext_path = str(tmp_path / "visible-proj")
        server.brain_action("register_workspace", {
            "slug": "visible-proj", "path": ext_path,
        })
        result = server.brain_read("workspace")
        assert "visible-proj" in result

    def test_registered_workspace_resolvable(self, initialized, tmp_path):
        """After registering, brain_read workspace name=slug should resolve."""
        ext_path = str(tmp_path / "resolvable")
        server.brain_action("register_workspace", {
            "slug": "resolvable", "path": ext_path,
        })
        result = server.brain_read("workspace", name="resolvable")
        assert "resolvable" in result
        assert "linked" in result


# ---------------------------------------------------------------------------
# Startup loads workspace registry
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# brain_process tool
# ---------------------------------------------------------------------------

class TestBrainProcess:
    def test_process_classify_context_assembly(self, initialized):
        result = server.brain_process(
            operation="classify",
            content="I have a new idea for solar powered keyboards",
            mode="context_assembly",
        )
        assert isinstance(result, str)
        assert "context_assembly" in result

    def test_process_classify_bm25(self, initialized):
        # Add Purpose/When To Use to taxonomy for BM25 scoring
        tax_ideas = os.path.join(str(initialized), "_Config", "Taxonomy", "Living", "ideas.md")
        with open(tax_ideas, "w") as f:
            f.write(
                "# Ideas\n\n"
                "A concept that needs iterative refinement.\n\n"
                "## Naming\n\n`{slug}.md` in `Ideas/`.\n\n"
                "## Frontmatter\n\n```yaml\n---\ntype: living/ideas\n---\n```\n\n"
                "## Purpose\n\nCapture concepts that need development.\n\n"
                "## When To Use\n\nWhen developing a concept that needs iterative refinement.\n\n"
                "## Template\n\n[[_Config/Templates/Living/Ideas]]\n"
            )
        # Re-initialize to rebuild index
        server.startup(vault_root=str(initialized))
        result = server.brain_process(
            operation="classify",
            content="a new concept idea that needs iterative development and refinement",
            mode="bm25_only",
        )
        assert isinstance(result, str)
        assert "**Classified**" in result
        assert "bm25_only" in result

    def test_process_resolve_create(self, initialized):
        result = server.brain_process(
            operation="resolve",
            content="Some content about a brand new topic",
            type="wiki",
            title="Quantum Computing Primer",
        )
        assert isinstance(result, str)
        assert "**Resolve**" in result
        assert "create" in result

    def test_process_resolve_update(self, initialized):
        result = server.brain_process(
            operation="resolve",
            content="Updated information",
            type="wiki",
            title="brain-overview-abc123",
        )
        assert isinstance(result, str)
        assert "**Resolve**" in result
        assert "update" in result

    def test_process_resolve_missing_params(self, initialized):
        result = server.brain_process(
            operation="resolve",
            content="Some content",
        )
        _assert_error(result, "resolve requires type and title")

    def test_process_ingest_creates_file(self, initialized):
        result = server.brain_process(
            operation="ingest",
            content="# Quantum Coffee\n\nWhat if coffee brewed itself?",
            type="ideas",
        )
        assert isinstance(result, str)
        assert "**Ingested**" in result
        assert "created" in result

    def test_process_ingest_needs_classification(self, initialized):
        # Without embeddings or BM25 type descriptions, falls to context_assembly
        result = server.brain_process(
            operation="ingest",
            content="Random content without hints",
        )
        # Should return context_assembly since no scoring available
        assert isinstance(result, str)
        assert "context_assembly" in result

    def test_process_unknown_operation(self, initialized):
        result = server.brain_process(
            operation="nonexistent",
            content="test",
        )
        _assert_error(result, "Unknown operation")


class TestWorkspaceStartup:
    def test_startup_loads_empty_registry(self, vault):
        """Startup with no .brain/ → empty registry."""
        server.startup(vault_root=str(vault))
        assert server._workspace_registry == {}

    def test_startup_loads_existing_registry(self, vault, tmp_path):
        """Startup with .brain/local/workspaces.json → loaded registry."""
        brain_local = vault / ".brain" / "local"
        brain_local.mkdir(parents=True)
        (brain_local / "workspaces.json").write_text(json.dumps({
            "workspaces": {"pre-existing": {"path": str(tmp_path / "pre")}}
        }))
        server.startup(vault_root=str(vault))
        assert "pre-existing" in server._workspace_registry


# ---------------------------------------------------------------------------
# brain_action fix-links tests
# ---------------------------------------------------------------------------

class TestBrainActionFixLinks:
    def test_dry_run_returns_json_with_summary(self, initialized):
        """Default fix-links (no fix param) returns dry_run JSON."""
        result = json.loads(server.brain_action("fix-links"))
        assert result["mode"] == "dry_run"
        assert "summary" in result
        assert "fixed" in result
        assert "ambiguous" in result
        assert "unresolvable" in result

    def test_dry_run_detects_broken_links(self, initialized):
        """Dry run detects a broken wikilink and classifies it."""
        vault = initialized
        (vault / "Wiki" / "has-broken-link.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "See [[nonexistent-target]].\n"
        )
        result = json.loads(server.brain_action("fix-links"))
        assert result["mode"] == "dry_run"
        assert result["summary"]["total_broken"] >= 1
        all_targets = (
            [f["target"] for f in result["fixed"]]
            + [a["target"] for a in result["ambiguous"]]
            + [u["target"] for u in result["unresolvable"]]
        )
        assert "nonexistent-target" in all_targets

    def test_fix_applies_resolved_links(self, initialized):
        """fix=True applies auto-resolved link fixes."""
        vault = initialized
        (vault / "Wiki" / "My Target Page.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n# My Target Page\n"
        )
        (vault / "Wiki" / "referrer.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "See [[my-target-page]].\n"
        )
        result = json.loads(server.brain_action("fix-links", {"fix": True}))
        assert result["mode"] == "fix"
        assert result["summary"]["fixed"] >= 1
        assert result.get("substitutions", 0) >= 1
        content = (vault / "Wiki" / "referrer.md").read_text()
        assert "[[My Target Page]]" in content

    def test_fix_marks_index_dirty(self, initialized):
        """Applying fixes should mark the index as dirty."""
        vault = initialized
        (vault / "Wiki" / "Target Title.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n# Target Title\n"
        )
        (vault / "Wiki" / "linker.md").write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\nSee [[target-title]].\n"
        )
        server.brain_action("fix-links", {"fix": True})
        assert server._index_dirty is True


# ---------------------------------------------------------------------------
# brain_action sync_definitions tests
# ---------------------------------------------------------------------------

class TestBrainActionSyncDefinitions:
    @pytest.fixture(autouse=True)
    def setup_library(self, initialized):
        """Add an artefact library with one type to the vault fixture."""
        self.vault = initialized
        lib_dir = initialized / ".brain-core" / "artefact-library" / "living" / "wiki"
        lib_dir.mkdir(parents=True, exist_ok=True)
        (lib_dir / "manifest.yaml").write_text(
            "files:\n"
            "  taxonomy:\n"
            "    source: taxonomy.md\n"
            "    target: _Config/Taxonomy/Living/wiki.md\n"
        )
        (lib_dir / "taxonomy.md").write_text(
            "# Wiki\n\n## Naming\n\n`{slug}.md` in `Wiki/`.\n\n"
            "## Frontmatter\n\n```yaml\n---\ntype: living/wiki\ntags: []\n---\n```\n"
        )

    def test_dry_run_returns_json(self, initialized):
        """Dry run returns structured JSON without modifying files."""
        result = json.loads(server.brain_action("sync_definitions", {"dry_run": True}))
        assert result["dry_run"] is True
        assert "status" in result
        assert "updated" in result
        assert "skipped" in result

    def test_sync_updates_files(self, initialized):
        """Non-dry-run sync updates outdated definitions."""
        vault = initialized
        tracking_path = vault / ".brain" / "tracking.json"
        if tracking_path.exists():
            tracking_path.unlink()
        result = json.loads(server.brain_action("sync_definitions"))
        assert result["status"] in ("ok", "warnings", "skipped")

    def test_sync_with_type_filter(self, initialized):
        """Type filter limits sync to specified types."""
        result = json.loads(server.brain_action("sync_definitions", {
            "dry_run": True,
            "types": ["living/wiki"],
        }))
        assert result["dry_run"] is True
        all_types = [u.get("type_key", "") for u in result.get("updated", [])]
        all_types += [s.get("type_key", "") for s in result.get("skipped", [])]
        for t in all_types:
            if t:
                assert "wiki" in t

    def test_sync_recompiles_router_on_update(self, initialized):
        """After a real sync with updates, router should be recompiled."""
        vault = initialized
        tracking_path = vault / ".brain" / "tracking.json"
        if tracking_path.exists():
            tracking_path.unlink()
        old_compiled_at = server._router["meta"]["compiled_at"]
        time.sleep(0.1)
        result = json.loads(server.brain_action("sync_definitions"))
        if result.get("updated"):
            assert result.get("post_sync") == "Recompiled router."
            assert server._router["meta"]["compiled_at"] != old_compiled_at

    def test_force_flag(self, initialized):
        """Force flag should allow overwrite even if files match."""
        result = json.loads(server.brain_action("sync_definitions", {
            "force": True,
            "dry_run": True,
        }))
        assert result["dry_run"] is True


# ---------------------------------------------------------------------------
# brain_action migrate_naming tests
# ---------------------------------------------------------------------------

class TestBrainActionMigrateNaming:
    @pytest.fixture(autouse=True)
    def setup_plans_type(self, initialized):
        """Add plans taxonomy and an old-convention file to test migration."""
        self.vault = initialized
        tax_temporal = initialized / "_Config" / "Taxonomy" / "Temporal"
        tax_temporal.mkdir(parents=True, exist_ok=True)
        (tax_temporal / "plans.md").write_text(
            "# Plans\n\n## Naming\n\n`yyyymmdd-plan~{Title}.md` in `_Temporal/Plans/yyyy-mm/`.\n\n"
            "## Frontmatter\n\n```yaml\n---\ntype: temporal/plans\ntags:\n  - plan\n---\n```\n"
        )
        month_dir = initialized / "_Temporal" / "Plans" / "2026-03"
        month_dir.mkdir(parents=True, exist_ok=True)
        server.brain_action("compile")

    def _create_old_convention_file(self, name="test-migrate", title="Test Migrate"):
        """Create an old-convention temporal file (double-dash separator)."""
        month_dir = self.vault / "_Temporal" / "Plans" / "2026-03"
        path = month_dir / f"20260301-plan--{name}.md"
        path.write_text(
            f"---\ntype: temporal/plans\ntags: [plan]\n---\n\n# {title}\n"
        )
        return path

    def test_dry_run_returns_json(self, initialized):
        """Dry run returns structured JSON summary."""
        result = json.loads(server.brain_action("migrate_naming", {"dry_run": True}))
        assert "renamed" in result
        assert "skipped" in result

    def test_dry_run_does_not_rename_files(self, initialized):
        """Dry run should not move any files."""
        self._create_old_convention_file()
        files_before = set(str(p) for p in self.vault.rglob("*.md"))
        server.brain_action("migrate_naming", {"dry_run": True})
        files_after = set(str(p) for p in self.vault.rglob("*.md"))
        assert files_before == files_after

    def test_migrate_renames_old_convention_files(self, initialized):
        """Actual migration renames double-dash files to tilde convention."""
        old_path = self._create_old_convention_file()
        assert old_path.exists()

        result = json.loads(server.brain_action("migrate_naming"))
        assert not old_path.exists(), "Old-convention file should have been renamed"
        # Verify the tilde-convention file exists
        month_dir = self.vault / "_Temporal" / "Plans" / "2026-03"
        new_files = [f.name for f in month_dir.iterdir() if "~" in f.name]
        assert len(new_files) >= 1

    def test_migrate_rebuilds_router_and_index_on_rename(self, initialized):
        """After actual renames, both router and index should be rebuilt."""
        self._create_old_convention_file(name="needs-rename", title="Needs Rename")
        old_compiled_at = server._router["meta"]["compiled_at"]
        old_built_at = server._index["meta"]["built_at"]
        time.sleep(0.1)

        result = json.loads(server.brain_action("migrate_naming"))
        renamed_count = result.get("renamed", 0)
        if isinstance(renamed_count, list):
            renamed_count = len(renamed_count)
        if renamed_count > 0:
            assert server._router["meta"]["compiled_at"] != old_compiled_at
            assert server._index["meta"]["built_at"] != old_built_at


# ---------------------------------------------------------------------------
# Operator profile tests
# ---------------------------------------------------------------------------

class TestOperatorProfiles:
    def test_session_default_profile(self, initialized):
        """No operator key → default profile (operator) from template."""
        result = json.loads(server.brain_session())
        assert result["active_profile"] == "operator"
        assert server._session_profile == "operator"

    def test_session_with_operator_key(self, initialized):
        """Authenticated session returns matched profile."""
        key = "timber-compass-violet"
        # Register an operator in config
        server._config["vault"]["operators"] = [
            {
                "id": "test-agent",
                "profile": "reader",
                "auth": {"type": "key", "hash": config_mod.hash_key(key)},
            },
        ]

        result = json.loads(server.brain_session(operator_key=key))
        assert result["active_profile"] == "reader"
        assert server._session_profile == "reader"

    def test_session_bad_operator_key(self, initialized):
        """Wrong key → error."""
        server._config["vault"]["operators"] = [
            {
                "id": "test-agent",
                "profile": "reader",
                "auth": {"type": "key", "hash": "sha256:wrong"},
            },
        ]

        result = server.brain_session(operator_key="bad-key")
        _assert_error(result, "does not match")

    def test_session_no_config(self, initialized):
        """No config loaded → session still works, no profile set."""
        server._config = None
        result = json.loads(server.brain_session())
        assert "active_profile" not in result
        assert server._session_profile is None

    def test_config_in_session_payload(self, initialized):
        """Session payload includes config metadata."""
        result = json.loads(server.brain_session())
        assert "config" in result
        cfg = result["config"]
        assert "brain_name" in cfg
        assert "default_profile" in cfg
        assert "profiles" in cfg
        assert "reader" in cfg["profiles"]
        assert "contributor" in cfg["profiles"]
        assert "operator" in cfg["profiles"]

    def test_environment_includes_config_info(self, initialized):
        """brain_read(resource="environment") includes config metadata."""
        server.brain_session()  # set profile
        result = server.brain_read("environment")
        assert "has_config=True" in result
        assert "active_profile=operator" in result

    def test_enforcement_reader_blocked(self, initialized):
        """Reader profile cannot call brain_create."""
        key = "timber-compass-violet"
        server._config["vault"]["operators"] = [
            {
                "id": "test-reader",
                "profile": "reader",
                "auth": {"type": "key", "hash": config_mod.hash_key(key)},
            },
        ]
        server.brain_session(operator_key=key)
        assert server._session_profile == "reader"

        # reader can call brain_read and brain_search
        result = server.brain_read("type")
        assert not isinstance(result, CallToolResult) or not result.isError

        # reader cannot call brain_create
        result = server.brain_create(type="ideas", title="test")
        _assert_error(result, "does not allow brain_create")

        # reader cannot call brain_edit
        result = server.brain_edit(operation="edit", path="test.md", body="test")
        _assert_error(result, "does not allow brain_edit")

        # reader cannot call brain_action
        result = server.brain_action(action="compile")
        _assert_error(result, "does not allow brain_action")

        # reader cannot call brain_process
        result = server.brain_process(operation="classify", content="test")
        _assert_error(result, "does not allow brain_process")

    def test_enforcement_contributor_allowed(self, initialized):
        """Contributor profile can call brain_create but not brain_action."""
        key = "forest-meadow-stream"
        server._config["vault"]["operators"] = [
            {
                "id": "test-contributor",
                "profile": "contributor",
                "auth": {"type": "key", "hash": config_mod.hash_key(key)},
            },
        ]
        server.brain_session(operator_key=key)
        assert server._session_profile == "contributor"

        # contributor can create
        result = server.brain_create(type="ideas", title="test idea")
        assert not isinstance(result, CallToolResult) or not result.isError

        # contributor cannot use brain_action
        result = server.brain_action(action="compile")
        _assert_error(result, "does not allow brain_action")

    def test_enforcement_no_config_allows_all(self, initialized):
        """No config loaded → all tools allowed (backward compat)."""
        server._config = None
        server._session_profile = None

        # Should work without enforcement
        result = server.brain_read("type")
        assert not isinstance(result, CallToolResult) or not result.isError

    def test_enforcement_brain_session_always_allowed(self, initialized):
        """brain_session works regardless of profile — it's the auth entry point."""
        key = "timber-compass-violet"
        server._config["vault"]["operators"] = [
            {
                "id": "test-reader",
                "profile": "reader",
                "auth": {"type": "key", "hash": config_mod.hash_key(key)},
            },
        ]
        # First session as reader
        result = json.loads(server.brain_session(operator_key=key))
        assert result["active_profile"] == "reader"

        # Can call brain_session again (e.g., re-auth with different key)
        result = json.loads(server.brain_session())
        assert result["active_profile"] == "operator"


# ---------------------------------------------------------------------------
# Index staleness detection
# ---------------------------------------------------------------------------

class TestIndexStaleness:
    """Tests for BM25 index staleness detection fixes."""

    def test_incremental_update_preserves_built_at(self, initialized):
        """Incremental updates must not advance built_at threshold."""
        original_built_at = server._index["meta"]["built_at"]

        # Create a new file on disk and queue it for incremental update
        wiki_dir = initialized / "Wiki"
        new_file = wiki_dir / "test-preserve-xyz999.md"
        new_file.write_text(
            "---\ntype: living/wiki\ntags: [test]\nstatus: draft\n---\n\n"
            "# Preserve Test\n\nContent for testing built_at preservation.\n"
        )
        server._mark_index_pending("Wiki/test-preserve-xyz999.md", "living/wiki")

        # Reset TTL so staleness check can fire
        server._index_checked_at = 0.0
        server._ensure_index_fresh()

        # built_at must not have advanced
        assert server._index["meta"]["built_at"] == original_built_at

    def test_incremental_then_external_file_triggers_rebuild(self, initialized):
        """After incremental update, external files are detected via count mismatch."""
        # Queue and process an incremental update
        wiki_dir = initialized / "Wiki"
        incr_file = wiki_dir / "test-incr-aaa111.md"
        incr_file.write_text(
            "---\ntype: living/wiki\ntags: [test]\nstatus: draft\n---\n\n"
            "# Incremental\n\nQueued via MCP.\n"
        )
        server._mark_index_pending("Wiki/test-incr-aaa111.md", "living/wiki")
        server._index_checked_at = 0.0
        server._ensure_index_fresh()

        doc_count_after_incr = server._index["meta"]["document_count"]

        # Now create an external file (not queued via MCP)
        ext_file = wiki_dir / "test-external-bbb222.md"
        ext_file.write_text(
            "---\ntype: living/wiki\ntags: [test]\nstatus: draft\n---\n\n"
            "# External\n\nCreated outside MCP.\n"
        )

        # Reset TTL so staleness check fires
        server._index_checked_at = 0.0
        server._ensure_index_fresh()

        # Index should have been rebuilt and include the external file
        assert server._index["meta"]["document_count"] > doc_count_after_incr

    def test_index_version_mismatch_triggers_rebuild(self, initialized):
        """Index with wrong version is detected as stale."""
        # With current version, should be fresh
        stale, _ = server._check_index(str(initialized))
        assert not stale

        # Patch INDEX_VERSION to simulate drift
        with patch.object(build_index, "INDEX_VERSION", "99.0.0"):
            stale, _ = server._check_index(str(initialized))
            assert stale

    def test_document_count_mismatch_triggers_rebuild(self, initialized):
        """New file on disk without index entry is detected via count mismatch."""
        # Get current threshold from built_at
        built_at = server._index["meta"]["built_at"]
        threshold = server._index["meta"]["document_count"]

        # Create a file with an OLD mtime (won't trigger mtime check)
        wiki_dir = initialized / "Wiki"
        old_file = wiki_dir / "test-count-ccc333.md"
        old_file.write_text(
            "---\ntype: living/wiki\ntags: [test]\nstatus: draft\n---\n\n"
            "# Count Test\n\nContent.\n"
        )
        # Set mtime to well before built_at so mtime check alone wouldn't catch it
        from datetime import datetime as dt
        built_ts = dt.fromisoformat(built_at).timestamp()
        old_ts = built_ts - 3600  # 1 hour before built_at
        os.utime(old_file, (old_ts, old_ts))

        # Count mismatch should detect staleness
        assert server._check_index_files(
            str(initialized), threshold, built_ts
        ) is True

    def test_split_ttl_constants(self):
        """Router and index TTL constants exist and differ."""
        assert hasattr(server, "_ROUTER_CHECK_TTL")
        assert hasattr(server, "_INDEX_CHECK_TTL")
        assert server._ROUTER_CHECK_TTL != server._INDEX_CHECK_TTL


# ---------------------------------------------------------------------------
# brain_list tests
# ---------------------------------------------------------------------------

def _list_text(response):
    """Join TextContent blocks into single string for list assertions."""
    if isinstance(response, str):
        return response
    return "\n".join(block.text for block in response)


def _list_result_lines(response):
    """Extract individual result lines from a brain_list response (skipping meta)."""
    if isinstance(response, str):
        return []
    if len(response) < 2:
        return []
    return response[1].text.strip().split("\n")


class TestBrainList:
    def test_list_all(self, initialized):
        """No filters returns all indexed documents; meta says 'Listed: N results'."""
        resp = server.brain_list()
        text = _list_text(resp)
        assert "Listed:" in text
        assert "results" in text

    def test_list_by_type(self, initialized):
        """Filter by type returns only artefacts of that type."""
        resp = server.brain_list(type="living/wiki")
        lines = _list_result_lines(resp)
        assert len(lines) >= 1
        for line in lines:
            assert "living/wiki" in line

    def test_list_by_since(self, initialized):
        """since filter with a past date returns all documents (all are newer)."""
        resp_all = server.brain_list()
        resp_since = server.brain_list(since="2020-01-01")
        assert _list_text(resp_since).count("\t") >= _list_text(resp_all).count("\t") - 1

        # since far in the future returns nothing
        resp_future = server.brain_list(since="2099-01-01")
        text = _list_text(resp_future)
        assert "0 results" in text

    def test_list_by_until(self, initialized):
        """until filter with a future date returns all; past date returns nothing."""
        resp_until = server.brain_list(until="2099-12-31")
        lines_all = _list_result_lines(server.brain_list())
        lines_until = _list_result_lines(resp_until)
        assert len(lines_until) == len(lines_all)

        resp_past = server.brain_list(until="2020-01-01")
        text = _list_text(resp_past)
        assert "0 results" in text

    def test_list_by_tag(self, initialized):
        """Tag filter returns only documents containing that tag."""
        resp = server.brain_list(tag="brain-core")
        lines = _list_result_lines(resp)
        assert len(lines) >= 1
        # Each result path should correspond to brain-overview (has brain-core tag)
        for line in lines:
            assert "brain-overview" in line or "brain-core" in line or "Wiki" in line

    def test_list_sort_date_asc(self, initialized):
        """date_asc sort returns dates in ascending order."""
        resp = server.brain_list(sort="date_asc")
        lines = _list_result_lines(resp)
        if len(lines) >= 2:
            dates = [line.split("\t")[0] for line in lines if "\t" in line]
            assert dates == sorted(dates)

    def test_list_sort_title(self, initialized):
        """title sort returns results in case-insensitive alphabetical title order."""
        resp = server.brain_list(type="living/wiki", sort="title")
        lines = _list_result_lines(resp)
        if len(lines) >= 2:
            titles = [line.split("\t")[1] for line in lines if line.count("\t") >= 1]
            assert titles == sorted(titles, key=str.lower)

    def test_list_top_k(self, initialized):
        """top_k=1 returns at most 1 result."""
        resp = server.brain_list(top_k=1)
        lines = _list_result_lines(resp)
        assert len(lines) <= 1

    def test_list_unknown_type(self, initialized):
        """Unknown type returns 0 results without raising an error."""
        resp = server.brain_list(type="living/nonexistent")
        text = _list_text(resp)
        assert "0 results" in text

    def test_list_result_shape(self, initialized):
        """Each result line is tab-separated: date, title, path, type[, status]."""
        resp = server.brain_list(type="living/wiki")
        lines = _list_result_lines(resp)
        assert len(lines) >= 1
        for line in lines:
            parts = line.split("\t")
            assert len(parts) >= 4
            # First column is a date string YYYY-MM-DD or empty
            assert len(parts[0]) == 0 or (len(parts[0]) == 10 and parts[0][4] == "-")
        assert server._INDEX_CHECK_TTL > server._ROUTER_CHECK_TTL
