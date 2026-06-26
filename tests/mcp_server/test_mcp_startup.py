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


class TestStartup:
    def test_startup_defers_router_compile_to_background_warmup(self, vault, gated_router_warmup):
        """Startup should return before router warmup completes."""
        router_path = vault / ".brain" / "local" / "compiled-router.json"
        assert not router_path.exists()

        started = time.monotonic()
        server.startup(vault_root=str(vault))
        elapsed = time.monotonic() - started

        assert elapsed < 1.0, f"startup blocked for {elapsed:.2f}s"
        assert gated_router_warmup.entered.wait(timeout=2.0), "warmup did not start router loading"
        assert server._router is None
        assert not router_path.exists()

        gated_router_warmup.release.set()
        assert server._wait_for_warmup(timeout=5.0), "warmup did not complete"
        assert router_path.exists()

    def test_startup_builds_index(self, vault):
        """Startup should build the index when none exists."""
        index_path = vault / ".brain" / "local" / "retrieval-index.json"
        assert not index_path.exists()
        server.startup(vault_root=str(vault))
        assert server._wait_for_warmup(timeout=5.0), "warmup did not complete"
        assert index_path.exists()

    def test_startup_loads_router_into_memory(self, vault):
        server.startup(vault_root=str(vault))
        assert server._wait_for_warmup(timeout=5.0), "warmup did not complete"
        assert server._router is not None
        assert "artefacts" in server._router
        assert "meta" in server._router

    def test_startup_loads_index_into_memory(self, vault):
        server.startup(vault_root=str(vault))
        assert server._wait_for_warmup(timeout=5.0), "warmup did not complete"
        assert server._index is not None
        assert "documents" in server._index
        assert "corpus_stats" in server._index

    def test_startup_writes_session_markdown(self, vault):
        session_path = vault / ".brain" / "local" / "session.md"
        assert not session_path.exists()
        server.startup(vault_root=str(vault))
        assert server._wait_for_warmup(timeout=5.0), "warmup did not complete"
        server._mirror_queue.join()
        assert session_path.exists()
        content = session_path.read_text()
        assert "# Brain Session" in content
        assert "## Always Rules" in content


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
        router_md = vault / "_Config" / "router.md"
        router_md.write_text(router_md.read_text() + "\n- New rule.\n")
        _bump_mtime(router_md)
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

    def test_router_stale_when_source_hash_missing(self, vault):
        server._compile_and_save(str(vault))
        router_path = vault / ".brain" / "local" / "compiled-router.json"
        data = json.loads(router_path.read_text(encoding="utf-8"))
        del data["meta"]["source_hash"]
        router_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        stale, loaded = server._check_router(str(vault))

        assert stale is True
        assert loaded is None

    def test_router_stale_when_meta_is_not_a_json_object(self, vault):
        server._compile_and_save(str(vault))
        router_path = vault / ".brain" / "local" / "compiled-router.json"
        data = json.loads(router_path.read_text(encoding="utf-8"))
        data["meta"] = "garbage"
        router_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        stale, loaded = server._check_router(str(vault))

        assert stale is True
        assert loaded is None

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
        # Add a new .md file
        (vault / "Wiki" / "new-file-zzz999.md").write_text(
            "---\ntype: living/wiki\n---\n\n# New File\n\nContent.\n"
        )
        stale, _ = server._check_index(str(vault))
        assert stale is True


