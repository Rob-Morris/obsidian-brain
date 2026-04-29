#!/usr/bin/env python3
"""
Brain MCP Server — thin MCP wrapper over brain-core scripts.

All logic lives in `.brain-core/scripts/` as importable functions.
The server imports them, holds the compiled router and search index in memory,
and exposes 8 MCP tools:
  brain_session — bootstrap an agent session (compiled payload, one call)
  brain_read    — read compiled router resources (safe, no side effects)
  brain_search  — BM25 keyword search, with optional Obsidian CLI live search
  brain_list    — exhaustive enumeration by type, date range, or tag (not relevance-ranked)
  brain_create  — create new vault artefacts (additive, safe to auto-approve)
  brain_edit    — modify existing vault artefacts (single-file mutation)
  brain_action  — vault-wide/destructive ops: compile, build_index, rename, delete, convert
  brain_process — content processing: classify, resolve duplicates, ingest

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
  2. Auto-compile router if stale
  3. Auto-build index if stale
  4. Load both into memory
  5. Probe Obsidian CLI availability
  6. Serve via stdio

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
from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent

# ---------------------------------------------------------------------------
# Script imports — add scripts dir to sys.path
# ---------------------------------------------------------------------------

SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, os.path.abspath(SCRIPTS_DIR))

import _common
import compile_router
import compile_colours
import build_index
import search_index
import read as read_mod
import rename
import create
from _common import (
    is_archived_path,
    iter_artefact_paths,
    parse_frontmatter,
    resolve_body_file,
    safe_write_json,
)
import edit
import obsidian_cli
import session
import shape_printable
import shape_presentation
import start_shaping
import migrate_naming
import workspace_registry
import process
import list_artefacts
import fix_links
import sync_definitions
import config as config_mod
from . import _server_actions
from . import _server_artefacts
from . import _server_content
from . import _server_reading
from . import _server_session
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
_type_embeddings = None    # numpy array or None
_embeddings_meta = None    # dict with "types" and "documents" keys, or None
_doc_embeddings = None     # numpy array or None
_embeddings_dirty: bool = False  # set True when doc embeddings are out of sync with index


# Staleness-check TTLs — intentionally different because the checks have
# very different costs. Router: stats a handful of source files (cheap, 5s).
# Index: walks every .md file in the vault to compare count + mtime (expensive,
# 30s). Don't unify these without understanding the cost difference.
_CLI_PROBE_TTL = 30
_ROUTER_CHECK_TTL = 5
_INDEX_CHECK_TTL = 30
_STARTUP_OP_TIMEOUT = 30   # seconds — guard against iCloud I/O hangs during startup
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


def _run_startup_phase(phase_key: str, fn):
    """Log a startup phase with begin/success/failure outcomes.

    Contract: all exceptions are logged and swallowed, returning ``None``.
    Per the mutation-safety phase 1 plan, this preserves pre-refactor
    semantics — readiness-model changes for critical phases (router, index)
    are deferred to phase 2.
    """
    started_at = time.monotonic()
    if _logger:
        _logger.info("startup phase begin: %s", phase_key)
    try:
        result = fn()
    except Exception as e:
        if _logger:
            _logger.error(
                "startup phase failure: %s %.3fs: %s",
                phase_key,
                time.monotonic() - started_at,
                e,
                exc_info=True,
            )
        return None
    if _logger:
        _logger.info(
            "startup phase success: %s %.3fs",
            phase_key,
            time.monotonic() - started_at,
        )
    return result


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


def _enqueue_mirror_refresh() -> None:
    """Enqueue a session-mirror refresh for the background worker.

    Non-blocking. Coalesces with any pending request — the queue has
    ``maxsize=1`` and the latest intent always wins. Safe to call from any
    MCP request thread or from startup.
    """
    if _vault_root is None or _router is None:
        return
    _ensure_mirror_worker_started()
    req = {
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
    """
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


