"""Tests for Brain MCP server — unit tests with a minimal vault fixture."""

import asyncio
import json
import os
import subprocess
import threading
import time
from unittest.mock import patch

import pytest

from mcp.types import CallToolResult

from brain_mcp import server
import build_index
import compile_router
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
    (bc / "session-core.md").write_text(
        "# Session Core\n\n"
        "## Principles\n\n"
        "Keep instruction files lean.\n\n"
        "## Core Docs\n\n"
        "- [Extend the vault: add artefact types, memories, and principles](standards/extending/README.md)\n"
        "- [Browse the artefact library: type definitions and install guidance](artefact-library/README.md)\n\n"
        "## Standards\n\n"
        "- [Track provenance and lineage between artefacts](standards/provenance.md)\n"
        "- [Run the artefact shaping process](standards/shaping.md)\n\n"
        "Always:\n"
        "- Prefer `brain_list` for exhaustive enumeration.\n"
    )

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
        "## Naming\n\n`{Title}.md` in `Wiki/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/wiki\ntags:\n  - topic-tag\n---\n```\n\n"
        "## Template\n\n[[_Config/Templates/Living/Wiki]]\n"
    )

    tax_temporal = config / "Taxonomy" / "Temporal"
    tax_temporal.mkdir(parents=True)
    (tax_temporal / "logs.md").write_text(
        "# Logs\n\n"
        "## Naming\n\n`log-{Title}.md` in `_Temporal/Logs/yyyy-mm/`.\n\n"
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
        "## Naming\n\n`{Title}.md` in `Ideas/`.\n\n"
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
    core_skills_dir = bc / "skills" / "test-skill"
    core_skills_dir.mkdir(parents=True)
    (core_skills_dir / "SKILL.md").write_text(
        "---\nname: test-skill\n---\n\n"
        "# Test Skill (Core)\n\nA test core skill.\n"
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

    def test_startup_writes_session_markdown(self, vault):
        session_path = vault / ".brain" / "local" / "session.md"
        assert not session_path.exists()
        server.startup(vault_root=str(vault))
        # Shape 2: mirror write is async via the worker queue.
        server._mirror_queue.join()
        assert session_path.exists()
        content = session_path.read_text()
        assert "# Brain Session" in content
        assert "## Always Rules" in content


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

    def test_router_not_stale_after_keyed_living_body_change(self, vault):
        artefact = vault / "Wiki" / "Body.md"
        artefact.write_text(
            "---\n"
            "type: living/wiki\n"
            "key: body\n"
            "---\n\n"
            "# Body\n\n"
            "First body.\n"
        )
        server._compile_and_save(str(vault))
        artefact.write_text(
            "---\n"
            "type: living/wiki\n"
            "key: body\n"
            "---\n\n"
            "# Body\n\n"
            "Second body.\n"
        )

        stale, data = server._check_router(str(vault))

        assert stale is False
        assert data is not None

    def test_router_stale_after_keyed_living_key_change(self, vault):
        artefact = vault / "Wiki" / "Slug.md"
        artefact.write_text(
            "---\n"
            "type: living/wiki\n"
            "key: before\n"
            "---\n\n"
            "# Slug\n"
        )
        server._compile_and_save(str(vault))
        artefact.write_text(
            "---\n"
            "type: living/wiki\n"
            "key: after\n"
            "---\n\n"
            "# Slug\n"
        )

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
    def test_read_type_requires_name(self, initialized):
        result = server.brain_read("type")
        _assert_error(result, "requires name")

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

    def test_read_trigger_requires_name(self, initialized):
        result = server.brain_read("trigger")
        _assert_error(result, "requires name")

    def test_read_style_requires_name(self, initialized):
        result = server.brain_read("style")
        _assert_error(result, "requires name")

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

    def test_read_skill_requires_name(self, initialized):
        result = server.brain_read("skill")
        _assert_error(result, "requires name")

    def test_read_core_skill_content(self, initialized):
        result = server.brain_read("skill", name="test-skill")
        assert "Test Skill (Core)" in result

    def test_read_skill_content(self, initialized):
        result = server.brain_read("skill", name="Vault Maintenance")
        assert "Keep the vault tidy." in result

    def test_read_plugin_requires_name(self, initialized):
        result = server.brain_read("plugin")
        _assert_error(result, "requires name")

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

    def test_read_memory_requires_name(self):
        result = server.brain_read("memory")
        _assert_error(result, "requires name")

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


class TestBrainReadArchive:
    """Tests for brain_read(resource='archive')."""

    def _make_archived(self, vault, rel="_Archive/Ideas/20260101-old-idea.md"):
        p = vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            "---\ntype: living/ideas\ntags: []\nstatus: adopted\n"
            "archiveddate: 2026-01-01\n---\n\nOld idea.\n"
        )
        return rel

    def test_list_archives_via_brain_list(self, initialized):
        self._make_archived(initialized)
        result = server.brain_list(resource="archive")
        text = _search_text(result)
        assert "1 archive(s)" in text
        assert "_Archive/Ideas/20260101-old-idea.md" in text

    def test_list_empty_archive(self, initialized):
        result = server.brain_list(resource="archive")
        text = _search_text(result)
        assert "0 archive(s)" in text

    def test_read_archive_requires_name(self, initialized):
        result = server.brain_read("archive")
        _assert_error(result, "requires name")

    def test_read_specific_archive(self, initialized):
        rel = self._make_archived(initialized)
        result = server.brain_read("archive", name=rel)
        assert "Old idea." in result

    def test_read_non_archive_path_rejected(self, initialized):
        result = server.brain_read("archive", name="Ideas/my-idea.md")
        _assert_error(result, "not in _Archive")

    def test_list_legacy_per_type_archives(self, initialized):
        """Per-type _Archive/ dirs are also scanned."""
        self._make_archived(initialized, "Ideas/_Archive/20260101-legacy.md")
        result = server.brain_list(resource="archive")
        text = _search_text(result)
        assert "1 archive(s)" in text
        assert "Ideas/_Archive/20260101-legacy.md" in text


class TestArchiveGuardsMcp:
    """Verify brain_read and brain_edit reject archived paths at the MCP boundary."""

    def _make_archived(self, vault, rel="Ideas/_Archive/20260101-old-idea.md"):
        p = vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            "---\ntype: living/ideas\ntags: []\nstatus: adopted\n"
            "archiveddate: 2026-01-01\n---\n\nOld idea.\n"
        )
        return rel

    def test_read_artefact_rejects_archived_path(self, initialized):
        rel = self._make_archived(initialized)
        result = server.brain_read("artefact", name=rel)
        _assert_error(result, "archived")

    def test_read_file_rejects_archived_path(self, initialized):
        rel = self._make_archived(initialized)
        result = server.brain_read("file", name=rel)
        _assert_error(result, "archived")

    def test_read_artefact_rejects_top_level_archive(self, initialized):
        rel = self._make_archived(initialized, "_Archive/Ideas/20260101-old-idea.md")
        result = server.brain_read("artefact", name=rel)
        _assert_error(result, "archived")

    def test_edit_rejects_archived_path(self, initialized):
        rel = self._make_archived(initialized)
        result = server.brain_edit(operation="edit", path=rel, body="new body")
        _assert_error(result, "archived")


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

    def test_search_cli_excludes_archived_paths(self, initialized, cli_available):
        """Verify archived paths are stripped from CLI search results."""
        cli_results = [
            "Wiki/brain-overview-abc123.md",
            "_Archive/Ideas/Brain/20260101-old-idea.md",
            "Ideas/_Archive/20260202-another.md",
        ]
        with patch.object(obsidian_cli, "search", return_value=cli_results), \
             patch.object(obsidian_cli, "check_available", return_value=True):
            server._cli_probed_at = 0.0
            resp = server.brain_search("brain")
            text = _search_text(resp)
            assert "obsidian_cli" in text
            assert "_Archive" not in text

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
        edit_result = server.brain_edit(
            operation="edit",
            path=created_path,
            body="Now has qwertymorphic content.",
            target=":body",
            scope="section",
        )
        assert "Error" not in edit_result
        # Verify the file on disk has the new content
        file_path = initialized / created_path
        assert "qwertymorphic" in file_path.read_text()
        text = _search_text(server.brain_search("qwertymorphic"))
        assert "1 results" in text

    def test_search_resource_skill(self, initialized):
        """brain_search(resource='skill') searches skills by text."""
        resp = server.brain_search("vault", resource="skill")
        text = _search_text(resp)
        assert "text" in text  # source should be "text"
        lines = _search_result_lines(resp)
        assert len(lines) >= 1
        assert "Vault Maintenance" in text or "vault-maintenance" in text.lower()

    def test_search_resource_trigger(self, initialized):
        """brain_search(resource='trigger') searches triggers."""
        resp = server.brain_search("log", resource="trigger")
        text = _search_text(resp)
        assert "text" in text

    def test_search_resource_default_artefact(self, initialized):
        """Default resource='artefact' uses BM25 as before."""
        resp = server.brain_search("brain")
        text = _search_text(resp)
        assert "bm25" in text


# ---------------------------------------------------------------------------
# brain_action tests
# ---------------------------------------------------------------------------