class TestStartupCaching:
    def test_startup_reuses_fresh_router(self, vault):
        """Second startup should load from disk, not recompile."""
        server.startup(vault_root=str(vault))
        assert server._wait_for_warmup(timeout=5.0), "warmup did not complete"
        compiled_at_1 = server._router["meta"]["compiled_at"]

        # Second startup — files haven't changed
        server.startup(vault_root=str(vault))
        assert server._wait_for_warmup(timeout=5.0), "warmup did not complete"
        compiled_at_2 = server._router["meta"]["compiled_at"]
        assert compiled_at_1 == compiled_at_2

    def test_startup_reuses_fresh_index(self, vault):
        """Second startup should load from disk, not rebuild."""
        server.startup(vault_root=str(vault))
        assert server._wait_for_warmup(timeout=5.0), "warmup did not complete"
        built_at_1 = server._index["meta"]["built_at"]

        server.startup(vault_root=str(vault))
        assert server._wait_for_warmup(timeout=5.0), "warmup did not complete"
        built_at_2 = server._index["meta"]["built_at"]
        assert built_at_1 == built_at_2


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

        router_md = initialized / "_Config" / "router.md"
        router_md.write_text(router_md.read_text() + "\n- Recompile for ensure_fresh.\n")
        _bump_mtime(router_md)
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
        tax_file = initialized / "_Config" / "Taxonomy" / "Living" / "wiki.md"
        tax_file.write_text(tax_file.read_text() + "\n")
        _bump_mtime(tax_file)

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

        with patch.object(
            server.search_index,
            "index_update",
            side_effect=OSError("boom"),
        ):
            server._ensure_index_fresh()

        assert server._index_dirty, "Index should be marked dirty after incremental failure"

    def test_search_surfaces_unreadable_index_rebuild_failures(self, initialized):
        old_index = server._index
        server._mark_index_dirty()

        with patch.object(
            server,
            "_build_index_and_save",
            side_effect=retrieval_errors.UnreadableRetrievalSourceError(
                "Wiki/broken.md",
                "building lexical retrieval state",
                UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
            ),
        ):
            result = server.brain_search("brain")

        assert server._index is old_index
        _assert_error(result, "unreadable retrieval source 'Wiki/broken.md'")
        assert "while building lexical retrieval state" in result.content[0].text

    @pytest.mark.parametrize(
        ("tool_name", "call"),
        [
            ("brain_search", lambda: server.brain_search("brain")),
            ("brain_list", lambda: server.brain_list(resource="artefact")),
            (
                "brain_process",
                lambda: server.brain_process(
                    operation="resolve",
                    content="Some content",
                    type="wiki",
                    title="Some Title",
                ),
            ),
        ],
    )
    def test_index_state_errors_block_index_backed_tools(
        self,
        initialized,
        tool_name,
        call,
    ):
        old_index = server._index
        server._mark_index_dirty()

        with patch.object(
            server,
            "_build_index_and_save",
            side_effect=retrieval_errors.CompiledRouterUnavailableError(
                "compiled router is unavailable",
                operation="building semantic embeddings",
            ),
        ):
            result = call()

        assert server._index is old_index, f"{tool_name} should preserve the last good index"
        _assert_error(result, "compiled router is unavailable while building semantic embeddings")

    @pytest.mark.parametrize(
        ("tool_name", "call"),
        [
            ("brain_search", lambda: server.brain_search("brain")),
            ("brain_list", lambda: server.brain_list(resource="artefact")),
            (
                "brain_process",
                lambda: server.brain_process(
                    operation="resolve",
                    content="Some content",
                    type="wiki",
                    title="Some Title",
                ),
            ),
        ],
    )
    def test_index_persistence_failures_block_index_backed_tools(
        self,
        initialized,
        tool_name,
        call,
    ):
        old_index = server._index
        server._mark_index_dirty()

        with patch.object(
            server,
            "_build_index_and_save",
            side_effect=retrieval_errors.RetrievalPersistenceError(
                search_paths.OUTPUT_PATH,
                "persisting lexical retrieval state",
                OSError("disk full"),
            ),
        ):
            result = call()

        assert server._index is old_index, f"{tool_name} should preserve the last good index"
        _assert_error(result, "failed to persist retrieval output")
        assert "while persisting lexical retrieval state" in result.content[0].text

    def test_ensure_mutation_index_ready_skips_staleness_sweep(self, initialized, monkeypatch):
        """Write-path readiness must not trigger the TTL-gated external scan."""
        server._index_checked_at = 0.0

        def fail(*args, **kwargs):
            raise AssertionError("_check_index should not run on the mutation path")

        monkeypatch.setattr(server, "_check_index", fail)

        server._ensure_mutation_index_ready()


