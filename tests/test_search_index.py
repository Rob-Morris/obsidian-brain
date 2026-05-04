"""Tests for search_index.py — BM25 retrieval search."""

import json
import math
import os

import pytest

import build_index as bi
import config as config_mod
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
    (bc / "session-core.md").write_text("# Session Core\n")
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


class TestSemanticSearch:
    def _doc_meta(self, index):
        return {
            "documents": [
                {
                    "path": doc["path"],
                    "title": doc["title"],
                    "type": doc["type"],
                    "tags": doc.get("tags", []),
                    "status": doc.get("status"),
                }
                for doc in index["documents"]
            ]
        }

    def _aligned_vectors(self, index, path_vectors):
        np = pytest.importorskip("numpy")
        return np.array([path_vectors[doc["path"]] for doc in index["documents"]], dtype=float)

    def test_default_mode_prefers_hybrid_when_semantic_available(self, vault, monkeypatch):
        cfg = {
            "defaults": {
                "flags": {"semantic_retrieval": True},
                "local_runtime": {"semantic_engine_installed": True},
            }
        }
        monkeypatch.setattr(
            si._retrieval_embeddings,
            "semantic_engine_available",
            lambda *_args, **kwargs: kwargs.get("skip_sidecar_check", False),
        )

        assert si.default_search_mode(vault, config=cfg) == "hybrid"

    def test_default_mode_falls_back_to_lexical_when_semantic_disabled(self, vault):
        cfg = {
            "defaults": {
                "flags": {"semantic_retrieval": False},
                "local_runtime": {"semantic_engine_installed": True},
            }
        }
        assert si.default_search_mode(vault, config=cfg) == "lexical"

    def test_default_mode_prefers_hybrid_when_semantic_engine_is_provisioned(
        self,
        vault,
        monkeypatch,
    ):
        cfg = {
            "defaults": {
                "flags": {"semantic_retrieval": True},
                "local_runtime": {"semantic_engine_installed": True},
            }
        }
        monkeypatch.setattr(
            si._retrieval_embeddings,
            "semantic_engine_available",
            lambda *_args, **kwargs: kwargs.get("skip_sidecar_check", False),
        )

        assert si.default_search_mode(vault, config=cfg) == "hybrid"

    def test_semantic_search_uses_document_vectors(self, index, vault, monkeypatch):
        np = pytest.importorskip("numpy")
        monkeypatch.setattr(si, "_encode_query", lambda _query: np.array([1.0, 0.0]))
        vectors = self._aligned_vectors(
            index,
            {
                "Wiki/python-basics.md": [0.80, 0.20],
                "Wiki/rust-ownership.md": [0.10, 0.90],
                "Wiki/javascript-async.md": [0.20, 0.80],
                "Designs/brain-tooling.md": [0.99, 0.01],
                "_Temporal/Logs/2026-03/20260315-python-log.md": [0.75, 0.25],
            },
        )
        meta = self._doc_meta(index)

        results = si.search_semantic(
            "brain tooling architecture",
            vault,
            top_k=3,
            doc_embeddings=vectors,
            embeddings_meta=meta,
        )

        assert len(results) == 3
        assert results[0]["path"] == "Designs/brain-tooling.md"

    def test_semantic_search_respects_filters(self, index, vault, monkeypatch):
        np = pytest.importorskip("numpy")
        monkeypatch.setattr(si, "_encode_query", lambda _query: np.array([1.0, 0.0]))
        vectors = self._aligned_vectors(
            index,
            {
                "Wiki/python-basics.md": [0.95, 0.05],
                "Wiki/rust-ownership.md": [0.10, 0.90],
                "Wiki/javascript-async.md": [0.20, 0.80],
                "Designs/brain-tooling.md": [0.85, 0.15],
                "_Temporal/Logs/2026-03/20260315-python-log.md": [0.75, 0.25],
            },
        )
        meta = self._doc_meta(index)

        results = si.search_semantic(
            "python",
            vault,
            tag_filter="brain-core",
            top_k=5,
            doc_embeddings=vectors,
            embeddings_meta=meta,
        )

        assert [result["path"] for result in results] == ["Designs/brain-tooling.md"]

    def test_semantic_search_errors_without_sidecars(self, vault):
        with pytest.raises(si.SearchModeUnavailableError):
            si.search_semantic("brain", vault)

    def test_hybrid_search_combines_lexical_and_semantic_results(self, index, vault, monkeypatch):
        np = pytest.importorskip("numpy")
        monkeypatch.setattr(si, "_encode_query", lambda _query: np.array([1.0, 0.0]))
        vectors = self._aligned_vectors(
            index,
            {
                "Wiki/python-basics.md": [0.40, 0.60],
                "Wiki/rust-ownership.md": [0.05, 0.95],
                "Wiki/javascript-async.md": [0.10, 0.90],
                "Designs/brain-tooling.md": [0.98, 0.02],
                "_Temporal/Logs/2026-03/20260315-python-log.md": [0.70, 0.30],
            },
        )
        meta = self._doc_meta(index)

        results = si.search_hybrid(
            index,
            "python programming",
            vault,
            top_k=5,
            doc_embeddings=vectors,
            embeddings_meta=meta,
        )

        paths = [result["path"] for result in results]
        assert "Wiki/python-basics.md" in paths
        assert "Designs/brain-tooling.md" in paths[:3]

    def test_hybrid_search_keeps_lexical_exact_anchor_wins(self, index, vault, monkeypatch):
        lexical_results = [
            {
                "path": "Wiki/python-basics.md",
                "title": "Python Basics",
                "type": "living/wiki",
                "status": "active",
                "score": 42.0,
                "snippet": "",
            }
        ]
        semantic_called = False

        def fake_search(*_args, **_kwargs):
            return lexical_results

        def fake_search_semantic(*_args, **_kwargs):
            nonlocal semantic_called
            semantic_called = True
            return []

        monkeypatch.setattr(si, "search", fake_search)
        monkeypatch.setattr(si, "search_semantic", fake_search_semantic)
        monkeypatch.setattr(si, "_attach_snippets", lambda *_args, **_kwargs: None)

        results = si.search_hybrid(index, "v0.16 release notes", vault, top_k=1)

        assert not semantic_called
        assert results == lexical_results

    def test_fuse_rrf_promotes_semantic_champion_when_margin_is_strong(self):
        lexical_results = [
            {"path": "A.md", "title": "A", "type": "living/wiki", "status": "active", "score": 10.0},
            {"path": "C.md", "title": "C", "type": "living/wiki", "status": "active", "score": 9.0},
            {"path": "B.md", "title": "B", "type": "living/wiki", "status": "active", "score": 8.0},
        ]
        semantic_results = [
            {"path": "B.md", "title": "B", "type": "living/wiki", "status": "active", "score": 0.90},
            {"path": "A.md", "title": "A", "type": "living/wiki", "status": "active", "score": 0.80},
        ]

        no_bonus = pytest.MonkeyPatch()
        no_bonus.setattr(si, "SEMANTIC_CHAMPION_BONUS", 0.0)
        try:
            baseline = si._fuse_rrf(lexical_results, semantic_results, top_k=2)
        finally:
            no_bonus.undo()

        boosted = si._fuse_rrf(lexical_results, semantic_results, top_k=2)

        assert baseline[0]["path"] == "A.md"
        assert boosted[0]["path"] == "B.md"

    def test_fuse_rrf_does_not_promote_semantic_champion_when_margin_is_small(self):
        lexical_results = [
            {"path": "A.md", "title": "A", "type": "living/wiki", "status": "active", "score": 10.0},
            {"path": "C.md", "title": "C", "type": "living/wiki", "status": "active", "score": 9.0},
            {"path": "B.md", "title": "B", "type": "living/wiki", "status": "active", "score": 8.0},
        ]
        semantic_results = [
            {"path": "B.md", "title": "B", "type": "living/wiki", "status": "active", "score": 0.81},
            {"path": "A.md", "title": "A", "type": "living/wiki", "status": "active", "score": 0.80},
        ]

        boosted = si._fuse_rrf(lexical_results, semantic_results, top_k=2)

        assert boosted[0]["path"] == "A.md"

    def test_hybrid_search_applies_semantic_rescue_when_lexical_and_semantic_disagree(self, index, vault, monkeypatch):
        lexical_results = [
            {"path": "A.md", "title": "Application Process Research", "type": "living/wiki", "status": "active", "score": 20.0},
            {"path": "C.md", "title": "Architecture Notes", "type": "living/wiki", "status": "active", "score": 19.9},
        ]
        semantic_results = [
            {"path": "B.md", "title": "Collaborative App Design Chat", "type": "living/wiki", "status": "active", "score": 0.39},
            {"path": "D.md", "title": "Three Level Context", "type": "living/wiki", "status": "active", "score": 0.378},
        ]
        monkeypatch.setattr(si, "search", lambda *_args, **_kwargs: lexical_results)
        monkeypatch.setattr(si, "search_semantic", lambda *_args, **_kwargs: semantic_results)
        monkeypatch.setattr(si, "_attach_snippets", lambda *_args, **_kwargs: None)

        results = si.search_hybrid(
            index,
            "conversational exploration of collaborative application framework with event propagation architecture",
            vault,
            top_k=2,
        )

        assert results[0]["path"] == "B.md"

    def test_hybrid_search_does_not_apply_semantic_rescue_when_top_results_overlap(self, index, vault, monkeypatch):
        lexical_results = [
            {"path": "A.md", "title": "Documentation Audit Skills", "type": "living/wiki", "status": "active", "score": 20.0},
            {"path": "C.md", "title": "Implementation Notes", "type": "living/wiki", "status": "active", "score": 19.5},
            {"path": "D.md", "title": "Shared Supporting Doc", "type": "living/wiki", "status": "active", "score": 19.0},
        ]
        semantic_results = [
            {"path": "B.md", "title": "Implementation Plan", "type": "living/wiki", "status": "active", "score": 0.39},
            {"path": "D.md", "title": "Shared Supporting Doc", "type": "living/wiki", "status": "active", "score": 0.378},
        ]
        monkeypatch.setattr(si, "search", lambda *_args, **_kwargs: lexical_results)
        monkeypatch.setattr(si, "search_semantic", lambda *_args, **_kwargs: semantic_results)
        monkeypatch.setattr(si, "_attach_snippets", lambda *_args, **_kwargs: None)

        results = si.search_hybrid(
            index,
            "skills for auditing docs so they are actually usable by agents",
            vault,
            top_k=2,
        )

        assert results[0]["path"] == "D.md"


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
        q, t, tag, s, k, j, m = si.parse_args(["prog", "hello world"])
        assert q == "hello world"
        assert t is None
        assert tag is None
        assert s is None
        assert k == 10
        assert j is False
        assert m is None

    def test_type_filter(self):
        q, t, tag, s, k, j, _m = si.parse_args(["prog", "query", "--type", "living/wiki"])
        assert q == "query"
        assert t == "living/wiki"

    def test_tag_filter(self):
        q, t, tag, s, k, j, _m = si.parse_args(["prog", "query", "--tag", "python"])
        assert tag == "python"

    def test_status_filter(self):
        q, t, tag, s, k, j, _m = si.parse_args(["prog", "query", "--status", "shaping"])
        assert s == "shaping"

    def test_top_k(self):
        q, t, tag, s, k, j, _m = si.parse_args(["prog", "query", "--top-k", "5"])
        assert k == 5

    def test_json_mode(self):
        q, t, tag, s, k, j, _m = si.parse_args(["prog", "query", "--json"])
        assert j is True

    def test_mode(self):
        q, t, tag, s, k, j, m = si.parse_args(["prog", "query", "--mode", "hybrid"])
        assert q == "query"
        assert m == "hybrid"

    def test_all_flags(self):
        q, t, tag, s, k, j, m = si.parse_args([
            "prog", "query", "--type", "temporal/logs", "--tag", "ai",
            "--status", "done", "--top-k", "3", "--mode", "semantic", "--json"
        ])
        assert q == "query"
        assert t == "temporal/logs"
        assert tag == "ai"
        assert s == "done"
        assert k == 3
        assert j is True
        assert m == "semantic"