class TestBrainAction:
    def test_action_compile(self, initialized):
        result = server.brain_action("compile")
        assert result.startswith("**Compiled:**")

    def test_action_compile_refreshes_session_markdown(self, initialized):
        session_path = initialized / ".brain" / "local" / "session.md"
        server._mirror_queue.join()  # drain any pending fixture startup refresh
        original = session_path.read_text()

        user_dir = initialized / "_Config" / "User"
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "preferences-always.md").write_text(
            "---\ntype: user-preferences\n---\n\nCompile refreshes the session mirror.\n"
        )

        result = server.brain_action("compile")
        server._mirror_queue.join()  # wait for background refresh to land

        assert result.startswith("**Compiled:**")
        updated = session_path.read_text()
        assert "Compile refreshes the session mirror." in updated
        assert updated != original

    def test_action_compile_does_not_emit_startup_session_phase(self, initialized):
        log_path = initialized / ".brain" / "local" / "mcp-server.log"
        before = log_path.read_text().count("startup phase begin: session_mirror_refresh")

        result = server.brain_action("compile")

        assert result.startswith("**Compiled:**")
        after = log_path.read_text().count("startup phase begin: session_mirror_refresh")
        assert after == before

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
            mock_move.assert_called_once()
            assert any(
                call.args[0].endswith("Wiki/subdir")
                for call in mock_makedirs.call_args_list
            )


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

    def test_no_exit_when_version_matches(self, initialized):
        """_check_version_drift should be a no-op when version is unchanged."""
        old_version = server._loaded_version
        server._check_version_drift()
        assert server._loaded_version == old_version

    def test_exits_with_code_10_when_version_changes(self, initialized):
        """_check_version_drift should call os._exit(10) when version differs."""
        version_path = initialized / ".brain-core" / "VERSION"
        version_path.write_text("99.0.0\n")
        with patch("os._exit") as mock_exit:
            server._check_version_drift()
            mock_exit.assert_called_once_with(10)

    def test_no_exit_when_version_file_missing(self, initialized):
        """_check_version_drift should be a no-op if VERSION file is deleted."""
        version_path = initialized / ".brain-core" / "VERSION"
        version_path.unlink()
        old_version = server._loaded_version
        server._check_version_drift()
        assert server._loaded_version == old_version


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

        with patch("_common._filesystem.os.replace", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                server._save_json({"corrupt": True}, str(tmp_path), "data.json")

        # Original file should be untouched
        result = json.loads(path.read_text())
        assert result == original

    def test_save_json_cleans_up_temp_on_failure(self, tmp_path):
        """No .tmp files should remain after a failed write."""
        (tmp_path / "sub").mkdir()
        server._save_json({"ok": True}, str(tmp_path), "sub/data.json")

        with patch("_common._filesystem.os.replace", side_effect=OSError("replace failed")):
            with pytest.raises(OSError, match="replace failed"):
                server._save_json({"bad": True}, str(tmp_path), "sub/data.json")

        tmp_files = list((tmp_path / "sub").glob("*.tmp"))
        assert tmp_files == [], f"Temp files not cleaned up: {tmp_files}"


# ---------------------------------------------------------------------------
# Reload robustness
# ---------------------------------------------------------------------------

class TestReloadRobustness:
    def test_version_drift_causes_clean_exit(self, initialized):
        """Version drift should call os._exit(10) for proxy restart."""
        version_path = initialized / ".brain-core" / "VERSION"
        version_path.write_text("99.0.0\n")
        with patch("os._exit") as mock_exit:
            server._check_version_drift()
            mock_exit.assert_called_once_with(10)

    def test_check_version_drift_survives_read_error(self, initialized):
        """_check_version_drift should not raise on version read errors."""
        version_path = initialized / ".brain-core" / "VERSION"
        version_path.write_text("99.0.0\n")

        with patch("brain_mcp.server._read_disk_version", side_effect=Exception("unexpected")):
            server._check_version_drift()  # should not raise


class TestEnsureFreshRobustness:
    def test_ensure_router_fresh_refreshes_session_markdown(self, initialized):
        session_path = initialized / ".brain" / "local" / "session.md"
        server._mirror_queue.join()
        original = session_path.read_text()

        user_dir = initialized / "_Config" / "User"
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "preferences-always.md").write_text(
            "---\ntype: user-preferences\n---\n\nEnsure fresh refreshes the session mirror.\n"
        )

        time.sleep(0.1)
        router_md = initialized / "_Config" / "router.md"
        router_md.write_text(router_md.read_text() + "\n- Recompile for ensure_fresh.\n")
        server._router_checked_at = 0.0

        server._ensure_router_fresh()
        server._mirror_queue.join()

        updated = session_path.read_text()
        assert "Ensure fresh refreshes the session mirror." in updated
        assert updated != original

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

        with patch("brain_mcp.server.build_index.index_update", side_effect=OSError("boom")):
            server._ensure_index_fresh()

        assert server._index_dirty, "Index should be marked dirty after incremental failure"


class TestStartupRobustness:
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
        with patch("brain_mcp.server.compile_router.find_vault_root", side_effect=FileNotFoundError("no vault")), \
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
            "## Naming\n\n`{Title}.md` in `Glossary/`.\n\n"
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

    def test_new_keyed_living_file_triggers_recompile(self, initialized):
        """Adding a new keyed living file should invalidate the router."""
        compiled_at = server._router["meta"]["compiled_at"]
        artefact = initialized / "Wiki" / "Fresh.md"
        artefact.write_text(
            "---\n"
            "type: living/wiki\n"
            "key: fresh\n"
            "---\n\n"
            "# Fresh\n"
        )

        server._ensure_router_fresh()

        assert server._router["meta"]["compiled_at"] != compiled_at
        assert "wiki/fresh" in server._router["artefact_index"]


# ---------------------------------------------------------------------------
# Resource-mtime cache (β₃ short-circuit for resource_counts walks)
# ---------------------------------------------------------------------------

class TestResourceMtimeCache:
    @pytest.fixture(autouse=True)
    def _reset_cache(self):
        server._resource_mtime_cache = None
        yield
        server._resource_mtime_cache = None

    @pytest.fixture
    def resource_counts_calls(self, monkeypatch):
        """Monkeypatch compile_router.resource_counts to count invocations."""
        calls = {"n": 0}
        real = compile_router.resource_counts

        def counting(vault_root):
            calls["n"] += 1
            return real(vault_root)

        monkeypatch.setattr(compile_router, "resource_counts", counting)
        return calls

    def test_signature_stable_across_noop_calls(self, initialized):
        sig1 = server._resource_mtime_signature(str(initialized))
        sig2 = server._resource_mtime_signature(str(initialized))
        assert sig1 == sig2
        assert len(sig1) > 0

    def test_first_call_walks_then_caches(self, initialized, resource_counts_calls):
        server._check_router_resource_counts(str(initialized), server._router)
        assert resource_counts_calls["n"] == 1
        assert server._resource_mtime_cache is not None

        server._check_router_resource_counts(str(initialized), server._router)
        assert resource_counts_calls["n"] == 1

    def test_new_artefact_file_invalidates_cache(self, initialized, resource_counts_calls):
        server._check_router_resource_counts(str(initialized), server._router)
        assert resource_counts_calls["n"] == 1

        time.sleep(0.05)
        (initialized / "Ideas" / "new-idea-abc123.md").write_text(
            "---\ntype: living/ideas\nslug: new-idea-abc123\n---\n# New Idea\n"
        )

        server._check_router_resource_counts(str(initialized), server._router)
        assert resource_counts_calls["n"] == 2

    def test_new_nested_artefact_file_invalidates_cache(
        self, initialized, resource_counts_calls
    ):
        server._check_router_resource_counts(str(initialized), server._router)
        assert resource_counts_calls["n"] == 1

        time.sleep(0.05)
        nested = initialized / "Ideas" / "2026-04" / "nested-idea-xyz789.md"
        nested.parent.mkdir(parents=True, exist_ok=True)
        nested.write_text(
            "---\ntype: living/ideas\nslug: nested-idea-xyz789\n---\n# Nested\n"
        )

        server._check_router_resource_counts(str(initialized), server._router)
        assert resource_counts_calls["n"] == 2

    def test_new_living_type_folder_invalidates_cache(
        self, initialized, resource_counts_calls
    ):
        server._check_router_resource_counts(str(initialized), server._router)
        assert resource_counts_calls["n"] == 1

        time.sleep(0.05)
        (initialized / "Projects").mkdir()

        server._check_router_resource_counts(str(initialized), server._router)
        assert resource_counts_calls["n"] == 2

    def test_archive_write_does_not_invalidate_cache(
        self, initialized, resource_counts_calls
    ):
        server._check_router_resource_counts(str(initialized), server._router)
        assert resource_counts_calls["n"] == 1

        time.sleep(0.05)
        archive = initialized / "Ideas" / "_Archive" / "2026-04"
        archive.mkdir(parents=True, exist_ok=True)
        (archive / "old-idea-abc123.md").write_text(
            "---\ntype: living/ideas\nslug: old-idea-abc123\n---\n# Old\n"
        )

        server._check_router_resource_counts(str(initialized), server._router)
        assert resource_counts_calls["n"] == 1

    def test_skill_md_delete_inside_subdir_invalidates_cache(self, initialized):
        skill_dir = initialized / "_Config" / "Skills" / "demo-for-mtime"
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("# Demo\n")

        sig_before = server._resource_mtime_signature(str(initialized))
        time.sleep(0.05)
        skill_md.unlink()
        sig_after = server._resource_mtime_signature(str(initialized))

        assert sig_before != sig_after

    def test_missing_resource_dirs_encode_as_none(self, tmp_path):
        sig = server._resource_mtime_signature(str(tmp_path))
        by_key = dict(sig)
        assert by_key[""] is not None
        for rel in ("_Temporal", "_Config/Styles", "_Config/Memories",
                    "_Config/Skills", ".brain-core/skills", "_Plugins"):
            assert by_key[rel] is None

    def test_index_count_failure_leaves_cache_untouched(self, initialized, monkeypatch):
        server._check_router_resource_counts(str(initialized), server._router)
        cached_before = server._resource_mtime_cache
        assert cached_before is not None

        time.sleep(0.05)
        (initialized / "Ideas" / "trigger-abc123.md").write_text(
            "---\ntype: living/ideas\nslug: trigger-abc123\n---\n# t\n"
        )

        def boom(vault_root, artefacts):
            raise RuntimeError("index count blew up")

        monkeypatch.setattr(
            compile_router, "count_living_artefact_index_entries", boom
        )
        assert server._check_router_resource_counts(
            str(initialized), server._router
        ) is True
        assert server._resource_mtime_cache == cached_before


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

    def test_create_living_with_explicit_key(self, initialized):
        result = server.brain_create(type="wiki", title="Slugged Page", key="slugged-page")
        path = _extract_create_path(result)
        with open(os.path.join(str(initialized), path)) as f:
            content = f.read()
        from _common import parse_frontmatter
        fields, _ = parse_frontmatter(content)
        assert fields["key"] == "slugged-page"

    def test_create_with_canonical_parent(self, initialized):
        parent_result = server.brain_create(type="wiki", title="Parent Page", key="parent-page")
        assert parent_result.startswith("**Created** living/wiki: ")
        child_result = server.brain_create(
            type="ideas", title="Child Idea", parent="wiki/parent-page"
        )
        child_path = _extract_create_path(child_result)
        assert child_path.startswith("Ideas/wiki~parent-page/")
        with open(os.path.join(str(initialized), child_path)) as f:
            content = f.read()
        from _common import parse_frontmatter
        fields, _ = parse_frontmatter(content)
        assert fields["parent"] == "wiki/parent-page"

    def test_create_skill_resource(self, initialized):
        result = server.brain_create(
            resource="skill", name="test-skill",
            body="# Test Skill\n\nDo something.\n",
        )
        assert "**Created** skill:" in result
        path = _extract_create_path(result)
        assert path == "_Config/Skills/test-skill/SKILL.md"
        assert os.path.isfile(os.path.join(str(initialized), path))

    def test_create_memory_resource(self, initialized):
        result = server.brain_create(
            resource="memory", name="test-memory",
            body="Remember this.\n",
            frontmatter={"triggers": ["keyword"]},
        )
        assert "**Created** memory:" in result
        path = _extract_create_path(result)
        assert path == "_Config/Memories/test-memory.md"

    def test_create_style_resource(self, initialized):
        result = server.brain_create(
            resource="style", name="test-style",
            body="# Test Style\n\nWrite this way.\n",
        )
        assert "**Created** style:" in result
        path = _extract_create_path(result)
        assert path == "_Config/Styles/test-style.md"

    def test_create_resource_not_creatable(self, initialized):
        result = server.brain_create(
            resource="workspace", name="ws", body="content",
        )
        _assert_error(result, "not creatable")

    def test_create_artefact_requires_type(self, initialized):
        result = server.brain_create(title="No Type")
        _assert_error(result, "type is required")

    def test_create_artefact_requires_title(self, initialized):
        result = server.brain_create(type="wiki")
        _assert_error(result, "title is required")