class TestStartupRobustness:
    def test_startup_survives_router_compile_failure(self, vault):
        """Warmup failure in router compile does not block index readiness."""
        server._router = None
        with patch.object(server.compile_router, "compile", side_effect=OSError("boom")):
            server.startup(vault_root=str(vault))
            assert server._wait_for_warmup(timeout=5.0), "warmup did not complete"

        assert server._router is None
        assert server._index is not None

    def test_startup_survives_index_build_failure(self, vault):
        """Warmup failure in index build does not block router readiness."""
        server._index = None
        real_run_with_timeout = server._run_with_timeout

        def fail_index_build(label, fn, timeout=server._STARTUP_OP_TIMEOUT):
            if label == "index build":
                raise OSError("boom")
            return real_run_with_timeout(label, fn, timeout=timeout)

        with patch.object(server, "_run_with_timeout", side_effect=fail_index_build):
            server.startup(vault_root=str(vault))
            assert server._wait_for_warmup(timeout=5.0), "warmup did not complete"

        assert server._router is not None
        assert server._index is None

    @pytest.mark.parametrize(
        ("tool_name", "call"),
        [
            ("brain_search", lambda: server.brain_search("brain")),
            ("brain_list", lambda: server.brain_list(resource="artefact")),
            (
                "brain_process",
                lambda: server.brain_process(
                    operation="resolve",
                    content="Some content",
                    type="wiki",
                    title="Some Title",
                ),
            ),
        ],
    )
    def test_startup_surfaces_typed_index_build_failures_to_index_backed_tools(
        self,
        vault,
        monkeypatch,
        tool_name,
        call,
    ):
        real_run_with_timeout = server._run_with_timeout

        def fail_index_build(label, fn, timeout=server._STARTUP_OP_TIMEOUT):
            if label == "index build":
                raise retrieval_errors.UnreadableRetrievalSourceError(
                    "Wiki/broken.md",
                    "building lexical retrieval state",
                    UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
                )
            return real_run_with_timeout(label, fn, timeout=timeout)

        with patch.object(server, "_run_with_timeout", side_effect=fail_index_build):
            server.startup(vault_root=str(vault))
            assert server._wait_for_warmup(timeout=5.0), "warmup did not complete"

        assert server._index is None
        monkeypatch.setattr(server, "_ensure_index_fresh", lambda: None)

        result = call()

        _assert_error(result, "unreadable retrieval source 'Wiki/broken.md'")
        assert "while building lexical retrieval state" in result.content[0].text

    @pytest.mark.parametrize(
        ("tool_name", "call"),
        [
            ("brain_search", lambda: server.brain_search("brain")),
            ("brain_list", lambda: server.brain_list(resource="artefact")),
            (
                "brain_process",
                lambda: server.brain_process(
                    operation="resolve",
                    content="Some content",
                    type="wiki",
                    title="Some Title",
                ),
            ),
        ],
    )
    def test_startup_surfaces_index_persistence_failures_to_index_backed_tools(
        self,
        vault,
        monkeypatch,
        tool_name,
        call,
    ):
        with patch.object(
            server.search_index,
            "persist_retrieval_index",
            side_effect=retrieval_errors.RetrievalPersistenceError(
                search_paths.OUTPUT_PATH,
                "persisting lexical retrieval state",
                OSError("disk full"),
            ),
        ):
            server.startup(vault_root=str(vault))
            assert server._wait_for_warmup(timeout=5.0), "warmup did not complete"

        assert server._index is None
        monkeypatch.setattr(server, "_ensure_index_fresh", lambda: None)

        result = call()

        _assert_error(result, "failed to persist retrieval output")
        assert "while persisting lexical retrieval state" in result.content[0].text

    def test_main_exits_on_vault_discovery_failure(self):
        """If vault root discovery fails, main should exit with code 1."""
        with patch("brain_mcp.server.compile_router.find_vault_root", side_effect=FileNotFoundError("no vault")), \
             patch.dict(os.environ, {}, clear=True):
            with pytest.raises(SystemExit) as exc_info:
                server.main()
            assert exc_info.value.code == 1


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
        # Touch a taxonomy source file to make it newer than compiled_at
        tax_file = initialized / "_Config" / "Taxonomy" / "Living" / "wiki.md"
        tax_file.write_text(tax_file.read_text() + "\n")
        _bump_mtime(tax_file)
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

        nested = initialized / "Ideas" / "2026-04" / "nested-idea-xyz789.md"
        nested.parent.mkdir(parents=True, exist_ok=True)
        nested.write_text(
            "---\ntype: living/ideas\nslug: nested-idea-xyz789\n---\n# Nested\n"
        )
        _bump_mtime(nested.parent)

        server._check_router_resource_counts(str(initialized), server._router)
        assert resource_counts_calls["n"] == 2

    def test_new_living_type_folder_invalidates_cache(
        self, initialized, resource_counts_calls
    ):
        server._check_router_resource_counts(str(initialized), server._router)
        assert resource_counts_calls["n"] == 1

        (initialized / "Projects").mkdir()
        _bump_mtime(initialized / "Projects")

        server._check_router_resource_counts(str(initialized), server._router)
        assert resource_counts_calls["n"] == 2

    def test_archive_write_does_not_invalidate_cache(
        self, initialized, resource_counts_calls
    ):
        server._check_router_resource_counts(str(initialized), server._router)
        assert resource_counts_calls["n"] == 1

        archive = initialized / "Ideas" / "_Archive" / "2026-04"
        archive.mkdir(parents=True, exist_ok=True)
        (archive / "old-idea-abc123.md").write_text(
            "---\ntype: living/ideas\nslug: old-idea-abc123\n---\n# Old\n"
        )
        # Ignored archive paths changing must not invalidate the resource cache.
        _bump_mtime(initialized / "Ideas" / "_Archive")
        _bump_mtime(archive)

        server._check_router_resource_counts(str(initialized), server._router)
        assert resource_counts_calls["n"] == 1

    def test_skill_md_delete_inside_subdir_invalidates_cache(self, initialized):
        skill_dir = initialized / "_Config" / "Skills" / "demo-for-mtime"
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("# Demo\n")

        sig_before = server._resource_mtime_signature(str(initialized))
        skill_md.unlink()
        _bump_mtime(skill_dir)
        sig_after = server._resource_mtime_signature(str(initialized))

        assert sig_before != sig_after

    def test_missing_resource_dirs_encode_as_none(self, tmp_path):
        sig = server._resource_mtime_signature(str(tmp_path))
        by_key = dict(sig)
        assert by_key[""] is not None
        for rel in ("_Temporal", "_Config/Styles", "_Config/Memories",
                    "_Config/Skills", ".brain-core/skills", "_Plugins"):
            assert by_key[rel] is None

    def test_index_count_failure_propagates_and_leaves_cache_untouched(self, initialized, monkeypatch):
        server._check_router_resource_counts(str(initialized), server._router)
        cached_before = server._resource_mtime_cache
        assert cached_before is not None

        (initialized / "Ideas" / "trigger-abc123.md").write_text(
            "---\ntype: living/ideas\nslug: trigger-abc123\n---\n# t\n"
        )

        def boom(vault_root, artefacts):
            raise RuntimeError("index count blew up")

        monkeypatch.setattr(
            compile_router, "count_living_artefact_index_entries", boom
        )
        with pytest.raises(RuntimeError, match="index count blew up"):
            server._check_router_resource_counts(
                str(initialized), server._router
            )
        assert server._resource_mtime_cache == cached_before


class TestWorkspaceStartup:
    def test_startup_loads_empty_registry(self, vault):
        """Startup with no .brain/ → empty registry."""
        server.startup(vault_root=str(vault))
        assert server._wait_for_warmup(timeout=5.0), "warmup did not complete"
        assert server._workspace_registry == {}

    def test_startup_loads_existing_registry(self, vault, tmp_path):
        """Startup with .brain/local/workspaces.json → loaded registry."""
        brain_local = vault / ".brain" / "local"
        brain_local.mkdir(parents=True)
        (brain_local / "workspaces.json").write_text(json.dumps({
            "workspaces": {"pre-existing": {"path": str(tmp_path / "pre")}}
        }))
        server.startup(vault_root=str(vault))
        assert server._wait_for_warmup(timeout=5.0), "warmup did not complete"
        assert "pre-existing" in server._workspace_registry