def _brain_process_enabled() -> bool:
    """Return True when the experimental process feature is enabled."""
    if not isinstance(_config, dict):
        return False
    defaults = _config.get("defaults", {})
    if not isinstance(defaults, dict):
        return False
    flags = defaults.get("flags", {})
    if not isinstance(flags, dict):
        return False
    return bool(flags.get(build_index.PROCESS_FEATURE_FLAG, False))


def _embeddings_enabled() -> bool:
    """Return True when the experimental process feature should use embeddings."""
    return _brain_process_enabled()


def _clear_loaded_embeddings() -> None:
    """Drop in-memory embeddings so callers cannot use stale arrays."""
    global _type_embeddings, _embeddings_meta, _doc_embeddings
    _type_embeddings = None
    _embeddings_meta = None
    _doc_embeddings = None


def _invalidate_embeddings_disk_state() -> None:
    """Delete persisted embeddings sidecars after router/index-affecting writes."""
    if _vault_root is None:
        return
    build_index.clear_embeddings_outputs(_vault_root)


def _mark_index_dirty() -> None:
    """Flag the index for a full rebuild (e.g. version drift, unknown scope of change)."""
    global _index_dirty, _embeddings_dirty
    _index_dirty = True
    _embeddings_dirty = True
    _clear_loaded_embeddings()
    _invalidate_embeddings_disk_state()


def _mark_router_dirty() -> None:
    """Flag the router for recompile on the next _ensure_router_fresh call."""
    global _router_dirty, _embeddings_dirty
    _router_dirty = True
    _embeddings_dirty = True
    _clear_loaded_embeddings()
    _invalidate_embeddings_disk_state()


def _mark_embeddings_dirty() -> None:
    """Flag doc embeddings as out of sync with the index."""
    global _embeddings_dirty
    _embeddings_dirty = True
    _clear_loaded_embeddings()


def _ensure_embeddings_fresh() -> None:
    """Rebuild doc embeddings if they're out of sync with the index.

    Called lazily before brain_process operations that use embeddings.
    Only rebuilds if deps are available and the router is loaded.
    """
    global _embeddings_dirty, _doc_embeddings, _embeddings_meta
    if _vault_root is None:
        return
    if not _embeddings_enabled():
        _clear_loaded_embeddings()
        _invalidate_embeddings_disk_state()
        _embeddings_dirty = False
        return
    if _index is None or _router is None:
        return
    needs_build = _embeddings_dirty or any(
        value is None for value in (_type_embeddings, _embeddings_meta, _doc_embeddings)
    )
    if not needs_build:
        return
    meta = build_index.refresh_embeddings_outputs(
        _vault_root,
        _router,
        _index["documents"],
        enable_embeddings=True,
    )
    if meta is not None:
        _embeddings_meta = meta
        _load_embeddings(_vault_root)
    else:
        _clear_loaded_embeddings()
    _embeddings_dirty = False


def _mark_index_pending(rel_path: str, type_hint: str | None = None) -> None:
    """Queue a single file for incremental index update on the next search."""
    with _index_pending_lock:
        _index_pending.append((rel_path, type_hint))
    _mark_embeddings_dirty()
    _invalidate_embeddings_disk_state()


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
            _mark_embeddings_dirty()
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
    global _router_checked_at, _router_dirty, _embeddings_dirty
    compiled = compile_router.compile(vault_root)
    _save_json(compiled, vault_root, _router_rel())
    compile_colours.generate(vault_root, compiled)
    build_index.clear_embeddings_outputs(vault_root)
    _clear_loaded_embeddings()
    _set_router(compiled)
    _router_checked_at = time.monotonic()
    _router_dirty = False
    _embeddings_dirty = _embeddings_enabled()
    return compiled


