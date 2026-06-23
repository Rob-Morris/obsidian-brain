"""Tests for search_index.py — BM25 retrieval search."""

import json
import os

import pytest

from _search.filters import SearchFilters
import _lifecycle.retrieval_errors as retrieval_errors
import _search.hybrid_query as hybrid_query
import _search.index as search_index_mod
import _search.lexical as lexical
import _search.lexical_query as lexical_query
import _search.mode as search_mode
import _search.resource as search_resource_mod
import _search.semantic_query as semantic_query
import _search.snippet as snippet_mod
import _semantic.model as semantic_model
import _semantic.runtime as semantic_runtime

from conftest import build_and_persist_index, make_searchable_vault


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path):
    """Create a vault with searchable content (shared corpus, see conftest)."""
    return make_searchable_vault(tmp_path)


@pytest.fixture
def index(vault):
    """Build and return an index for the test vault."""
    return search_index_mod.build_index(vault).index


def write_local_config(vault, body):
    """Write a local config override for CLI wrapper tests."""
    config_path = vault / ".brain" / "local" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(body, encoding="utf-8")


# ---------------------------------------------------------------------------
# Tokenisation consistency
# ---------------------------------------------------------------------------

class TestTokenise:
    def test_matches_build_index(self):
        """Lexical query and token helpers intentionally share one tokenizer."""
        assert lexical_query.tokenise is lexical.tokenise


class TestSemanticRuntime:
    @pytest.mark.semantic
    def test_rank_against_requires_matrix_and_metadata_lengths_to_match(self):
        np = pytest.importorskip("numpy")
        matrix = np.array([[1.0, 0.0], [0.0, 1.0]])
        query_vec = np.array([1.0, 0.0])

        with pytest.raises(AssertionError, match="matrix row count must match"):
            semantic_runtime.rank_against(query_vec, matrix, [{"path": "A.md"}])


# ---------------------------------------------------------------------------
# Search results
# ---------------------------------------------------------------------------

class TestSearch:
    def test_basic_search(self, index, vault):
        results = lexical_query.search(index, "python programming", vault)
        assert len(results) > 0
        # Python basics should rank high — has "python" multiple times
        assert results[0]["path"].endswith("python-basics.md") or "python" in results[0]["title"].lower()

    def test_result_fields(self, index, vault):
        results = lexical_query.search(index, "python", vault)
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
        results = lexical_query.search(index, "xyznonexistent", vault)
        assert results == []

    def test_empty_query(self, index, vault):
        results = lexical_query.search(index, "", vault)
        assert results == []

    def test_top_k_limit(self, index, vault):
        results = lexical_query.search(index, "python", vault, top_k=1)
        assert len(results) <= 1

    def test_type_filter(self, index, vault):
        results = lexical_query.search(
            index,
            "python",
            vault,
            filters=SearchFilters(type="temporal/logs"),
        )
        assert len(results) > 0
        assert all(r["type"] == "temporal/logs" for r in results)

    def test_type_filter_no_match(self, index, vault):
        results = lexical_query.search(
            index,
            "python",
            vault,
            filters=SearchFilters(type="living/nonexistent"),
        )
        assert results == []

    def test_tag_filter(self, index, vault):
        results = lexical_query.search(
            index,
            "programming",
            vault,
            filters=SearchFilters(tag="python"),
        )
        assert len(results) > 0
        # Should only include docs tagged with python

    def test_status_filter(self, index, vault):
        results = lexical_query.search(
            index,
            "python",
            vault,
            filters=SearchFilters(status="active"),
        )
        assert len(results) > 0
        assert all(r["status"] == "active" for r in results)

    def test_status_filter_no_match(self, index, vault):
        results = lexical_query.search(
            index,
            "python",
            vault,
            filters=SearchFilters(status="nonexistent"),
        )
        assert results == []

    def test_ranking_order(self, index, vault):
        results = lexical_query.search(index, "python", vault)
        if len(results) >= 2:
            assert results[0]["score"] >= results[1]["score"]

    def test_multi_term_query(self, index, vault):
        results = lexical_query.search(index, "rust ownership memory", vault)
        assert len(results) > 0
        # Rust ownership doc should rank highest
        assert "rust" in results[0]["path"].lower() or "rust" in results[0]["title"].lower()

    def test_snippet_present(self, index, vault):
        results = lexical_query.search(index, "python", vault)
        assert len(results) > 0
        # At least one result should have a non-empty snippet
        assert any(r["snippet"] for r in results)

    def test_scores_are_positive(self, index, vault):
        results = lexical_query.search(index, "programming language", vault)
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
        index = search_index_mod.build_index(vault).index
        results = lexical_query.search(index, "zephyr", vault)
        assert len(results) >= 2
        # Title match should rank first despite fewer body occurrences
        assert "zephyr-guide" in results[0]["path"]

    def test_title_boost_backward_compatible(self, vault):
        """Index without title_tf still works (graceful fallback)."""
        index = search_index_mod.build_index(vault).index
        # Strip title_tf from all docs to simulate old index
        for doc in index["documents"]:
            doc.pop("title_tf", None)
        results = lexical_query.search(index, "python", vault)
        assert len(results) > 0


