"""Tests for search_index.py — BM25 retrieval search."""

import json
import math
import os

import pytest

import build_index as bi
import search_index as si


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path):
    """Create a vault with searchable content."""
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("1.0.0\n")
    (tmp_path / "_Config").mkdir()

    wiki = tmp_path / "Wiki"
    wiki.mkdir()
    (wiki / "python-basics.md").write_text(
        "---\ntype: living/wiki\ntags: [python, programming]\nstatus: active\n---\n\n"
        "# Python Basics\n\nPython is a versatile programming language. "
        "Python supports object-oriented programming and functional programming. "
        "Python is widely used in data science and web development.\n"
    )
    (wiki / "rust-ownership.md").write_text(
        "---\ntype: living/wiki\ntags: [rust, systems]\nstatus: active\n---\n\n"
        "# Rust Ownership\n\nRust uses an ownership system to manage memory. "
        "The borrow checker enforces ownership rules at compile time. "
        "Rust prevents data races through its type system.\n"
    )
    (wiki / "javascript-async.md").write_text(
        "---\ntype: living/wiki\ntags: [javascript, web]\nstatus: draft\n---\n\n"
        "# JavaScript Async\n\nJavaScript uses promises and async/await for asynchronous programming. "
        "The event loop processes callbacks. Node.js is a JavaScript runtime.\n"
    )

    designs = tmp_path / "Designs"
    designs.mkdir()
    (designs / "brain-tooling.md").write_text(
        "---\ntype: living/design\ntags: [brain-core, tooling]\nstatus: active\n---\n\n"
        "# Brain Tooling Design\n\nThe brain-core tooling architecture uses Python scripts. "
        "Each script is self-contained with no external dependencies. "
        "The compiled router is the central configuration interface.\n"
    )

    temporal = tmp_path / "_Temporal"
    temporal.mkdir()
    logs = temporal / "Logs"
    logs.mkdir()
    month = logs / "2026-03"
    month.mkdir()
    (month / "20260315-python-log.md").write_text(
        "---\ntype: temporal/logs\ntags: [python, log]\nstatus: done\n---\n\n"
        "# Python Research Log\n\nResearched Python packaging tools. "
        "Compared pip, poetry, and pdm. Python packaging is evolving rapidly.\n"
    )

    return tmp_path


@pytest.fixture
def index(vault):
    """Build and return an index for the test vault."""
    return bi.build_index(vault)


# ---------------------------------------------------------------------------
# Tokenisation consistency
# ---------------------------------------------------------------------------

class TestTokenise:
    def test_matches_build_index(self):
        """Ensure search tokeniser matches build tokeniser."""
        text = "BM25 retrieval with Python 3.8"
        assert si.tokenise(text) == bi.tokenise(text)


# ---------------------------------------------------------------------------
# Search results
# ---------------------------------------------------------------------------

class TestSearch:
    def test_basic_search(self, index, vault):
        results = si.search(index, "python programming", vault)
        assert len(results) > 0
        # Python basics should rank high — has "python" multiple times
        assert results[0]["path"].endswith("python-basics.md") or "python" in results[0]["title"].lower()

    def test_result_fields(self, index, vault):
        results = si.search(index, "python", vault)
        assert len(results) > 0
        r = results[0]
        assert "path" in r
        assert "title" in r
        assert "type" in r
        assert "status" in r
        assert "score" in r
        assert "snippet" in r
        assert r["score"] > 0

    def test_no_results(self, index, vault):
        results = si.search(index, "xyznonexistent", vault)
        assert results == []

    def test_empty_query(self, index, vault):
        results = si.search(index, "", vault)
        assert results == []

    def test_top_k_limit(self, index, vault):
        results = si.search(index, "python", vault, top_k=1)
        assert len(results) <= 1

    def test_type_filter(self, index, vault):
        results = si.search(index, "python", vault, type_filter="temporal/logs")
        assert len(results) > 0
        assert all(r["type"] == "temporal/logs" for r in results)

    def test_type_filter_no_match(self, index, vault):
        results = si.search(index, "python", vault, type_filter="living/nonexistent")
        assert results == []

    def test_tag_filter(self, index, vault):
        results = si.search(index, "programming", vault, tag_filter="python")
        assert len(results) > 0
        # Should only include docs tagged with python

    def test_status_filter(self, index, vault):
        results = si.search(index, "python", vault, status_filter="active")
        assert len(results) > 0
        assert all(r["status"] == "active" for r in results)

    def test_status_filter_no_match(self, index, vault):
        results = si.search(index, "python", vault, status_filter="nonexistent")
        assert results == []

    def test_ranking_order(self, index, vault):
        results = si.search(index, "python", vault)
        if len(results) >= 2:
            assert results[0]["score"] >= results[1]["score"]

    def test_multi_term_query(self, index, vault):
        results = si.search(index, "rust ownership memory", vault)
        assert len(results) > 0
        # Rust ownership doc should rank highest
        assert "rust" in results[0]["path"].lower() or "rust" in results[0]["title"].lower()

    def test_snippet_present(self, index, vault):
        results = si.search(index, "python", vault)
        assert len(results) > 0
        # At least one result should have a non-empty snippet
        assert any(r["snippet"] for r in results)

    def test_scores_are_positive(self, index, vault):
        results = si.search(index, "programming language", vault)
        for r in results:
            assert r["score"] > 0

    def test_title_boost_ranks_title_match_higher(self, vault):
        """A doc with query terms in its title should rank above one with terms only in body."""
        # Create two docs: one with "zephyr" in title, one with "zephyr" only in body
        (vault / "Wiki" / "zephyr-guide.md").write_text(
            "---\ntype: living/wiki\ntags: []\nstatus: active\n---\n\n"
            "# Zephyr Guide\n\nThis guide covers the basics.\n"
        )
        (vault / "Wiki" / "wind-patterns.md").write_text(
            "---\ntype: living/wiki\ntags: []\nstatus: active\n---\n\n"
            "# Wind Patterns\n\nThe zephyr is a gentle western wind. "
            "Zephyr winds are common in spring. The zephyr brings warm air.\n"
        )
        index = bi.build_index(vault)
        results = si.search(index, "zephyr", vault)
        assert len(results) >= 2
        # Title match should rank first despite fewer body occurrences
        assert "zephyr-guide" in results[0]["path"]

    def test_title_boost_backward_compatible(self, vault):
        """Index without title_tf still works (graceful fallback)."""
        index = bi.build_index(vault)
        # Strip title_tf from all docs to simulate old index
        for doc in index["documents"]:
            doc.pop("title_tf", None)
        results = si.search(index, "python", vault)
        assert len(results) > 0


