"""Tests for Brain MCP server — unit tests with a minimal vault fixture."""

import asyncio
import contextlib
import json
import os
import subprocess
import tempfile
import threading
import time
import types
from unittest.mock import patch

import pytest

from mcp.types import CallToolResult

import _lifecycle.retrieval_assets as retrieval_assets
import _lifecycle.retrieval_errors as retrieval_errors
import _search.paths as search_paths
import _search.semantic_query as semantic_query
import _semantic.assets as semantic_assets
import _semantic.model as semantic_model
import _semantic.runtime as semantic_runtime
from brain_mcp import _server_artefacts, _server_content, _server_reading, server
import compile_router
import obsidian_cli
import process
import retrieval_embeddings
import workspace_registry
import config as config_mod
from _common._yaml import dump_mapping_text



from _mcp_helpers import (
    _assert_error,
    _bump_mtime,
    _extract_create_path,
    _list_result_lines,
    _list_text,
    _progress_payload,
    _search_result_lines,
    _search_text,
    _write_config_text,
    _write_config_yaml,
)


class TestBrainSearch:
    def _enable_semantic_engine(self):
        defaults = server._config.setdefault("defaults", {})
        defaults.setdefault("local_runtime", {})["semantic_engine_installed"] = True

    def _enable_semantic_retrieval(self):
        self._enable_semantic_engine()
        server._config.setdefault("defaults", {}).setdefault("flags", {})["semantic_retrieval"] = True

    def _enable_semantic_processing(self):
        self._enable_semantic_engine()
        server._config.setdefault("defaults", {}).setdefault("flags", {})["semantic_processing"] = True

    def _log_doc_path(self):
        for doc in server._index["documents"]:
            if doc["type"] == "temporal/logs":
                return doc["path"]
        raise AssertionError("expected a temporal/logs fixture document in the index")

    def _semantic_meta(self):
        return {
            "documents": [
                {
                    "path": doc["path"],
                    "title": doc["title"],
                    "type": doc["type"],
                    "tags": doc.get("tags", []),
                    "status": doc.get("status"),
                }
                for doc in server._index["documents"]
            ]
        }

    def _aligned_vectors(self, path_vectors):
        np = pytest.importorskip("numpy")
        return np.array(
            [path_vectors[doc["path"]] for doc in server._index["documents"]],
            dtype=float,
        )

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

    def test_search_resource_reports_unreadable_source(self, initialized):
        with patch.object(
            _server_reading.search_resource,
            "search_resource",
            side_effect=retrieval_errors.UnreadableRetrievalSourceError(
                "_Config/Skills/Vault Maintenance/SKILL.md",
                "searching non-artefact resource text",
                FileNotFoundError("missing"),
            ),
        ):
            result = server.brain_search("vault", resource="skill")

        _assert_error(result, "unreadable retrieval source")
        assert "while searching non-artefact resource text" in result.content[0].text

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

    def test_search_rejects_unknown_mode(self, initialized):
        result = server.brain_search("brain", mode="vectorish")
        _assert_error(result, "unknown search mode")

    def test_search_semantic_mode_errors_when_disabled(self, initialized):
        result = server.brain_search("brain", mode="semantic")
        _assert_error(result, "semantic retrieval is disabled")

    def test_search_hybrid_mode_errors_when_semantic_unavailable(self, initialized, monkeypatch):
        self._enable_semantic_retrieval()
        monkeypatch.setattr(server, "_ensure_embeddings_fresh", lambda: None)
        monkeypatch.setattr(
            semantic_runtime,
            "semantic_engine_available",
            lambda *args, **kwargs: False,
        )

        result = server.brain_search("brain", mode="hybrid")
        _assert_error(result, "semantic retrieval is unavailable")

    def test_search_resource_rejects_semantic_mode(self, initialized):
        result = server.brain_search("vault", resource="skill", mode="semantic")
        _assert_error(result, "mode applies only to artefact search")

    def test_search_semantic_mode_uses_semantic_source(self, initialized, monkeypatch):
        np = pytest.importorskip("numpy")
        self._enable_semantic_retrieval()
        log_path = self._log_doc_path()
        vectors = self._aligned_vectors(
            {
                "Wiki/brain-overview-abc123.md": [0.98, 0.02],
                "Wiki/python-guide-def456.md": [0.10, 0.90],
                "Designs/brain-tooling-design.md": [0.92, 0.08],
                log_path: [0.40, 0.60],
            }
        )
        meta = self._semantic_meta()
        monkeypatch.setattr(
            semantic_query,
            "encode_query_or_unavailable",
            lambda _vault, _query, *, query_encoder=None: np.array([1.0, 0.0]),
        )
        monkeypatch.setattr(retrieval_embeddings, "semantic_engine_available", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(_server_reading._retrieval_embeddings, "semantic_engine_available", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(semantic_runtime, "semantic_engine_available", lambda *_args, **_kwargs: True)

        def fake_ensure_embeddings_fresh():
            server._doc_embeddings = vectors
            server._embeddings_meta = meta

        monkeypatch.setattr(server, "_ensure_embeddings_fresh", fake_ensure_embeddings_fresh)

        text = _search_text(server.brain_search("brain", mode="semantic"))
        assert "semantic" in text
        assert "Wiki/brain-overview-abc123.md" in text

    def test_search_hybrid_mode_ignores_obsidian_cli(self, initialized, cli_available, monkeypatch):
        np = pytest.importorskip("numpy")
        self._enable_semantic_retrieval()
        log_path = self._log_doc_path()
        vectors = self._aligned_vectors(
            {
                "Wiki/brain-overview-abc123.md": [0.60, 0.40],
                "Wiki/python-guide-def456.md": [0.20, 0.80],
                "Designs/brain-tooling-design.md": [0.99, 0.01],
                log_path: [0.50, 0.50],
            }
        )
        meta = self._semantic_meta()
        monkeypatch.setattr(
            semantic_query,
            "encode_query_or_unavailable",
            lambda _vault, _query, *, query_encoder=None: np.array([1.0, 0.0]),
        )
        monkeypatch.setattr(retrieval_embeddings, "semantic_engine_available", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(_server_reading._retrieval_embeddings, "semantic_engine_available", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(semantic_runtime, "semantic_engine_available", lambda *_args, **_kwargs: True)

        def fake_ensure_embeddings_fresh():
            server._doc_embeddings = vectors
            server._embeddings_meta = meta

        monkeypatch.setattr(server, "_ensure_embeddings_fresh", fake_ensure_embeddings_fresh)
        def _no_cli_search(*_args, **_kwargs):
            raise AssertionError("CLI search should not run for hybrid mode")

        monkeypatch.setattr(obsidian_cli, "search", _no_cli_search)
        monkeypatch.setattr(obsidian_cli, "check_available", lambda: True)
        server._cli_probed_at = 0.0

        text = _search_text(server.brain_search("brain", mode="hybrid"))
        assert "hybrid" in text
        assert "Wiki/brain-overview-abc123.md" in text

    def test_search_omitted_mode_prefers_hybrid_when_semantic_available(self, initialized, cli_available, monkeypatch):
        np = pytest.importorskip("numpy")
        self._enable_semantic_retrieval()
        log_path = self._log_doc_path()
        vectors = self._aligned_vectors(
            {
                "Wiki/brain-overview-abc123.md": [0.60, 0.40],
                "Wiki/python-guide-def456.md": [0.20, 0.80],
                "Designs/brain-tooling-design.md": [0.99, 0.01],
                log_path: [0.50, 0.50],
            }
        )
        meta = self._semantic_meta()
        monkeypatch.setattr(
            semantic_query,
            "encode_query_or_unavailable",
            lambda _vault, _query, *, query_encoder=None: np.array([1.0, 0.0]),
        )
        monkeypatch.setattr(retrieval_embeddings, "semantic_engine_available", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(_server_reading._retrieval_embeddings, "semantic_engine_available", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(semantic_runtime, "semantic_engine_available", lambda *_args, **_kwargs: True)

        def fake_ensure_embeddings_fresh():
            server._doc_embeddings = vectors
            server._embeddings_meta = meta

        monkeypatch.setattr(server, "_ensure_embeddings_fresh", fake_ensure_embeddings_fresh)
        def _no_cli_search(*_args, **_kwargs):
            raise AssertionError("CLI search should not run for omitted mode when hybrid is available")

        monkeypatch.setattr(obsidian_cli, "search", _no_cli_search)
        monkeypatch.setattr(obsidian_cli, "check_available", lambda: True)
        server._cli_probed_at = 0.0

        text = _search_text(server.brain_search("brain"))
        assert "hybrid" in text

    def test_search_omitted_mode_falls_back_to_lexical_when_semantic_unavailable(
        self,
        initialized,
        monkeypatch,
    ):
        self._enable_semantic_retrieval()
        monkeypatch.setattr(server, "_ensure_embeddings_fresh", lambda: None)
        monkeypatch.setattr(retrieval_embeddings, "semantic_engine_available", lambda *args, **kwargs: False)

        text = _search_text(server.brain_search("brain"))
        assert "bm25" in text

    def test_semantic_search_and_process_reuse_cached_query_encoder(self, initialized, monkeypatch):
        np = pytest.importorskip("numpy")
        self._enable_semantic_retrieval()
        self._enable_semantic_processing()
        log_path = self._log_doc_path()
        server._doc_embeddings = self._aligned_vectors(
            {
                "Wiki/brain-overview-abc123.md": [0.98, 0.02],
                "Wiki/python-guide-def456.md": [0.10, 0.90],
                "Designs/brain-tooling-design.md": [0.92, 0.08],
                log_path: [0.40, 0.60],
            }
        )
        server._embeddings_meta = self._semantic_meta()

        load_calls = []

        class FakeEncoder:
            def encode(self, texts, normalize_embeddings=True):
                return np.array([[1.0, 0.0]])

        def fake_load_query_encoder(_vault_root):
            load_calls.append("load")
            return FakeEncoder()

        monkeypatch.setattr(server, "_ensure_embeddings_fresh", lambda: None)
        monkeypatch.setattr(retrieval_embeddings, "semantic_engine_available", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(_server_reading._retrieval_embeddings, "semantic_engine_available", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(semantic_runtime, "semantic_engine_available", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(_server_content._retrieval_embeddings, "semantic_engine_available", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(
            semantic_model,
            "load_local_model",
            fake_load_query_encoder,
        )
        monkeypatch.setattr(
            semantic_model,
            "read_manifest",
            lambda _vault_root: semantic_model.ModelManifest(
                model_name=semantic_model.SHIPPED_MODEL_NAME,
                revision=semantic_model.SHIPPED_MODEL_REVISION,
                provisioned_at="2026-05-06T00:00:00+10:00",
            ),
        )

        semantic_runtime.clear_query_encoder()
        try:
            text = _search_text(server.brain_search("brain", mode="semantic"))
            assert "semantic" in text

            result = server.brain_process(
                operation="resolve",
                content="Reference notes about the Brain system.",
                type="wiki",
                title="Brain Overview",
            )
            assert isinstance(result, str)
            assert len(load_calls) == 1
        finally:
            semantic_runtime.clear_query_encoder()


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
        server._doc_embeddings_dirty = False
        server._type_embeddings_dirty = False

        server._runtime().mark_embeddings_dirty()

        assert server._doc_embeddings_dirty is True
        assert server._type_embeddings_dirty is True

    def test_mark_index_pending_queues_path_and_clears_in_memory(self, initialized):
        """Per-path doc-embedding pending tracking; no eager sidecar delete.

        After _mark_index_pending, the path is queued for embedding refresh and
        in-memory caches are cleared, but on-disk sidecars remain (stale-but-
        loadable preferred over missing-and-failing). Refresh overwrites them.
        """
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
        with server._doc_embeddings_pending_lock:
            server._doc_embeddings_pending.clear()

        server._mark_index_pending("Wiki/brain-overview-abc123.md", "living/wiki")

        assert "Wiki/brain-overview-abc123.md" in server._doc_embeddings_pending
        assert server._type_embeddings is None
        assert server._doc_embeddings is None
        assert server._embeddings_meta is None
        # Sidecars remain on disk; next refresh overwrites them in place.
        assert (local_dir / "type-embeddings.npy").exists()
        assert (local_dir / "doc-embeddings.npy").exists()
        assert (local_dir / "embeddings-meta.json").exists()

    def test_ensure_embeddings_fresh_loads_current_sidecars_before_rebuild(self, initialized, monkeypatch):
        expected_type_embeddings = object()
        expected_doc_embeddings = object()
        expected_meta = {
            retrieval_embeddings.ROUTER_SOURCE_HASH_KEY: server._router["meta"]["source_hash"],
            "documents": [],
            "types": [],
        }

        monkeypatch.setattr(server, "_embeddings_enabled", lambda: True)
        monkeypatch.setattr(
            retrieval_embeddings,
            "load_embeddings_state",
            lambda _vault: (expected_type_embeddings, expected_doc_embeddings, expected_meta),
        )
        monkeypatch.setattr(
            retrieval_assets,
            "refresh_embeddings_for_loaded_state",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("refresh_embeddings_for_loaded_state should not run on a fast-path hit")
            ),
        )

        server._type_embeddings = None
        server._doc_embeddings = None
        server._embeddings_meta = None
        server._doc_embeddings_dirty = False
        server._type_embeddings_dirty = False
        with server._doc_embeddings_pending_lock:
            server._doc_embeddings_pending.clear()

        server._ensure_embeddings_fresh()

        assert server._type_embeddings is expected_type_embeddings
        assert server._doc_embeddings is expected_doc_embeddings
        assert server._embeddings_meta is expected_meta
        assert server._doc_embeddings_dirty is False
        assert server._type_embeddings_dirty is False

    def test_ensure_embeddings_fresh_rebuilds_when_sidecars_are_corrupt(self, initialized, monkeypatch):
        rebuilt_type_embeddings = object()
        rebuilt_doc_embeddings = object()
        rebuilt_meta = {
            retrieval_embeddings.ROUTER_SOURCE_HASH_KEY: server._router["meta"]["source_hash"],
            "documents": [],
            "types": [],
        }
        refresh_calls = []

        def fake_refresh(
            vault_root,
            router,
            documents,
            *,
            embedding_parts_by_path=None,
            config=None,
        ):
            refresh_calls.append((str(vault_root), len(documents)))
            return (rebuilt_type_embeddings, rebuilt_doc_embeddings, rebuilt_meta)

        monkeypatch.setattr(server, "_embeddings_enabled", lambda: True)
        monkeypatch.setattr(
            retrieval_embeddings,
            "load_embeddings_state",
            lambda _vault: (_ for _ in ()).throw(
                retrieval_embeddings.SemanticEmbeddingsLoadError("corrupt sidecars")
            ),
        )
        monkeypatch.setattr(retrieval_assets, "refresh_embeddings_for_loaded_state", fake_refresh)

        server._type_embeddings = None
        server._doc_embeddings = None
        server._embeddings_meta = None
        server._doc_embeddings_dirty = False
        server._type_embeddings_dirty = False
        with server._doc_embeddings_pending_lock:
            server._doc_embeddings_pending.clear()

        server._ensure_embeddings_fresh()

        assert refresh_calls == [(str(initialized), len(server._index["documents"]))]
        assert server._type_embeddings is rebuilt_type_embeddings
        assert server._doc_embeddings is rebuilt_doc_embeddings
        assert server._embeddings_meta is rebuilt_meta
        assert server._doc_embeddings_dirty is False
        assert server._type_embeddings_dirty is False

    def test_ensure_embeddings_fresh_rebuilds_router_stale_sidecars(self, initialized, monkeypatch):
        np = pytest.importorskip("numpy")
        stale_meta = {
            retrieval_embeddings.ROUTER_SOURCE_HASH_KEY: "sha256:stale-router-hash",
            "documents": [],
            "types": [],
        }

        class FakeModel:
            def encode(self, texts, normalize_embeddings=True):
                return np.zeros((len(texts), 1), dtype=float)

        manifest = semantic_model.ModelManifest(
            model_name=semantic_model.SHIPPED_MODEL_NAME,
            revision=semantic_model.SHIPPED_MODEL_REVISION,
            provisioned_at="2026-05-10T00:00:00+10:00",
        )

        monkeypatch.setattr(
            retrieval_embeddings,
            "semantic_engine_available",
            lambda *_args, **_kwargs: True,
        )
        monkeypatch.setattr(
            retrieval_assets.semantic_runtime,
            "semantic_engine_available",
            lambda *_args, **_kwargs: True,
        )
        monkeypatch.setattr(
            semantic_assets.semantic_model,
            "load_local_model_with_manifest",
            lambda _vault: (FakeModel(), manifest),
        )

        server._config["defaults"]["flags"]["semantic_processing"] = True
        server._config["defaults"]["flags"]["semantic_retrieval"] = True
        server._config["defaults"].setdefault("local_runtime", {})[
            "semantic_engine_installed"
        ] = True

        replacement_router = json.loads(json.dumps(server._router))
        replacement_router["meta"]["source_hash"] = "sha256:current-router-hash"
        server._set_router(replacement_router)

        router_path = initialized / ".brain" / "local" / "compiled-router.json"
        router_path.write_text(json.dumps(server._router, indent=2), encoding="utf-8")

        meta_path = initialized / retrieval_embeddings.EMBEDDINGS_META_REL
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(stale_meta), encoding="utf-8")

        server._doc_embeddings_dirty = False
        server._type_embeddings_dirty = False

        server._ensure_embeddings_fresh()

        assert server._type_embeddings is not None
        assert server._doc_embeddings is not None
        refreshed_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert refreshed_meta[retrieval_embeddings.ROUTER_SOURCE_HASH_KEY] == "sha256:current-router-hash"
        assert server._embeddings_meta == refreshed_meta
        assert server._doc_embeddings_dirty is False
        assert server._type_embeddings_dirty is False

    def test_process_context_assembly_skips_embeddings_refresh(self, initialized, monkeypatch):
        server._config["defaults"]["flags"]["semantic_processing"] = True
        server._config["defaults"].setdefault("local_runtime", {})["semantic_engine_installed"] = True
        server._type_embeddings = None
        server._doc_embeddings = None
        server._embeddings_meta = None

        calls = []

        def fake_refresh(
            vault_root,
            router,
            documents,
            *,
            embedding_parts_by_path=None,
            config=None,
        ):
            calls.append((str(vault_root), len(documents)))
            return (object(), object(), {"documents": [], "types": []})

        monkeypatch.setattr(retrieval_assets, "refresh_embeddings_for_loaded_state", fake_refresh)

        result = server.brain_process(
            operation="classify",
            content="some content",
            mode="context_assembly",
        )

        assert "context_assembly" in result
        assert calls == []
        assert server._type_embeddings is None
        assert server._doc_embeddings is None

    def test_disabled_embeddings_clear_cached_query_encoder(self, initialized, monkeypatch):
        sentinel = object()
        monkeypatch.setattr(semantic_model, "_CACHED_QUERY_ENCODER", sentinel)
        server._config["defaults"]["flags"]["semantic_processing"] = False
        server._config["defaults"]["flags"]["semantic_retrieval"] = False

        server._ensure_embeddings_fresh()

        assert semantic_model._CACHED_QUERY_ENCODER is None

    def test_process_auto_refreshes_embeddings_when_enabled(self, initialized, monkeypatch):
        server._config["defaults"]["flags"]["semantic_processing"] = True
        server._config["defaults"].setdefault("local_runtime", {})["semantic_engine_installed"] = True
        server._type_embeddings = None
        server._doc_embeddings = None
        server._embeddings_meta = None

        calls = []

        def fake_refresh(
            vault_root,
            router,
            documents,
            *,
            embedding_parts_by_path=None,
            config=None,
        ):
            calls.append((str(vault_root), len(documents)))
            return (object(), object(), {"documents": [], "types": []})

        monkeypatch.setattr(retrieval_assets, "refresh_embeddings_for_loaded_state", fake_refresh)
        monkeypatch.setattr(
            retrieval_embeddings,
            "semantic_engine_available",
            lambda *args, **kwargs: True,
        )
        monkeypatch.setattr(
            retrieval_assets.semantic_runtime,
            "semantic_engine_available",
            lambda *_args, **_kwargs: True,
        )
        monkeypatch.setattr(
            _server_content._retrieval_embeddings,
            "semantic_engine_available",
            lambda *_args, **_kwargs: True,
        )
        monkeypatch.setattr(
            retrieval_embeddings,
            "load_embeddings_state",
            lambda *_args, **_kwargs: (None, None, None),
        )
        monkeypatch.setattr(
            process,
            "classify_content",
            lambda *args, **kwargs: {
                "mode": "context_assembly",
                "type_descriptions": [],
                "instruction": "stub",
            },
        )

        result = server.brain_process(
            operation="classify",
            content="some content",
        )

        assert isinstance(result, str)
        assert calls == [(str(initialized), len(server._index["documents"]))]
        assert server._type_embeddings is not None
        assert server._doc_embeddings is not None

    def test_process_ingest_queues_index_update_instead_of_rebuilding_inline(self, initialized, monkeypatch):
        server._config["defaults"]["flags"]["semantic_processing"] = True
        server._config["defaults"].setdefault("local_runtime", {})["semantic_engine_installed"] = True
        server._type_embeddings = None
        server._doc_embeddings = None
        server._embeddings_meta = None
        monkeypatch.setattr(server, "_ensure_embeddings_fresh", lambda: None)
        original_count = len(server._index["documents"])

        result = server.brain_process(
            operation="ingest",
            content="# Quantum Coffee\n\nWhat if coffee brewed itself?",
            type="ideas",
        )

        assert isinstance(result, str)
        assert "created" in result
        assert len(server._index["documents"]) == original_count
        assert len(server._index_pending) == 1
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
        with patch.object(server.search_paths, "INDEX_VERSION", "99.0.0"):
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