def _build_index_and_save(vault_root: str) -> dict:
    """Build retrieval index, write to disk, return index data.

    Always clears _index_dirty, _index_pending, and resets the staleness-check
    TTL so that callers don't need to remember to do it themselves.
    """
    global _type_embeddings, _embeddings_meta, _doc_embeddings, _index_dirty, _index_checked_at, _embeddings_dirty
    index = build_index.build_index(vault_root)
    meta = build_index.persist_retrieval_outputs(
        vault_root,
        index,
        router=_router,
        enable_embeddings=_embeddings_enabled(),
        config=_config,
    )
    _index_dirty = False
    _embeddings_dirty = False
    with _index_pending_lock:
        _index_pending.clear()
    _index_checked_at = time.monotonic()
    if meta is not None:
        _embeddings_meta = meta
        _load_embeddings(vault_root)
    else:
        _clear_loaded_embeddings()
    return index


def _load_embeddings(vault_root: str) -> None:
    """Load pre-built embeddings from disk if available."""
    global _type_embeddings, _embeddings_meta, _doc_embeddings
    if not _embeddings_enabled():
        _clear_loaded_embeddings()
        build_index.clear_embeddings_outputs(vault_root)
        return
    try:
        import numpy as np
    except ImportError:
        _clear_loaded_embeddings()
        return
    type_path = os.path.join(vault_root, build_index.TYPE_EMBEDDINGS_REL)
    doc_path = os.path.join(vault_root, build_index.DOC_EMBEDDINGS_REL)
    meta_path = os.path.join(vault_root, build_index.EMBEDDINGS_META_REL)
    try:
        if os.path.isfile(type_path) and os.path.isfile(meta_path):
            _type_embeddings = np.load(type_path)
            with open(meta_path, "r", encoding="utf-8") as f:
                _embeddings_meta = json.load(f)
            if not isinstance(_embeddings_meta, dict):
                _embeddings_meta = None
        if os.path.isfile(doc_path):
            _doc_embeddings = np.load(doc_path)
    except (OSError, ValueError):
        _clear_loaded_embeddings()  # embeddings unavailable — graceful degradation


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

def startup(vault_root: str | None = None) -> None:
    """Initialize server state: find vault, compile/build if stale, load data."""
    global _vault_root, _config, _router, _index, _vault_name, _loaded_version, _workspace_registry, _logger

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

    # Sweep orphaned session-mirror tempfiles from a prior killed worker.
    # Cheap, idempotent, and the one layer with authority to decide "stale".
    _sweep_mirror_tmpfiles(_vault_root)

    # Start the session-mirror worker thread and register the atexit drain
    # before any caller might enqueue. Both are idempotent across repeated
    # startup() invocations (test fixtures call startup many times).
    _ensure_mirror_worker_started()
    _register_mirror_drain_once()

    # Load vault config (three-layer merge: template → vault → local)
    _config = _run_startup_phase(
        "config_load",
        lambda: config_mod.load_config(_vault_root),
    )

    # Auto-compile router if stale (reuse parsed data when fresh)
    # Timeout guard: compile writes CSS + graph.json to the vault, which can
    # hang indefinitely on iCloud-synced vaults if files are mid-upload.
    def _load_router():
        global _router
        stale, data = _check_router(_vault_root)
        if stale:
            t0 = time.monotonic()
            _router = _run_with_timeout("router compile",
                                        lambda: _compile_and_save(_vault_root))
            _logger.info("router compile (stale) %.1fs", time.monotonic() - t0)
            return _router
        else:
            _router = data
            _logger.info("router compile (fresh)")
            return _router
    _run_startup_phase("router_freshness", _load_router)

    # Auto-build index if stale (same iCloud timeout concern)
    def _load_index():
        global _index
        stale, data = _check_index(_vault_root)
        if stale:
            t0 = time.monotonic()
            _index = _run_with_timeout("index build",
                                       lambda: _build_index_and_save(_vault_root))
            _logger.info("index build (stale) %.1fs", time.monotonic() - t0)
            return _index
        else:
            _index = data
            _logger.info("index build (fresh)")
            return _index
    _run_startup_phase("index_freshness", _load_index)

    # Load pre-built embeddings if available
    _run_startup_phase("embeddings_load", lambda: _load_embeddings(_vault_root))

    # Load workspace registry
    _workspace_registry = _run_startup_phase(
        "workspace_registry_load",
        lambda: workspace_registry.load_registry(_vault_root),
    )

    # Session mirror is owned by startup, not by _compile_and_save, so
    # mid-session recompiles don't log under a misleading "startup phase"
    # label. The refresh is enqueued (non-blocking) and handled by the
    # mirror worker; startup completes the phase as soon as the request
    # lands in the queue. Failures (if any) are logged from the worker.
    _run_startup_phase("session_mirror_refresh", _enqueue_mirror_refresh)

    # CLI availability is probed lazily on first tool call via _refresh_cli_available()
    # to avoid blocking startup (the Obsidian IPC socket check is fast but we defer entirely).
    # Vault name: config > env var > directory basename
    config_brain_name = (_config or {}).get("vault", {}).get("brain_name", "")
    _vault_name = config_brain_name or os.environ.get("BRAIN_VAULT_NAME") or os.path.basename(_vault_root)

    _logger.info("startup complete")