# ---------------------------------------------------------------------------
# Snippet extraction
# ---------------------------------------------------------------------------

class TestSnippet:
    def test_snippet_length(self, vault):
        snippet = si.extract_snippet(vault, "Wiki/python-basics.md", ["python"])
        assert len(snippet) <= 300  # Some slack for word boundaries + ellipsis

    def test_snippet_contains_term(self, vault):
        snippet = si.extract_snippet(vault, "Wiki/python-basics.md", ["python"])
        assert "python" in snippet.lower() or "Python" in snippet

    def test_snippet_missing_file(self, vault):
        snippet = si.extract_snippet(vault, "nonexistent.md", ["test"])
        assert snippet == ""

    def test_snippet_no_match_returns_start(self, vault):
        snippet = si.extract_snippet(vault, "Wiki/python-basics.md", ["xyznotfound"])
        # Should return start of body
        assert len(snippet) > 0


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------

class TestParseArgs:
    def test_query_only(self):
        q, t, tag, s, k, j = si.parse_args(["prog", "hello world"])
        assert q == "hello world"
        assert t is None
        assert tag is None
        assert s is None
        assert k == 10
        assert j is False

    def test_type_filter(self):
        q, t, tag, s, k, j = si.parse_args(["prog", "query", "--type", "living/wiki"])
        assert q == "query"
        assert t == "living/wiki"

    def test_tag_filter(self):
        q, t, tag, s, k, j = si.parse_args(["prog", "query", "--tag", "python"])
        assert tag == "python"

    def test_status_filter(self):
        q, t, tag, s, k, j = si.parse_args(["prog", "query", "--status", "shaping"])
        assert s == "shaping"

    def test_top_k(self):
        q, t, tag, s, k, j = si.parse_args(["prog", "query", "--top-k", "5"])
        assert k == 5

    def test_json_mode(self):
        q, t, tag, s, k, j = si.parse_args(["prog", "query", "--json"])
        assert j is True

    def test_all_flags(self):
        q, t, tag, s, k, j = si.parse_args([
            "prog", "query", "--type", "temporal/logs", "--tag", "ai",
            "--status", "done", "--top-k", "3", "--json"
        ])
        assert q == "query"
        assert t == "temporal/logs"
        assert tag == "ai"
        assert s == "done"
        assert k == 3
        assert j is True


# ---------------------------------------------------------------------------
# Resource-scoped search
# ---------------------------------------------------------------------------