# ---------------------------------------------------------------------------
# brain_edit tests
# ---------------------------------------------------------------------------

class TestBrainEdit:
    def test_brain_edit_schema_exposes_scope_and_resource_enums(self):
        tool = asyncio.run(server.mcp.list_tools())
        brain_edit_tool = next(item for item in tool if item.name == "brain_edit")
        schema = brain_edit_tool.inputSchema
        props = schema["properties"]

        assert props["resource"]["enum"] == [
            "artefact",
            "skill",
            "memory",
            "style",
            "template",
        ]
        assert props["scope"]["anyOf"] == [
            {
                "enum": ["section", "intro", "body", "heading", "header"],
                "type": "string",
            },
            {"type": "null"},
        ]

    def test_edit_replaces_body(self, initialized):
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            body="# New Body\n\nReplaced.\n",
            target=":body",
            scope="section",
        )
        assert result == "**Edited:** Wiki/brain-overview-abc123.md (body section)"
        content = (initialized / "Wiki" / "brain-overview-abc123.md").read_text()
        assert "Replaced." in content

    def test_edit_preserves_frontmatter(self, initialized):
        server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            body="# New\n",
            target=":body",
            scope="section",
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
            frontmatter={"status": "archived"},
            target=":body",
            scope="section",
        )
        content = (initialized / "Wiki" / "brain-overview-abc123.md").read_text()
        from _common import parse_frontmatter
        fields, _ = parse_frontmatter(content)
        assert fields["status"] == "archived"

    def test_append_works(self, initialized):
        result = server.brain_edit(
            operation="append",
            path="Wiki/brain-overview-abc123.md",
            body="\n\nAppended text.\n",
            target=":body",
            scope="section",
        )
        assert result == "**Appended:** Wiki/brain-overview-abc123.md (body section)"
        content = (initialized / "Wiki" / "brain-overview-abc123.md").read_text()
        assert "Appended text." in content
        assert "Brain Overview" in content  # original preserved

    def test_invalid_path_rejected(self, initialized):
        result = server.brain_edit(
            operation="edit",
            path="Unknown/file.md",
            body="test",
            target=":body",
            scope="section",
        )
        _assert_error(result)

    def test_file_not_found(self, initialized):
        result = server.brain_edit(
            operation="edit",
            path="Wiki/nonexistent.md",
            body="test",
            target=":body",
            scope="section",
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
            body="Prepended text.\n\n",
            target=":body",
            scope="section",
        )
        assert result == "**Prepended:** Wiki/brain-overview-abc123.md (body section)"
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

    def test_noop_append_with_entire_body_target_rejected(self, initialized):
        result = server.brain_edit(
            operation="append",
            path="Wiki/brain-overview-abc123.md",
            target=":entire_body",
        )
        _assert_error(result, "target=':entire_body' is no longer valid")

    def test_noop_prepend_rejected(self, initialized):
        result = server.brain_edit(
            operation="prepend",
            path="Wiki/brain-overview-abc123.md",
        )
        _assert_error(result, "no-op")

    def test_noop_prepend_with_entire_body_target_rejected(self, initialized):
        result = server.brain_edit(
            operation="prepend",
            path="Wiki/brain-overview-abc123.md",
            target=":entire_body",
        )
        _assert_error(result, "target=':entire_body' is no longer valid")

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
            scope="body",
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

    def test_targeted_frontmatter_only_append_omits_structural_summary(self, initialized):
        from _common import parse_frontmatter

        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: [overview]\nstatus: active\n---\n\n"
            "## Alpha\n\nBody.\n"
        )
        result = server.brain_edit(
            operation="append",
            path="Wiki/brain-overview-abc123.md",
            frontmatter={"tags": ["server-tag"]},
            target="## Alpha",
            scope="body",
        )
        assert result == "**Appended:** Wiki/brain-overview-abc123.md"
        fields, body = parse_frontmatter(path.read_text())
        assert "server-tag" in fields["tags"]
        assert body == "## Alpha\n\nBody.\n"

    def test_targeted_frontmatter_only_prepend_omits_structural_summary(self, initialized):
        from _common import parse_frontmatter

        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: [overview]\nstatus: active\n---\n\n"
            "## Alpha\n\nBody.\n"
        )
        result = server.brain_edit(
            operation="prepend",
            path="Wiki/brain-overview-abc123.md",
            frontmatter={"tags": ["server-tag"]},
            target="## Alpha",
            scope="body",
        )
        assert result == "**Prepended:** Wiki/brain-overview-abc123.md"
        fields, body = parse_frontmatter(path.read_text())
        assert "server-tag" in fields["tags"]
        assert body == "## Alpha\n\nBody.\n"

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

    def test_delete_section_removes_heading_and_content(self, initialized):
        """delete_section removes the target heading and its content."""
        path = "Wiki/brain-overview-abc123.md"
        # Set up a file with multiple sections
        server.brain_edit(
            operation="edit",
            path=path,
            target=":body",
            scope="section",
            body="## Intro\n\nIntro content.\n\n## Notes\n\nNotes content.\n\n## Summary\n\nSummary content.\n"
        )
        result = server.brain_edit(
            operation="delete_section",
            path=path,
            target="Notes"
        )
        assert "Error" not in str(result)
        content = (initialized / path).read_text()
        assert "## Notes" not in content
        assert "Notes content." not in content
        assert "## Intro" in content
        assert "## Summary" in content

    def test_delete_section_requires_target(self, initialized):
        """delete_section with no target returns an error."""
        result = server.brain_edit(
            operation="delete_section",
            path="Wiki/brain-overview-abc123.md",
        )
        _assert_error(result, "target")

    def test_delete_section_missing_heading_returns_error(self, initialized):
        """delete_section with a non-existent heading returns an error."""
        result = server.brain_edit(
            operation="delete_section",
            path="Wiki/brain-overview-abc123.md",
            target="Nonexistent Heading"
        )
        _assert_error(result, "not found")

    def test_targeted_edit_mentions_resolved_scope(self, initialized):
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
            scope="body",
        )
        assert result == (
            "**Edited:** Wiki/brain-overview-abc123.md "
            "(heading body: ## Beta)"
        )

    def test_body_section_edit_returns_resolved_summary(self, initialized):
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "One.\n\nTwo.\n"
        )
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            target=":body",
            scope="section",
            body="Replacement.\n",
        )
        assert result == "**Edited:** Wiki/brain-overview-abc123.md (body section)"

    def test_body_target_response_uses_scope_not_context(self, initialized):
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "Intro text.\n\n## Alpha\n\nAlpha content.\n"
        )
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            target=":body",
            scope="section",
            body="Replacement.\n",
        )
        assert result == "**Edited:** Wiki/brain-overview-abc123.md (body section)"

    def test_body_section_append_and_prepend_work_explicitly(self, initialized):
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\nBody.\n"
        )
        prepend_result = server.brain_edit(
            operation="prepend",
            path="Wiki/brain-overview-abc123.md",
            target=":body",
            scope="section",
            body="Before.\n\n",
        )
        append_result = server.brain_edit(
            operation="append",
            path="Wiki/brain-overview-abc123.md",
            target=":body",
            scope="section",
            body="\nAfter.\n",
        )
        assert prepend_result == "**Prepended:** Wiki/brain-overview-abc123.md (body section)"
        assert append_result == "**Appended:** Wiki/brain-overview-abc123.md (body section)"
        content = path.read_text()
        assert "Before." in content
        assert "After." in content

    def test_body_intro_replaces_only_heading_defined_intro(self, initialized):
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "Intro text.\n\n"
            "> [!note] Status\n"
            "> Status content.\n"
            "\n"
            "## Alpha\n\nAlpha content.\n"
        )
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            target=":body",
            scope="intro",
            body="Updated intro.\n",
        )
        assert result == "**Edited:** Wiki/brain-overview-abc123.md (body intro)"
        content = path.read_text()
        assert "Updated intro.\n## Alpha" in content
        assert "Intro text." not in content
        assert "> [!note] Status" not in content
        assert "## Alpha" in content
        assert "Alpha content." in content

    def test_body_intro_inserts_before_heading_first_doc(self, initialized):
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n"
        )
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            target=":body",
            scope="intro",
            body="Lead text.\n",
        )
        assert result == "**Edited:** Wiki/brain-overview-abc123.md (body intro)"
        content = path.read_text()
        assert "Lead text.\n## Alpha" in content

    def test_body_intro_replaces_whole_body_without_headings(self, initialized):
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "> [!note] Status\n"
            "> Status content.\n"
        )
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            target=":body",
            scope="intro",
            body="Lead text.\n",
        )
        assert result == "**Edited:** Wiki/brain-overview-abc123.md (body intro)"
        content = path.read_text()
        from _common import parse_frontmatter
        _fields, body = parse_frontmatter(content)
        assert body == "Lead text.\n"

    def test_body_target_requires_scope_for_mutations(self, initialized):
        edit_result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            target=":body",
            body="Replacement.\n",
        )
        _assert_error(edit_result, "requires scope")

        append_result = server.brain_edit(
            operation="append",
            path="Wiki/brain-overview-abc123.md",
            target=":body",
            body="Extra.\n",
        )
        _assert_error(append_result, "requires scope")

        prepend_result = server.brain_edit(
            operation="prepend",
            path="Wiki/brain-overview-abc123.md",
            target=":body",
            body="Extra.\n",
        )
        _assert_error(prepend_result, "requires scope")

        delete_result = server.brain_edit(
            operation="delete_section",
            path="Wiki/brain-overview-abc123.md",
            target=":body",
        )
        _assert_error(delete_result, "delete_section requires a heading or callout target")

    def test_legacy_body_before_first_heading_target_rejected(self, initialized):
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            target=":body_before_first_heading",
            body="Replacement.\n",
        )
        _assert_error(result, "target=':body_before_first_heading' is no longer valid")

    def test_legacy_body_preamble_rejected_for_append_and_prepend(self, initialized):
        append_result = server.brain_edit(
            operation="append",
            path="Wiki/brain-overview-abc123.md",
            target=":body_preamble",
            body="Extra.\n",
        )
        _assert_error(append_result, "Use target=':body' with scope='intro'")

        prepend_result = server.brain_edit(
            operation="prepend",
            path="Wiki/brain-overview-abc123.md",
            target=":body_preamble",
            body="Extra.\n",
        )
        _assert_error(prepend_result, "Use target=':body' with scope='intro'")

    def test_targeted_edit_heading_body_rejects_heading_wrapper(self, initialized):
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n"
        )
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            target="## Alpha",
            scope="body",
            body="## Alpha\n\nUpdated alpha.\n",
        )
        _assert_error(result, "Use scope='section'")

    def test_targeted_edit_rejects_structural_change_without_section_mode(self, initialized):
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n"
        )
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            target="## Alpha",
            scope="body",
            body="# Alpha\n\nPromoted.\n",
        )
        _assert_error(result, "Use scope='section'")

    def test_targeted_edit_allows_nested_heading_content(self, initialized):
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n"
        )
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            target="## Alpha",
            scope="body",
            body="### Overview\n\nPromoted content.\n",
        )
        assert "Error" not in str(result)
        content = path.read_text()
        assert "## Alpha" in content
        assert "### Overview" in content

    def test_targeted_edit_allows_callout_content(self, initialized):
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n"
        )
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            target="## Alpha",
            scope="body",
            body="> [!note] Fresh note\n> Promoted content.\n",
        )
        assert "Error" not in str(result)
        content = path.read_text()
        assert "## Alpha" in content
        assert "[!note] Fresh note" in content

    def test_targeted_edit_section_mode_replaces_heading(self, initialized):
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Alpha\n\nAlpha content.\n\n## Beta\n\nBeta content.\n"
        )
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            target="## Alpha",
            scope="section",
            body="# Renamed Alpha\n\nUpdated alpha.\n",
        )
        assert result == (
            "**Edited:** Wiki/brain-overview-abc123.md "
            "(heading section: ## Alpha)"
        )
        content = path.read_text()
        assert "## Alpha" not in content
        assert "# Renamed Alpha" in content
        assert "## Beta" in content

    def test_callout_header_response_mentions_resolved_scope(self, initialized):
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "## Status\n\n"
            "> [!note] Implementation status\n"
            "> Old status content.\n"
        )
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            target="[!note] Implementation status",
            scope="header",
            body="> [!warning] Updated status\n",
        )
        assert result == (
            "**Edited:** Wiki/brain-overview-abc123.md "
            "(callout header: [!note] Implementation status)"
        )
        content = path.read_text()
        assert "[!warning] Updated status" in content

    def test_selector_disambiguates_duplicate_targets(self, initialized):
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "# API\n\n"
            "## Notes\n\nFirst notes.\n\n"
            "# API\n\n"
            "## Notes\n\nSecond notes.\n"
        )
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            target="## Notes",
            selector={"within": [{"target": "# API", "occurrence": 2}]},
            scope="body",
            body="Selected notes.\n",
        )
        assert result == (
            "**Edited:** Wiki/brain-overview-abc123.md "
            "(heading body: # API [2] > ## Notes)"
        )
        content = path.read_text()
        assert "First notes." in content
        assert "Selected notes." in content
        assert "Second notes." not in content

    def test_ambiguous_target_reports_candidates(self, initialized):
        path = initialized / "Wiki" / "brain-overview-abc123.md"
        path.write_text(
            "---\ntype: living/wiki\ntags: []\n---\n\n"
            "# API\n\n"
            "## Notes\n\nFirst notes.\n\n"
            "# API\n\n"
            "## Notes\n\nSecond notes.\n"
        )
        result = server.brain_edit(
            operation="edit",
            path="Wiki/brain-overview-abc123.md",
            target="## Notes",
            scope="body",
            body="Selected notes.\n",
        )
        _assert_error(result, "Ambiguous target '## Notes'")
        _assert_error(result, "Candidates:")

    def test_edit_skill_resource(self, initialized):
        # Create a skill first
        skill_dir = initialized / "_Config" / "Skills" / "test-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            "---\n---\n\n# Test Skill\n\nOriginal.\n"
        )
        result = server.brain_edit(
            resource="skill", operation="edit", name="test-skill",
            body="# Updated Skill\n\nNew content.\n",
            target=":body",
            scope="section",
        )
        assert "**Edited:**" in result
        assert "_Config/Skills/test-skill/SKILL.md" in result
        content = (skill_dir / "SKILL.md").read_text()
        assert "New content." in content

    def test_edit_memory_resource(self, initialized):
        mem_dir = initialized / "_Config" / "Memories"
        mem_dir.mkdir(parents=True, exist_ok=True)
        (mem_dir / "test-memory.md").write_text(
            "---\ntriggers:\n  - kw1\n---\n\nOriginal.\n"
        )
        result = server.brain_edit(
            resource="memory", operation="append", name="test-memory",
            body="\nAppended.\n",
            target=":body",
            scope="section",
        )
        assert "**Appended:**" in result

    def test_edit_memory_trigger_is_immediately_readable(self, initialized):
        mem_dir = initialized / "_Config" / "Memories"
        mem_dir.mkdir(parents=True, exist_ok=True)
        (mem_dir / "test-memory.md").write_text(
            "---\ntriggers:\n  - kw1\n---\n\nOriginal.\n"
        )
        server.brain_action("compile")

        result = server.brain_edit(
            resource="memory",
            operation="append",
            name="test-memory",
            frontmatter={"triggers": ["new-trigger"]},
        )

        assert "**Appended:**" in result
        read_result = server.brain_read("memory", name="new-trigger")
        assert "Original." in read_result

    def test_edit_memory_does_not_pollute_artefact_search(self, initialized):
        mem_dir = initialized / "_Config" / "Memories"
        mem_dir.mkdir(parents=True, exist_ok=True)
        (mem_dir / "test-memory.md").write_text(
            "---\ntriggers:\n  - kw1\n---\n\nOriginal.\n"
        )
        server.brain_action("compile")

        baseline = _search_text(server.brain_search("xenocrypticmemorytoken"))
        assert "0 results" in baseline

        result = server.brain_edit(
            resource="memory",
            operation="append",
            name="test-memory",
            body="\nContains xenocrypticmemorytoken.\n",
            target=":body",
            scope="section",
        )

        assert "**Appended:**" in result
        search_result = _search_text(server.brain_search("xenocrypticmemorytoken"))
        assert "0 results" in search_result
        assert "_Config/Memories/test-memory.md" not in search_result

    def test_edit_resource_not_editable(self, initialized):
        result = server.brain_edit(
            resource="workspace", operation="edit", name="ws",
            body="content",
        )
        _assert_error(result, "not editable")

    def test_edit_artefact_requires_path(self, initialized):
        result = server.brain_edit(
            operation="edit", body="content",
        )
        _assert_error(result, "path is required")


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

    @staticmethod
    def _fake_marp_run(cmd, capture_output, text):
        pdf_path = cmd[cmd.index("-o") + 1]
        os.makedirs(os.path.dirname(pdf_path), exist_ok=True)
        with open(pdf_path, "wb") as f:
            f.write(b"%PDF-1.4\n")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    @patch("shape_presentation.subprocess.Popen")
    @patch("shape_presentation.subprocess.run")
    def test_creates_file_and_returns_status(self, mock_run, mock_popen, initialized):
        mock_run.side_effect = self._fake_marp_run
        mock_popen.return_value.pid = 12345
        result = json.loads(server.brain_action("shape-presentation", {
            "source": "Wiki/brain-overview-abc123.md",
            "slug": "test-deck",
        }))
        assert result["status"] == "ok"
        assert "presentation" in result["path"]
        assert "test-deck" in result["path"]
        assert result["created"] is True
        assert result["rendered"] is True
        assert "pdf_path" in result
        assert result["preview_pid"] == 12345
        # Verify file was created on disk
        abs_path = os.path.join(str(initialized), result["path"])
        pdf_abs = os.path.join(str(initialized), result["pdf_path"])
        assert os.path.isfile(abs_path)
        assert os.path.isfile(pdf_abs)

    @patch("shape_presentation.subprocess.Popen")
    @patch("shape_presentation.subprocess.run")
    def test_does_not_recreate_existing_file(self, mock_run, mock_popen, initialized):
        mock_run.side_effect = self._fake_marp_run
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

    @patch("shape_presentation.subprocess.run", side_effect=FileNotFoundError)
    def test_works_without_marp_installed(self, mock_run, initialized):
        """Should create markdown but return partial when Marp is unavailable."""
        result = json.loads(server.brain_action("shape-presentation", {
            "source": "Wiki/brain-overview-abc123.md",
            "slug": "no-marp",
        }))
        assert result["status"] == "partial"
        assert result["rendered"] is False
        assert "marp" in result["warning"]
        assert "preview_pid" not in result


