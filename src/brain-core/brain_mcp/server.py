#!/usr/bin/env python3
"""
Brain MCP Server — thin MCP wrapper over brain-core scripts.

All logic lives in `.brain-core/scripts/` as importable functions.
The server imports them, holds the compiled router and search index in memory,
and exposes 9 MCP tools:
  brain_init    — additive bootstrap/orientation snapshot, cheap and idempotent
  brain_session — bootstrap an agent session (compiled payload, one call)
  brain_read    — read compiled router resources (safe, no side effects)
  brain_search  — BM25 keyword search, with optional Obsidian CLI live search
  brain_list    — exhaustive enumeration by type, date range, or tag (not relevance-ranked)
  brain_create  — create new vault artefacts (additive, safe to auto-approve)
  brain_edit    — modify existing vault artefacts (single-file mutation)
  brain_move    — destructive content-move ops: rename, convert, archive, unarchive
  brain_action  — workflow/utility bucket: delete, shaping helpers, fix-links

Why this pattern: scripts are the source of truth for all vault operations.
The MCP server gets in-memory caching for free (router/index loaded once at
startup). Standalone scripts pay a cold-start cost reading JSON from disk.
Agents without MCP use the scripts directly — same logic, same results.

Optional native Obsidian CLI integration (Obsidian 1.12+ IPC socket):
  - Search: CLI-first with BM25 fallback (CLI uses Obsidian's live index)
  - Rename: CLI-first with grep-and-replace fallback (CLI auto-updates wikilinks)
  - Requires Obsidian to be running with CLI enabled (communicates via ~/.obsidian-cli.sock)

Startup sequence:
  1. Find vault root (server is launched with BRAIN_VAULT_ROOT by init.py-managed client config)
  2. Build the minimal runtime skeleton required to answer MCP initialize quickly
  3. Start background warmup for router/index/session readiness work
  4. Serve via stdio

Composition-root by design: the resilience shell, runtime state, startup,
shutdown, and MCP registration stay here. Tool implementation logic now
delegates to sibling modules that align with the bounded-context map while
preserving the stable `server.py` module surface used by tests and the proxy.

Requires Python >=3.12 and the `mcp` SDK (see requirements.txt).
"""

import atexit
import contextlib
import errno
import glob
import json
import logging
import logging.handlers
import os
import queue
import signal
import sys
import threading
import time
import traceback
from datetime import datetime
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Script imports — add scripts dir to sys.path
# ---------------------------------------------------------------------------

SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, os.path.abspath(SCRIPTS_DIR))

import compile_router
import build_index
from _common import (
    SELECTOR_OCCURRENCE_DESCRIPTION,
    SELECTOR_WITHIN_DESCRIPTION,
    SELECTOR_WITHIN_OCCURRENCE_DESCRIPTION,
    SELECTOR_WITHIN_TARGET_DESCRIPTION,
    cleanup_temp_body_file,
    is_archived_path,
    iter_artefact_paths,
    safe_write_json,
    temp_body_file_cleanup_path,
)
import edit
from _resource_contract import RESOURCE_KINDS
import obsidian_cli
import session
import workspace_registry
import config as config_mod
from . import _server_actions
from . import _server_artefacts
from . import _server_init
from . import _server_reading
from . import _server_session
from ._server_contracts import (
    create_contract_hint,
    edit_contract_hint,
    list_contract_hint,
    read_contract_hint,
    validate_spec,
)
from _resource_contract import CREATE_SPECS, EDIT_SPECS, READ_SPECS, LIST_SPECS
from ._server_runtime import ServerRuntime, ServerState

# Path constants — read from script modules (single source of truth).
def _router_rel() -> str:
    return compile_router.OUTPUT_PATH

def _index_rel() -> str:
    return build_index.OUTPUT_PATH

# ---------------------------------------------------------------------------
# Server state
# ---------------------------------------------------------------------------

mcp = FastMCP(name="brain")

_vault_root: str | None = None
_config: dict | None = None
_session_profile: str | None = None
_router: dict | None = None
_index: dict | None = None
_index_dirty: bool = False       # set True for full rebuild (e.g. version drift)
# _index_pending queues (rel_path, type_hint) pairs from brain_create/brain_edit
# for incremental index updates on the next search. _index_pending_lock MUST be
# held for any read or write — three call sites: _mark_index_pending (append),
# _ensure_index_fresh (drain), _build_index_and_save (clear on full rebuild).
_index_pending: list[tuple[str, str | None]] = []
_index_pending_lock = threading.Lock()
_mutation_lock = threading.RLock()
_cli_available: bool = False
_cli_probed_at: float = 0.0  # monotonic timestamp of last CLI probe
_vault_name: str | None = None
_loaded_version: str | None = None
_workspace_registry: dict | None = None
_readiness: str = "cold"
_warmup_state: str = "not_started"
_warmup_active_phase: str | None = None
_last_warmup_error: str | None = None
_last_warmup_reason: str | None = None
_warmup_generation: int = 0
_warmup_thread: threading.Thread | None = None
_warmup_lock = threading.Lock()


# Staleness-check TTLs — intentionally different because the checks have
# very different costs. Router: stats a handful of source files (cheap, 5s).
# Index: walks every .md file in the vault to compare count + mtime (expensive,
# 30s). Don't unify these without understanding the cost difference.
_CLI_PROBE_TTL = 30
_ROUTER_CHECK_TTL = 5
_INDEX_CHECK_TTL = 30
_STARTUP_OP_TIMEOUT = 30   # seconds — guard against iCloud I/O hangs during startup
_PROGRESS_RETRY_AFTER_MS = 1000
_MIRROR_DRAIN_TIMEOUT = 2.0  # seconds — atexit drain cap; filesystem stalls terminate normally
_router_checked_at: float = 0.0
_index_checked_at: float = 0.0
_router_dirty: bool = False  # set True by MCP writes; next _ensure_router_fresh recompiles

_resource_mtime_cache: tuple[tuple[str, float | None], ...] | None = None

# Session-mirror worker: one long-lived daemon thread drains a coalescing
# queue (maxsize=1 so rapid-fire refreshes collapse to the latest intent).
# This replaces the old per-refresh "spawn thread + abandon on timeout"
# pattern — there is no abandonment, no late-writer clobber, and startup
# never blocks on a slow `fsync`. See docs/architecture/decisions/
# dd-036-safe-write-pattern.md for the phase-2 rationale.
_MIRROR_SHUTDOWN = object()
_mirror_queue: "queue.Queue" = queue.Queue(maxsize=1)
_mirror_worker_thread: threading.Thread | None = None
_mirror_worker_lock = threading.Lock()  # guards worker thread start/replace
_mirror_drain_registered: bool = False

# Logging
_LOG_REL = os.path.join(".brain", "local", "mcp-server.log")
_LOG_MAX_BYTES = 2 * 1024 * 1024   # 2 MB
_LOG_BACKUP_COUNT = 1
_logger: logging.Logger | None = None


def _flush_log() -> None:
    """Flush all logger handlers (call before os._exit or sys.exit).

    In stdio MCP mode the client/proxy may close stderr before the server
    receives SIGTERM. Flushing the stderr stream in that state can raise
    BrokenPipeError, EPIPE, or ValueError for a closed stream, none of which
    should turn an otherwise clean shutdown into a noisy false crash.
    """
    if _logger:
        for h in _logger.handlers:
            try:
                h.flush()
            except BrokenPipeError:
                continue
            except OSError as e:
                if e.errno == errno.EPIPE:
                    continue
                raise
            except ValueError as e:
                stream = getattr(h, "stream", None)
                if getattr(stream, "closed", False):
                    continue
                if "closed file" in str(e).lower():
                    continue
                raise


def _setup_logging(vault_root: str) -> logging.Logger:
    """Configure file + stderr logging for the MCP server.

    File handler: INFO by default (DEBUG via BRAIN_LOG_LEVEL env var).
    Stderr handler: WARN+ only (preserves MCP client visibility).
    """
    log_path = os.path.join(vault_root, _LOG_REL)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    logger = logging.getLogger("brain-core")
    if logger.handlers:
        return logger  # already configured (e.g. repeated startup() in tests)
    logger.setLevel(logging.DEBUG)  # logger accepts all; handlers filter

    # File handler — INFO by default, DEBUG if BRAIN_LOG_LEVEL=DEBUG
    env_level = os.environ.get("BRAIN_LOG_LEVEL", "INFO").upper()
    file_level = getattr(logging, env_level, logging.INFO)
    file_handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=_LOG_MAX_BYTES, backupCount=_LOG_BACKUP_COUNT,
    )
    file_handler.setLevel(file_level)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(file_handler)

    # Stderr handler — WARN and above (replaces old print-to-stderr pattern)
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(logging.Formatter("brain-core: %(message)s"))
    logger.addHandler(stderr_handler)

    return logger


@contextlib.contextmanager
def _trace_tool(tool_name: str, **kwargs):
    """Log tool call entry and exit with timing."""
    if _logger:
        _logger.info("tool call: %s", tool_name)
        _logger.debug("tool args: %s %s", tool_name, kwargs)
    t0 = time.monotonic()
    yield
    if _logger:
        _logger.info("tool done: %s %.3fs", tool_name, time.monotonic() - t0)