class TestSemanticSearch:
    def _write_doc(
        self,
        vault,
        rel_path,
        title,
        body,
        *,
        artefact_type="living/wiki",
        tags=None,
        status="active",
    ):
        path = vault / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tag_list = ", ".join(tags or [])
        path.write_text(
            "---\n"
            f"type: {artefact_type}\n"
            f"tags: [{tag_list}]\n"
            f"status: {status}\n"
            "---\n\n"
            f"# {title}\n\n"
            f"{body}\n",
            encoding="utf-8",
        )

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

    def _default_vectors(self, index, base_vector):
        return {doc["path"]: list(base_vector) for doc in index["documents"]}

    def test_default_mode_prefers_hybrid_when_semantic_available(self, vault, monkeypatch):
        cfg = {
            "defaults": {
                "flags": {"semantic_retrieval": True},
                "local_runtime": {"semantic_engine_installed": True},
            }
        }
        monkeypatch.setattr(
            semantic_runtime,
            "semantic_engine_available",
            lambda *_args, **kwargs: kwargs.get("skip_sidecar_check", False),
        )

        assert search_mode.default_search_mode(vault, config=cfg) == "hybrid"

    def test_default_mode_falls_back_to_lexical_when_semantic_disabled(self, vault):
        cfg = {
            "defaults": {
                "flags": {"semantic_retrieval": False},
                "local_runtime": {"semantic_engine_installed": True},
            }
        }
        assert search_mode.default_search_mode(vault, config=cfg) == "lexical"

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
            semantic_runtime,
            "semantic_engine_available",
            lambda *_args, **kwargs: kwargs.get("skip_sidecar_check", False),
        )

        assert search_mode.default_search_mode(vault, config=cfg) == "hybrid"

    @pytest.mark.semantic
    def test_semantic_search_uses_document_vectors(self, index, vault, monkeypatch):
        np = pytest.importorskip("numpy")
        monkeypatch.setattr(
            semantic_query,
            "encode_query_or_unavailable",
            lambda _vault, _query, *, query_encoder=None: np.array([1.0, 0.0]),
        )
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

        results = semantic_query.search_semantic(
            "brain tooling architecture",
            vault,
            top_k=3,
            doc_embeddings=vectors,
            embeddings_meta=meta,
        )

        assert len(results) == 3
        assert results[0]["path"] == "Designs/brain-tooling.md"

    @pytest.mark.semantic
    def test_semantic_search_respects_filters(self, index, vault, monkeypatch):
        np = pytest.importorskip("numpy")
        monkeypatch.setattr(
            semantic_query,
            "encode_query_or_unavailable",
            lambda _vault, _query, *, query_encoder=None: np.array([1.0, 0.0]),
        )
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

        results = semantic_query.search_semantic(
            "python",
            vault,
            filters=SearchFilters(tag="brain-core"),
            top_k=5,
            doc_embeddings=vectors,
            embeddings_meta=meta,
        )

        assert [result["path"] for result in results] == ["Designs/brain-tooling.md"]

    def test_semantic_search_errors_without_sidecars(self, vault):
        with pytest.raises(search_mode.SearchModeUnavailableError):
            semantic_query.search_semantic("brain", vault)

    def test_semantic_search_reports_corrupt_sidecars(self, vault, monkeypatch):
        def boom(_vault):
            raise semantic_runtime.SemanticEmbeddingsLoadError("corrupt sidecars")

        monkeypatch.setattr(semantic_runtime, "load_doc_embeddings", boom)

        with pytest.raises(search_mode.SearchModeUnavailableError, match="corrupt sidecars"):
            semantic_query.search_semantic("brain", vault)

    @pytest.mark.semantic
    def test_load_doc_embeddings_reports_stale_router_sidecars(self, vault):
        np = pytest.importorskip("numpy")
        local_dir = vault / ".brain" / "local"
        local_dir.mkdir(parents=True, exist_ok=True)
        (local_dir / "compiled-router.json").write_text(
            json.dumps({"meta": {"source_hash": "sha256:current-router"}}),
            encoding="utf-8",
        )
        np.save(vault / semantic_runtime.DOC_EMBEDDINGS_REL, np.zeros((1, 2)))
        (vault / semantic_runtime.EMBEDDINGS_META_REL).write_text(
            json.dumps(
                {
                    semantic_runtime.ROUTER_SOURCE_HASH_KEY: "sha256:stale-router",
                    "documents": [],
                    "types": [],
                }
            ),
            encoding="utf-8",
        )

        with pytest.raises(
            search_mode.SearchModeUnavailableError,
            match="built for a different compiled router",
        ):
            semantic_query.load_doc_embeddings_or_unavailable(vault)

    @pytest.mark.semantic
    def test_semantic_search_wraps_semantic_model_errors(self, vault, monkeypatch):
        np = pytest.importorskip("numpy")

        def boom(_vault, _query, *, query_encoder=None):
            raise semantic_model.SemanticModelMissingError("missing model snapshot")

        monkeypatch.setattr(semantic_runtime, "encode_query", boom)

        with pytest.raises(search_mode.SearchModeUnavailableError, match="missing model snapshot"):
            semantic_query.search_semantic(
                "brain",
                vault,
                doc_embeddings=np.array([[1.0, 0.0]]),
                embeddings_meta={"documents": [{"path": "Wiki/python-basics.md"}]},
            )

    @pytest.mark.semantic
    def test_hybrid_search_combines_lexical_and_semantic_results(self, index, vault, monkeypatch):
        np = pytest.importorskip("numpy")
        monkeypatch.setattr(
            semantic_query,
            "encode_query_or_unavailable",
            lambda _vault, _query, *, query_encoder=None: np.array([1.0, 0.0]),
        )
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

        results = hybrid_query.search_hybrid(
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

        monkeypatch.setattr(hybrid_query, "search", fake_search)
        monkeypatch.setattr(hybrid_query, "search_semantic", fake_search_semantic)
        monkeypatch.setattr(hybrid_query, "attach_snippets", lambda *_args, **_kwargs: None)

        results = hybrid_query.search_hybrid(index, "v0.16 release notes", vault, top_k=1)

        assert not semantic_called
        assert results == lexical_results

    def test_hybrid_search_anchor_query_honours_attach_snippets_flag(self, vault):
        (vault / "Wiki" / "DD-046-retrieval-ownership.md").write_text(
            "---\ntype: living/wiki\ntags: [design]\nstatus: active\n---\n\n"
            "# DD-046 Retrieval Ownership\n\n"
            "DD-046 defines retrieval ownership and wrapper boundaries.\n",
            encoding="utf-8",
        )
        index = search_index_mod.build_index(vault).index

        results = hybrid_query.search_hybrid(
            index,
            "DD-046 retrieval ownership",
            vault,
            top_k=1,
            attach_snippets_to_results=False,
        )

        assert results
        assert results[0]["path"] == "Wiki/DD-046-retrieval-ownership.md"
        assert "snippet" not in results[0]

    def test_core_title_tokens_strip_brain_product_prefix_only(self):
        assert hybrid_query._core_title_tokens("Brain MCP Server") == ["mcp", "server"]
        assert hybrid_query._core_title_tokens("Atlas MCP Server") == ["atlas", "mcp", "server"]

    @pytest.mark.semantic
    def test_hybrid_search_end_to_end_promotes_semantic_champion(self, vault, monkeypatch):
        self._write_doc(
            vault,
            "Wiki/lattice-harbour-guide.md",
            "Lattice Harbour Guide",
            (
                "Lattice harbour planning notes explain lattice harbour routing, "
                "lattice harbour maintenance, and lattice harbour planning."
            ),
        )
        self._write_doc(
            vault,
            "Wiki/harbour-operations.md",
            "Harbour Operations",
            (
                "Harbour operations describe lattice traffic, harbour routing, "
                "and manual harbour operations."
            ),
        )
        self._write_doc(
            vault,
            "Wiki/conceptual-docking-notes.md",
            "Conceptual Docking Notes",
            (
                "Conceptual docking notes mention lattice harbour once while "
                "focusing on docking abstractions."
            ),
        )
        index = search_index_mod.build_index(vault).index
        path_vectors = self._default_vectors(index, [0.0, 1.0])
        path_vectors.update(
            {
                "Wiki/lattice-harbour-guide.md": [0.2, 0.98],
                "Wiki/harbour-operations.md": [0.0, 1.0],
                "Wiki/conceptual-docking-notes.md": [1.0, 0.0],
            }
        )
        vectors = self._aligned_vectors(index, path_vectors)
        meta = self._doc_meta(index)
        np = pytest.importorskip("numpy")
        monkeypatch.setattr(
            semantic_query,
            "encode_query_or_unavailable",
            lambda _vault, _query, *, query_encoder=None: np.array([1.0, 0.0]),
        )

        no_bonus = pytest.MonkeyPatch()
        no_bonus.setattr(hybrid_query, "SEMANTIC_CHAMPION_BONUS", 0.0)
        try:
            baseline = hybrid_query.search_hybrid(
                index,
                "lattice harbour",
                vault,
                top_k=2,
                doc_embeddings=vectors,
                embeddings_meta=meta,
            )
        finally:
            no_bonus.undo()

        boosted = hybrid_query.search_hybrid(
            index,
            "lattice harbour",
            vault,
            top_k=2,
            doc_embeddings=vectors,
            embeddings_meta=meta,
        )

        assert baseline[0]["path"] == "Wiki/lattice-harbour-guide.md"
        assert boosted[0]["path"] == "Wiki/conceptual-docking-notes.md"

    @pytest.mark.semantic
    def test_hybrid_search_end_to_end_promotes_brain_title_champion(self, vault, monkeypatch):
        self._write_doc(
            vault,
            "Wiki/brain-mcp-server.md",
            "Brain MCP Server",
            (
                "The MCP server design is the authoritative MCP server tool "
                "surface for the Brain tooling stack."
            ),
            tags=["brain-core", "tooling"],
        )
        self._write_doc(
            vault,
            "Wiki/brain-remote-administration.md",
            "Brain Remote Administration",
            (
                "Remote administration guidance covers operator access and "
                "maintenance procedures."
            ),
            tags=["brain-core"],
        )
        self._write_doc(
            vault,
            "Wiki/mcp-server-logging.md",
            "MCP Server Logging",
            (
                "Logging guidance describes MCP server traces, MCP server "
                "spans, and log inspection workflows."
            ),
            tags=["brain-core", "logging"],
        )
        index = search_index_mod.build_index(vault).index
        path_vectors = self._default_vectors(index, [0.0, 1.0])
        path_vectors.update(
            {
                "Wiki/brain-mcp-server.md": [0.2, 0.98],
                "Wiki/brain-remote-administration.md": [0.0, 1.0],
                "Wiki/mcp-server-logging.md": [1.0, 0.0],
            }
        )
        vectors = self._aligned_vectors(index, path_vectors)
        meta = self._doc_meta(index)
        np = pytest.importorskip("numpy")
        monkeypatch.setattr(
            semantic_query,
            "encode_query_or_unavailable",
            lambda _vault, _query, *, query_encoder=None: np.array([1.0, 0.0]),
        )
        query = "authoritative design for the MCP server tool surface"

        no_bonus = pytest.MonkeyPatch()
        no_bonus.setattr(hybrid_query, "LEXICAL_TITLE_CHAMPION_BONUS", 0.0)
        try:
            baseline = hybrid_query.search_hybrid(
                index,
                query,
                vault,
                top_k=2,
                doc_embeddings=vectors,
                embeddings_meta=meta,
            )
        finally:
            no_bonus.undo()

        boosted = hybrid_query.search_hybrid(
            index,
            query,
            vault,
            top_k=2,
            doc_embeddings=vectors,
            embeddings_meta=meta,
        )

        assert baseline[0]["path"] == "Wiki/mcp-server-logging.md"
        assert boosted[0]["path"] == "Wiki/brain-mcp-server.md"

    @pytest.mark.semantic
    def test_hybrid_search_end_to_end_applies_semantic_rescue(self, vault, monkeypatch):
        self._write_doc(
            vault,
            "Wiki/application-process-research.md",
            "Application Process Research",
            (
                "This research covers conversational exploration of a "
                "collaborative application framework with event propagation "
                "orchestration for operators."
            ),
        )
        self._write_doc(
            vault,
            "Wiki/workflow-notes.md",
            "Workflow Notes",
            (
                "Workflow notes examine collaborative application framework "
                "patterns and event propagation trade-offs."
            ),
        )
        self._write_doc(
            vault,
            "Wiki/operations-study.md",
            "Operations Study",
            (
                "Operations study tracks conversational exploration and "
                "framework orchestration patterns."
            ),
        )
        self._write_doc(
            vault,
            "Wiki/collaborative-app-design-chat.md",
            "Collaborative App Design Chat",
            (
                "This chat captures design discussions about a collaborative "
                "app and facilitation patterns."
            ),
        )
        self._write_doc(
            vault,
            "Wiki/three-level-context.md",
            "Three Level Context",
            "Layered context notes for semantic ranking experiments.",
        )
        self._write_doc(
            vault,
            "Wiki/ambient-operations.md",
            "Ambient Operations",
            "Ambient operational notes for background coordination flows.",
        )
        index = search_index_mod.build_index(vault).index
        path_vectors = self._default_vectors(index, [0.0, 1.0])
        path_vectors.update(
            {
                "Wiki/application-process-research.md": [0.3, 0.95],
                "Wiki/workflow-notes.md": [0.2, 0.98],
                "Wiki/operations-study.md": [0.1, 1.0],
                "Wiki/collaborative-app-design-chat.md": [1.0, 0.0],
                "Wiki/three-level-context.md": [0.95, 0.05],
                "Wiki/ambient-operations.md": [0.9, 0.1],
            }
        )
        vectors = self._aligned_vectors(index, path_vectors)
        meta = self._doc_meta(index)
        np = pytest.importorskip("numpy")
        monkeypatch.setattr(
            semantic_query,
            "encode_query_or_unavailable",
            lambda _vault, _query, *, query_encoder=None: np.array([1.0, 0.0]),
        )
        monkeypatch.setattr(hybrid_query, "SEMANTIC_CHAMPION_BONUS", 0.0)
        query = (
            "conversational exploration of collaborative application framework "
            "with event propagation orchestration"
        )

        no_bonus = pytest.MonkeyPatch()
        no_bonus.setattr(hybrid_query, "SEMANTIC_RESCUE_BONUS", 0.0)
        try:
            baseline = hybrid_query.search_hybrid(
                index,
                query,
                vault,
                top_k=2,
                doc_embeddings=vectors,
                embeddings_meta=meta,
            )
        finally:
            no_bonus.undo()

        boosted = hybrid_query.search_hybrid(
            index,
            query,
            vault,
            top_k=2,
            doc_embeddings=vectors,
            embeddings_meta=meta,
        )

        assert baseline[0]["path"] == "Wiki/application-process-research.md"
        assert boosted[0]["path"] == "Wiki/collaborative-app-design-chat.md"

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
        no_bonus.setattr(hybrid_query, "SEMANTIC_CHAMPION_BONUS", 0.0)
        try:
            baseline = hybrid_query._fuse_rrf(lexical_results, semantic_results, top_k=2)
        finally:
            no_bonus.undo()

        boosted = hybrid_query._fuse_rrf(lexical_results, semantic_results, top_k=2)

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

        boosted = hybrid_query._fuse_rrf(lexical_results, semantic_results, top_k=2)

        assert boosted[0]["path"] == "A.md"

    def test_fuse_rrf_promotes_lexical_champion_when_query_contains_core_title_phrase(self):
        lexical_results = [
            {
                "path": "A.md",
                "title": "Brain MCP Server",
                "type": "living/wiki",
                "status": "active",
                "score": 24.0,
            },
            {
                "path": "C.md",
                "title": "Brain Remote Administration",
                "type": "living/wiki",
                "status": "active",
                "score": 18.0,
            },
            {
                "path": "B.md",
                "title": "MCP Server Logging",
                "type": "living/wiki",
                "status": "active",
                "score": 17.0,
            },
        ]
        semantic_results = [
            {
                "path": "B.md",
                "title": "MCP Server Logging",
                "type": "living/wiki",
                "status": "active",
                "score": 0.90,
            },
            {
                "path": "A.md",
                "title": "Brain MCP Server",
                "type": "living/wiki",
                "status": "active",
                "score": 0.80,
            },
        ]

        no_bonus = pytest.MonkeyPatch()
        no_bonus.setattr(hybrid_query, "LEXICAL_TITLE_CHAMPION_BONUS", 0.0)
        try:
            baseline = hybrid_query._fuse_rrf(
                lexical_results,
                semantic_results,
                top_k=2,
                query_tokens=lexical.tokenise(
                    "authoritative design for the MCP server tool surface"
                ),
            )
        finally:
            no_bonus.undo()

        boosted = hybrid_query._fuse_rrf(
            lexical_results,
            semantic_results,
            top_k=2,
            query_tokens=lexical.tokenise(
                "authoritative design for the MCP server tool surface"
            ),
        )

        assert baseline[0]["path"] == "B.md"
        assert boosted[0]["path"] == "A.md"

    def test_fuse_rrf_does_not_promote_lexical_champion_without_core_title_phrase(self):
        lexical_results = [
            {
                "path": "A.md",
                "title": "Brain Bootstrap Doctor",
                "type": "living/wiki",
                "status": "active",
                "score": 24.0,
            },
            {
                "path": "C.md",
                "title": "Brain Bootstrap Doctor Phase 2",
                "type": "living/wiki",
                "status": "active",
                "score": 18.0,
            },
            {
                "path": "B.md",
                "title": "Bootstrap Streamlining",
                "type": "living/wiki",
                "status": "active",
                "score": 17.0,
            },
        ]
        semantic_results = [
            {
                "path": "B.md",
                "title": "Bootstrap Streamlining",
                "type": "living/wiki",
                "status": "active",
                "score": 0.90,
            },
            {
                "path": "A.md",
                "title": "Brain Bootstrap Doctor",
                "type": "living/wiki",
                "status": "active",
                "score": 0.80,
            },
        ]

        boosted = hybrid_query._fuse_rrf(
            lexical_results,
            semantic_results,
            top_k=2,
            query_tokens=lexical.tokenise(
                "repair workflow for bootstrap and initialisation failures"
            ),
        )

        assert boosted[0]["path"] == "B.md"

    def test_fuse_rrf_does_not_promote_lexical_champion_without_clear_margin(self):
        lexical_results = [
            {
                "path": "A.md",
                "title": "Brain MCP Server",
                "type": "living/wiki",
                "status": "active",
                "score": 20.0,
            },
            {
                "path": "C.md",
                "title": "Brain Remote Administration",
                "type": "living/wiki",
                "status": "active",
                "score": 17.5,
            },
            {
                "path": "B.md",
                "title": "MCP Server Logging",
                "type": "living/wiki",
                "status": "active",
                "score": 17.0,
            },
        ]
        semantic_results = [
            {
                "path": "B.md",
                "title": "MCP Server Logging",
                "type": "living/wiki",
                "status": "active",
                "score": 0.90,
            },
            {
                "path": "A.md",
                "title": "Brain MCP Server",
                "type": "living/wiki",
                "status": "active",
                "score": 0.80,
            },
        ]

        boosted = hybrid_query._fuse_rrf(
            lexical_results,
            semantic_results,
            top_k=2,
            query_tokens=lexical.tokenise(
                "authoritative design for the MCP server tool surface"
            ),
        )

        assert boosted[0]["path"] == "B.md"

    def test_hybrid_search_applies_semantic_rescue_when_lexical_and_semantic_disagree(self, index, vault, monkeypatch):
        lexical_results = [
            {"path": "A.md", "title": "Application Process Research", "type": "living/wiki", "status": "active", "score": 20.0},
            {"path": "C.md", "title": "Architecture Notes", "type": "living/wiki", "status": "active", "score": 19.9},
        ]
        semantic_results = [
            {"path": "B.md", "title": "Collaborative App Design Chat", "type": "living/wiki", "status": "active", "score": 0.39},
            {"path": "D.md", "title": "Three Level Context", "type": "living/wiki", "status": "active", "score": 0.378},
        ]
        monkeypatch.setattr(hybrid_query, "search", lambda *_args, **_kwargs: lexical_results)
        monkeypatch.setattr(hybrid_query, "search_semantic", lambda *_args, **_kwargs: semantic_results)
        monkeypatch.setattr(hybrid_query, "attach_snippets", lambda *_args, **_kwargs: None)

        results = hybrid_query.search_hybrid(
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
        monkeypatch.setattr(hybrid_query, "search", lambda *_args, **_kwargs: lexical_results)
        monkeypatch.setattr(hybrid_query, "search_semantic", lambda *_args, **_kwargs: semantic_results)
        monkeypatch.setattr(hybrid_query, "attach_snippets", lambda *_args, **_kwargs: None)

        results = hybrid_query.search_hybrid(
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
        snippet = snippet_mod.extract_snippet(vault, "Wiki/python-basics.md", ["python"])
        assert len(snippet) <= 300  # Some slack for word boundaries + ellipsis

    def test_snippet_contains_term(self, vault):
        snippet = snippet_mod.extract_snippet(vault, "Wiki/python-basics.md", ["python"])
        assert "python" in snippet.lower() or "Python" in snippet

    def test_snippet_missing_file(self, vault):
        snippet = snippet_mod.extract_snippet(vault, "nonexistent.md", ["test"])
        assert snippet is None

    def test_snippet_no_match_returns_start(self, vault):
        snippet = snippet_mod.extract_snippet(vault, "Wiki/python-basics.md", ["xyznotfound"])
        # Should return start of body
        assert len(snippet) > 0

    def test_search_marks_snippet_unavailable_when_source_is_unreadable(self, vault):
        index = search_index_mod.build_index(vault).index
        (vault / "Wiki" / "python-basics.md").unlink()

        results = lexical_query.search(index, "python", vault)

        assert results
        assert results[0]["path"] == "Wiki/python-basics.md"
        assert results[0]["snippet"] is None


# ---------------------------------------------------------------------------
# Shared mode dispatch
# ---------------------------------------------------------------------------

class TestDispatchSearch:
    def test_dispatch_search_routes_lexical_mode(self, monkeypatch, tmp_path):
        calls = []

        def fake_search(index, query, vault_root, **kwargs):
            calls.append((index, query, vault_root, kwargs))
            return [{"path": "A.md"}]

        monkeypatch.setattr(lexical_query, "search", fake_search)
        index = {"documents": []}

        results = search_mode.dispatch_search(
            index,
            "query",
            tmp_path,
            "lexical",
            filters=SearchFilters(type="living/wiki", tag="python", status="active"),
            top_k=3,
            attach_snippets=False,
        )

        assert results == [{"path": "A.md"}]
        assert calls == [
            (
                index,
                "query",
                tmp_path,
                {
                    "filters": SearchFilters(type="living/wiki", tag="python", status="active"),
                    "top_k": 3,
                    "attach_snippets_to_results": False,
                },
            )
        ]

    def test_dispatch_search_routes_semantic_mode(self, monkeypatch, tmp_path):
        calls = []

        def fake_search_semantic(query, vault_root, **kwargs):
            calls.append((query, vault_root, kwargs))
            return [{"path": "B.md"}]

        monkeypatch.setattr(semantic_query, "search_semantic", fake_search_semantic)

        results = search_mode.dispatch_search(
            {"documents": []},
            "query",
            tmp_path,
            "semantic",
            filters=SearchFilters(type="living/wiki", tag="python", status="active"),
            top_k=4,
            doc_embeddings="doc-vectors",
            embeddings_meta={"documents": []},
            query_encoder="encoder",
            attach_snippets=False,
        )

        assert results == [{"path": "B.md"}]
        assert calls == [
            (
                "query",
                tmp_path,
                {
                    "filters": SearchFilters(type="living/wiki", tag="python", status="active"),
                    "top_k": 4,
                    "doc_embeddings": "doc-vectors",
                    "embeddings_meta": {"documents": []},
                    "query_encoder": "encoder",
                    "attach_snippets_to_results": False,
                },
            )
        ]

    def test_dispatch_search_routes_hybrid_mode(self, monkeypatch, tmp_path):
        calls = []

        def fake_search_hybrid(index, query, vault_root, **kwargs):
            calls.append((index, query, vault_root, kwargs))
            return [{"path": "C.md"}]

        monkeypatch.setattr(hybrid_query, "search_hybrid", fake_search_hybrid)
        index = {"documents": []}

        results = search_mode.dispatch_search(
            index,
            "query",
            tmp_path,
            "hybrid",
            filters=SearchFilters(type="living/wiki", tag="python", status="active"),
            top_k=5,
            doc_embeddings="doc-vectors",
            embeddings_meta={"documents": []},
            query_encoder="encoder",
            attach_snippets=False,
        )

        assert results == [{"path": "C.md"}]
        assert calls == [
            (
                index,
                "query",
                tmp_path,
                {
                    "filters": SearchFilters(type="living/wiki", tag="python", status="active"),
                    "top_k": 5,
                    "doc_embeddings": "doc-vectors",
                    "embeddings_meta": {"documents": []},
                    "query_encoder": "encoder",
                    "attach_snippets_to_results": False,
                },
            )
        ]

    def test_dispatch_search_rejects_unknown_mode(self, tmp_path):
        with pytest.raises(ValueError, match="unknown search mode 'bogus'"):
            search_mode.dispatch_search({}, "query", tmp_path, "bogus")


# ---------------------------------------------------------------------------
class TestCliModes:
    def test_main_errors_when_config_load_fails(self, vault, wrapper_cli):
        config_path = vault / ".brain" / "config.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.mkdir()

        result = wrapper_cli(vault, "search_index.py", "brain")

        assert result.returncode == 1
        assert "failed to load config" in result.stderr

    def test_main_returns_json_results_for_lexical_query(self, vault, wrapper_cli):
        build_and_persist_index(vault)

        result = wrapper_cli(vault, "search_index.py", "python", "--json")

        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload
        assert payload[0]["path"].endswith("python-basics.md")

    def test_main_errors_without_query(self, vault, wrapper_cli):
        build_and_persist_index(vault)

        result = wrapper_cli(vault, "search_index.py")

        assert result.returncode == 1
        assert "Usage: search_index.py" in result.stderr

    def test_main_respects_filters_and_top_k_via_cli(self, vault, wrapper_cli):
        build_and_persist_index(vault)

        result = wrapper_cli(
            vault,
            "search_index.py",
            "python",
            "--type",
            "temporal/logs",
            "--tag",
            "log",
            "--status",
            "done",
            "--top-k",
            "1",
            "--json",
        )

        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert len(payload) == 1
        assert payload[0]["path"] == "_Temporal/Logs/2026-03/20260315-python-log.md"

    def test_main_errors_when_semantic_mode_disabled(self, vault, wrapper_cli):
        build_and_persist_index(vault)

        result = wrapper_cli(vault, "search_index.py", "brain", "--mode", "semantic")

        assert result.returncode == 1
        assert "semantic retrieval is disabled" in result.stderr

    def test_main_errors_when_hybrid_mode_unavailable(self, vault, wrapper_cli):
        build_and_persist_index(vault)
        write_local_config(
            vault,
            "defaults:\n"
            "  flags:\n"
            "    semantic_retrieval: true\n"
            "  local_runtime:\n"
            "    semantic_engine_installed: false\n",
        )

        result = wrapper_cli(vault, "search_index.py", "brain", "--mode", "hybrid")

        assert result.returncode == 1
        assert "semantic retrieval is unavailable" in result.stderr

    def test_main_rejects_unknown_flags(self, vault, wrapper_cli):
        build_and_persist_index(vault)

        result = wrapper_cli(vault, "search_index.py", "brain", "--bogus")

        assert result.returncode == 2
        assert "unrecognized arguments: --bogus" in result.stderr


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
        results = search_resource_mod.search_resource(router, vault, "skill", "vault")
        assert len(results) >= 1
        assert results[0]["title"] == "vault-maintenance"

    def test_search_skill_by_content(self, router_with_resources):
        vault, router = router_with_resources
        results = search_resource_mod.search_resource(router, vault, "skill", "archive")
        assert len(results) >= 1
        assert results[0]["title"] == "vault-maintenance"

    def test_search_skill_no_match(self, router_with_resources):
        vault, router = router_with_resources
        results = search_resource_mod.search_resource(router, vault, "skill", "xyznonexistent")
        assert results == []

    def test_search_memory_by_trigger(self, router_with_resources):
        vault, router = router_with_resources
        results = search_resource_mod.search_resource(router, vault, "memory", "python")
        assert len(results) >= 1
        assert results[0]["title"] == "python-setup"

    def test_search_memory_by_content(self, router_with_resources):
        vault, router = router_with_resources
        results = search_resource_mod.search_resource(router, vault, "memory", "venv")
        assert len(results) >= 1
        assert results[0]["title"] == "python-setup"

    def test_search_style_by_content(self, router_with_resources):
        vault, router = router_with_resources
        results = search_resource_mod.search_resource(router, vault, "style", "concise")
        assert len(results) >= 1
        assert results[0]["title"] == "writing"

    def test_search_trigger_by_condition(self, router_with_resources):
        vault, router = router_with_resources
        results = search_resource_mod.search_resource(router, vault, "trigger", "meaningful")
        assert len(results) >= 1
        assert results[0]["title"] == "after-work"

    def test_search_trigger_by_target(self, router_with_resources):
        vault, router = router_with_resources
        results = search_resource_mod.search_resource(router, vault, "trigger", "router")
        assert len(results) >= 1
        assert results[0]["title"] == "session-start"

    def test_search_plugin_by_content(self, router_with_resources):
        vault, router = router_with_resources
        results = search_resource_mod.search_resource(router, vault, "plugin", "task management")
        assert len(results) >= 1
        assert results[0]["title"] == "Undertask"

    def test_search_result_fields(self, router_with_resources):
        vault, router = router_with_resources
        results = search_resource_mod.search_resource(router, vault, "skill", "vault")
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
        results = search_resource_mod.search_resource(router, vault, "skill", "brain", top_k=1)
        assert len(results) <= 1

    def test_search_artefact_raises(self, router_with_resources):
        vault, router = router_with_resources
        with pytest.raises(ValueError, match="Use lexical_query\\.search"):
            search_resource_mod.search_resource(router, vault, "artefact", "test")

    def test_search_invalid_resource_raises(self, router_with_resources):
        vault, router = router_with_resources
        with pytest.raises(ValueError, match="not searchable"):
            search_resource_mod.search_resource(router, vault, "workspace", "test")

    def test_search_ranking(self, router_with_resources):
        """Results should be ranked by score descending."""
        vault, router = router_with_resources
        results = search_resource_mod.search_resource(router, vault, "skill", "brain")
        if len(results) >= 2:
            assert results[0]["score"] >= results[1]["score"]

    def test_search_raises_when_resource_source_is_unreadable(self, router_with_resources):
        vault, router = router_with_resources
        (vault / "_Config" / "Skills" / "vault-maintenance" / "SKILL.md").unlink()

        with pytest.raises(
            retrieval_errors.UnreadableRetrievalSourceError,
            match="_Config/Skills/vault-maintenance/SKILL.md",
        ) as exc:
            search_resource_mod.search_resource(router, vault, "skill", "vault")
        assert "while searching non-artefact resource text" in str(exc.value)