# ---------------------------------------------------------------------------
# brain_action shape-printable tests
# ---------------------------------------------------------------------------

class TestBrainActionShapePrintable:
    @pytest.fixture(autouse=True)
    def setup_printable_files(self, initialized):
        """Add printable template and support files to the vault fixture."""
        self.vault = initialized
        templates_dir = initialized / "_Config" / "Templates" / "Temporal"
        templates_dir.mkdir(parents=True, exist_ok=True)
        (templates_dir / "Printables.md").write_text(
            "---\ntype: temporal/printable\ntags:\n  - printable\n"
            "keep_heading_with_next: true\n---\n\n"
            "# PRINTABLE TITLE\n\n"
            "**{{date:YYYY-MM-DD}}**\n\n"
            "**Origin:** [[source-artefact|Source document]]\n\n"
            "## Summary\n\n"
            "Summary text.\n"
        )
        skills_dir = initialized / "_Config" / "Skills" / "printables"
        skills_dir.mkdir(parents=True, exist_ok=True)
        (skills_dir / "base.tex").write_text("\\usepackage{parskip}\n")
        (skills_dir / "keep-headings.tex").write_text("\\usepackage{needspace}\n")

    def test_missing_params_returns_error(self, initialized):
        result = server.brain_action("shape-printable")
        _assert_error(result)

    def test_missing_source_returns_error(self, initialized):
        result = server.brain_action("shape-printable", {"slug": "brief"})
        _assert_error(result)

    def test_missing_slug_returns_error(self, initialized):
        result = server.brain_action("shape-printable", {"source": "Wiki/brain-overview-abc123.md"})
        _assert_error(result)

    def test_source_not_found_returns_error(self, initialized):
        result = server.brain_action("shape-printable", {
            "source": "Wiki/nonexistent.md",
            "slug": "brief",
        })
        _assert_error(result)

    @staticmethod
    def _fake_which_default(cmd):
        if cmd in {"pandoc", "xelatex"}:
            return f"/usr/bin/{cmd}"
        return None

    @staticmethod
    def _fake_pandoc_run(cmd, capture_output, text):
        pdf_path = cmd[cmd.index("--output") + 1]
        os.makedirs(os.path.dirname(pdf_path), exist_ok=True)
        with open(pdf_path, "wb") as f:
            f.write(b"%PDF-1.4\n")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    @patch("shape_printable.subprocess.run")
    @patch("shape_printable.shutil.which")
    def test_creates_file_and_renders_pdf(self, mock_which, mock_run, initialized):
        mock_which.side_effect = self._fake_which_default
        mock_run.side_effect = self._fake_pandoc_run

        result = json.loads(server.brain_action("shape-printable", {
            "source": "Wiki/brain-overview-abc123.md",
            "slug": "board-brief",
        }))
        assert result["status"] == "ok"
        assert "printable" in result["path"]
        assert "board-brief" in result["path"]
        assert result["created"] is True
        assert result["rendered"] is True
        assert result["pdf_engine"] == "xelatex"
        abs_path = os.path.join(str(initialized), result["path"])
        pdf_abs = os.path.join(str(initialized), result["pdf_path"])
        assert os.path.isfile(abs_path)
        assert os.path.isfile(pdf_abs)
        cmd = mock_run.call_args[0][0]
        assert any(arg.endswith("keep-headings.tex") for arg in cmd)

    @patch("shape_printable.subprocess.run")
    @patch("shape_printable.shutil.which")
    def test_can_disable_keep_heading_with_next(self, mock_which, mock_run, initialized):
        mock_which.side_effect = self._fake_which_default
        mock_run.side_effect = self._fake_pandoc_run

        result = json.loads(server.brain_action("shape-printable", {
            "source": "Wiki/brain-overview-abc123.md",
            "slug": "tight-layout",
            "keep_heading_with_next": False,
        }))
        assert result["status"] == "ok"
        assert result["keep_heading_with_next"] is False
        cmd = mock_run.call_args[0][0]
        assert not any(arg.endswith("keep-headings.tex") for arg in cmd)

    @patch("shape_printable.shutil.which")
    def test_works_without_pandoc_installed(self, mock_which, initialized):
        def fake_which(cmd):
            if cmd == "pandoc":
                return None
            if cmd == "xelatex":
                return "/usr/bin/xelatex"
            return None

        mock_which.side_effect = fake_which
        result = json.loads(server.brain_action("shape-printable", {
            "source": "Wiki/brain-overview-abc123.md",
            "slug": "no-pandoc",
        }))
        assert result["status"] == "partial"
        assert result["rendered"] is False
        assert "pandoc" in result["warning"]

    @patch("shape_printable.subprocess.run")
    @patch("shape_printable.shutil.which")
    def test_uses_local_config_tool_paths(self, mock_which, mock_run, initialized):
        config_dir = initialized / ".brain" / "local"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "config.yaml").write_text(
            "defaults:\n"
            "  tool_paths:\n"
            "    pandoc: /opt/brain-tools/pandoc\n"
            "    xelatex: /opt/brain-tools/xelatex\n"
        )

        def fake_which(cmd):
            if cmd in {"/opt/brain-tools/pandoc", "/opt/brain-tools/xelatex"}:
                return cmd
            return None

        mock_which.side_effect = fake_which
        mock_run.side_effect = self._fake_pandoc_run

        result = json.loads(server.brain_action("shape-printable", {
            "source": "Wiki/brain-overview-abc123.md",
            "slug": "configured-tools",
        }))
        assert result["status"] == "ok"
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/opt/brain-tools/pandoc"
        assert "--pdf-engine=/opt/brain-tools/xelatex" in cmd

    @patch("shape_printable.subprocess.run")
    @patch("shape_printable.shutil.which")
    def test_env_tool_paths_override_local_config(self, mock_which, mock_run, initialized, monkeypatch):
        config_dir = initialized / ".brain" / "local"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "config.yaml").write_text(
            "defaults:\n"
            "  tool_paths:\n"
            "    pandoc: /opt/brain-tools/pandoc-config\n"
            "    xelatex: /opt/brain-tools/xelatex-config\n"
        )
        monkeypatch.setenv("BRAIN_PANDOC_PATH", "/env/brain-tools/pandoc")
        monkeypatch.setenv("BRAIN_XELATEX_PATH", "/env/brain-tools/xelatex")

        def fake_which(cmd):
            if cmd in {
                "/env/brain-tools/pandoc",
                "/env/brain-tools/xelatex",
                "/opt/brain-tools/pandoc-config",
                "/opt/brain-tools/xelatex-config",
            }:
                return cmd
            return None

        mock_which.side_effect = fake_which
        mock_run.side_effect = self._fake_pandoc_run

        result = json.loads(server.brain_action("shape-printable", {
            "source": "Wiki/brain-overview-abc123.md",
            "slug": "env-tools",
        }))
        assert result["status"] == "ok"
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/env/brain-tools/pandoc"
        assert "--pdf-engine=/env/brain-tools/xelatex" in cmd