def _reset_runtime_state_for_startup() -> int:
    """Reset warmup-owned state for a fresh startup cycle."""
    global _session_profile, _router, _index, _workspace_registry
    global _index_dirty, _router_dirty, _router_checked_at, _index_checked_at
    global _resource_mtime_cache, _warmup_generation, _warmup_thread
    global _readiness, _warmup_state, _warmup_active_phase
    global _last_warmup_error, _last_warmup_reason

    _session_profile = None
    _router = None
    _index = None
    _workspace_registry = None
    _index_dirty = False
    _router_dirty = False
    _router_checked_at = 0.0
    _index_checked_at = 0.0
    _resource_mtime_cache = None
    with _index_pending_lock:
        _index_pending.clear()
    with _warmup_lock:
        _warmup_generation += 1
        _warmup_thread = None
        _readiness = "cold"
        _warmup_state = "not_started"
        _warmup_active_phase = None
        _last_warmup_error = None
        _last_warmup_reason = None
        return _warmup_generation


def _warmup_generation_matches(generation: int) -> bool:
    with _warmup_lock:
        return generation == _warmup_generation


def _set_warmup_phase(generation: int, phase_key: str | None) -> None:
    global _warmup_active_phase
    with _warmup_lock:
        if generation != _warmup_generation:
            return
        _warmup_active_phase = phase_key


def _record_warmup_failure(generation: int, phase_key: str, error: str) -> None:
    global _last_warmup_error
    with _warmup_lock:
        if generation != _warmup_generation:
            return
        _last_warmup_error = f"{phase_key}: {error}"


def _finish_warmup(generation: int, *, success: bool) -> None:
    global _readiness, _warmup_state, _warmup_active_phase, _warmup_thread
    with _warmup_lock:
        if generation != _warmup_generation:
            return
        _warmup_active_phase = None
        _warmup_thread = None
        if success:
            _readiness = "ready"
            _warmup_state = "complete"
        else:
            _readiness = "failed"
            _warmup_state = "failed"


def _ensure_warmup_started(reason: str | None = None) -> None:
    """Ensure the background warmup thread is running or already complete."""
    global _readiness, _warmup_state, _warmup_active_phase
    global _last_warmup_reason, _last_warmup_error, _warmup_thread

    if _vault_root is None:
        return

    with _warmup_lock:
        if _warmup_state == "complete":
            return
        if _warmup_thread is not None and _warmup_thread.is_alive():
            return
        generation = _warmup_generation
        _readiness = "warming"
        _warmup_state = "running"
        _warmup_active_phase = None
        _last_warmup_error = None
        _last_warmup_reason = reason
        _warmup_thread = threading.Thread(
            target=_run_warmup,
            args=(generation, _vault_root),
            daemon=True,
            name="brain-startup-warmup",
        )
        _warmup_thread.start()
        if _logger:
            _logger.info("warmup started (%s)", reason or "unspecified")


def _wait_for_warmup(timeout: float = 5.0) -> bool:
    """Join the current warmup thread for tests and bounded shutdown paths."""
    thread = _warmup_thread
    if thread is None:
        return True
    thread.join(timeout=timeout)
    return not thread.is_alive()


def _readiness_next_action(
    readiness: str,
    warmup_state: str,
    *,
    tool_name: str | None = None,
) -> str:
    next_tool = tool_name or "brain_session"
    if readiness == "ready":
        return "Call `brain_session` when you start real Brain work."
    if warmup_state == "failed":
        return (
            f"Retry `{next_tool}` or call `brain_init(warmup=true, debug=true)` "
            "to restart warmup and inspect cheap diagnostics."
        )
    if warmup_state == "not_started":
        return (
            f"Call `{next_tool}` or `brain_init(warmup=true)` to start background warmup."
        )
    return f"Retry `{next_tool}` shortly while Brain warmup continues."


def _readiness_snapshot(debug: bool = False, *, tool_name: str | None = None) -> dict:
    with _warmup_lock:
        readiness = _readiness
        warmup_state = _warmup_state
        active_phase = _warmup_active_phase
        last_error = _last_warmup_error
        last_reason = _last_warmup_reason
    state = _get_state()
    payload = {
        "version": "1",
        "brain_core_version": state.loaded_version,
        "vault_root": state.vault_root,
        "vault_name": state.vault_name,
        "readiness": readiness,
        "warmup_state": warmup_state,
        "next_action": _readiness_next_action(
            readiness,
            warmup_state,
            tool_name=tool_name,
        ),
    }
    if active_phase:
        payload["active_phase"] = active_phase
    if last_error:
        payload["last_error"] = last_error
    if debug:
        payload["debug"] = {
            "active_phase": active_phase,
            "last_error": last_error,
            "last_reason": last_reason,
            "router_ready": state.router is not None,
            "index_ready": state.index is not None,
            "workspace_registry_ready": state.workspace_registry is not None,
        }
    return payload


def _fmt_progress(tool_name: str, needs: tuple[str, ...] = ()) -> CallToolResult:
    snapshot = _readiness_snapshot(debug=False, tool_name=tool_name)
    snapshot["status"] = "failed" if snapshot["warmup_state"] == "failed" else "starting"
    snapshot["tool"] = tool_name
    snapshot["retry_after_ms"] = _PROGRESS_RETRY_AFTER_MS
    if needs:
        snapshot["needs"] = list(needs)
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(snapshot, ensure_ascii=False))],
        isError=True,
    )


@contextlib.contextmanager
def _serialize_mutation(label: str):
    """Serialize vault mutations within this MCP server process.

    The script layer remains the source of truth for mutation behaviour; this
    lock only prevents overlapping mutating MCP calls from interleaving writes
    inside the shared server process.
    """
    if _logger:
        _logger.debug("mutation wait: %s", label)
    with _mutation_lock:
        if _logger:
            _logger.debug("mutation enter: %s", label)
        try:
            yield
        finally:
            if _logger:
                _logger.debug("mutation exit: %s", label)


def _run_with_timeout(label, fn, timeout=_STARTUP_OP_TIMEOUT):
    """Run fn() in a daemon thread with a timeout.

    On success returns the result. On timeout raises RuntimeError — a
    timed-out compile means the server would start with stale definitions
    that may not match the current scripts, so it's safer to fail loudly
    than serve silently broken data.
    """
    result = None
    exc_info = None

    def worker():
        nonlocal result, exc_info
        try:
            result = fn()
        except Exception:
            exc_info = sys.exc_info()

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        raise RuntimeError(
            f"{label} timed out after {timeout}s "
            f"(iCloud sync contention?)"
        )
    if exc_info:
        raise exc_info[1].with_traceback(exc_info[2])
    return result


def _run_phase(kind: str, phase_key: str, fn, *, on_error=None):
    """Log a named phase with begin/success/failure outcomes.

    Contract: all exceptions are logged and swallowed, returning ``None``.
    Callers that need stateful failure tracking can provide *on_error*.
    """
    started_at = time.monotonic()
    if _logger:
        _logger.info("%s phase begin: %s", kind, phase_key)
    try:
        result = fn()
    except Exception as e:
        if _logger:
            _logger.error(
                "%s phase failure: %s %.3fs: %s",
                kind,
                phase_key,
                time.monotonic() - started_at,
                e,
                exc_info=True,
            )
        if on_error is not None:
            on_error(str(e))
        return None
    if _logger:
        _logger.info(
            "%s phase success: %s %.3fs",
            kind,
            phase_key,
            time.monotonic() - started_at,
        )
    return result


def _load_router_for_warmup(vault_root: str, generation: int) -> dict | None:
    """Load or compile the router for the active warmup generation."""
    global _router, _router_checked_at, _router_dirty

    stale, data = _check_router(vault_root)
    if not stale and data is not None and _check_router_resource_counts(vault_root, data):
        stale = True
    if stale:
        t0 = time.monotonic()
        compiled = _run_with_timeout("router compile", lambda: compile_router.compile(vault_root))
        compile_router.persist_compiled_router(vault_root, compiled)
        if _logger:
            _logger.info("router compile (stale) %.1fs", time.monotonic() - t0)
        if _warmup_generation_matches(generation):
            _router = compiled
            _router_checked_at = time.monotonic()
            _router_dirty = False
        return compiled

    if _logger:
        _logger.info("router compile (fresh)")
    if _warmup_generation_matches(generation):
        _router = data
        _router_checked_at = time.monotonic()
        _router_dirty = False
    return data


def _load_index_for_warmup(vault_root: str, generation: int) -> dict | None:
    """Load or build the retrieval index for the active warmup generation."""
    global _index, _index_dirty, _index_checked_at

    stale, data = _check_index(vault_root)
    if stale:
        t0 = time.monotonic()
        index = _run_with_timeout("index build", lambda: build_index.build_index(vault_root))
        build_index.persist_retrieval_index(vault_root, index)
        if _logger:
            _logger.info("index build (stale) %.1fs", time.monotonic() - t0)
        if _warmup_generation_matches(generation):
            _index = index
            _index_dirty = False
            with _index_pending_lock:
                _index_pending.clear()
            _index_checked_at = time.monotonic()
        return index

    if _logger:
        _logger.info("index build (fresh)")
    if _warmup_generation_matches(generation):
        _index = data
        _index_dirty = False
        _index_checked_at = time.monotonic()
    return data


