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
    _assert_any_error,
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


class TestBrainInit:
    def test_returns_minimal_snapshot(self, initialized):
        result = json.loads(server.brain_init())
        assert result["version"] == "1"
        assert result["readiness"] == "ready"
        assert result["warmup_state"] == "complete"
        assert "bootstrap_hint" in result
        assert "next_action" in result
        assert result["bootstrap_hint"] == result["next_action"]

    def test_debug_adds_only_cheap_diagnostics(self, initialized):
        result = json.loads(server.brain_init(debug=True))
        assert result["readiness"] == "ready"
        assert "debug" in result
        assert result["debug"]["router_ready"] is True
        assert result["debug"]["index_ready"] is True
        assert result["debug"]["workspace_registry_ready"] is True

    def test_is_idempotent(self, initialized):
        first = json.loads(server.brain_init())
        second = json.loads(server.brain_init())
        assert first == second


class TestWarmupBoundary:
    def test_brain_init_warmup_true_returns_immediately_while_warming(
        self, vault, gated_router_warmup
    ):
        server.startup(vault_root=str(vault))
        assert gated_router_warmup.entered.wait(timeout=2.0), "warmup did not start router loading"
        started = time.monotonic()
        result = json.loads(server.brain_init(warmup=True, debug=True))
        elapsed = time.monotonic() - started
        assert elapsed < 1.0, f"brain_init blocked for {elapsed:.2f}s"
        assert result["warmup_state"] == "running"
        assert result["debug"]["router_ready"] is False

    def test_brain_session_returns_progress_while_warming(self, vault, gated_router_warmup):
        server.startup(vault_root=str(vault))
        assert gated_router_warmup.entered.wait(timeout=2.0), "warmup did not start router loading"
        payload = _progress_payload(server.brain_session())
        assert payload["status"] == "starting"
        assert payload["tool"] == "brain_session"
        assert payload["needs"] == ["router"]
        assert payload["warmup_state"] == "running"
        assert payload["next_action"] == "Retry `brain_session` shortly while Brain warmup continues."

    def test_brain_read_returns_progress_while_warming(self, vault, gated_router_warmup):
        server.startup(vault_root=str(vault))
        assert gated_router_warmup.entered.wait(timeout=2.0), "warmup did not start router loading"
        payload = _progress_payload(server.brain_read("router"))
        assert payload["status"] == "starting"
        assert payload["tool"] == "brain_read"
        assert payload["needs"] == ["router"]
        assert payload["next_action"] == "Retry `brain_read` shortly while Brain warmup continues."

    @pytest.mark.parametrize(
        ("tool_name", "call"),
        [
            (
                "brain_list",
                lambda: server.brain_list(resource="skill"),
            ),
            (
                "brain_action",
                lambda: server.brain_action(
                    "delete",
                    params={"path": "Wiki/python-guide-def456.md"},
                ),
            ),
            (
                "brain_move",
                lambda: server.brain_move(
                    op="rename",
                    source="Wiki/python-guide-def456.md",
                    dest="Wiki/python-guide-def456-renamed.md",
                ),
            ),
        ],
    )
    def test_multiple_handlers_share_router_progress_contract_while_warming(
        self,
        vault,
        gated_router_warmup,
        tool_name,
        call,
    ):
        server.startup(vault_root=str(vault))
        assert gated_router_warmup.entered.wait(timeout=2.0), "warmup did not start router loading"

        payload = _progress_payload(call())

        assert payload["status"] == "starting"
        assert payload["tool"] == tool_name
        assert payload["needs"] == ["router"]
        assert payload["warmup_state"] == "running"
        assert payload["next_action"] == f"Retry `{tool_name}` shortly while Brain warmup continues."

    @pytest.mark.parametrize(
        ("tool_name", "call"),
        [
            (
                "brain_search",
                lambda: server.brain_search("brain"),
            ),
            (
                "brain_list",
                lambda: server.brain_list(resource="artefact"),
            ),
            (
                "brain_process",
                lambda: server.brain_process(
                    operation="resolve",
                    content="some content",
                    type="wiki",
                    title="Some Title",
                ),
            ),
        ],
    )
    def test_multiple_handlers_share_index_progress_contract_when_index_missing(
        self,
        initialized,
        monkeypatch,
        tool_name,
        call,
    ):
        monkeypatch.setattr(server, "_ensure_warmup_started", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(server, "_ensure_index_fresh", lambda: None)

        server._index = None
        with server._warmup_lock:
            server._readiness = "warming"
            server._warmup_state = "running"

        payload = _progress_payload(call())

        assert payload["status"] == "starting"
        assert payload["tool"] == tool_name
        assert payload["needs"] == ["index"]
        assert payload["warmup_state"] == "running"
        assert payload["next_action"] == f"Retry `{tool_name}` shortly while Brain warmup continues."


class TestSemanticWarmup:
    def test_startup_skips_semantic_warmup_when_embeddings_disabled(self, initialized):
        assert server._semantic_warmup_state == "disabled"
        assert server._wait_for_semantic_warmup(timeout=0.1)

    def test_semantic_warmup_loads_current_sidecars_from_disk(self, vault, monkeypatch):
        expected_type_embeddings = object()
        expected_doc_embeddings = object()
        calls = []

        def fake_load(vault_root):
            calls.append(str(vault_root))
            return (
                expected_type_embeddings,
                expected_doc_embeddings,
                {
                    retrieval_embeddings.ROUTER_SOURCE_HASH_KEY: server._router["meta"]["source_hash"],
                    "documents": [],
                    "types": [],
                },
            )

        monkeypatch.setattr(server, "_embeddings_enabled", lambda: True)
        monkeypatch.setattr(retrieval_embeddings, "load_embeddings_state", fake_load)
        monkeypatch.setattr(
            retrieval_assets,
            "refresh_embeddings_for_loaded_state",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("refresh_embeddings_for_loaded_state should not run during semantic warmup load")
            ),
        )

        server.startup(vault_root=str(vault))

        assert server._wait_for_warmup(timeout=5.0), "warmup did not complete"
        assert server._wait_for_semantic_warmup(timeout=5.0), "semantic warmup did not complete"
        assert calls == [str(vault)]
        assert server._semantic_warmup_state == "ready"
        assert server._type_embeddings is expected_type_embeddings
        assert server._doc_embeddings is expected_doc_embeddings
        assert (
            server._embeddings_meta[retrieval_embeddings.ROUTER_SOURCE_HASH_KEY]
            == server._router["meta"]["source_hash"]
        )
        assert server._doc_embeddings_dirty is False
        assert server._type_embeddings_dirty is False

    def test_semantic_warmup_marks_corrupt_sidecars_dirty_until_first_lazy_refresh(
        self, vault, monkeypatch
    ):
        rebuilt_type_embeddings = object()
        rebuilt_doc_embeddings = object()
        rebuilt_meta = {
            retrieval_embeddings.ROUTER_SOURCE_HASH_KEY: "sha256:rebuilt-router-hash",
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
            return (
                rebuilt_type_embeddings,
                rebuilt_doc_embeddings,
                {
                    **rebuilt_meta,
                    retrieval_embeddings.ROUTER_SOURCE_HASH_KEY: router["meta"]["source_hash"],
                },
            )

        monkeypatch.setattr(server, "_embeddings_enabled", lambda: True)
        monkeypatch.setattr(
            retrieval_embeddings,
            "load_embeddings_state",
            lambda _vault_root: (_ for _ in ()).throw(
                retrieval_embeddings.SemanticEmbeddingsLoadError("corrupt sidecars")
            ),
        )
        monkeypatch.setattr(retrieval_assets, "refresh_embeddings_for_loaded_state", fake_refresh)

        server.startup(vault_root=str(vault))

        assert server._wait_for_warmup(timeout=5.0), "warmup did not complete"
        assert server._wait_for_semantic_warmup(timeout=5.0), "semantic warmup did not complete"
        assert server._semantic_warmup_state == "ready"
        assert server._type_embeddings is None
        assert server._doc_embeddings is None
        assert server._embeddings_meta is None
        assert server._doc_embeddings_dirty is True
        assert server._type_embeddings_dirty is True

        server._ensure_embeddings_fresh()

        assert refresh_calls == [(str(vault), len(server._index["documents"]))]
        assert server._type_embeddings is rebuilt_type_embeddings
        assert server._doc_embeddings is rebuilt_doc_embeddings
        assert (
            server._embeddings_meta[retrieval_embeddings.ROUTER_SOURCE_HASH_KEY]
            == server._router["meta"]["source_hash"]
        )
        assert server._doc_embeddings_dirty is False
        assert server._type_embeddings_dirty is False

    def test_semantic_warmup_defers_to_lazy_refresh_on_unexpected_exception(self, vault, monkeypatch):
        monkeypatch.setattr(server, "_embeddings_enabled", lambda: True)
        monkeypatch.setattr(
            retrieval_embeddings,
            "load_embeddings_state",
            lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        server.startup(vault_root=str(vault))

        assert server._wait_for_warmup(timeout=5.0), "warmup did not complete"
        assert server._wait_for_semantic_warmup(timeout=5.0), "semantic warmup did not complete"
        assert server._semantic_warmup_state == "deferred"
        assert server._semantic_ready() is False

        server._config.setdefault("defaults", {}).setdefault("flags", {})["semantic_retrieval"] = True
        server._config["defaults"].setdefault("local_runtime", {})["semantic_engine_installed"] = True
        monkeypatch.setattr(semantic_runtime, "semantic_engine_available", lambda *_a, **_k: True)

        result = server.brain_search("brain", mode="hybrid")

        _assert_error(result, "semantic warmup failed unexpectedly: boom")

    def test_semantic_warmup_reports_startup_failure_before_serving_semantic_requests(
        self, initialized, monkeypatch
    ):
        monkeypatch.setattr(server, "_embeddings_enabled", lambda: True)

        with server._warmup_lock:
            server._warmup_state = "failed"
            server._last_warmup_error = "workspace_registry_load: registry broken"

        server._run_semantic_warmup(
            server._warmup_generation,
            str(initialized),
            server._semantic_enablement_generation,
        )

        assert server._semantic_warmup_state == "deferred"

        result = server._ensure_semantic_ready("brain_search")

        _assert_error(result, "startup warmup failed")
        assert "workspace_registry_load: registry broken" in result.content[0].text

    def test_semantic_warmup_marks_missing_or_stale_sidecars_dirty_until_first_lazy_refresh(
        self, vault, monkeypatch
    ):
        rebuilt_type_embeddings = object()
        rebuilt_doc_embeddings = object()
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
            return (
                rebuilt_type_embeddings,
                rebuilt_doc_embeddings,
                {
                    retrieval_embeddings.ROUTER_SOURCE_HASH_KEY: router["meta"]["source_hash"],
                    "documents": [],
                    "types": [],
                },
            )

        monkeypatch.setattr(server, "_embeddings_enabled", lambda: True)
        monkeypatch.setattr(
            retrieval_embeddings,
            "load_embeddings_state",
            lambda *_a, **_k: (None, None, None),
        )
        monkeypatch.setattr(retrieval_assets, "refresh_embeddings_for_loaded_state", fake_refresh)

        server.startup(vault_root=str(vault))

        assert server._wait_for_warmup(timeout=5.0), "warmup did not complete"
        assert server._wait_for_semantic_warmup(timeout=5.0), "semantic warmup did not complete"
        assert server._semantic_warmup_state == "ready"
        assert server._type_embeddings is None
        assert server._doc_embeddings is None
        assert server._embeddings_meta is None
        assert server._doc_embeddings_dirty is True
        assert server._type_embeddings_dirty is True

        server._ensure_embeddings_fresh()

        assert refresh_calls == [(str(vault), len(server._index["documents"]))]
        assert server._type_embeddings is rebuilt_type_embeddings
        assert server._doc_embeddings is rebuilt_doc_embeddings
        assert (
            server._embeddings_meta[retrieval_embeddings.ROUTER_SOURCE_HASH_KEY]
            == server._router["meta"]["source_hash"]
        )
        assert server._doc_embeddings_dirty is False
        assert server._type_embeddings_dirty is False

    def test_semantic_warmup_preserves_pending_mutations(self, vault, monkeypatch):
        release = threading.Event()
        entered = threading.Event()
        expected_type_embeddings = object()
        expected_doc_embeddings = object()

        def slow_load_embeddings_state(_vault_root):
            entered.set()
            assert release.wait(timeout=2.0), "semantic warmup gate did not release"
            return (
                expected_type_embeddings,
                expected_doc_embeddings,
                {
                    retrieval_embeddings.ROUTER_SOURCE_HASH_KEY: server._router["meta"]["source_hash"],
                    "documents": [],
                    "types": [],
                },
            )

        monkeypatch.setattr(server, "_embeddings_enabled", lambda: True)
        monkeypatch.setattr(
            retrieval_embeddings,
            "load_embeddings_state",
            slow_load_embeddings_state,
        )

        server.startup(vault_root=str(vault))

        assert entered.wait(timeout=2.0), "semantic warmup did not reach sidecar load"
        assert server._wait_for_warmup(timeout=5.0), "warmup did not complete"

        rel_path = "Wiki/brain-overview-abc123.md"
        server._mark_index_pending(rel_path, "living/wiki")

        release.set()
        assert server._wait_for_semantic_warmup(timeout=5.0), "semantic warmup did not complete"

        assert rel_path in server._doc_embeddings_pending
        assert server._type_embeddings is None
        assert server._doc_embeddings is None
        assert server._embeddings_meta is None
        assert server._doc_embeddings_dirty is True
        assert server._type_embeddings_dirty is True

    def test_set_router_clears_loaded_embeddings_state(self, initialized):
        server._type_embeddings = object()
        server._doc_embeddings = object()
        server._embeddings_meta = {
            retrieval_embeddings.ROUTER_SOURCE_HASH_KEY: server._router["meta"]["source_hash"],
            "documents": [],
            "types": [],
        }

        new_router = json.loads(json.dumps(server._router))
        new_router["meta"]["source_hash"] = "sha256:replacement-router-hash"

        server._set_router(new_router)

        assert server._router["meta"]["source_hash"] == "sha256:replacement-router-hash"
        assert server._type_embeddings is None
        assert server._doc_embeddings is None
        assert server._embeddings_meta is None

    def test_semantic_warmup_rejects_snapshot_for_replaced_router(self, vault, monkeypatch):
        release = threading.Event()
        entered = threading.Event()
        expected_type_embeddings = object()
        expected_doc_embeddings = object()
        router_source_hash = {"value": None}

        def slow_load_embeddings_state(_vault_root):
            entered.set()
            assert release.wait(timeout=2.0), "semantic warmup gate did not release"
            return (
                expected_type_embeddings,
                expected_doc_embeddings,
                {
                    retrieval_embeddings.ROUTER_SOURCE_HASH_KEY: router_source_hash["value"],
                    "documents": [],
                    "types": [],
                },
            )

        monkeypatch.setattr(server, "_embeddings_enabled", lambda: True)
        monkeypatch.setattr(
            retrieval_embeddings,
            "load_embeddings_state",
            slow_load_embeddings_state,
        )

        server.startup(vault_root=str(vault))

        assert entered.wait(timeout=2.0), "semantic warmup did not reach sidecar load"
        assert server._wait_for_warmup(timeout=5.0), "warmup did not complete"

        router_source_hash["value"] = server._router["meta"]["source_hash"]
        replacement_router = json.loads(json.dumps(server._router))
        replacement_router["meta"]["source_hash"] = "sha256:replacement-router-hash"
        server._set_router(replacement_router)

        release.set()
        assert server._wait_for_semantic_warmup(timeout=5.0), "semantic warmup did not complete"

        assert server._router["meta"]["source_hash"] == "sha256:replacement-router-hash"
        assert server._type_embeddings is None
        assert server._doc_embeddings is None
        assert server._embeddings_meta is None
        assert server._doc_embeddings_dirty is True
        assert server._type_embeddings_dirty is True

    def test_apply_loaded_embeddings_snapshot_ignores_stale_generation(self, initialized):
        stale_generation = server._warmup_generation
        current_router = json.loads(json.dumps(server._router))
        current_router_source_hash = current_router["meta"]["source_hash"]
        loaded = (
            object(),
            object(),
            {
                retrieval_embeddings.ROUTER_SOURCE_HASH_KEY: current_router_source_hash,
                "documents": [],
                "types": [],
            },
        )

        server._reset_runtime_state_for_startup()
        server._set_router(current_router)

        applied = server._apply_loaded_embeddings_snapshot(
            loaded,
            generation=stale_generation,
            expected_router_source_hash=current_router_source_hash,
        )

        assert applied is False
        assert server._type_embeddings is None
        assert server._doc_embeddings is None
        assert server._embeddings_meta is None

    def test_lexical_search_succeeds_while_semantic_warmup_is_still_running(
        self, vault, gated_semantic_warmup, monkeypatch
    ):
        monkeypatch.setattr(server, "_embeddings_enabled", lambda: True)
        monkeypatch.setattr(semantic_runtime, "semantic_engine_available", lambda *_a, **_k: True)

        server.startup(vault_root=str(vault))

        assert gated_semantic_warmup.entered.wait(timeout=2.0), "semantic warmup did not reach disk load"
        assert server._wait_for_warmup(timeout=5.0), "warmup did not complete"
        server._config.setdefault("defaults", {}).setdefault("flags", {})["semantic_retrieval"] = True
        server._config["defaults"].setdefault("local_runtime", {})["semantic_engine_installed"] = True

        lexical = _search_text(server.brain_search("brain", mode="lexical"))
        payload = _progress_payload(server.brain_search("brain", mode="hybrid"))

        assert server._warmup_state == "complete"
        assert server._semantic_warmup_state == "warming"
        assert "bm25" in lexical
        assert payload["needs"] == ["semantic"]

    def test_process_context_assembly_bypasses_semantic_gate_while_auto_waits(
        self, vault, gated_semantic_warmup, monkeypatch
    ):
        monkeypatch.setattr(server, "_embeddings_enabled", lambda: True)
        monkeypatch.setattr(
            _server_content._retrieval_embeddings,
            "semantic_engine_available",
            lambda *_a, **_k: True,
        )

        server.startup(vault_root=str(vault))

        assert gated_semantic_warmup.entered.wait(timeout=2.0), "semantic warmup did not reach disk load"
        assert server._wait_for_warmup(timeout=5.0), "warmup did not complete"
        server._config.setdefault("defaults", {}).setdefault("flags", {})["semantic_processing"] = True
        server._config["defaults"].setdefault("local_runtime", {})["semantic_engine_installed"] = True

        degraded = server.brain_process(
            operation="classify",
            content="some content",
            mode="context_assembly",
        )
        payload = _progress_payload(
            server.brain_process(
                operation="classify",
                content="some content",
                mode="auto",
            )
        )

        assert server._semantic_warmup_state == "warming"
        assert "context_assembly" in degraded
        assert payload["needs"] == ["semantic"]

    def test_multiple_handlers_share_semantic_progress_contract_while_warming(
        self, vault, gated_semantic_warmup, monkeypatch
    ):
        monkeypatch.setattr(server, "_embeddings_enabled", lambda: True)
        monkeypatch.setattr(semantic_runtime, "semantic_engine_available", lambda *_a, **_k: True)
        monkeypatch.setattr(
            _server_content._retrieval_embeddings,
            "semantic_engine_available",
            lambda *_a, **_k: True,
        )

        server.startup(vault_root=str(vault))

        assert gated_semantic_warmup.entered.wait(timeout=2.0), "semantic warmup did not reach disk load"
        assert server._wait_for_warmup(timeout=5.0), "warmup did not complete"
        server._config.setdefault("defaults", {}).setdefault("flags", {})["semantic_retrieval"] = True
        server._config["defaults"].setdefault("local_runtime", {})["semantic_engine_installed"] = True
        server._config["defaults"]["flags"]["semantic_processing"] = True

        search_payload = _progress_payload(server.brain_search("brain", mode="hybrid"))
        process_payload = _progress_payload(
            server.brain_process(
                operation="classify",
                content="some content",
                mode="auto",
            )
        )

        for tool_name, payload in (
            ("brain_search", search_payload),
            ("brain_process", process_payload),
        ):
            assert payload["status"] == "starting"
            assert payload["tool"] == tool_name
            assert payload["needs"] == ["semantic"]
            assert payload["warmup_state"] == "complete"
            assert payload["next_action"] == f"Retry `{tool_name}` shortly while Brain warmup continues."


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
        # Add memories and restart to recompile.
        memories_dir = initialized / "_Config" / "Memories"
        memories_dir.mkdir(parents=True, exist_ok=True)
        (memories_dir / "test-mem.md").write_text(
            "---\ntriggers: [test, memory]\n---\n\n# Test Memory\n"
        )
        server.startup(vault_root=str(initialized))
        assert server._wait_for_warmup(timeout=5.0), "warmup did not complete"

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
            _assert_any_error(result)
        finally:
            server._router = saved_router
            server._vault_root = saved_root


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

        hub_file.write_text(
            "---\ntype: living/workspace\nkey: alpha\nstatus: parked\n---\n\n# A\n"
        )
        _bump_mtime(hub_file)
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
        result = server.brain_action(
            action="delete",
            params={"path": "Wiki/python-guide-def456.md"},
        )
        _assert_error(result, "does not allow brain_action")

        # reader cannot call brain_move
        result = server.brain_move(
            op="rename",
            source="Wiki/brain-overview-abc123.md",
            dest="Wiki/brain-overview-renamed.md",
        )
        _assert_error(result, "does not allow brain_move")

    def test_reader_create_does_not_force_router_refresh(self, initialized):
        key = "timber-compass-violet"
        server._config["vault"]["operators"] = [
            {
                "id": "test-reader",
                "profile": "reader",
                "auth": {"type": "key", "hash": config_mod.hash_key(key)},
            },
        ]
        server.brain_session(operator_key=key)

        with patch.object(server, "_ensure_router_fresh") as mock_ensure:
            result = server.brain_create(type="ideas", title="test")

        _assert_error(result, "does not allow brain_create")
        mock_ensure.assert_not_called()

    def test_reader_edit_does_not_force_router_refresh(self, initialized):
        key = "timber-compass-violet"
        server._config["vault"]["operators"] = [
            {
                "id": "test-reader",
                "profile": "reader",
                "auth": {"type": "key", "hash": config_mod.hash_key(key)},
            },
        ]
        server.brain_session(operator_key=key)

        with patch.object(server, "_ensure_router_fresh") as mock_ensure:
            result = server.brain_edit(operation="edit", path="test.md", body="test")

        _assert_error(result, "does not allow brain_edit")
        mock_ensure.assert_not_called()

    def test_denied_read_search_list_do_not_force_router_refresh(self, initialized):
        key = "sealed-profile-key"
        server._config["vault"]["profiles"]["sealed"] = {"allow": ["brain_session"]}
        server._config["vault"]["operators"] = [
            {
                "id": "test-sealed",
                "profile": "sealed",
                "auth": {"type": "key", "hash": config_mod.hash_key(key)},
            },
        ]
        server.brain_session(operator_key=key)

        with patch.object(server, "_ensure_router_fresh") as mock_ensure:
            read_result = server.brain_read("type", name="wiki")
            search_result = server.brain_search("brain")
            list_result = server.brain_list()

        _assert_error(read_result, "does not allow brain_read")
        _assert_error(search_result, "does not allow brain_search")
        _assert_error(list_result, "does not allow brain_list")
        mock_ensure.assert_not_called()

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
        result = server.brain_action(
            action="delete",
            params={"path": "Wiki/python-guide-def456.md"},
        )
        _assert_error(result, "does not allow brain_action")

        # contributor cannot use brain_move
        result = server.brain_move(
            op="rename",
            source="Wiki/brain-overview-abc123.md",
            dest="Wiki/brain-overview-renamed.md",
        )
        _assert_error(result, "does not allow brain_move")

    def test_config_error_blocks_guarded_tools(self, initialized):
        """Config reload errors fail closed before profile enforcement."""
        config_path = initialized / ".brain" / "local" / "config.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("defaults: [unterminated\n", encoding="utf-8")
        _bump_mtime(config_path)

        result = server.brain_read("type", name="wiki")

        _assert_error(result, "config reload failed")

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


class TestConfigFreshness:
    def test_startup_config_signature_failure_records_known_debug_error(
        self, vault, monkeypatch
    ):
        monkeypatch.setattr(
            server.config_mod,
            "config_input_paths",
            lambda _vault_root: (_ for _ in ()).throw(FileNotFoundError("missing defaults/config.yaml")),
        )

        server.startup(vault_root=str(vault))

        payload = json.loads(server.brain_init(debug=True))
        assert "config_error" in payload["debug"]
        assert "missing defaults/config.yaml" in payload["debug"]["config_error"]

    def test_startup_internal_config_signature_bug_is_not_reported_as_user_config_error(
        self, vault, monkeypatch
    ):
        monkeypatch.setattr(
            server.config_mod,
            "config_input_paths",
            lambda _vault_root: (_ for _ in ()).throw(RuntimeError("programmer bug")),
        )

        with pytest.raises(RuntimeError, match="programmer bug"):
            server.startup(vault_root=str(vault))

    def test_internal_config_signature_bug_is_not_reported_as_user_config_error(
        self, initialized, monkeypatch
    ):
        monkeypatch.setattr(
            server.config_mod,
            "config_input_paths",
            lambda _vault_root: (_ for _ in ()).throw(RuntimeError("programmer bug")),
        )

        result = server.brain_search("brain")

        _assert_error(result, "Unexpected error: programmer bug")
        assert "config_error" not in json.loads(server.brain_init(debug=True))["debug"]

    def test_transient_config_signature_failure_recovers_without_file_change(
        self, initialized, monkeypatch
    ):
        real_config_input_paths = server.config_mod.config_input_paths
        fail_once = {"remaining": 1}

        def flaky_config_input_paths(vault_root):
            if fail_once["remaining"]:
                fail_once["remaining"] -= 1
                raise FileNotFoundError("temporary config path failure")
            return real_config_input_paths(vault_root)

        monkeypatch.setattr(server.config_mod, "config_input_paths", flaky_config_input_paths)

        first = server.brain_search("brain")
        _assert_error(first, "temporary config path failure")
        assert "config_error" in json.loads(server.brain_init(debug=True))["debug"]

        second = server.brain_search("brain")
        assert "bm25" in _search_text(second)
        assert "config_error" not in json.loads(server.brain_init(debug=True))["debug"]

    def test_changed_config_reload_uses_already_computed_signature_once(
        self, initialized, monkeypatch
    ):
        real_config_input_paths = server.config_mod.config_input_paths
        calls = {"count": 0}

        def counted_config_input_paths(vault_root):
            calls["count"] += 1
            if calls["count"] > 1:
                raise FileNotFoundError("second signature probe should not happen")
            return real_config_input_paths(vault_root)

        _write_config_yaml(
            initialized / ".brain" / "local" / "config.yaml",
            {"defaults": {"default_profile": "reader"}},
        )
        monkeypatch.setattr(server.config_mod, "config_input_paths", counted_config_input_paths)

        result = server.brain_search("brain")

        assert "bm25" in _search_text(result)
        assert calls["count"] == 1
        assert "config_error" not in json.loads(server.brain_init(debug=True))["debug"]

    def test_current_config_returns_same_object_and_fails_closed(self, initialized):
        # Fresh: the accessor returns the very same dict object (no copy), so
        # in-place reads and mutations stay effective.
        assert isinstance(server._config_state, server._server_config_state.ConfigFresh)
        assert server._current_config() is server._config

        # Force a sticky load error: `_config` lingers as last-good, but every
        # reader fails closed to None.
        lingering = server._config
        config_path = initialized / ".brain" / "local" / "config.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("defaults: [unterminated\n", encoding="utf-8")
        _bump_mtime(config_path)

        server.brain_search("brain")

        assert isinstance(server._config_state, server._server_config_state.ConfigLoadError)
        assert server._config is lingering        # last-good still held internally
        assert server._current_config() is None    # but readers fail closed

    def test_reload_recovery_into_semantic_enabled_config_starts_warmup(
        self, initialized, monkeypatch
    ):
        # Reloading OUT of an error state INTO a semantic-enabling config must let
        # publish's side effects observe the freshly-committed ConfigFresh state.
        # If the state were committed after publish, _current_config() would still
        # see the ConfigLoadError, _embeddings_enabled() would fail closed, and
        # semantic warmup would be wrongly (and stickily) left "disabled".
        monkeypatch.setattr(
            retrieval_assets.semantic_runtime,
            "semantic_engine_available",
            lambda *_args, **_kwargs: True,
        )
        config_path = initialized / ".brain" / "local" / "config.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)

        # 1. Malformed config → sticky ConfigLoadError; last-good _config lingers
        #    (semantic disabled in the initialized vault).
        config_path.write_text("defaults: [unterminated\n", encoding="utf-8")
        _bump_mtime(config_path)
        _assert_error(server.brain_search("brain"), "config reload failed")
        assert isinstance(
            server._config_state, server._server_config_state.ConfigLoadError
        )

        # 2. Valid config that enables semantic.
        _write_config_yaml(
            config_path,
            {
                "defaults": {
                    "flags": {"semantic_retrieval": True},
                    "local_runtime": {"semantic_engine_installed": True},
                }
            },
        )

        # 3. Reload recovers; publish must see ConfigFresh and start warmup.
        server.brain_search("brain", mode="lexical")

        assert isinstance(server._config_state, server._server_config_state.ConfigFresh)
        assert server._semantic_warmup_state != "disabled"

    def test_transient_probe_error_uses_last_good_config_for_semantic_warmup(
        self, initialized, monkeypatch
    ):
        """A stat/path blip must not permanently degrade semantic warmup."""
        server._config.setdefault("defaults", {}).setdefault("flags", {})[
            "semantic_retrieval"
        ] = True
        server._config["defaults"].setdefault("local_runtime", {})[
            "semantic_engine_installed"
        ] = True
        monkeypatch.setattr(
            retrieval_assets.semantic_runtime,
            "semantic_engine_available",
            lambda *_args, **_kwargs: True,
        )
        calls = []

        def fake_run_semantic_warmup(
            generation, vault_root, semantic_enablement_generation
        ):
            calls.append((generation, vault_root, semantic_enablement_generation))

        monkeypatch.setattr(server, "_run_semantic_warmup", fake_run_semantic_warmup)
        server._config_state = server._server_config_state.ConfigProbeError(
            server._config_state.signature,
            "temporary config stat failure",
        )

        server._ensure_semantic_warmup_started("transient probe error")
        thread = server._semantic_warmup_thread
        assert thread is not None
        thread.join(timeout=5.0)

        assert calls == [
            (
                server._warmup_generation,
                str(initialized),
                server._semantic_enablement_generation,
            )
        ]
        assert server._semantic_warmup_state == "warming"

    def test_changed_config_load_runs_while_holding_config_lock(
        self, initialized, monkeypatch
    ):
        real_load = server._load_config
        observed = {}

        def probing_load(paths, *, error_prefix):
            # A different thread must not be able to acquire the config lock while
            # a load is in flight — decide → load → commit is one atomic section,
            # so two concurrent refreshes cannot both load and race to publish.
            acquired = []

            def try_acquire():
                got = server._config_lock.acquire(blocking=False)
                acquired.append(got)
                if got:
                    server._config_lock.release()

            t = threading.Thread(target=try_acquire)
            t.start()
            t.join()
            observed["lock_held_during_load"] = acquired == [False]
            return real_load(paths, error_prefix=error_prefix)

        monkeypatch.setattr(server, "_load_config", probing_load)
        _write_config_yaml(
            initialized / ".brain" / "local" / "config.yaml",
            {"defaults": {"default_profile": "reader"}},
        )

        result = server.brain_search("brain")

        assert "bm25" in _search_text(result)
        assert observed.get("lock_held_during_load") is True

    def test_transient_signature_failure_does_not_clear_existing_load_error(
        self, initialized, monkeypatch
    ):
        config_path = initialized / ".brain" / "local" / "config.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("defaults: [unterminated\n", encoding="utf-8")
        _bump_mtime(config_path)

        load_error = server.brain_search("brain")
        _assert_error(load_error, "inline sequence is missing a closing")

        real_config_input_paths = server.config_mod.config_input_paths
        fail_once = {"remaining": 1}

        def flaky_config_input_paths(vault_root):
            if fail_once["remaining"]:
                fail_once["remaining"] -= 1
                raise FileNotFoundError("temporary config path failure")
            return real_config_input_paths(vault_root)

        monkeypatch.setattr(server.config_mod, "config_input_paths", flaky_config_input_paths)

        probe_error = server.brain_search("brain")
        _assert_error(probe_error, "inline sequence is missing a closing")

        still_failing = server.brain_search("brain")
        _assert_error(still_failing, "inline sequence is missing a closing")
        payload = json.loads(server.brain_init(debug=True))
        assert "inline sequence is missing a closing" in payload["debug"]["config_error"]
        assert "temporary config path failure" not in payload["debug"]["config_error"]

    def test_startup_malformed_config_records_known_debug_error(self, vault):
        config_path = vault / ".brain" / "local" / "config.yaml"
        _write_config_text(config_path, "defaults: [unterminated\n")

        server.startup(vault_root=str(vault))

        payload = json.loads(server.brain_init(debug=True))
        assert "config_error" in payload["debug"]
        assert "config reload failed during startup" in payload["debug"]["config_error"]

    @pytest.mark.parametrize(
        "text",
        [
            "defaults: hello\n",
            "vault: hello\n",
            "defaults:\n  semantic_processing: true\nvault:\n  profiles:\n    - reader\n",
        ],
    )
    def test_startup_structurally_invalid_config_records_known_debug_error(
        self, vault, text
    ):
        _write_config_text(vault / ".brain" / "config.yaml", text)

        server.startup(vault_root=str(vault))

        payload = json.loads(server.brain_init(debug=True))
        assert "config_error" in payload["debug"]
        assert "config reload failed during startup" in payload["debug"]["config_error"]

    def test_semantic_search_reloads_local_config_enabled_after_startup(
        self, initialized
    ):
        result = server.brain_search("brain", mode="semantic")
        _assert_error(result, "semantic retrieval is disabled")

        _write_config_yaml(
            initialized / ".brain" / "local" / "config.yaml",
            {
                "defaults": {
                    "flags": {"semantic_retrieval": True},
                    "local_runtime": {"semantic_engine_installed": True},
                }
            },
        )

        result = server.brain_search("brain", mode="semantic")

        _assert_error(result, "semantic retrieval is unavailable")

    def test_semantic_disable_reload_prevents_stale_warmup_ready(
        self, vault, gated_semantic_warmup, monkeypatch
    ):
        monkeypatch.setattr(
            retrieval_assets.semantic_runtime,
            "semantic_engine_available",
            lambda *_args, **_kwargs: True,
        )
        config_path = vault / ".brain" / "local" / "config.yaml"
        _write_config_yaml(
            config_path,
            {
                "defaults": {
                    "flags": {"semantic_retrieval": True},
                    "local_runtime": {"semantic_engine_installed": True},
                }
            },
        )

        server.startup(vault_root=str(vault))
        assert gated_semantic_warmup.entered.wait(timeout=2.0)
        assert server._wait_for_warmup(timeout=5.0)
        stale_thread = server._semantic_warmup_thread
        assert stale_thread is not None

        _write_config_yaml(
            config_path,
            {
                "defaults": {
                    "flags": {"semantic_retrieval": False},
                    "local_runtime": {"semantic_engine_installed": True},
                }
            },
        )

        server.brain_search("brain", mode="lexical")
        gated_semantic_warmup.release.set()
        stale_thread.join(timeout=5.0)

        assert server._semantic_warmup_state == "disabled"
        assert server._type_embeddings is None
        assert server._doc_embeddings is None
        assert server._embeddings_meta is None
        payload = json.loads(server.brain_init(debug=True))
        assert payload["debug"]["semantic_warmup_state"] == "disabled"
        result = server.brain_search("brain", mode="semantic")
        _assert_error(result, "semantic retrieval is disabled")

    def test_config_reload_failure_fails_closed_and_brain_init_reports_debug(
        self, initialized
    ):
        config_path = initialized / ".brain" / "local" / "config.yaml"
        _write_config_text(config_path, "defaults: [unterminated\n")

        result = server.brain_search("brain")

        _assert_error(result, "config reload failed")
        payload = json.loads(server.brain_init(debug=True))
        assert "config_error" in payload["debug"]
        assert "config reload failed" in payload["debug"]["config_error"]

    @pytest.mark.parametrize(
        "text",
        [
            "defaults: hello\n",
            "vault: hello\n",
            "defaults:\n  semantic_processing: true\nvault:\n  profiles:\n    - reader\n",
        ],
    )
    def test_structurally_invalid_config_reload_fails_closed(
        self, initialized, text
    ):
        _write_config_text(initialized / ".brain" / "config.yaml", text)

        result = server.brain_search("brain")

        _assert_error(result, "config reload failed")
        assert "Unexpected error" not in result.content[0].text
        payload = json.loads(server.brain_init(debug=True))
        assert "config_error" in payload["debug"]

    def test_config_reload_error_recovers_after_valid_signature_change(
        self, initialized
    ):
        config_path = initialized / ".brain" / "local" / "config.yaml"
        _write_config_text(config_path, "defaults: [unterminated\n")

        result = server.brain_search("brain")
        _assert_error(result, "config reload failed")
        assert "config_error" in json.loads(server.brain_init(debug=True))["debug"]

        _write_config_yaml(
            config_path,
            {"defaults": {"default_profile": "reader"}},
        )

        result = server.brain_read("environment")
        assert "config_error" not in result
        payload = json.loads(server.brain_init(debug=True))
        assert "config_error" not in payload["debug"]

    def test_brain_session_authentication_reloads_on_disk_operator_config(
        self, initialized
    ):
        key = "cedar-river-signal"
        config_path = initialized / ".brain" / "config.yaml"
        _write_config_yaml(
            config_path,
            {
                "vault": {
                    "operators": [
                        {
                            "id": "disk-operator",
                            "profile": "reader",
                            "auth": {
                                "type": "key",
                                "hash": config_mod.hash_key(key),
                            },
                        }
                    ]
                }
            },
        )

        result = json.loads(server.brain_session(operator_key=key))
        assert result["active_profile"] == "reader"
        assert server._session_profile == "reader"

        _write_config_yaml(
            config_path,
            {
                "vault": {
                    "operators": [
                        {
                            "id": "disk-operator",
                            "profile": "reader",
                            "auth": {"type": "key", "hash": "sha256:not-this-key"},
                        }
                    ]
                }
            },
        )

        result = server.brain_session(operator_key=key)
        _assert_error(result, "operator key does not match")

    def test_profile_allow_list_reload_runs_before_authorising_tool(
        self, initialized
    ):
        config_path = initialized / ".brain" / "config.yaml"
        _write_config_yaml(
            config_path,
            {
                "vault": {
                    "profiles": {
                        "dynamic": {"allow": ["brain_session", "brain_search"]}
                    }
                },
                "defaults": {"default_profile": "dynamic"},
            },
        )

        result = json.loads(server.brain_session())
        assert result["active_profile"] == "dynamic"

        _write_config_yaml(
            config_path,
            {
                "vault": {
                    "profiles": {
                        "dynamic": {"allow": ["brain_session"]}
                    }
                },
                "defaults": {"default_profile": "dynamic"},
            },
        )

        result = server.brain_search("brain")

        _assert_error(result, "does not allow brain_search")

    def test_removed_active_profile_fails_closed_after_reload(self, initialized):
        config_path = initialized / ".brain" / "config.yaml"
        _write_config_yaml(
            config_path,
            {
                "vault": {
                    "profiles": {
                        "dynamic": {"allow": ["brain_session", "brain_read"]}
                    }
                },
                "defaults": {"default_profile": "dynamic"},
            },
        )

        result = json.loads(server.brain_session())
        assert result["active_profile"] == "dynamic"

        _write_config_yaml(
            config_path,
            {
                "defaults": {"default_profile": "operator"},
            },
        )

        result = server.brain_read("type", name="wiki")

        _assert_error(result, "active operator profile 'dynamic' is no longer defined")

    def test_config_reload_updates_vault_name_for_cli_search(
        self, initialized, cli_available, monkeypatch
    ):
        config_path = initialized / ".brain" / "config.yaml"
        _write_config_yaml(
            config_path,
            {"vault": {"brain_name": "before-reload"}},
        )
        server.brain_session()
        assert server._vault_name == "before-reload"

        calls = []
        monkeypatch.setattr(obsidian_cli, "check_available", lambda: True)
        monkeypatch.setattr(
            obsidian_cli,
            "search",
            lambda vault_name, query: calls.append((vault_name, query)) or [],
        )
        server._cli_probed_at = 0.0
        _write_config_yaml(
            config_path,
            {"vault": {"brain_name": "after-reload"}},
        )

        server.brain_search("brain", mode="lexical")

        assert calls == [("after-reload", "brain")]

    def test_config_reload_enqueues_session_mirror_refresh(
        self, initialized
    ):
        session_path = initialized / ".brain" / "local" / "session.md"
        json.loads(server.brain_session())
        assert "`default_profile`: `operator`" in session_path.read_text()

        _write_config_yaml(
            initialized / ".brain" / "local" / "config.yaml",
            {"defaults": {"default_profile": "reader"}},
        )

        _search_text(server.brain_search("brain", mode="lexical"))
        server._mirror_queue.join()

        content = session_path.read_text()
        assert "`default_profile`: `reader`" in content
        assert "`default_profile`: `operator`" not in content