# ---------------------------------------------------------------------------
# brain_action start-shaping tests
# ---------------------------------------------------------------------------

class TestBrainActionStartShaping:
    @pytest.fixture(autouse=True)
    def setup_shaping_files(self, initialized):
        """Add shaping transcript template and a design with status."""
        self.vault = initialized
        # Designs dir + taxonomy
        designs_dir = initialized / "Designs"
        designs_dir.mkdir(exist_ok=True)
        (designs_dir / "Test Design.md").write_text(
            "---\ntype: living/designs\ntags:\n  - design\nstatus: new\n"
            "created: 2026-03-01T10:00:00+00:00\n"
            "modified: 2026-03-01T10:00:00+00:00\n---\n\n"
            "# Test Design\n\nA design.\n"
        )
        tax_living = initialized / "_Config" / "Taxonomy" / "Living"
        (tax_living / "designs.md").write_text(
            "# Designs\n\n"
            "## Lifecycle\n\n"
            "| `new` | Newly created |\n"
            "| `shaping` | Being shaped |\n"
            "| `ready` | Ready |\n\n"
            "## Naming\n\n`{Title}.md` in `Designs/`.\n\n"
            "## Frontmatter\n\n```yaml\n---\ntype: living/designs\ntags:\n  - design\n"
            "status: new  # new | shaping | ready\n---\n```\n\n"
            "## Template\n\n[[_Config/Templates/Living/Designs]]\n"
        )
        templates_living = initialized / "_Config" / "Templates" / "Living"
        templates_living.mkdir(parents=True, exist_ok=True)
        (templates_living / "Designs.md").write_text(
            "---\ntype: living/designs\ntags: []\nstatus: new\n---\n\n"
        )
        # Shaping transcript taxonomy + template
        tax_temporal = initialized / "_Config" / "Taxonomy" / "Temporal"
        (tax_temporal / "shaping-transcripts.md").write_text(
            "# Shaping Transcripts\n\n"
            "## Naming\n\n`yyyymmdd-shaping-transcript~{Title}.md` in "
            "`_Temporal/Shaping Transcripts/yyyy-mm/`.\n\n"
            "## Frontmatter\n\n```yaml\n---\ntype: temporal/shaping-transcript\ntags:\n"
            "  - transcript\n---\n```\n\n"
            "## Template\n\n[[_Config/Templates/Temporal/Shaping Transcripts]]\n"
        )
        temporal = initialized / "_Temporal"
        temporal.mkdir(exist_ok=True)
        (temporal / "Shaping Transcripts").mkdir(exist_ok=True)
        templates_temporal = initialized / "_Config" / "Templates" / "Temporal"
        templates_temporal.mkdir(parents=True, exist_ok=True)
        (templates_temporal / "Shaping Transcripts.md").write_text(
            "---\ntype: temporal/shaping-transcript\ntags:\n  - transcript\n"
            "  - SOURCE_TYPE\n---\n"
            "Shaping transcript for [[SOURCE_DOC_PATH|SOURCE_DOC_TITLE]].\n\n"
            "## {{date:YYYY-MM-DD}}\n\nQ.\n> A.\n"
        )
        # Recompile router to pick up new types
        server.brain_action("compile")

    def test_missing_params_returns_error(self):
        result = server.brain_action("start-shaping")
        _assert_error(result)

    def test_missing_target_returns_error(self):
        result = server.brain_action("start-shaping", {})
        _assert_error(result)

    def test_target_not_found_returns_error(self):
        result = server.brain_action("start-shaping", {
            "target": "Nonexistent File",
        })
        _assert_error(result)

    def test_happy_path_creates_transcript(self):
        result = json.loads(server.brain_action("start-shaping", {
            "target": "Designs/Test Design.md",
        }))
        assert result["status"] == "ok"
        assert result["target_path"] == "Designs/Test Design.md"
        assert "shaping-transcript" in result["transcript_path"]
        assert result["set_status"] is True
        # Transcript exists on disk
        abs_path = os.path.join(str(self.vault), result["transcript_path"])
        assert os.path.isfile(abs_path)


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
            "core_bootstrap", "core_docs", "always_rules", "preferences", "gotchas",
            "triggers", "artefacts", "environment",
            "memories", "skills", "plugins", "styles",
            "config", "active_profile",
        }
        assert set(result.keys()) == expected_keys

    def test_core_bootstrap_present(self, initialized):
        result = json.loads(server.brain_session())
        assert "## Principles" in result["core_bootstrap"]
        assert "## Core Docs" not in result["core_bootstrap"]
        assert "Prefer `brain_list`" not in result["core_bootstrap"]

    def test_core_docs_are_structured_and_loadable(self, initialized):
        result = json.loads(server.brain_session())
        assert isinstance(result["core_docs"], list)
        assert len(result["core_docs"]) == 2

        section_names = [section["section"] for section in result["core_docs"]]
        assert section_names == ["Core Docs", "Standards"]

        first_doc = result["core_docs"][0]["docs"][0]
        assert first_doc["title"] == "Extend the vault: add artefact types, memories, and principles"
        assert first_doc["path"] == ".brain-core/standards/extending/README.md"
        assert first_doc["load_with"] == {
            "tool": "brain_read",
            "resource": "file",
            "name": ".brain-core/standards/extending/README.md",
        }

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

    def test_workspace_metadata_from_env(self, initialized, monkeypatch):
        workspace_dir = str(initialized.parent / "demo-workspace")
        monkeypatch.setenv("BRAIN_WORKSPACE_DIR", workspace_dir)

        result = json.loads(server.brain_session())

        assert result["workspace"] == {
            "directory": workspace_dir,
            "name": "demo-workspace",
            "location": "external",
        }

    def test_workspace_defaults_from_manifest(self, initialized, monkeypatch):
        workspace_dir = initialized.parent / "demo-workspace"
        (workspace_dir / ".brain" / "local").mkdir(parents=True)
        (workspace_dir / ".brain" / "local" / "workspace.yaml").write_text(
            "slug: demo-workspace\n"
            "links:\n"
            "  workspace: brain-demo\n"
            "defaults:\n"
            "  tags:\n"
            "    - workspace/brain-demo\n"
            "    - project/brain\n"
        )
        monkeypatch.setenv("BRAIN_WORKSPACE_DIR", str(workspace_dir))

        result = json.loads(server.brain_session())

        assert result["workspace_defaults"] == {
            "tags": ["workspace/brain-demo", "project/brain"],
        }
        assert result["workspace_record"] == {
            "slug": "brain-demo",
            "workspace_mode": "linked",
        }

    def test_markdown_mirror_tracks_brain_session(self, initialized):
        user_dir = initialized / "_Config" / "User"
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "preferences-always.md").write_text(
            "---\ntype: user-preferences\n---\n\nPrefer tests before docs.\n"
        )

        result = json.loads(server.brain_session())
        session_path = initialized / ".brain" / "local" / "session.md"
        content = session_path.read_text()

        assert result["core_bootstrap"] in content
        assert "[Extend the vault: add artefact types, memories, and principles](../../.brain-core/standards/extending/README.md)" in content
        assert "[Track provenance and lineage between artefacts](../../.brain-core/standards/provenance.md)" in content
        assert "[[.brain-core/standards/provenance]]" not in content
        for rule in result["always_rules"]:
            assert rule in content
        assert "Prefer tests before docs." in content
        assert result["active_profile"] in content

    def test_markdown_mirror_includes_workspace_metadata(self, initialized, monkeypatch):
        workspace_dir = str(initialized.parent / "demo-workspace")
        monkeypatch.setenv("BRAIN_WORKSPACE_DIR", workspace_dir)

        json.loads(server.brain_session())
        content = (initialized / ".brain" / "local" / "session.md").read_text()

        assert "## Workspace" in content
        assert "`name`: `demo-workspace`" in content
        assert f"`directory`: `{workspace_dir}`" in content
        assert "`location`: `external`" in content

    def test_markdown_mirror_includes_workspace_defaults(self, initialized, monkeypatch):
        workspace_dir = initialized.parent / "demo-workspace"
        (workspace_dir / ".brain" / "local").mkdir(parents=True, exist_ok=True)
        (workspace_dir / ".brain" / "local" / "workspace.yaml").write_text(
            "slug: demo-workspace\n"
            "defaults:\n"
            "  tags:\n"
            "    - workspace/demo-workspace\n"
            "    - project/brain\n"
        )
        monkeypatch.setenv("BRAIN_WORKSPACE_DIR", str(workspace_dir))

        json.loads(server.brain_session())
        content = (initialized / ".brain" / "local" / "session.md").read_text()

        assert "## Workspace Defaults" in content
        assert '`tags`: `["workspace/demo-workspace", "project/brain"]`' in content

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
# Workspace registry — script tests
# ---------------------------------------------------------------------------