def _run_warmup(generation: int, vault_root: str) -> None:
    """Run heavyweight startup work behind the readiness boundary."""
    if not _warmup_generation_matches(generation):
        return

    phase_results: dict[str, object | None] = {}

    for phase_key, fn in (
        ("router_freshness", lambda: _load_router_for_warmup(vault_root, generation)),
        ("index_freshness", lambda: _load_index_for_warmup(vault_root, generation)),
        ("workspace_registry_load", lambda: workspace_registry.load_registry(vault_root)),
        ("session_mirror_refresh", lambda: _enqueue_mirror_refresh(generation=generation)),
    ):
        _set_warmup_phase(generation, phase_key)
        result = _run_phase(
            "warmup",
            phase_key,
            fn,
            on_error=lambda error, phase_key=phase_key: _record_warmup_failure(
                generation,
                phase_key,
                error,
            ),
        )
        phase_results[phase_key] = result
        if phase_key == "workspace_registry_load" and result is not None and _warmup_generation_matches(generation):
            _set_workspace_registry(result)

    success = (
        _warmup_generation_matches(generation)
        and phase_results.get("router_freshness") is not None
        and phase_results.get("index_freshness") is not None
        and phase_results.get("workspace_registry_load") is not None
    )
    _finish_warmup(generation, success=success)


def _refresh_cli_available() -> bool:
    """Re-probe Obsidian CLI availability if TTL has elapsed."""
    global _cli_available, _cli_probed_at
    now = time.monotonic()
    if now - _cli_probed_at >= _CLI_PROBE_TTL:
        _cli_available = obsidian_cli.check_available()
        _cli_probed_at = now
    return _cli_available


def _mirror_worker_loop() -> None:
    """Drain the session-mirror queue until a shutdown sentinel arrives.

    One long-lived daemon thread runs this loop for the lifetime of the
    server process. Requests are coalesced by the enqueue path (maxsize=1
    queue; latest intent wins), so rapid-fire refreshes do not pile up. A
    slow filesystem stalls only this worker — every other caller just
    enqueues and returns.
    """
    while True:
        req = _mirror_queue.get()
        try:
            if req is _MIRROR_SHUTDOWN:
                return
            if not _warmup_generation_matches(req.get("generation", -1)):
                continue
            vault_root = req["vault_root"]
            build_kwargs = req["build_kwargs"]
            try:
                model = session.build_session_model(**build_kwargs)
                session.persist_session_markdown(model, vault_root)
            except Exception as e:
                if _logger:
                    _logger.warning(
                        "session mirror refresh failed: %s", e, exc_info=True,
                    )
        finally:
            _mirror_queue.task_done()


def _ensure_mirror_worker_started() -> None:
    """Start the session-mirror worker thread if it is not already running.

    Idempotent. Safe to call from repeated startup() invocations (tests).
    """
    global _mirror_worker_thread
    with _mirror_worker_lock:
        if _mirror_worker_thread is not None and _mirror_worker_thread.is_alive():
            return
        _mirror_worker_thread = threading.Thread(
            target=_mirror_worker_loop,
            daemon=True,
            name="brain-mirror-worker",
        )
        _mirror_worker_thread.start()


def _enqueue_mirror_refresh(*, generation: int | None = None) -> None:
    """Enqueue a session-mirror refresh for the background worker.

    Non-blocking. Coalesces with any pending request — the queue has
    ``maxsize=1`` and the latest intent always wins. Safe to call from any
    MCP request thread or from startup.
    """
    if _vault_root is None or _router is None:
        return
    generation = _warmup_generation if generation is None else generation
    if not _warmup_generation_matches(generation):
        return
    _ensure_mirror_worker_started()
    req = {
        "generation": generation,
        "vault_root": _vault_root,
        "build_kwargs": {
            "router": _router,
            "vault_root": _vault_root,
            "obsidian_cli_available": _cli_available,
            "config": _config,
            "active_profile": _session_profile,
            "load_config_if_missing": False,
        },
    }
    # Coalesce: drop a pending request, enqueue the latest. A race with the
    # worker dequeuing between our get_nowait() and put_nowait() just means
    # both requests land in order (worker processes stale, then latest) —
    # still converges to the latest state on disk. queue.Full can only fire
    # if the worker has not yet claimed the slot we just freed; rare and
    # harmless (latest intent arrives via the next enqueue).
    try:
        _mirror_queue.get_nowait()
        _mirror_queue.task_done()
    except queue.Empty:
        pass
    try:
        _mirror_queue.put_nowait(req)
    except queue.Full:
        pass


def _drain_mirror_queue(timeout: float = _MIRROR_DRAIN_TIMEOUT) -> None:
    """Signal the mirror worker to exit and wait briefly for it to finish.

    Registered via atexit. Best-effort: on a stuck filesystem the worker
    may outlive the timeout, in which case the process exits normally
    (daemon threads are killed on interpreter exit). Any orphaned
    tempfile is swept on the next startup by ``_sweep_mirror_tmpfiles``.
    """
    global _mirror_worker_thread
    thread = _mirror_worker_thread
    if thread is None or not thread.is_alive():
        return
    try:
        _mirror_queue.put(_MIRROR_SHUTDOWN, timeout=0.1)
    except queue.Full:
        # A pending refresh holds the slot; drain it so shutdown can enqueue.
        try:
            _mirror_queue.get_nowait()
            _mirror_queue.task_done()
        except queue.Empty:
            pass
        try:
            _mirror_queue.put_nowait(_MIRROR_SHUTDOWN)
        except queue.Full:
            return
    thread.join(timeout=timeout)


def _register_mirror_drain_once() -> None:
    """Register the atexit drain hook at most once per process."""
    global _mirror_drain_registered
    if _mirror_drain_registered:
        return
    atexit.register(_drain_mirror_queue)
    _mirror_drain_registered = True