class TestSearchResource:
    """Tests for search_resource() — text search across non-artefact resources."""

    @pytest.fixture
    def router_with_resources(self, vault):
        """Create a vault with skills, memories, triggers, styles, plugins and a router."""
        config = vault / "_Config"
        config.mkdir(exist_ok=True)

        # Skills
        skills = config / "Skills"
        (skills / "vault-maintenance").mkdir(parents=True)
        (skills / "vault-maintenance" / "SKILL.md").write_text(
            "---\nname: vault-maintenance\n---\n\n# Vault Maintenance\n\n"
            "Keep the vault tidy. Archive completed designs. Fix broken links.\n"
        )
        (skills / "brain-remote").mkdir(parents=True)
        (skills / "brain-remote" / "SKILL.md").write_text(
            "---\nname: brain-remote\n---\n\n# Brain Remote\n\n"
            "Use Brain MCP tools from external projects. Remote workflow for agents.\n"
        )

        # Styles
        styles = config / "Styles"
        styles.mkdir(exist_ok=True)
        (styles / "writing.md").write_text(
            "---\nname: writing\n---\n\n# Writing Style\n\n"
            "Be concise. Avoid jargon. Use active voice.\n"
        )

        # Memories
        memories = config / "Memories"
        memories.mkdir(exist_ok=True)
        (memories / "python-setup.md").write_text(
            "---\nname: python-setup\ntriggers: [python, setup, environment]\n---\n\n"
            "# Python Setup\n\nUse Python 3.12 with venv. Install via make install.\n"
        )

        # Plugins
        plugins = config / "Plugins"
        plugins.mkdir(exist_ok=True)
        (plugins / "Undertask").mkdir()
        (plugins / "Undertask" / "SKILL.md").write_text(
            "---\nname: Undertask\n---\n\n# Undertask Plugin\n\n"
            "Task management integration. Syncs tasks with external tools.\n"
        )

        router = {
            "skills": [
                {"name": "vault-maintenance", "skill_doc": "_Config/Skills/vault-maintenance/SKILL.md"},
                {"name": "brain-remote", "skill_doc": "_Config/Skills/brain-remote/SKILL.md"},
            ],
            "styles": [
                {"name": "writing", "style_doc": "_Config/Styles/writing.md"},
            ],
            "memories": [
                {"name": "python-setup", "triggers": ["python", "setup", "environment"],
                 "memory_doc": "_Config/Memories/python-setup.md"},
            ],
            "triggers": [
                {"name": "after-work", "category": "after", "condition": "meaningful work",
                 "target": "log", "detail": "Append timestamped entry to today's log"},
                {"name": "session-start", "category": "always", "condition": "session begins",
                 "target": "router", "detail": "Read the router for always-rules"},
            ],
            "plugins": [
                {"name": "Undertask", "skill_doc": "_Config/Plugins/Undertask/SKILL.md"},
            ],
        }
        return vault, router

    def test_search_skill_by_name(self, router_with_resources):
        vault, router = router_with_resources
        results = si.search_resource(router, vault, "skill", "vault")
        assert len(results) >= 1
        assert results[0]["title"] == "vault-maintenance"

    def test_search_skill_by_content(self, router_with_resources):
        vault, router = router_with_resources
        results = si.search_resource(router, vault, "skill", "archive")
        assert len(results) >= 1
        assert results[0]["title"] == "vault-maintenance"

    def test_search_skill_no_match(self, router_with_resources):
        vault, router = router_with_resources
        results = si.search_resource(router, vault, "skill", "xyznonexistent")
        assert results == []

    def test_search_memory_by_trigger(self, router_with_resources):
        vault, router = router_with_resources
        results = si.search_resource(router, vault, "memory", "python")
        assert len(results) >= 1
        assert results[0]["title"] == "python-setup"

    def test_search_memory_by_content(self, router_with_resources):
        vault, router = router_with_resources
        results = si.search_resource(router, vault, "memory", "venv")
        assert len(results) >= 1
        assert results[0]["title"] == "python-setup"

    def test_search_style_by_content(self, router_with_resources):
        vault, router = router_with_resources
        results = si.search_resource(router, vault, "style", "concise")
        assert len(results) >= 1
        assert results[0]["title"] == "writing"

    def test_search_trigger_by_condition(self, router_with_resources):
        vault, router = router_with_resources
        results = si.search_resource(router, vault, "trigger", "meaningful")
        assert len(results) >= 1
        assert results[0]["title"] == "after-work"

    def test_search_trigger_by_target(self, router_with_resources):
        vault, router = router_with_resources
        results = si.search_resource(router, vault, "trigger", "router")
        assert len(results) >= 1
        assert results[0]["title"] == "session-start"

    def test_search_plugin_by_content(self, router_with_resources):
        vault, router = router_with_resources
        results = si.search_resource(router, vault, "plugin", "task management")
        assert len(results) >= 1
        assert results[0]["title"] == "Undertask"

    def test_search_result_fields(self, router_with_resources):
        vault, router = router_with_resources
        results = si.search_resource(router, vault, "skill", "vault")
        assert len(results) > 0
        r = results[0]
        assert "path" in r
        assert "title" in r
        assert "type" in r
        assert "score" in r
        assert "snippet" in r
        assert r["type"] == "skill"
        assert r["score"] > 0

    def test_search_top_k(self, router_with_resources):
        vault, router = router_with_resources
        results = si.search_resource(router, vault, "skill", "brain", top_k=1)
        assert len(results) <= 1

    def test_search_artefact_raises(self, router_with_resources):
        vault, router = router_with_resources
        with pytest.raises(ValueError, match="Use search"):
            si.search_resource(router, vault, "artefact", "test")

    def test_search_invalid_resource_raises(self, router_with_resources):
        vault, router = router_with_resources
        with pytest.raises(ValueError, match="not searchable"):
            si.search_resource(router, vault, "workspace", "test")

    def test_search_ranking(self, router_with_resources):
        """Results should be ranked by score descending."""
        vault, router = router_with_resources
        results = si.search_resource(router, vault, "skill", "brain")
        if len(results) >= 2:
            assert results[0]["score"] >= results[1]["score"]