class TestWorkspaceRegistryScript:
    """Tests for workspace_registry.py script functions."""

    @pytest.fixture(autouse=True)
    def _reset_hub_metadata_cache(self):
        workspace_registry._hub_metadata_cache.clear()
        yield
        workspace_registry._hub_metadata_cache.clear()

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
        """Unknown key raises ValueError."""
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
        keys = [w["slug"] for w in result]
        assert "alpha" in keys
        assert "beta" in keys
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
        keys = [w["slug"] for w in result]
        assert "local" in keys
        assert "remote" in keys

    def test_list_skips_system_dirs(self, vault):
        """System dirs (_Archive, .hidden) in _Workspaces/ are excluded."""
        ws = vault / "_Workspaces"
        ws.mkdir(parents=True)
        (ws / "_Archive").mkdir()
        (ws / ".hidden").mkdir()
        (ws / "real-workspace").mkdir()
        result = workspace_registry.list_workspaces(str(vault))
        keys = [w["slug"] for w in result]
        assert keys == ["real-workspace"]

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

    def test_list_enriched_with_completed_hub_metadata(self, vault):
        """Hubs in Workspaces/+Completed/ also enrich the listing."""
        (vault / "_Workspaces" / "old-project").mkdir(parents=True)
        completed_dir = vault / "Workspaces" / "+Completed"
        completed_dir.mkdir(parents=True)
        (completed_dir / "old-project.md").write_text(
            "---\ntype: living/workspace\nkey: old-project\nstatus: completed\n"
            "workspace_mode: embedded\ntags:\n  - workspace/old-project\n---\n\n# Old\n"
        )
        result = workspace_registry.list_workspaces(str(vault))
        assert len(result) == 1
        ws = result[0]
        assert ws["slug"] == "old-project"
        assert ws["status"] == "completed"
        assert ws["hub_path"] == os.path.join("Workspaces", "+Completed", "old-project.md")
        assert "workspace/old-project" in ws["tags"]

    def test_hub_metadata_cache_skips_unchanged(self, vault, monkeypatch):
        """Unchanged hub mtime → frontmatter not re-read on subsequent scans."""
        (vault / "_Workspaces" / "alpha").mkdir(parents=True)
        hub_dir = vault / "Workspaces"
        hub_dir.mkdir(parents=True)
        (hub_dir / "alpha.md").write_text(
            "---\ntype: living/workspace\nkey: alpha\nstatus: active\n---\n\n# A\n"
        )

        calls = {"n": 0}
        real = workspace_registry.read_frontmatter

        def counting(path):
            calls["n"] += 1
            return real(path)

        monkeypatch.setattr(workspace_registry, "read_frontmatter", counting)
        workspace_registry.list_workspaces(str(vault))
        assert calls["n"] == 1
        workspace_registry.list_workspaces(str(vault))
        assert calls["n"] == 1

    def test_hub_metadata_cache_invalidates_on_mtime_change(self, vault, monkeypatch):
        """Hub mtime change → frontmatter re-read."""
        (vault / "_Workspaces" / "alpha").mkdir(parents=True)
        hub_dir = vault / "Workspaces"
        hub_dir.mkdir(parents=True)
        hub_file = hub_dir / "alpha.md"
        hub_file.write_text(
            "---\ntype: living/workspace\nkey: alpha\nstatus: active\n---\n\n# A\n"
        )

        calls = {"n": 0}
        real = workspace_registry.read_frontmatter

        def counting(path):
            calls["n"] += 1
            return real(path)

        monkeypatch.setattr(workspace_registry, "read_frontmatter", counting)
        workspace_registry.list_workspaces(str(vault))
        assert calls["n"] == 1

        time.sleep(0.05)
        hub_file.write_text(
            "---\ntype: living/workspace\nkey: alpha\nstatus: parked\n---\n\n# A\n"
        )
        result = workspace_registry.list_workspaces(str(vault))
        assert calls["n"] == 2
        assert result[0]["status"] == "parked"

    def test_hub_metadata_cache_isolates_callers_from_mutation(self, vault):
        """Mutating a returned entry's tags must not corrupt the cache."""
        (vault / "_Workspaces" / "alpha").mkdir(parents=True)
        hub_dir = vault / "Workspaces"
        hub_dir.mkdir(parents=True)
        (hub_dir / "alpha.md").write_text(
            "---\ntype: living/workspace\nkey: alpha\nstatus: active\n"
            "tags:\n  - workspace/alpha\n---\n\n# A\n"
        )

        first = workspace_registry._scan_hub_metadata(str(vault))
        first["alpha"]["tags"].append("poisoned")

        second = workspace_registry._scan_hub_metadata(str(vault))
        assert "poisoned" not in second["alpha"]["tags"]

    def test_hub_metadata_cache_evicts_deleted_hubs(self, vault):
        """Deleted hubs drop out of the cache."""
        (vault / "_Workspaces" / "ghost").mkdir(parents=True)
        hub_dir = vault / "Workspaces"
        hub_dir.mkdir(parents=True)
        hub_file = hub_dir / "ghost.md"
        hub_file.write_text(
            "---\ntype: living/workspace\nkey: ghost\nstatus: active\n---\n\n# G\n"
        )

        workspace_registry.list_workspaces(str(vault))
        assert any("ghost.md" in p for p in workspace_registry._hub_metadata_cache)

        hub_file.unlink()
        workspace_registry.list_workspaces(str(vault))
        assert not any("ghost.md" in p for p in workspace_registry._hub_metadata_cache)

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
        result = server.brain_list(resource="workspace")
        text = _search_text(result)
        assert "analysis" in text

    def test_list_workspace_shape(self):
        result = server.brain_list(resource="workspace")
        text = _search_text(result)
        assert "embedded" in text

    def test_read_workspace_requires_name(self):
        result = server.brain_read("workspace")
        _assert_error(result, "requires name")

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

    def test_registered_workspace_visible_in_list(self, initialized, tmp_path):
        """After registering, brain_list(resource='workspace') should show it."""
        ext_path = str(tmp_path / "visible-proj")
        server.brain_action("register_workspace", {
            "slug": "visible-proj", "path": ext_path,
        })
        result = server.brain_list(resource="workspace")
        text = _search_text(result)
        assert "visible-proj" in text

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

class TestBrainProcessFeatureGate:
    def test_process_disabled_by_default(self, initialized):
        result = server.brain_process(
            operation="classify",
            content="I have a new idea",
        )
        _assert_error(result, "brain_process is disabled")


