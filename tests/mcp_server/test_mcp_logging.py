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

import logging
from logging.handlers import RotatingFileHandler

def _file_handler(logger):
    """Extract the single RotatingFileHandler from a logger."""
    return next(h for h in logger.handlers if isinstance(h, RotatingFileHandler))



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
        assert server._wait_for_warmup(timeout=5.0), "warmup did not complete"
        log_path = vault / ".brain" / "local" / "mcp-server.log"
        content = log_path.read_text()
        assert "startup begin" in content
        assert "startup phase begin: config_load" in content
        assert "startup phase success: config_load" in content
        assert "warmup started (startup)" in content
        assert "warmup phase begin: router_freshness" in content
        assert "warmup phase begin: index_freshness" in content
        assert "warmup phase begin: workspace_registry_load" in content
        assert "warmup phase begin: session_mirror_refresh" in content
        assert "startup complete" in content

    def test_router_compile_logged(self, vault):
        """Stale router compile is logged with timing."""
        server.startup(vault_root=str(vault))
        assert server._wait_for_warmup(timeout=5.0), "warmup did not complete"
        log_path = vault / ".brain" / "local" / "mcp-server.log"
        content = log_path.read_text()
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
            assert elapsed < 2.0, f"startup blocked for {elapsed:.2f}s"
            content = ""
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                content = log_path.read_text()
                if "warmup phase begin: router_freshness" in content:
                    break
                time.sleep(0.01)
            assert "warmup phase begin: router_freshness" in content
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