# ---------------------------------------------------------------------------
# CLI mode handling
# ---------------------------------------------------------------------------

class TestCliModes:
    def test_main_errors_when_config_load_fails(self, vault, monkeypatch, capsys):
        monkeypatch.setattr(si, "find_vault_root", lambda: vault)
        monkeypatch.setattr(
            si,
            "load_index",
            lambda _vault: {
                "documents": [],
                "corpus_stats": {"total_docs": 0, "avg_dl": 0, "df": {}},
                "bm25_params": {"k1": 1.5, "b": 0.75},
            },
        )
        monkeypatch.setattr(
            config_mod,
            "load_config",
            lambda _vault: (_ for _ in ()).throw(ValueError("bad config")),
        )
        monkeypatch.setattr(si.sys, "argv", ["search_index.py", "brain"])

        with pytest.raises(SystemExit) as exc:
            si.main()

        assert exc.value.code == 1
        assert "failed to load config: bad config" in capsys.readouterr().err

    def test_main_errors_when_semantic_mode_disabled(self, vault, monkeypatch, capsys):
        monkeypatch.setattr(si, "find_vault_root", lambda: vault)
        monkeypatch.setattr(
            si,
            "load_index",
            lambda _vault: {
                "documents": [],
                "corpus_stats": {"total_docs": 0, "avg_dl": 0, "df": {}},
                "bm25_params": {"k1": 1.5, "b": 0.75},
            },
        )
        monkeypatch.setattr(
            si._retrieval_embeddings,
            "semantic_retrieval_enabled",
            lambda _vault, **_: False,
        )
        monkeypatch.setattr(si.sys, "argv", ["search_index.py", "brain", "--mode", "semantic"])

        with pytest.raises(SystemExit) as exc:
            si.main()

        assert exc.value.code == 1
        assert "semantic retrieval is disabled" in capsys.readouterr().err

    def test_main_errors_when_hybrid_mode_unavailable(
        self,
        vault,
        monkeypatch,
        capsys,
    ):
        monkeypatch.setattr(si, "find_vault_root", lambda: vault)
        monkeypatch.setattr(
            si,
            "load_index",
            lambda _vault: {
                "documents": [],
                "corpus_stats": {"total_docs": 0, "avg_dl": 0, "df": {}},
                "bm25_params": {"k1": 1.5, "b": 0.75},
            },
        )
        monkeypatch.setattr(
            si._retrieval_embeddings,
            "semantic_retrieval_enabled",
            lambda _vault, **_: True,
        )
        monkeypatch.setattr(
            si._retrieval_embeddings,
            "semantic_engine_available",
            lambda *_args, **_kwargs: False,
        )
        monkeypatch.setattr(si.sys, "argv", ["search_index.py", "brain", "--mode", "hybrid"])

        with pytest.raises(SystemExit) as exc:
            si.main()

        assert exc.value.code == 1
        assert "semantic retrieval is unavailable" in capsys.readouterr().err


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
        (skills / "test-skill").mkdir(parents=True)
        (skills / "test-skill" / "SKILL.md").write_text(
            "---\nname: test-skill\n---\n\n# Test Skill\n\n"
            "A test skill for search index testing.\n"
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
                {"name": "test-skill", "skill_doc": "_Config/Skills/test-skill/SKILL.md"},
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