class TestBrainProcess:
    @pytest.fixture(autouse=True)
    def enable_process_feature(self, initialized):
        server._config["defaults"]["flags"]["brain_process"] = True

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
                "## Naming\n\n`{Title}.md` in `Ideas/`.\n\n"
                "## Frontmatter\n\n```yaml\n---\ntype: living/ideas\n---\n```\n\n"
                "## Purpose\n\nCapture concepts that need development.\n\n"
                "## When To Use\n\nWhen developing a concept that needs iterative refinement.\n\n"
                "## Template\n\n[[_Config/Templates/Living/Ideas]]\n"
        )
        # Re-initialize to rebuild index
        server.startup(vault_root=str(initialized))
        server._config["defaults"]["flags"]["brain_process"] = True
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
            "# Wiki\n\n## Naming\n\n`{Title}.md` in `Wiki/`.\n\n"
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

    def test_sync_enqueues_mirror_refresh_on_update(self, initialized, monkeypatch):
        """Finding 3: sync_definitions with updates triggers a background mirror refresh."""
        import session as session_mod

        vault = initialized
        tracking_path = vault / ".brain" / "tracking.json"
        if tracking_path.exists():
            tracking_path.unlink()

        calls: list[tuple] = []
        original_persist = session_mod.persist_session_markdown

        def tracking_persist(model, vault_root):
            calls.append((time.monotonic(), vault_root))
            original_persist(model, vault_root)

        monkeypatch.setattr(session_mod, "persist_session_markdown", tracking_persist)

        # Drain any pending work from fixture startup.
        server._mirror_queue.join()
        baseline = len(calls)

        result = json.loads(server.brain_action("sync_definitions"))
        server._mirror_queue.join()

        if result.get("updated"):
            assert len(calls) > baseline, (
                "sync_definitions with updates should trigger a mirror refresh "
                "via refresh_session_mirror_best_effort"
            )


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

    def test_migrate_enqueues_mirror_refresh_on_rename(self, initialized, monkeypatch):
        """Finding 3: migrate_naming with renames triggers a background mirror refresh."""
        import session as session_mod

        self._create_old_convention_file(name="mirror-refresh", title="Mirror Refresh")

        calls: list[tuple] = []
        original_persist = session_mod.persist_session_markdown

        def tracking_persist(model, vault_root):
            calls.append((time.monotonic(), vault_root))
            original_persist(model, vault_root)

        monkeypatch.setattr(session_mod, "persist_session_markdown", tracking_persist)

        server._mirror_queue.join()
        baseline = len(calls)

        result = json.loads(server.brain_action("migrate_naming"))
        server._mirror_queue.join()

        renamed_count = result.get("renamed", 0)
        if isinstance(renamed_count, list):
            renamed_count = len(renamed_count)
        if renamed_count > 0:
            assert len(calls) > baseline, (
                "migrate_naming with renames should trigger a mirror refresh "
                "via refresh_session_mirror_best_effort"
            )


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
        result = server.brain_read("type", name="wiki")
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
        result = server.brain_read("type", name="wiki")
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

    def test_runtime_can_mark_embeddings_dirty(self, initialized):
        """ServerRuntime should expose the embeddings-dirty flag helper."""
        server._embeddings_dirty = False

        server._runtime().mark_embeddings_dirty()

        assert server._embeddings_dirty is True

    def test_mark_index_pending_invalidates_embeddings_sidecars(self, initialized):
        local_dir = initialized / ".brain" / "local"
        local_dir.mkdir(parents=True, exist_ok=True)
        for rel_path in (
            "type-embeddings.npy",
            "doc-embeddings.npy",
            "embeddings-meta.json",
        ):
            (local_dir / rel_path).write_bytes(b"stale")

        server._type_embeddings = object()
        server._doc_embeddings = object()
        server._embeddings_meta = {"documents": [], "types": []}
        server._embeddings_dirty = False

        server._mark_index_pending("Wiki/brain-overview-abc123.md", "living/wiki")

        assert server._embeddings_dirty is True
        assert server._type_embeddings is None
        assert server._doc_embeddings is None
        assert server._embeddings_meta is None
        assert not (local_dir / "type-embeddings.npy").exists()
        assert not (local_dir / "doc-embeddings.npy").exists()
        assert not (local_dir / "embeddings-meta.json").exists()

    def test_process_lazily_refreshes_embeddings_when_enabled(self, initialized, monkeypatch):
        server._config["defaults"]["flags"]["brain_process"] = True
        server._type_embeddings = None
        server._doc_embeddings = None
        server._embeddings_meta = None
        server._embeddings_dirty = False

        calls = []

        def fake_refresh(vault_root, router, documents, *, enable_embeddings=None, config=None):
            calls.append((str(vault_root), enable_embeddings, len(documents)))
            return {"documents": [], "types": []}

        def fake_load(_vault_root):
            server._type_embeddings = object()
            server._doc_embeddings = object()
            server._embeddings_meta = {"documents": [], "types": []}

        monkeypatch.setattr(build_index, "refresh_embeddings_outputs", fake_refresh)
        monkeypatch.setattr(server, "_load_embeddings", fake_load)

        result = server.brain_process(
            operation="classify",
            content="some content",
            mode="context_assembly",
        )

        assert "context_assembly" in result
        assert calls == [(str(initialized), True, len(server._index["documents"]))]
        assert server._type_embeddings is not None
        assert server._doc_embeddings is not None

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

    def test_list_by_parent(self, initialized):
        server.brain_create(type="wiki", title="Owner", key="owner")
        server.brain_create(type="ideas", title="Owned Idea", parent="wiki/owner")
        resp = server.brain_list(parent="wiki/owner")
        lines = _list_result_lines(resp)
        assert len(lines) == 1
        assert "Ideas/wiki~owner/" in lines[0]
        assert "parent=wiki/owner" in lines[0]

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


# ---------------------------------------------------------------------------
# Logging tests (Step 0)
# ---------------------------------------------------------------------------

import logging
from logging.handlers import RotatingFileHandler


def _file_handler(logger):
    """Extract the single RotatingFileHandler from a logger."""
    return next(h for h in logger.handlers if isinstance(h, RotatingFileHandler))


@pytest.fixture(autouse=True)
def _clean_logger():
    """Clear handlers from the brain-core logger between tests."""
    yield
    logger = logging.getLogger("brain-core")
    logger.handlers.clear()
    logger.setLevel(logging.WARNING)  # reset to default


class TestSetupLogging:
    """0a. _setup_logging unit tests."""

    def test_creates_log_directory(self, tmp_path):
        """Creates .brain/local/ directory if missing."""
        logger = server._setup_logging(str(tmp_path))
        log_dir = tmp_path / ".brain" / "local"
        assert log_dir.is_dir()

    def test_returns_named_logger(self, tmp_path):
        """Returns a logging.Logger named 'brain-core'."""
        logger = server._setup_logging(str(tmp_path))
        assert isinstance(logger, logging.Logger)
        assert logger.name == "brain-core"

    def test_file_handler_exists(self, tmp_path):
        """Logger has exactly one RotatingFileHandler at the correct path."""
        logger = server._setup_logging(str(tmp_path))
        fh = _file_handler(logger)
        expected = str(tmp_path / ".brain" / "local" / "mcp-server.log")
        assert fh.baseFilename == expected

    def test_file_handler_formatter(self, tmp_path):
        """Handler uses expected format string."""
        logger = server._setup_logging(str(tmp_path))
        fh = _file_handler(logger)
        assert "%(asctime)s" in fh.formatter._fmt
        assert "[%(levelname)s]" in fh.formatter._fmt
        assert "%(message)s" in fh.formatter._fmt

    def test_file_handler_rotation(self, tmp_path):
        """Handler maxBytes is 2MB, backupCount is 1."""
        logger = server._setup_logging(str(tmp_path))
        fh = _file_handler(logger)
        assert fh.maxBytes == 2 * 1024 * 1024
        assert fh.backupCount == 1

    def test_logger_level_is_debug(self, tmp_path):
        """Logger level is DEBUG (handlers filter, not logger)."""
        logger = server._setup_logging(str(tmp_path))
        assert logger.level == logging.DEBUG

    def test_writes_to_log_file(self, tmp_path):
        """Writes a test message and confirms it appears in the log file."""
        logger = server._setup_logging(str(tmp_path))
        logger.info("test message 12345")
        # Flush handlers
        for h in logger.handlers:
            h.flush()
        log_path = tmp_path / ".brain" / "local" / "mcp-server.log"
        content = log_path.read_text()
        assert "test message 12345" in content


class TestLogLevelOverride:
    """0b. BRAIN_LOG_LEVEL override."""

    def test_default_file_level_is_info(self, tmp_path):
        """Default file handler level is INFO."""
        logger = server._setup_logging(str(tmp_path))
        assert _file_handler(logger).level == logging.INFO

    def test_debug_level_override(self, tmp_path, monkeypatch):
        """With BRAIN_LOG_LEVEL=DEBUG, file handler level is DEBUG."""
        monkeypatch.setenv("BRAIN_LOG_LEVEL", "DEBUG")
        logger = server._setup_logging(str(tmp_path))
        assert _file_handler(logger).level == logging.DEBUG

    def test_invalid_level_falls_back_to_info(self, tmp_path, monkeypatch):
        """Invalid BRAIN_LOG_LEVEL value falls back to INFO."""
        monkeypatch.setenv("BRAIN_LOG_LEVEL", "BOGUS")
        logger = server._setup_logging(str(tmp_path))
        assert _file_handler(logger).level == logging.INFO


class TestStderrHandler:
    """0c. Stderr handler."""

    def test_stderr_handler_exists_at_warn(self, tmp_path):
        """A StreamHandler to stderr exists at WARN level."""
        logger = server._setup_logging(str(tmp_path))
        stream_handlers = [h for h in logger.handlers if isinstance(h, logging.StreamHandler)
                          and not isinstance(h, RotatingFileHandler)]
        assert len(stream_handlers) == 1
        assert stream_handlers[0].level == logging.WARNING

    def test_stderr_does_not_get_info(self, tmp_path, capsys):
        """Stderr handler does NOT write INFO messages."""
        logger = server._setup_logging(str(tmp_path))
        logger.info("should not appear on stderr")
        for h in logger.handlers:
            h.flush()
        captured = capsys.readouterr()
        assert "should not appear on stderr" not in captured.err