# ---------------------------------------------------------------------------
# Runtime adapter and response formatting helpers (DD-026)
# ---------------------------------------------------------------------------

def _get_state() -> ServerState:
    return ServerState(
        vault_root=_vault_root,
        config=_config,
        session_profile=_session_profile,
        router=_router,
        index=_index,
        cli_available=_cli_available,
        vault_name=_vault_name,
        workspace_registry=_workspace_registry,
        type_embeddings=_type_embeddings,
        embeddings_meta=_embeddings_meta,
        doc_embeddings=_doc_embeddings,
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
        enforce_profile=_enforce_profile,
        refresh_cli_available=_refresh_cli_available,
        ensure_router_fresh=_ensure_router_fresh,
        ensure_index_fresh=_ensure_index_fresh,
        ensure_embeddings_fresh=_ensure_embeddings_fresh,
        check_version_drift=_check_version_drift,
        mark_index_dirty=_mark_index_dirty,
        mark_embeddings_dirty=_mark_embeddings_dirty,
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
# brain_session — agent bootstrap, one-call session setup
# ---------------------------------------------------------------------------

@mcp.tool()
def brain_session(context: str | None = None, operator_key: str | None = None):
    """Bootstrap an agent session. Returns everything needed to work with the Brain in one call.

    Args:
        context: Optional context slug for scoped sessions (e.g., "mcp-spike").
                 Context scoping is not yet implemented — parameter accepted for forward compatibility.
        operator_key: Optional three-word operator key (e.g., "timber-compass-violet").
                      Authenticates the caller against registered operators in config.
                      If omitted, the default profile from config is used.

    Returns a compiled JSON payload: always-rules, user preferences, gotchas,
    triggers, artefact type summaries, environment, memory/skill/plugin/style indexes.
    Call this once at session start. Use brain_read for individual resources after.
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
    resource: Literal[
        "type", "trigger", "style", "template", "skill", "plugin",
        "memory", "workspace", "environment", "router", "compliance",
        "artefact", "file", "archive",
    ],
    name: str | None = None,
):
    """Read Brain vault resources. Safe, no side effects.

    Resources:
      type        — read a specific artefact type definition (name = type key)
      trigger     — read a specific trigger (name required)
      style       — read a specific style file (name required)
      template    — read a template file (name = artefact type key)
      skill       — read a specific skill file (name required)
      plugin      — read a specific plugin file (name required)
      memory      — read a specific memory by trigger/name (case-insensitive substring)
      workspace   — resolve a specific workspace by slug (name = slug)
      environment — runtime environment info
      router      — always-rules and metadata
      compliance  — run structural compliance checks (name = severity filter: error/warning/info)
      artefact    — read an artefact file (name = relative path or basename; resolves like wikilinks)
      file        — read any vault file by name (resolves and delegates to the correct resource handler)
      archive     — read a specific archived file (name = path inside _Archive/)

    To list collections (all skills, all types, etc.), use brain_list(resource=...).
    """
    with _trace_tool("brain_read", resource=resource, name=name):
        try:
            return _server_reading.handle_brain_read(
                resource=resource,
                name=name,
                runtime=_runtime(),
            )
        except ValueError as e:
            # Name-required errors from read handlers
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
def brain_search(query: str,
                 resource: Literal[
                     "artefact", "skill", "trigger", "style",
                     "memory", "plugin",
                 ] = "artefact",
                 type: str | None = None, tag: str | None = None,
                 status: str | None = None, top_k: int = 10):
    """Search vault content. Uses Obsidian CLI live index when available, BM25 fallback.

    Returns ranked results with path, title, type, status, and source.
    Optional filters: type (e.g. 'living/wiki'), tag, status (e.g. 'shaping'), top_k (default 10).

    Use resource to search non-artefact collections (e.g. resource='skill').
    Non-artefact search uses text matching on name + file content.
    Artefact-specific filters (type, tag, status) only apply when resource='artefact'.
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
def brain_list(resource: Literal[
                   "artefact", "skill", "trigger", "style", "plugin",
                   "memory", "template", "type", "workspace", "archive",
               ] = "artefact",
               query: str | None = None,
               type: str | None = None, parent: str | None = None,
               since: str | None = None,
               until: str | None = None, tag: str | None = None,
               top_k: int = 500,
               sort: Literal["date_desc", "date_asc", "title"] = "date_desc"):
    """List vault artefacts by type, date range, or tag. Exhaustive — not relevance-ranked.

    Unlike brain_search, returns all matching artefacts up to top_k (default 500).
    Optional filters: type (e.g. 'temporal/research'), parent (canonical artefact key),
    since/until (ISO dates e.g.
    '2026-03-20'), tag, top_k, sort ('date_desc', 'date_asc', 'title').

    Use resource to list non-artefact collections (e.g. resource='skill' lists all skills).
    The query parameter filters non-artefact resources by name substring.
    Artefact-specific filters (type, since, until, tag, sort) only apply when resource='artefact'.
    """
    with _trace_tool("brain_list", resource=resource, type=type, since=since, tag=tag):
        try:
            return _server_reading.handle_brain_list(
                resource=resource,
                query=query,
                type=type,
                parent=parent,
                since=since,
                until=until,
                tag=tag,
                top_k=top_k,
                sort=sort,
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
def brain_create(type: str = "", title: str = "", body: str = "", body_file: str = "", frontmatter: dict | None = None, parent: str | None = None, key: str | None = None, resource: str = "artefact", name: str = "", fix_links: bool = False):
    """Create a new vault resource. Additive — creates a file, cannot destroy existing work.

    For bodies over ~1 KB, prefer body_file over body to save tokens in the tool call.

    Parameters:
      resource   — resource kind (default "artefact"). Creatable: artefact, skill,
                   memory, style, template.
      type       — artefact type key (e.g. "ideas"). Required when resource="artefact".
      title      — human-readable title for artefacts. Required when resource="artefact".
      name       — resource name for non-artefact resources (e.g. "my-skill").
                   Required when resource is skill, memory, style, or template.
                   For templates, name is the artefact type key (e.g. "wiki").
      body       — markdown body content (optional for artefacts, required for others).
                   Mutually exclusive with body_file.
      body_file  — absolute path to a file containing the body content (optional).
                   Must be inside the vault or the system temp directory.
                   Temp files are deleted after reading; vault files are left in place.
                   Use for large content to keep MCP call displays compact.
                   Mutually exclusive with body.
                   To stage content: run mktemp /tmp/brain-body-XXXXXX to get a
                   safe temp path, write content there, then pass that path here.
      frontmatter — optional frontmatter field overrides (e.g. {"status": "shaping"}).
                   For memories, use {"triggers": ["keyword1", "keyword2"]}.
      parent     — optional parent artefact reference for child artefacts.
                   Accepts canonical artefact keys like "design/brain", unique
                   artefact names, or relative paths. Living children use
                   owner-derived folders; temporal children keep date-based
                   filing.
      key        — optional explicit key override for living artefacts.
      fix_links  — optional boolean (default false). When true, resolvable broken
                   wikilinks in the created file are auto-rewritten to their
                   target immediately after creation. Remaining unresolvable or
                   ambiguous links are still reported as warnings.

    Returns: confirmation message with path.
    """
    with _trace_tool("brain_create", resource=resource, type=type, title=title, name=name):
        try:
            with _serialize_mutation(f"brain_create:{resource}:{type or name or title}"):
                return _server_artefacts.handle_brain_create(
                    type=type,
                    title=title,
                    body=body,
                    body_file=body_file,
                    frontmatter=frontmatter,
                    parent=parent,
                    key=key,
                    resource=resource,
                    name=name,
                    runtime=_runtime(),
                    fix_links=fix_links,
                )
        except (ValueError, FileNotFoundError) as e:
            return _fmt_error(str(e))
        except Exception as e:
            if _logger:
                _logger.error("brain_create: %s", e, exc_info=True)
            return _fmt_error(f"Unexpected error: {e}")


# ---------------------------------------------------------------------------
# brain_edit — single-file mutation
# ---------------------------------------------------------------------------

@mcp.tool()
def brain_edit(
    operation: Literal["edit", "append", "prepend", "delete_section"],
    path: str = "",
    body: str = "",
    body_file: str = "",
    frontmatter: dict | None = None,
    target: str | None = None,
    selector: dict | None = None,
    scope: Literal["section", "intro", "body", "heading", "header"] | None = None,
    resource: Literal["artefact", "skill", "memory", "style", "template"] = "artefact",
    name: str = "",
    fix_links: bool = False,
):
    """Modify an existing vault resource. Single-file mutation.

    For bodies over ~1 KB, prefer body_file over body to save tokens in the tool call.

    Parameters:
      resource   — resource kind (default "artefact"). Editable: artefact, skill,
                   memory, style, template.
      operation  — "edit" (replace a resolved structural range), "append" (add after),
                   "prepend" (insert before), or "delete_section" (remove a resolved
                   heading-owned section or callout block; requires target)
      path       — canonical artefact key (e.g. "design/brain"), vault-relative
                   path, or filename basename. For temporal artefacts, the
                   display-name portion of the dated filename also resolves
                   (e.g. "Colour Theory" -> "20260404-research~Colour Theory.md").
                   Required when resource="artefact".
      name       — resource name for non-artefact resources (e.g. "my-skill").
                   Required when resource is skill, memory, style, or template.
                   For templates, name is the artefact type key (e.g. "wiki").
      body       — new body content (edit), content to append (append), or content to prepend (prepend).
                   Mutually exclusive with body_file.
                   Omit body for frontmatter-only changes.
      body_file  — absolute path to a file containing the body content (optional).
                   Must be inside the vault or the system temp directory.
                   Temp files are deleted after reading; vault files are left in place.
                   Use for large content to keep MCP call displays compact.
                   Mutually exclusive with body.
                   To stage content: run mktemp /tmp/brain-body-XXXXXX to get a
                   safe temp path, write content there, then pass that path here.
      frontmatter — optional frontmatter changes. Merge strategy depends on operation:
                   edit overwrites fields; append/prepend extend list fields (with dedup)
                   and overwrite scalars. Set a field to null to delete it.
                   All operations can be used for frontmatter-only changes by omitting body.
      target     — optional structural target. Use ":body" for the markdown body
                   after frontmatter, a heading target such as "## Notes", or a
                   callout target such as "[!note] Implementation status".
      selector   — optional target disambiguation object. Supported fields:
                   "within" (ordered ancestor chain of {target, occurrence?} steps)
                   and "occurrence" (1-based duplicate selector in the current
                   search space). ":body" is only valid as the top-level target.
      scope      — optional mutable range within the resolved target.
                   Required for structural edit/append/prepend calls.
                   Valid scopes:
                   - body target: "section", "intro"
                   - heading target: "section", "body", "intro", "heading"
                     ("heading" is edit-only)
                   - callout target: "section", "body", "header"
                     ("header" is edit-only)
                   delete_section does not accept scope.
      fix_links  — optional boolean (default false). When true, resolvable broken
                   wikilinks in the edited file are auto-rewritten to their
                   target after the edit completes. Remaining unresolvable or
                   ambiguous links are still reported as warnings.

    Artefact paths validated against compiled router — wrong folder or naming rejected with helpful error.
    Non-artefact resources resolve via _Config/ conventions (no terminal status auto-move).
    """
    with _trace_tool(
        "brain_edit",
        resource=resource,
        operation=operation,
        path=path,
        name=name,
        target=target,
        selector=selector,
        scope=scope,
    ):
        try:
            with _serialize_mutation(f"brain_edit:{resource}:{path or name}"):
                return _server_artefacts.handle_brain_edit(
                    operation=operation,
                    path=path,
                    body=body,
                    body_file=body_file,
                    frontmatter=frontmatter,
                    target=target,
                    selector=selector,
                    scope=scope,
                    resource=resource,
                    name=name,
                    runtime=_runtime(),
                    fix_links=fix_links,
                )
        except (ValueError, FileNotFoundError) as e:
            return _fmt_error(str(e))
        except Exception as e:
            if _logger:
                _logger.error("brain_edit: %s", e, exc_info=True)
            return _fmt_error(f"Unexpected error: {e}")


# ---------------------------------------------------------------------------
# brain_action — vault-wide/destructive ops, gated by approval
# ---------------------------------------------------------------------------

@mcp.tool()
def brain_action(
    action: Literal[
        "compile", "build_index", "rename", "delete", "convert",
        "shape-printable", "shape-presentation", "start-shaping", "migrate_naming",
        "register_workspace", "unregister_workspace", "fix-links",
        "sync_definitions", "archive", "unarchive",
    ],
    params: dict | None = None,
):
    """Perform vault-wide actions. Mutations — may modify multiple files.

    Actions:
      compile              — recompile the router from source files
      build_index          — rebuild the BM25 retrieval index
      rename               — rename/move a file (params: {source, dest} as relative paths)
      delete               — delete a file and clean wikilinks (params: {path})
      convert              — convert artefact to different type (params: {path, target_type}, optional: {parent})
      shape-printable      — create printable + render PDF (params: {source, slug}, optional: {render, keep_heading_with_next, pdf_engine})
      shape-presentation   — create presentation + render PDF + optional live preview (params: {source, slug}, optional: {render, preview})
      start-shaping        — bootstrap shaping session (params: {target}, optional: {title})
      migrate_naming       — migrate vault filenames to generous naming conventions (optional: {dry_run})
      register_workspace   — register a linked workspace (params: {slug, path})
      unregister_workspace — remove a linked workspace registration (params: {slug})
      fix-links            — scan/fix broken wikilinks (optional: {fix} to apply;
                             {path} to scope scan/fix to a single file;
                             {links} = list of target stems to limit which
                             resolvable links are fixed when {path} is set)
      sync_definitions     — sync artefact library definitions to vault (optional: {dry_run, force, types, preference, status}). Set status=true for a read-only classification of every library type (uninstalled, in_sync, sync_ready, locally_customised, conflict) plus a not_installable bucket. Install a new library type with types=["living/X"] — bare sync (no types) never installs, only updates already-installed types.
      archive              — archive an artefact to _Archive/ (params: {path}). Must have terminal status.
      unarchive            — restore an archived artefact to its original type folder (params: {path})
    """
    with _trace_tool("brain_action", action=action, params=params):
        try:
            with _serialize_mutation(f"brain_action:{action}"):
                return _server_actions.handle_brain_action(
                    action=action,
                    params=params,
                    runtime=_runtime(),
                )

        except Exception as e:
            if _logger:
                _logger.error("brain_action: %s", e, exc_info=True)
            return _fmt_error(f"Unexpected error: {e}")


# ---------------------------------------------------------------------------
# brain_process — content classification, resolution, ingestion
# ---------------------------------------------------------------------------

def _fmt_classify(result):
    """Format classification result as DD-026 plain text."""
    if result.get("mode") == "context_assembly":
        lines = ["**Classify** → context_assembly (no scoring available)\n"]
        for td in result.get("type_descriptions", []):
            lines.append(f"**{td['key']}** ({td['type']})")
            lines.append(td["description"])
            lines.append("")
        lines.append(result.get("instruction", ""))
        return "\n".join(lines)

    alt_lines = []
    for alt in result.get("alternatives", []):
        alt_lines.append(f"- {alt['key']} ({alt['type']}) — {alt['confidence']}%")

    parts = [f"**Classified** ({result['mode']}) → {result['key']} ({result['confidence']}%)"]
    if result.get("reasoning"):
        parts.append(result["reasoning"])
    if alt_lines:
        parts.append("\nAlternatives:")
        parts.extend(alt_lines)
    return "\n".join(parts)


def _fmt_resolve(result):
    """Format resolution result as DD-026 plain text."""
    if result.get("action") == "error":
        return None  # caller uses _fmt_error

    action = result["action"]
    if action == "create":
        return f"**Resolve** → create {result['key']}: {result['title']}\n{result['reasoning']}"
    elif action == "update":
        return f"**Resolve** → update {result['target_path']}\n{result['reasoning']}"
    elif action == "ambiguous":
        lines = [f"**Resolve** → ambiguous ({len(result.get('candidates', []))} candidates)"]
        lines.append(result["reasoning"])
        lines.append("\nCandidates:")
        for c in result.get("candidates", []):
            lines.append(f"- {c}")
        return "\n".join(lines)
    return json.dumps(result, indent=2)


def _fmt_ingest(result):
    """Format ingestion result as DD-026 plain text."""
    action = result.get("action_taken")
    if action == "created":
        return f"**Ingested** → created {result['type']}: {result['path']}"
    elif action == "updated":
        return f"**Ingested** → updated {result['path']}"
    elif action == "ambiguous":
        lines = [f"**Ingest paused** — needs decision"]
        if result.get("resolution", {}).get("candidates"):
            lines.append("\nCandidates:")
            for c in result["resolution"]["candidates"]:
                lines.append(f"- {c}")
        return "\n".join(lines)
    elif action == "needs_classification":
        return _fmt_classify(result.get("classification", {}))
    elif action == "error":
        return None  # caller uses _fmt_error
    return json.dumps(result, indent=2)


@mcp.tool()
def brain_process(
    operation: Literal["classify", "resolve", "ingest"],
    content: str,
    type: str | None = None,
    title: str | None = None,
    mode: Literal["auto", "embedding", "bm25_only", "context_assembly"] = "auto",
):
    """Process content for vault operations.

    Operations:
      classify  — Determine the best artefact type for content.
                  Returns ranked type matches with confidence scores.
      resolve   — Check if content should create a new artefact or update an existing one.
                  Requires type and title. Returns create/update/ambiguous decision.
      ingest    — Full pipeline: classify → resolve → create/update.
                  Optional type/title hints skip their respective steps.

    Modes (for classify/ingest): "auto", "embedding", "bm25_only", "context_assembly".
    """
    with _trace_tool("brain_process", operation=operation):
        try:
            if operation == "ingest":
                with _serialize_mutation(f"brain_process:{operation}"):
                    return _server_content.handle_brain_process(
                        operation=operation,
                        content=content,
                        type=type,
                        title=title,
                        mode=mode,
                        runtime=_runtime(),
                    )
            return _server_content.handle_brain_process(
                operation=operation,
                content=content,
                type=type,
                title=title,
                mode=mode,
                runtime=_runtime(),
            )
        except Exception as e:
            if _logger:
                _logger.error("brain_process: %s", e, exc_info=True)
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