def _sweep_mirror_tmpfiles(vault_root: str) -> None:
    """Remove orphaned session.md tempfiles from ``.brain/local/``.

    The mirror worker writes via ``safe_write`` (tmp → fsync → rename). If
    the process was killed mid-write, the tempfile is orphaned. Sweep at
    startup — the rename-on-success guarantee means any unrenamed tempfile
    is stale by definition.

    Skip the sweep while this process still has a live mirror worker —
    same-process restart-style startup() calls can overlap an older
    in-flight mirror write, and deleting its tempfile would manufacture a
    false FileNotFoundError on the eventual rename.
    """
    if _mirror_worker_thread is not None and _mirror_worker_thread.is_alive():
        return
    pattern = os.path.join(vault_root, ".brain", "local", "session.md.*.tmp")
    for path in glob.glob(pattern):
        try:
            os.unlink(path)
            if _logger:
                _logger.info("swept orphaned session mirror tempfile: %s", path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Version drift — exit for proxy restart
# ---------------------------------------------------------------------------

VERSION_REL = os.path.join(".brain-core", "VERSION")


def _read_disk_version(vault_root: str) -> str | None:
    """Read the current .brain-core/VERSION from disk."""
    version_path = os.path.join(vault_root, VERSION_REL)
    try:
        with open(version_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


_EXIT_VERSION_DRIFT = 10  # proxy.py interprets this exit code


def _check_version_drift() -> None:
    """Exit if brain-core on disk has been upgraded.

    The MCP proxy will detect the exit code and relaunch the server
    with new code.

    Uses os._exit() instead of sys.exit() because sys.exit() raises
    SystemExit which gets wrapped in BaseExceptionGroup by anyio's task
    groups inside the MCP SDK. The async shutdown path loses the exit
    code — the server exits via "stdin closed" with code 0 instead of
    code 10, and the proxy treats it as a clean exit rather than a
    planned restart.
    """
    if _vault_root is None or _loaded_version is None:
        return
    try:
        disk_version = _read_disk_version(_vault_root)
    except Exception:
        return
    if disk_version is None or disk_version == _loaded_version:
        return
    if _logger:
        _logger.warning("version drift: %s -> %s, exiting for proxy restart",
                        _loaded_version, disk_version)
    _flush_log()
    os._exit(_EXIT_VERSION_DRIFT)


# ---------------------------------------------------------------------------
# Staleness detection
# ---------------------------------------------------------------------------

def _check_router(vault_root: str) -> tuple[bool, dict | None]:
    """Check staleness and return parsed data if fresh. (stale, data|None)"""
    router_path = os.path.join(vault_root, _router_rel())
    if not os.path.isfile(router_path):
        return True, None

    try:
        with open(router_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return True, None
    if not isinstance(data, dict):
        return True, None

    meta = data.get("meta", {})
    compiled_at = meta.get("compiled_at")
    sources = meta.get("sources", {})
    if not compiled_at or not sources:
        return True, None

    try:
        compiled_ts = datetime.fromisoformat(compiled_at).timestamp()
    except (ValueError, TypeError):
        return True, None

    all_types = data.get("artefacts", [])
    artefact_index = data.get("artefact_index", {})
    artefact_index_sources = meta.get("artefact_index_sources")
    if artefact_index and artefact_index_sources is None:
        return True, None
    if artefact_index_sources is not None and not isinstance(artefact_index_sources, list):
        return True, None
    artefact_index_source_paths = set(artefact_index_sources or [])

    expected_index_source_count = meta.get("artefact_index_source_count")
    if expected_index_source_count is not None:
        current_index_source_count = compile_router.count_living_artefact_index_entries(
            vault_root, all_types
        )
        if current_index_source_count != expected_index_source_count:
            return True, None

    for rel_path, expected_hash in sources.items():
        abs_path = os.path.join(vault_root, rel_path)
        if rel_path in artefact_index_source_paths:
            try:
                current_hash = compile_router.hash_living_artefact_source(abs_path)
            except (OSError, UnicodeDecodeError):
                return True, None
            if current_hash != expected_hash:
                return True, None
            continue

        try:
            if os.path.getmtime(abs_path) > compiled_ts:
                return True, None
        except OSError:
            return True, None

    return False, data


def _check_index(vault_root: str) -> tuple[bool, dict | None]:
    """Check staleness and return parsed data if fresh. (stale, data|None)"""
    index_path = os.path.join(vault_root, _index_rel())
    if not os.path.isfile(index_path):
        return True, None

    try:
        with open(index_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return True, None
    if not isinstance(data, dict):
        return True, None

    if data.get("meta", {}).get("index_version") != build_index.INDEX_VERSION:
        return True, None

    built_at = data.get("meta", {}).get("built_at")
    if not built_at:
        return True, None

    try:
        threshold = datetime.fromisoformat(built_at).timestamp()
    except (ValueError, TypeError):
        return True, None

    expected_count = data.get("meta", {}).get("document_count", 0)
    if _check_index_files(vault_root, expected_count, threshold):
        return True, None

    return False, data


def _check_index_files(vault_root: str, expected_count: int, threshold: float) -> bool:
    """Return True if stale: file count differs or any .md is newer than threshold."""
    all_types = compile_router.scan_living_types(vault_root) + compile_router.scan_temporal_types(vault_root)
    count = 0
    for type_info in all_types:
        for rel_path in iter_artefact_paths(vault_root, type_info):
            count += 1
            if count > expected_count:
                return True  # new files — short-circuit
            try:
                if os.path.getmtime(os.path.join(vault_root, rel_path)) > threshold:
                    return True
            except OSError:
                continue
    return count != expected_count  # catches deletions


def _resource_mtime_signature(
    vault_root: str,
) -> tuple[tuple[str, float | None], ...]:
    """Hashable signature governing router staleness.

    Shallow dirs are stat'd. Tree dirs are enumerated via ``os.scandir``,
    skipping ``_``/``.``-prefixed children to match ``iter_markdown_under``
    — we deliberately do not stat the tree root itself, because its mtime
    advances when a filtered child (e.g. ``_Archive/``) appears, which would
    force a full walk on every archive operation despite no counted state
    having changed.

    Missing dirs encode as None so absence is distinguishable from mtime 0.0.
    """
    out: list[tuple[str, float | None]] = []
    for rel, descend in compile_router.resource_source_dirs(vault_root):
        abs_root = os.path.join(vault_root, rel) if rel else vault_root
        if descend:
            _append_filtered_tree(abs_root, rel, vault_root, out)
        else:
            try:
                out.append((rel, os.path.getmtime(abs_root)))
            except OSError:
                out.append((rel, None))
    return tuple(out)


def _append_filtered_tree(
    abs_root: str,
    rel_root: str,
    vault_root: str,
    out: list[tuple[str, float | None]],
) -> None:
    """Recursively append entries for non-hidden children under *abs_root*.

    Dirs contribute ``(rel, mtime)``; files contribute ``(rel, None)`` for
    presence-only tracking (content edits are caught by ``_check_router``'s
    source-mtime path). Missing root encodes as a single ``(rel_root, None)``.
    """
    try:
        entries = sorted(os.scandir(abs_root), key=lambda e: e.name)
    except OSError:
        out.append((rel_root, None))
        return
    for entry in entries:
        if entry.name.startswith(".") or entry.name.startswith("_"):
            continue
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
            mtime = entry.stat(follow_symlinks=False).st_mtime if is_dir else None
        except OSError:
            continue
        child_rel = os.path.relpath(entry.path, vault_root)
        out.append((child_rel, mtime))
        if is_dir:
            _append_filtered_tree(entry.path, child_rel, vault_root, out)


def _check_router_resource_counts(vault_root: str, router: dict) -> bool:
    """Return True if any resource count on disk differs from the cached router.

    Complements ``_check_router`` (mtime-based): mtime checks detect edits to
    *existing* sources, while count checks detect *new or deleted* resources
    that were never in the manifest.

    Fast path: if no resource-holding directory has moved since the last full
    walk, the on-disk counts cannot have diverged, so we skip the walk.
    """
    global _resource_mtime_cache
    signature = _resource_mtime_signature(vault_root)
    if _resource_mtime_cache is not None and signature == _resource_mtime_cache:
        return False

    for key, fs_count in compile_router.resource_counts(vault_root).items():
        if fs_count != len(router.get(key, [])):
            return True
    try:
        current_index_source_count = compile_router.count_living_artefact_index_entries(
            vault_root, router.get("artefacts", [])
        )
    except Exception:
        return True
    if current_index_source_count != len(router.get("artefact_index", {})):
        return True
    _resource_mtime_cache = signature
    return False


def _ensure_router_fresh() -> None:
    """Auto-recompile if the router is stale (new types or modified sources).

    Filesystem staleness checks are throttled by _STALENESS_CHECK_TTL to
    avoid per-call I/O overhead. External changes are still detected within
    a few seconds. MCP writes that mark the router dirty bypass the TTL
    so the next read sees their effects immediately.
    """
    global _router, _router_checked_at, _router_dirty
    if _vault_root is None or _router is None:
        return
    if _router_dirty:
        try:
            _router = _compile_and_save(_vault_root)
        except Exception as e:
            if _logger:
                _logger.error("router recompile failed: %s", e, exc_info=True)
            _router_dirty = False  # prevent tight retry loop; staleness TTL will re-detect
            _router_checked_at = time.monotonic()
            return
        _refresh_session_mirror_best_effort()
        return
    now = time.monotonic()
    if now - _router_checked_at < _ROUTER_CHECK_TTL:
        return
    _router_checked_at = now
    stale, data = _check_router(_vault_root)
    if not stale and not _check_router_resource_counts(_vault_root, _router):
        return
    try:
        _router = _compile_and_save(_vault_root)
    except Exception as e:
        if _logger:
            _logger.error("router recompile failed: %s", e, exc_info=True)
        return
    _refresh_session_mirror_best_effort()


def _refresh_session_mirror_best_effort() -> None:
    """Enqueue a background mirror refresh; never blocks or raises.

    Used on mid-session recompile paths where a failed or slow refresh
    must not impact the triggering tool call. The actual build + persist
    runs in the session-mirror worker; failures are logged there.
    """
    _enqueue_mirror_refresh()


def _mark_index_dirty() -> None:
    """Flag the index for a full rebuild (e.g. version drift, unknown scope of change)."""
    global _index_dirty
    _index_dirty = True


def _mark_router_dirty() -> None:
    """Flag the router for recompile on the next _ensure_router_fresh call."""
    global _router_dirty
    _router_dirty = True


def _mark_index_pending(rel_path: str, type_hint: str | None = None) -> None:
    """Queue a single file for incremental index update on the next search."""
    with _index_pending_lock:
        _index_pending.append((rel_path, type_hint))


def _ensure_index_fresh() -> None:
    """Update the index if needed: incremental for queued paths, full rebuild
    if dirty flag is set, filesystem staleness check on TTL for external changes.
    """
    global _index, _index_checked_at, _index_dirty
    if _vault_root is None:
        return

    # Full rebuild takes priority over incremental
    if _index_dirty:
        try:
            _index = _build_index_and_save(_vault_root)
        except Exception as e:
            if _logger:
                _logger.error("index full rebuild failed: %s", e)
            _index_dirty = False  # prevent tight retry loop; staleness TTL will re-detect
            _index_checked_at = time.monotonic()
        return

    # Incremental updates for paths queued by brain_create/brain_edit
    if _index_pending and _index is not None:
        with _index_pending_lock:
            pending = _index_pending[:]
            _index_pending.clear()
        try:
            saved_built_at = _index["meta"].get("built_at")
            for rel_path, type_hint in pending:
                build_index.index_update(_index, _vault_root, rel_path, type_hint=type_hint, recompute=False)
            build_index._recompute_corpus_stats(_index)
            if saved_built_at:
                _index["meta"]["built_at"] = saved_built_at  # Don't advance threshold
            _save_json(_index, _vault_root, _index_rel())
            _index_checked_at = time.monotonic()
        except Exception as e:
            if _logger:
                _logger.error("index incremental update failed: %s", e)
            _mark_index_dirty()
        # Fall through to TTL-gated staleness check (detects external files)

    # Filesystem staleness check for external changes (throttled)
    now = time.monotonic()
    if now - _index_checked_at < _INDEX_CHECK_TTL:
        return
    _index_checked_at = now
    stale, data = _check_index(_vault_root)
    if not stale:
        return
    try:
        _index = _build_index_and_save(_vault_root)
    except Exception as e:
        if _logger:
            _logger.error("index staleness rebuild failed: %s", e)


# ---------------------------------------------------------------------------
# Compile & build helpers
# ---------------------------------------------------------------------------

def _save_json(data: dict, vault_root: str, rel_path: str) -> None:
    """Write a dict as JSON to vault_root/rel_path (atomic via safe_write_json)."""
    output_path = os.path.join(vault_root, rel_path)
    safe_write_json(output_path, data, bounds=vault_root)


def _compile_and_save(vault_root: str) -> dict:
    """Compile router and colours, write to disk, return compiled data.

    Always clears _router_dirty and resets the staleness-check TTL so that
    callers don't need to remember to do it themselves. Does not refresh
    the session mirror — that belongs to the caller so the operation is
    logged against the right scope (startup phase vs. mid-session recompile).
    """
    global _router_checked_at, _router_dirty
    compiled = compile_router.compile(vault_root)
    compile_router.persist_compiled_router(vault_root, compiled)
    _set_router(compiled)
    _router_checked_at = time.monotonic()
    _router_dirty = False
    return compiled


def _build_index_and_save(vault_root: str) -> dict:
    """Build retrieval index, write to disk, return index data.

    Always clears _index_dirty, _index_pending, and resets the staleness-check
    TTL so that callers don't need to remember to do it themselves.
    """
    global _index_dirty, _index_checked_at
    index = build_index.build_index(vault_root)
    build_index.persist_retrieval_index(vault_root, index)
    _index_dirty = False
    with _index_pending_lock:
        _index_pending.clear()
    _index_checked_at = time.monotonic()
    return index


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

def startup(vault_root: str | None = None) -> None:
    """Initialize the minimal server skeleton and start background warmup."""
    global _vault_root, _config, _vault_name, _loaded_version, _logger

    if vault_root is None:
        vault_root = os.environ.get("BRAIN_VAULT_ROOT")
    if vault_root is None:
        _vault_root = str(compile_router.find_vault_root())
    else:
        _vault_root = str(vault_root)

    # Record loaded version for drift detection
    _loaded_version = _read_disk_version(_vault_root)

    # Set up logging early so all subsequent startup steps are captured
    _logger = _setup_logging(_vault_root)
    _logger.info("startup begin (version %s, vault %s)", _loaded_version, _vault_root)
    generation = _reset_runtime_state_for_startup()

    # Sweep orphaned session-mirror tempfiles from a prior killed worker.
    # Cheap, idempotent, and the one layer with authority to decide "stale".
    _sweep_mirror_tmpfiles(_vault_root)

    # Start the session-mirror worker thread and register the atexit drain
    # before any caller might enqueue. Both are idempotent across repeated
    # startup() invocations (test fixtures call startup many times).
    _ensure_mirror_worker_started()
    _register_mirror_drain_once()

    # Load vault config (three-layer merge: template → vault → local)
    _config = _run_phase(
        "startup",
        "config_load",
        lambda: config_mod.load_config(_vault_root),
    )

    # CLI availability is probed lazily on first tool call via _refresh_cli_available()
    # to avoid blocking startup (the Obsidian IPC socket check is fast but we defer entirely).
    # Vault name: config > env var > directory basename
    config_brain_name = (_config or {}).get("vault", {}).get("brain_name", "")
    _vault_name = config_brain_name or os.environ.get("BRAIN_VAULT_NAME") or os.path.basename(_vault_root)

    _ensure_warmup_started("startup")
    if _logger:
        _logger.info("startup warmup enqueued (generation %s)", generation)
    _logger.info("startup complete")


# ---------------------------------------------------------------------------
# Runtime adapter and response formatting helpers (DD-026)
# ---------------------------------------------------------------------------

def _get_state() -> ServerState:
    return ServerState(
        vault_root=_vault_root,
        loaded_version=_loaded_version,
        config=_config,
        session_profile=_session_profile,
        router=_router,
        index=_index,
        cli_available=_cli_available,
        vault_name=_vault_name,
        workspace_registry=_workspace_registry,
        logger=_logger,
    )


def _set_router(router: dict | None) -> None:
    global _router
    _router = router


def _set_index(index: dict | None) -> None:
    global _index
    _index = index


def _set_workspace_registry(registry: dict | None) -> None:
    global _workspace_registry
    _workspace_registry = registry


def _set_session_profile(profile: str | None) -> None:
    global _session_profile
    _session_profile = profile


def _runtime() -> ServerRuntime:
    return ServerRuntime(
        get_state=_get_state,
        set_router=_set_router,
        set_index=_set_index,
        set_workspace_registry=_set_workspace_registry,
        set_session_profile=_set_session_profile,
        fmt_error=_fmt_error,
        fmt_progress=_fmt_progress,
        enforce_profile=_enforce_profile,
        refresh_cli_available=_refresh_cli_available,
        ensure_warmup_started=_ensure_warmup_started,
        ensure_router_fresh=_ensure_router_fresh,
        ensure_index_fresh=_ensure_index_fresh,
        get_readiness_snapshot=_readiness_snapshot,
        check_version_drift=_check_version_drift,
        mark_index_dirty=_mark_index_dirty,
        mark_index_pending=_mark_index_pending,
        mark_router_dirty=_mark_router_dirty,
        compile_and_save=_compile_and_save,
        build_index_and_save=_build_index_and_save,
        refresh_session_mirror_best_effort=_refresh_session_mirror_best_effort,
    )


def _enforce_profile(tool_name: str) -> CallToolResult | None:
    """Check if current session profile allows this tool.

    Returns None if allowed, or an error CallToolResult if denied.
    No enforcement if config or session profile is not set (backward compat).
    """
    if _config is None or _session_profile is None:
        return None
    profiles = _config.get("vault", {}).get("profiles", {})
    profile = profiles.get(_session_profile)
    if profile is None:
        return None  # unknown profile = no enforcement
    if tool_name not in profile.get("allow", []):
        return _fmt_error(
            f"operator profile '{_session_profile}' does not allow {tool_name}"
        )
    return None


def _fmt_error(msg):
    """Format an error as a CallToolResult with isError flag."""
    return CallToolResult(
        content=[TextContent(type="text", text=f"Error: {msg}")],
        isError=True,
    )


def _fmt_environment(env):
    """Format environment dict as key=value lines."""
    return "\n".join(f"{k}={v}" for k, v in env.items())


def _fmt_workspace_list(workspaces):
    """Format workspace list as readable plain text."""
    lines = []
    for ws in workspaces:
        status = ws.get("status", "")
        status_part = f"\t[{status}]" if status else ""
        lines.append(f"{ws['slug']}\t{ws['mode']}\t{ws['path']}{status_part}")
    return "\n".join(lines)


def _fmt_workspace_single(ws):
    """Format a single workspace as plain text."""
    return f"{ws['slug']}\t{ws['mode']}\t{ws['path']}"


# Dispatch table for brain_read single-item resources
_READ_FORMATTERS = {
    "type": lambda result, name: json.dumps(result, indent=2, ensure_ascii=False),
    "trigger": lambda result, name: json.dumps(result, indent=2, ensure_ascii=False),
    "memory": lambda result, name: json.dumps(result, indent=2, ensure_ascii=False),
    "environment": lambda result, name: _fmt_environment(result),
}


# ---------------------------------------------------------------------------
# Shared parameter descriptions — used by multiple @mcp.tool() registrations.
# Kept here (not duplicated at each call site) so the prose doesn't drift.
# ---------------------------------------------------------------------------

_BODY_FILE_DESCRIPTION = (
    "Absolute path to a body-content file in the vault or system temp "
    "directory. Mutually exclusive with body. Temp files are deleted after "
    "reading; vault files are left in place. To stage content, run "
    "`mktemp /tmp/brain-body-XXXXXX`, write the content, then pass that path."
)

_NAME_DESCRIPTION = (
    "Resource name for skill, memory, style, or template. Required when "
    "resource is one of those kinds. For templates, use the artefact type key, "
    "e.g. 'wiki'."
)

_FIX_LINKS_DESCRIPTION = "Repair resolvable broken wikilinks in this file."

_ARTEFACT_TYPE_FILTER_DESCRIPTION = (
    "Artefact type filter (e.g. 'living/wiki', 'temporal/research'). "
    "Applied only when resource='artefact'."
)

_ARTEFACT_TAG_FILTER_DESCRIPTION = (
    "Exact tag match in artefact frontmatter. Applied only when resource='artefact'."
)

class _SelectorWithinStep(BaseModel):
    """One ancestor step in a structural selector's disambiguation chain."""

    model_config = ConfigDict(extra="forbid")

    target: Annotated[
        str,
        Field(description=SELECTOR_WITHIN_TARGET_DESCRIPTION),
    ]
    occurrence: Annotated[
        int | None,
        Field(description=SELECTOR_WITHIN_OCCURRENCE_DESCRIPTION, ge=1),
    ] = None


class _StructuralSelector(BaseModel):
    """Disambiguates duplicate structural targets for brain_edit."""

    model_config = ConfigDict(extra="forbid")

    occurrence: Annotated[
        int | None,
        Field(description=SELECTOR_OCCURRENCE_DESCRIPTION, ge=1),
    ] = None
    within: Annotated[
        list[_SelectorWithinStep] | None,
        Field(description=SELECTOR_WITHIN_DESCRIPTION),
    ] = None

def _dump_model_payload(value):
    """Convert Pydantic tool arguments back to plain Python data for scripts."""
    if isinstance(value, BaseModel):
        return value.model_dump(exclude_none=True)
    return value


def _build_brain_move_params(
    op: str,
    *,
    source: str | None,
    dest: str | None,
    path: str | None,
    target_type: str | None,
    parent: str | None,
):
    """Validate flat brain_move fields and collapse them into handler params."""
    spec = _server_actions.MOVE_SPECS.get(op)
    if spec is None:
        raise ValueError(
            f"Unknown move op '{op}'. Valid: {', '.join(_server_actions.MOVE_SPECS)}"
        )
    payload = {
        "source": source,
        "dest": dest,
        "path": path,
        "target_type": target_type,
        "parent": parent,
    }
    return validate_spec(
        spec,
        payload,
        label=f"Move op '{op}'",
        hint=_server_actions.move_contract_hint(op),
        field_term="top-level field",
    )


def _build_brain_create_params(
    resource: str,
    *,
    type: str | None,
    title: str | None,
    body: str | None,
    body_file: str | None,
    frontmatter: dict | None,
    parent: str | None,
    key: str | None,
    name: str | None,
    fix_links: bool | None,
):
    """Validate flat brain_create fields and collapse them into handler params."""
    spec = CREATE_SPECS.get(resource)
    if spec is None:
        raise ValueError(
            f"Resource '{resource}' is not creatable via brain_create. "
            f"Creatable resources: {', '.join(CREATE_SPECS)}"
        )
    payload = {
        "type": type,
        "title": title,
        "body": body,
        "body_file": body_file,
        "frontmatter": frontmatter,
        "parent": parent,
        "key": key,
        "name": name,
        "fix_links": fix_links,
    }
    return validate_spec(
        spec,
        payload,
        label=f"Resource '{resource}'",
        hint=create_contract_hint(resource),
        field_term="top-level field",
    )


def _build_brain_edit_params(
    resource: str,
    operation: str,
    *,
    path: str | None,
    body: str | None,
    body_file: str | None,
    frontmatter: dict | None,
    target: str | None,
    selector: dict | None,
    scope: str | None,
    name: str | None,
    fix_links: bool | None,
):
    """Validate flat brain_edit fields and collapse them into handler params."""
    key = (resource, operation)
    spec = EDIT_SPECS.get(key)
    if spec is None:
        valid_ops = sorted({op for (_r, op) in EDIT_SPECS if _r == resource})
        if valid_ops:
            raise ValueError(
                f"Operation '{operation}' is not valid for resource='{resource}' "
                f"via brain_edit. Valid operations: {', '.join(valid_ops)}"
            )
        raise ValueError(
            f"Resource '{resource}' op '{operation}' is not supported by brain_edit. "
            f"Supported resources: {sorted({r for (r, _o) in EDIT_SPECS})}"
        )
    payload = {
        "path": path,
        "body": body,
        "body_file": body_file,
        "frontmatter": frontmatter,
        "target": target,
        "selector": selector,
        "scope": scope,
        "name": name,
        "fix_links": fix_links,
    }
    return validate_spec(
        spec,
        payload,
        label=f"Resource '{resource}' op '{operation}'",
        hint=edit_contract_hint(resource, operation),
        field_term="top-level field",
    )


def _build_brain_read_params(
    resource: str,
    *,
    name: str | None,
):
    """Validate flat brain_read fields and collapse them into handler params."""
    spec = READ_SPECS.get(resource)
    if spec is None:
        raise ValueError(
            f"Resource '{resource}' is not readable via brain_read. "
            f"Readable resources: {', '.join(READ_SPECS)}"
        )
    payload = {"name": name}
    return validate_spec(
        spec,
        payload,
        label=f"Resource '{resource}'",
        hint=read_contract_hint(resource),
        field_term="top-level field",
    )


def _build_brain_list_params(
    resource: str,
    *,
    query: str | None,
    type: str | None,
    parent: str | None,
    since: str | None,
    until: str | None,
    tag: str | None,
    top_k: int | None,
    sort: str | None,
):
    """Validate flat brain_list fields and collapse them into handler params."""
    spec = LIST_SPECS.get(resource)
    if spec is None:
        raise ValueError(
            f"Resource '{resource}' is not listable via brain_list. "
            f"Listable resources: {', '.join(LIST_SPECS)}"
        )
    payload = {
        "query": query,
        "type": type,
        "parent": parent,
        "since": since,
        "until": until,
        "tag": tag,
        "top_k": top_k,
        "sort": sort,
    }
    return validate_spec(
        spec,
        payload,
        label=f"Resource '{resource}'",
        hint=list_contract_hint(resource),
        field_term="top-level field",
    )


class _BrainActionDeleteParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: Annotated[
        str,
        Field(description="Vault-relative path to the artefact file to delete."),
    ]


class _BrainActionShapePrintableParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Annotated[
        str,
        Field(description="Source artefact path to shape into a printable."),
    ]
    slug: Annotated[
        str,
        Field(description="Printable slug used for the artefact and rendered output filenames."),
    ]
    render: Annotated[
        bool | None,
        Field(description="When true, render the printable output immediately after shaping."),
    ] = None
    keep_heading_with_next: Annotated[
        bool | None,
        Field(description="When true, keep headings with the following block during pagination."),
    ] = None
    pdf_engine: Annotated[
        str | None,
        Field(description="Optional Pandoc PDF engine override, e.g. 'xelatex' or 'lualatex'."),
    ] = None


class _BrainActionShapePresentationParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Annotated[
        str,
        Field(description="Source artefact path to shape into a presentation."),
    ]
    slug: Annotated[
        str,
        Field(description="Presentation slug used for the artefact and rendered output filenames."),
    ]
    render: Annotated[
        bool | None,
        Field(description="When true, render the presentation output immediately after shaping."),
    ] = None
    preview: Annotated[
        bool | None,
        Field(description="When true, launch the live preview after shaping."),
    ] = None


class _BrainActionStartShapingParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: Annotated[
        str,
        Field(description="Existing artefact path or resolvable name to shape."),
    ]
    title: Annotated[
        str | None,
        Field(description="Optional transcript title override."),
    ] = None
    skill_type: Annotated[
        str | None,
        Field(description="Optional shaping sub-skill label such as Brainstorm, Refine, or Discover."),
    ] = None


class _BrainActionFixLinksParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fix: Annotated[
        bool | None,
        Field(description="When true, apply unambiguous fixes instead of returning a dry-run report."),
    ] = None
    path: Annotated[
        str | None,
        Field(description="Optional file path to scope fix-links to one file instead of the whole vault."),
    ] = None
    links: Annotated[
        list[str] | None,
        Field(description="Optional list of target link stems to limit which resolvable links are rewritten."),
    ] = None


# ---------------------------------------------------------------------------
# brain_init — additive bootstrap/orientation snapshot
# ---------------------------------------------------------------------------

@mcp.tool()
def brain_init(
    warmup: Annotated[
        bool | None,
        Field(description=(
            "When true, ensure background warmup is underway or already complete, "
            "then return immediately."
        )),
    ] = None,
    debug: Annotated[
        bool | None,
        Field(description=(
            "When true, include cheap already-known diagnostics such as the active "
            "phase and capability readiness. Never triggers deep inspection."
        )),
    ] = None,
):
    """Return a cheap Brain bootstrap snapshot. Safe, idempotent, never blocks.

    Use as an additive orientation probe: returns vault identity plus coarse
    readiness and warmup state, with `next_action` guidance for the caller.
    Lighter than `brain_session` — does not compile the session payload and
    does not wait for warmup to finish even when `warmup=True`. `debug=True`
    only surfaces already-known cheap diagnostics; it never triggers deep
    inspection or forced rebuilds. Call `brain_session` when starting real
    Brain work.
    """
    with _trace_tool("brain_init", warmup=warmup, debug=debug):
        try:
            return _server_init.handle_brain_init(
                warmup=bool(warmup),
                debug=bool(debug),
                runtime=_runtime(),
            )
        except Exception as e:
            if _logger:
                _logger.error("brain_init: %s", e, exc_info=True)
            return _fmt_error(f"Unexpected error: {e}")


# ---------------------------------------------------------------------------
# brain_session — agent bootstrap, one-call session setup
# ---------------------------------------------------------------------------

@mcp.tool()
def brain_session(
    context: Annotated[
        str | None,
        Field(description=(
            "Context slug for scoped sessions (e.g. 'mcp-spike'). "
            "Context scoping is not yet implemented — accepted for forward compatibility."
        )),
    ] = None,
    operator_key: Annotated[
        str | None,
        Field(description=(
            "Three-word operator key (e.g. 'timber-compass-violet') authenticating the "
            "caller against registered operators in config. If omitted, the default "
            "profile from config is used."
        )),
    ] = None,
):
    """Bootstrap an agent session in one call.

    Returns a compiled JSON payload: always-rules, user preferences, gotchas, triggers,
    artefact type summaries, environment, and memory/skill/plugin/style indexes.
    Call once at session start; use brain_read for individual resources after.
    """
    with _trace_tool("brain_session", context=context, operator_key=operator_key):
        try:
            return _server_session.handle_brain_session(
                context=context,
                operator_key=operator_key,
                runtime=_runtime(),
            )
        except Exception as e:
            if _logger:
                _logger.error("brain_session: %s", e, exc_info=True)
            return _fmt_error(f"Unexpected error: {e}")


# ---------------------------------------------------------------------------
# brain_read — safe, no side effects
# ---------------------------------------------------------------------------

@mcp.tool()
def brain_read(
    resource: Annotated[
        Literal[
            "type", "trigger", "style", "template", "skill", "plugin",
            "memory", "workspace", "environment", "router", "compliance",
            "artefact", "file", "archive",
        ],
        Field(description=(
            "Resource kind. Use brain_list(resource=...) to enumerate collections."
        )),
    ],
    name: Annotated[
        str | None,
        Field(description=(
            "Resource identifier. Use the type key for 'type'/'template'; a "
            "trigger/name substring for 'memory'; the workspace slug for "
            "'workspace'; optional severity for 'compliance'; an artefact key, "
            "path, or basename for 'artefact'; and a vault-relative path for "
            "'file'/'archive'. Omit for 'environment'/'router'."
        )),
    ] = None,
):
    """Read a Brain vault resource. Safe, no side effects.

    Resolves and returns a single resource of the named kind. To list collections
    (all skills, all types, etc.), use brain_list(resource=...) instead.
    """
    with _trace_tool("brain_read", resource=resource, name=name):
        try:
            params = _build_brain_read_params(resource, name=name)
            return _server_reading.handle_brain_read(
                resource=resource,
                params=params,
                runtime=_runtime(),
            )
        except ValueError as e:
            return _fmt_error(str(e))
        except Exception as e:
            if _logger:
                _logger.error("brain_read: %s", e, exc_info=True)
            return _fmt_error(f"Unexpected error: {e}")


# ---------------------------------------------------------------------------
# brain_search — safe, no side effects
# ---------------------------------------------------------------------------

def _transform_cli_results(cli_results: list[str], type_filter: str | None,
                           tag_filter: str | None, status_filter: str | None,
                           top_k: int) -> list[dict]:
    """Transform Obsidian CLI search results (file paths) to match brain_search schema."""
    index_by_path = {}
    if _index:
        index_by_path = {doc["path"]: doc for doc in _index.get("documents", []) if "path" in doc}
    transformed = []
    for path in cli_results:
        if is_archived_path(path):
            continue
        doc_meta = index_by_path.get(path, {})
        doc_type = doc_meta.get("type", "")
        doc_tags = doc_meta.get("tags", [])
        doc_status = doc_meta.get("status")

        if type_filter and doc_type != type_filter:
            continue
        if tag_filter and tag_filter not in doc_tags:
            continue
        if status_filter and doc_status != status_filter:
            continue

        transformed.append({
            "path": path,
            "title": doc_meta.get("title", os.path.splitext(os.path.basename(path))[0]),
            "type": doc_type,
            "status": doc_status,
        })

    return transformed[:top_k]


def _fmt_search(source, results):
    """Format search results as multi-block TextContent."""
    meta = f"**Searched:** {len(results)} results (source: {source})"
    if not results:
        return [TextContent(type="text", text=meta)]
    lines = []
    for r in results:
        status_part = f"\t{r['status']}" if r.get("status") else ""
        lines.append(f"{r['title']}\t{r['path']}\t{r['type']}{status_part}")
    return [
        TextContent(type="text", text=meta),
        TextContent(type="text", text="\n".join(lines)),
    ]


def _fmt_list(results, type_filter=None):
    """Format brain_list results as multi-block TextContent."""
    type_part = f" (type: {type_filter})" if type_filter else ""
    meta = f"**Listed:** {len(results)} results{type_part}"
    if not results:
        return [TextContent(type="text", text=meta)]
    lines = []
    for r in results:
        status_part = f"\t{r['status']}" if r.get("status") else ""
        lines.append(f"{r['date']}\t{r['title']}\t{r['path']}\t{r['type']}{status_part}")
    return [
        TextContent(type="text", text=meta),
        TextContent(type="text", text="\n".join(lines)),
    ]


@mcp.tool()
def brain_search(
    query: Annotated[
        str,
        Field(description=(
            "Matched against artefact body and metadata, or against file name + "
            "content for non-artefact resources."
        )),
    ],
    resource: Annotated[
        Literal["artefact", "skill", "trigger", "style", "memory", "plugin"],
        Field(description=(
            "Collection to search. Non-artefact resources use text matching; "
            "type/tag/status apply only to artefacts."
        )),
    ] = "artefact",
    type: Annotated[
        str | None,
        Field(description=_ARTEFACT_TYPE_FILTER_DESCRIPTION),
    ] = None,
    tag: Annotated[
        str | None,
        Field(description=_ARTEFACT_TAG_FILTER_DESCRIPTION),
    ] = None,
    status: Annotated[
        str | None,
        Field(description=(
            "Frontmatter status filter (e.g. 'shaping', 'active'). "
            "Applied only when resource='artefact'."
        )),
    ] = None,
    top_k: Annotated[
        int,
        Field(description="Maximum number of results to return."),
    ] = 10,
):
    """Search vault content, relevance-ranked.

    Uses the Obsidian CLI live index when available; falls back to BM25 over the
    pre-built keyword index. For exhaustive enumeration (not relevance-ranked), use
    brain_list. Returns ranked results with path, title, type, status, and source.
    """
    with _trace_tool("brain_search", query=query, resource=resource, type=type, tag=tag):
        try:
            return _server_reading.handle_brain_search(
                query=query,
                resource=resource,
                type=type,
                tag=tag,
                status=status,
                top_k=top_k,
                runtime=_runtime(),
            )
        except ValueError as e:
            return _fmt_error(str(e))
        except Exception as e:
            if _logger:
                _logger.error("brain_search: %s", e, exc_info=True)
            return _fmt_error(f"Unexpected error: {e}")


# ---------------------------------------------------------------------------
# brain_list — exhaustive enumeration, not relevance-ranked
# ---------------------------------------------------------------------------

@mcp.tool()
def brain_list(
    resource: Annotated[
        Literal[
            "artefact", "skill", "trigger", "style", "plugin",
            "memory", "template", "type", "workspace", "archive",
        ],
        Field(description=(
            "Collection to list. query applies to non-artefact names; "
            "type/parent/since/until/tag/sort apply only to artefacts."
        )),
    ] = "artefact",
    query: Annotated[
        str | None,
        Field(description="Substring filter for non-artefact names."),
    ] = None,
    type: Annotated[
        str | None,
        Field(description=_ARTEFACT_TYPE_FILTER_DESCRIPTION),
    ] = None,
    parent: Annotated[
        str | None,
        Field(description=(
            "Return artefacts whose frontmatter parent matches this canonical "
            "artefact key. Artefact lists only."
        )),
    ] = None,
    since: Annotated[
        str | None,
        Field(description=(
            "Inclusive ISO start date on artefact created date. Artefact lists only."
        )),
    ] = None,
    until: Annotated[
        str | None,
        Field(description=(
            "Inclusive ISO end date on artefact created date. Artefact lists only."
        )),
    ] = None,
    tag: Annotated[
        str | None,
        Field(description=_ARTEFACT_TAG_FILTER_DESCRIPTION),
    ] = None,
    top_k: Annotated[
        int | None,
        Field(description="Hard cap on results returned (the list is exhaustive up to this limit). Artefact lists only; default 500."),
    ] = None,
    sort: Annotated[
        Literal["date_desc", "date_asc", "title"] | None,
        Field(description=(
            "Artefact list sort order: newest first, oldest first, or title. Artefact lists only; default date_desc."
        )),
    ] = None,
):
    """List vault artefacts exhaustively, not relevance-ranked.

    Unlike brain_search, returns all matching artefacts up to top_k. Use this when
    enumerating or filtering by type, date range, tag, or parent. Use resource to
    list non-artefact collections (e.g. resource='skill').
    """
    with _trace_tool("brain_list", resource=resource, type=type, since=since, tag=tag):
        try:
            params = _build_brain_list_params(
                resource,
                query=query,
                type=type,
                parent=parent,
                since=since,
                until=until,
                tag=tag,
                top_k=top_k,
                sort=sort,
            )
            return _server_reading.handle_brain_list(
                resource=resource,
                params=params,
                runtime=_runtime(),
            )
        except ValueError as e:
            return _fmt_error(str(e))
        except Exception as e:
            if _logger:
                _logger.error("brain_list: %s", e, exc_info=True)
            return _fmt_error(f"Unexpected error: {e}")


# ---------------------------------------------------------------------------
# brain_create — additive, safe to auto-approve
# ---------------------------------------------------------------------------

@mcp.tool()
def brain_create(
    type: Annotated[
        str,
        Field(description=(
            "Artefact type key. Required when resource='artefact'."
        )),
    ] = "",
    title: Annotated[
        str,
        Field(description=(
            "Artefact title. Required when resource='artefact'; used to derive "
            "the filename."
        )),
    ] = "",
    body: Annotated[
        str,
        Field(description=(
            "Markdown body content. Mutually exclusive with body_file. Required "
            "for non-artefact resources; optional for artefacts. Prefer "
            "body_file for larger content."
        )),
    ] = "",
    body_file: Annotated[str, Field(description=_BODY_FILE_DESCRIPTION)] = "",
    frontmatter: Annotated[
        dict | None,
        Field(description=(
            "Frontmatter overrides. For memories, use {'triggers': [...]}."
        )),
    ] = None,
    parent: Annotated[
        str | None,
        Field(description=(
            "Parent artefact reference. Accepts canonical key, resolvable name, "
            "or relative path. Living children use owner folders; temporal "
            "children keep date-based filing."
        )),
    ] = None,
    key: Annotated[
        str | None,
        Field(description=(
            "Explicit living-artefact key override."
        )),
    ] = None,
    resource: Annotated[
        Literal[*RESOURCE_KINDS],
        Field(description=(
            "Resource kind to create. Use 'name' instead of 'type'/'title' for "
            "non-artefact resources."
        )),
    ] = "artefact",
    name: Annotated[str, Field(description=_NAME_DESCRIPTION)] = "",
    fix_links: Annotated[bool | None, Field(description=_FIX_LINKS_DESCRIPTION)] = None,
):
    """Create a new vault resource. Additive — creates a file, cannot destroy existing work.

    For artefacts, requires type + title; the type's naming pattern derives the filename.
    For non-artefact resources (skill/memory/style/template), requires name + body.
    Returns the resolved path plus any wikilink warnings.
    """
    cleanup_path = temp_body_file_cleanup_path(body_file)
    with _trace_tool("brain_create", resource=resource, type=type, title=title, name=name):
        try:
            params = _build_brain_create_params(
                resource,
                type=type or None,
                title=title or None,
                body=body or None,
                body_file=body_file or None,
                frontmatter=frontmatter,
                parent=parent,
                key=key,
                name=name or None,
                fix_links=fix_links,
            )
            with _serialize_mutation(f"brain_create:{resource}:{type or name or title}"):
                return _server_artefacts.handle_brain_create(
                    resource=resource,
                    params=params,
                    cleanup_path=cleanup_path,
                    runtime=_runtime(),
                )
        except (ValueError, FileNotFoundError) as e:
            cleanup_temp_body_file(cleanup_path)
            return _fmt_error(str(e))
        except Exception as e:
            cleanup_temp_body_file(cleanup_path)
            if _logger:
                _logger.error("brain_create: %s", e, exc_info=True)
            return _fmt_error(f"Unexpected error: {e}")


# ---------------------------------------------------------------------------
# brain_edit — single-file mutation
# ---------------------------------------------------------------------------

@mcp.tool()
def brain_edit(
    operation: Annotated[
        Literal["edit", "append", "prepend", "delete_section"],
        Field(description=(
            "Mutation kind. edit replaces, append/prepend insert, "
            "delete_section removes a heading/callout section."
        )),
    ],
    path: Annotated[
        str,
        Field(description=(
            "Artefact identifier. Accepts canonical key, relative path, or "
            "resolvable basename/display name. Required when resource='artefact'."
        )),
    ] = "",
    body: Annotated[
        str,
        Field(description=(
            "Body content for edit/append/prepend. Mutually exclusive with "
            "body_file. Omit for frontmatter-only changes. Prefer body_file "
            "for larger content."
        )),
    ] = "",
    body_file: Annotated[str, Field(description=_BODY_FILE_DESCRIPTION)] = "",
    frontmatter: Annotated[
        dict | None,
        Field(description=(
            "Frontmatter changes. edit overwrites; append/prepend extend lists "
            "with dedup and overwrite scalars. Use null to delete fields."
        )),
    ] = None,
    target: Annotated[
        str | None,
        Field(description=(
            "Structural target: ':body', a heading like '## Notes', or a "
            "callout like '[!note] Status'. Required for structural edits and "
            "delete_section."
        )),
    ] = None,
    selector: Annotated[
        _StructuralSelector | None,
        Field(description="Disambiguates duplicate structural matches."),
    ] = None,
    scope: Annotated[
        Literal["section", "intro", "body", "heading", "header"] | None,
        Field(description=edit.brain_edit_scope_description()),
    ] = None,
    resource: Annotated[
        Literal[*RESOURCE_KINDS],
        Field(description=(
            "Resource kind to edit. Use 'name' instead of 'path' for "
            "skill/memory/style/template."
        )),
    ] = "artefact",
    name: Annotated[str, Field(description=_NAME_DESCRIPTION)] = "",
    fix_links: Annotated[bool, Field(description=_FIX_LINKS_DESCRIPTION)] = False,
):
    """Modify an existing vault resource via single-file mutation.

    Use for in-place edits to artefacts, skills, memories, styles, or templates.
    Structural edits (edit/append/prepend with target) require scope; delete_section
    requires target only. Returns the resolved path plus any wikilink warnings.
    """
    selector_payload = _dump_model_payload(selector)
    cleanup_path = temp_body_file_cleanup_path(body_file)
    with _trace_tool(
        "brain_edit",
        resource=resource,
        operation=operation,
        path=path,
        name=name,
        target=target,
        selector=selector_payload,
        scope=scope,
    ):
        try:
            params = _build_brain_edit_params(
                resource,
                operation,
                path=path or None,
                body=body or None,
                body_file=body_file or None,
                frontmatter=frontmatter,
                target=target,
                selector=selector_payload,
                scope=scope,
                name=name or None,
                fix_links=fix_links or None,
            )
            with _serialize_mutation(f"brain_edit:{resource}:{path or name}"):
                return _server_artefacts.handle_brain_edit(
                    resource=resource,
                    operation=operation,
                    params=params,
                    cleanup_path=cleanup_path,
                    runtime=_runtime(),
                )
        except (ValueError, FileNotFoundError) as e:
            cleanup_temp_body_file(cleanup_path)
            return _fmt_error(str(e))
        except Exception as e:
            cleanup_temp_body_file(cleanup_path)
            if _logger:
                _logger.error("brain_edit: %s", e, exc_info=True)
            return _fmt_error(f"Unexpected error: {e}")


# ---------------------------------------------------------------------------
# brain_move — destructive content-move ops, gated by approval
# ---------------------------------------------------------------------------

@mcp.tool()
def brain_move(
    op: Annotated[
        Literal["rename", "convert", "archive", "unarchive"],
        Field(description=(
            "Move operation selector. Use 'rename' to move a file, 'convert' to "
            "change artefact type and location, 'archive' to move a terminal-status "
            "artefact into _Archive/, or 'unarchive' to restore an archived artefact."
        )),
    ],
    source: Annotated[
        str | None,
        Field(description="Vault-relative source path used only when op='rename'."),
    ] = None,
    dest: Annotated[
        str | None,
        Field(description="Vault-relative destination path used only when op='rename'."),
    ] = None,
    path: Annotated[
        str | None,
        Field(description="Vault-relative artefact path used by convert, archive, and unarchive."),
    ] = None,
    target_type: Annotated[
        str | None,
        Field(description="Destination artefact type key used only when op='convert'."),
    ] = None,
    parent: Annotated[
        str | None,
        Field(description="Optional parent artefact reference used only when op='convert'."),
    ] = None,
):
    """Perform a destructive content move while preserving artefact semantics.

    Uses a flat top-level MCP surface for caller ergonomics, with explicit runtime
    validation of op-specific field requirements before delegating to the existing
    rename/convert/archive implementations.
    """
    trace_payload = {"op": op}
    for key, value in (
        ("source", source),
        ("dest", dest),
        ("path", path),
        ("target_type", target_type),
        ("parent", parent),
    ):
        if value is not None:
            trace_payload[key] = value

    with _trace_tool("brain_move", **trace_payload):
        try:
            params = _build_brain_move_params(
                op,
                source=source,
                dest=dest,
                path=path,
                target_type=target_type,
                parent=parent,
            )
            with _serialize_mutation(f"brain_move:{op}"):
                return _server_actions.handle_brain_move(
                    op=op,
                    params=params,
                    runtime=_runtime(),
                )
        except ValueError as e:
            return _fmt_error(str(e))
        except Exception as e:
            if _logger:
                _logger.error("brain_move: %s", e, exc_info=True)
            return _fmt_error(f"Unexpected error: {e}")


# ---------------------------------------------------------------------------
# brain_action — workflow/utility bucket, gated by approval
# ---------------------------------------------------------------------------

@mcp.tool()
def brain_action(
    action: Annotated[
        Literal[
            "delete",
            "shape-printable",
            "shape-presentation",
            "start-shaping",
            "fix-links",
        ],
        Field(description=(
            "Workflow or utility action selector. The remaining brain_action "
            "surface covers delete, shaping helpers, and fix-links."
        )),
    ],
    params: Annotated[
        (
            _BrainActionDeleteParams
            | _BrainActionShapePrintableParams
            | _BrainActionShapePresentationParams
            | _BrainActionStartShapingParams
            | _BrainActionFixLinksParams
            | None
        ),
        Field(description=(
            "Action-specific parameters object. The schema expands into named variants "
            "for delete, shaping helpers, and fix-links."
        )),
    ] = None,
):
    """Perform a workflow or utility action that may touch multiple files.

    The smaller residual brain_action surface intentionally uses the simple
    action-plus-params contract. Mutating actions are serialised and validated
    by the existing handler and script layers.
    """
    params_payload = _dump_model_payload(params)
    with _trace_tool("brain_action", action=action, params=params_payload):
        try:
            with _serialize_mutation(f"brain_action:{action}"):
                return _server_actions.handle_brain_action(
                    action=action,
                    params=params_payload,
                    runtime=_runtime(),
                )
        except Exception as e:
            if _logger:
                _logger.error("brain_action: %s", e, exc_info=True)
            return _fmt_error(f"Unexpected error: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _shutdown(reason: str) -> None:
    """Log a clean shutdown message and exit."""
    if _logger:
        _logger.info("shutdown: %s", reason)
    _flush_log()
    sys.exit(0)


def _handle_signal(signum: int, _frame) -> None:
    """Handle SIGTERM/SIGINT per MCP stdio lifecycle spec."""
    try:
        name = signal.Signals(signum).name
    except ValueError:
        name = f"signal({signum})"
    _shutdown(f"received {name}")


def main():
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        startup()
    except Exception as e:
        if _logger:
            _logger.error("fatal startup error: %s", e, exc_info=True)
            _flush_log()
        else:
            print(f"brain-core fatal startup error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        mcp.run(transport="stdio")
    except SystemExit:
        # Preserve exit code (e.g. 10 for version drift) so the proxy
        # can distinguish planned restarts from crashes.
        _flush_log()
        raise
    except BaseException as e:
        if _logger:
            _logger.error("unexpected error: %s", e, exc_info=True)
            _flush_log()
        else:
            print(f"brain-core unexpected error: {e}", file=sys.stderr)
            print(traceback.format_exc(), file=sys.stderr)
        sys.exit(1)

    _shutdown("stdin closed")


if __name__ == "__main__":
    main()