class TestStartupLogging:
    """0d. Startup logging (integration with vault fixture)."""

    def test_log_file_exists_after_startup(self, vault):
        """After startup(), the log file exists."""
        server.startup(vault_root=str(vault))
        log_path = vault / ".brain" / "local" / "mcp-server.log"
        assert log_path.is_file()

    def test_startup_messages_logged(self, vault):
        """Log file contains startup begin, phase markers, and startup complete."""
        server.startup(vault_root=str(vault))
        log_path = vault / ".brain" / "local" / "mcp-server.log"
        content = log_path.read_text()
        assert "startup begin" in content
        assert "startup phase begin: config_load" in content
        assert "startup phase success: config_load" in content
        assert "startup phase begin: router_freshness" in content
        assert "startup phase begin: index_freshness" in content
        assert "startup phase begin: embeddings_load" in content
        assert "startup phase begin: workspace_registry_load" in content
        assert "startup phase begin: session_mirror_refresh" in content
        assert "startup complete" in content

    def test_router_compile_logged(self, vault):
        """Stale router compile is logged with timing."""
        server.startup(vault_root=str(vault))
        log_path = vault / ".brain" / "local" / "mcp-server.log"
        content = log_path.read_text()
        # First startup always compiles (no cached router)
        assert "router compile" in content

    def test_startup_does_not_block_on_slow_mirror_write(self, vault, monkeypatch):
        """Startup returns promptly even if the background mirror write would stall."""
        import session as session_mod

        release = threading.Event()
        entered = threading.Event()

        def slow_persist(model, vault_root):
            entered.set()
            release.wait(timeout=2.0)
            # Intentionally do not actually write — this test only asserts
            # that startup() does not wait for this call to complete.

        monkeypatch.setattr(session_mod, "persist_session_markdown", slow_persist)

        try:
            started = time.monotonic()
            server.startup(vault_root=str(vault))
            elapsed = time.monotonic() - started
            assert elapsed < 1.0, f"startup blocked for {elapsed:.2f}s"
            assert entered.wait(timeout=2.0), "worker did not pick up the refresh"
        finally:
            release.set()
            # Let the worker finish so subsequent tests start from a clean state.
            try:
                server._mirror_queue.join()
            except Exception:
                pass

    def test_startup_does_not_block_on_slow_mirror_write_after_recompile(
        self, vault, monkeypatch
    ):
        """Stale-router startup also returns promptly under a slow background write."""
        import session as session_mod

        monkeypatch.setattr(server, "_check_router", lambda _vault_root: (True, None))

        release = threading.Event()
        entered = threading.Event()

        def slow_persist(model, vault_root):
            entered.set()
            release.wait(timeout=2.0)

        monkeypatch.setattr(session_mod, "persist_session_markdown", slow_persist)

        try:
            started = time.monotonic()
            server.startup(vault_root=str(vault))
            elapsed = time.monotonic() - started
            log_path = vault / ".brain" / "local" / "mcp-server.log"
            content = log_path.read_text()
            assert elapsed < 2.0, f"startup blocked for {elapsed:.2f}s"
            assert "startup phase begin: router_freshness" in content
            assert entered.wait(timeout=2.0), "worker did not pick up the refresh"
        finally:
            release.set()
            try:
                server._mirror_queue.join()
            except Exception:
                pass


class TestMirrorWorker:
    """Shape 2: queue-based session-mirror worker — coalescing, drain, sweep."""

    def test_mirror_worker_coalesces_rapid_fire_refreshes(self, vault, monkeypatch):
        """Rapid-fire enqueues while the worker is busy collapse to the latest."""
        import session as session_mod

        calls = []
        calls_lock = threading.Lock()
        in_first = threading.Event()
        release_first = threading.Event()

        original_persist = session_mod.persist_session_markdown

        def counting_persist(model, vault_root):
            with calls_lock:
                calls.append(time.monotonic())
                count = len(calls)
            if count == 1:
                in_first.set()
                release_first.wait(timeout=2.0)
            original_persist(model, vault_root)

        monkeypatch.setattr(session_mod, "persist_session_markdown", counting_persist)

        server.startup(vault_root=str(vault))
        assert in_first.wait(timeout=2.0), "first refresh did not enter worker"

        # Fire many refreshes while the worker is blocked on the first call.
        for _ in range(20):
            server._enqueue_mirror_refresh()

        release_first.set()
        server._mirror_queue.join()

        with calls_lock:
            total = len(calls)
        # Expected: 1 (the initial startup refresh we blocked) + at most 1
        # coalesced follow-up. A racing ordering may process 2 follow-ups
        # if the worker drained between the startup enqueue and the loop.
        assert total <= 3, f"coalescing failed: {total} persist calls"

    def test_mirror_worker_drains_pending_on_shutdown(self, vault, monkeypatch):
        """Atexit drain waits briefly for the in-flight refresh to complete."""
        import session as session_mod

        completed = threading.Event()
        original_persist = session_mod.persist_session_markdown

        def tracked_persist(model, vault_root):
            original_persist(model, vault_root)
            completed.set()

        monkeypatch.setattr(session_mod, "persist_session_markdown", tracked_persist)

        server.startup(vault_root=str(vault))
        assert completed.wait(timeout=2.0), "initial refresh did not complete"

        # Force-enqueue one more, then drain explicitly with a short timeout.
        completed.clear()
        server._enqueue_mirror_refresh()
        server._drain_mirror_queue(timeout=2.0)

        # After drain, the worker thread should have processed the pending
        # request (or exited cleanly via the SHUTDOWN sentinel).
        assert completed.is_set() or not server._mirror_worker_thread.is_alive()

    def test_sweep_mirror_tmpfiles_removes_orphans(self, vault):
        """Orphaned session.md.*.tmp files in .brain/local/ are swept at startup."""
        local = vault / ".brain" / "local"
        local.mkdir(parents=True, exist_ok=True)
        orphan = local / "session.md.orphan.tmp"
        orphan.write_text("abandoned by a killed worker")
        assert orphan.exists()

        server.startup(vault_root=str(vault))

        assert not orphan.exists(), "orphaned tempfile was not swept"

    def test_sweep_leaves_non_tmp_files_alone(self, vault):
        """Sweep does not touch session.md itself or unrelated files."""
        local = vault / ".brain" / "local"
        local.mkdir(parents=True, exist_ok=True)
        keeper = local / "session.md"
        keeper.write_text("existing mirror")
        sibling = local / "other.tmp"
        sibling.write_text("not a mirror tempfile")

        server._sweep_mirror_tmpfiles(str(vault))

        assert keeper.exists()
        assert sibling.exists()


class TestToolCallTracing:
    """0e. Tool call tracing."""

    def test_tool_call_logged(self, initialized):
        """Call a tool and verify log file contains tool name and duration."""
        server.brain_read(resource="type")
        log_path = initialized / ".brain" / "local" / "mcp-server.log"
        content = log_path.read_text()
        assert "tool call: brain_read" in content
        assert "tool done: brain_read" in content

    def test_debug_args_logged(self, vault, monkeypatch):
        """With BRAIN_LOG_LEVEL=DEBUG, log file also contains tool arguments."""
        monkeypatch.setenv("BRAIN_LOG_LEVEL", "DEBUG")
        server.startup(vault_root=str(vault))
        server.brain_read(resource="type")
        log_path = vault / ".brain" / "local" / "mcp-server.log"
        content = log_path.read_text()
        assert "tool args: brain_read" in content

    def test_debug_args_not_logged_at_info(self, initialized):
        """At default INFO level, tool arguments are NOT logged."""
        server.brain_read(resource="type")
        log_path = initialized / ".brain" / "local" / "mcp-server.log"
        content = log_path.read_text()
        assert "tool args:" not in content


class TestMutationSerialization:
    def test_brain_edit_calls_are_serialized(self, initialized):
        active = 0
        max_active = 0
        state_lock = threading.Lock()
        first_entered = threading.Event()
        second_entered = threading.Event()
        release_first = threading.Event()
        results = []

        def fake_handle_brain_edit(**_kwargs):
            nonlocal active, max_active
            with state_lock:
                active += 1
                max_active = max(max_active, active)
                first_call = not first_entered.is_set()
            if first_call:
                first_entered.set()
                release_first.wait(timeout=2)
            else:
                second_entered.set()
            time.sleep(0.01)
            with state_lock:
                active -= 1
            return "ok"

        def invoke():
            results.append(server.brain_edit(operation="edit", path="test.md", body="x"))

        with patch.object(server._server_artefacts, "handle_brain_edit", side_effect=fake_handle_brain_edit):
            t1 = threading.Thread(target=invoke)
            t2 = threading.Thread(target=invoke)
            t1.start()
            assert first_entered.wait(timeout=1)
            t2.start()
            time.sleep(0.05)
            assert not second_entered.is_set()
            release_first.set()
            t1.join(timeout=1)
            t2.join(timeout=1)

        assert results == ["ok", "ok"]
        assert second_entered.is_set()
        assert max_active == 1


class TestShutdownLogging:
    """0f. Shutdown logging."""

    def test_shutdown_logs_message(self, vault):
        """_shutdown() writes a shutdown message to the log."""
        server.startup(vault_root=str(vault))
        with pytest.raises(SystemExit):
            server._shutdown("test reason")
        log_path = vault / ".brain" / "local" / "mcp-server.log"
        content = log_path.read_text()
        assert "shutdown: test reason" in content

    def test_flush_log_ignores_broken_pipe(self):
        """_flush_log() tolerates closed stderr/stdout pipes during shutdown."""

        class _BrokenPipeHandler(logging.Handler):
            def flush(self):
                raise BrokenPipeError()

        class _CountingHandler(logging.Handler):
            def __init__(self):
                super().__init__()
                self.flushed = 0

            def flush(self):
                self.flushed += 1

        counting = _CountingHandler()
        logger = logging.Logger("flush-test")
        logger.addHandler(_BrokenPipeHandler())
        logger.addHandler(counting)

        old_logger = server._logger
        server._logger = logger
        try:
            server._flush_log()
        finally:
            server._logger = old_logger

        assert counting.flushed == 1

    def test_flush_log_ignores_closed_stream_value_error(self):
        """_flush_log() tolerates closed stream handlers during shutdown."""

        class _CountingHandler(logging.Handler):
            def __init__(self):
                super().__init__()
                self.flushed = 0

            def flush(self):
                self.flushed += 1

        stream = open(os.devnull, "w", encoding="utf-8")
        closed_stream_handler = logging.StreamHandler(stream)
        stream.close()

        counting = _CountingHandler()
        logger = logging.Logger("flush-test-closed-stream")
        logger.addHandler(closed_stream_handler)
        logger.addHandler(counting)

        old_logger = server._logger
        server._logger = logger
        try:
            server._flush_log()
        finally:
            server._logger = old_logger

        assert counting.flushed == 1


class TestNoStdoutContamination:
    """0g. No stdout contamination."""

    def test_startup_no_stdout(self, vault, capsys):
        """Capture stdout during startup, assert it is empty."""
        server.startup(vault_root=str(vault))
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_tool_call_no_stdout(self, initialized, capsys):
        """Capture stdout during a tool call, assert it is empty."""
        server.brain_read(resource="type")
        captured = capsys.readouterr()
        assert captured.out == ""
