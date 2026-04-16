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

Requires Python >=3.10 and the `mcp` SDK (see requirements.txt).
"""

import contextlib
import errno
import json
import logging
import logging.handlers
import os
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
    collect_headings,
    find_section,
    is_archived_path,
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
_router_checked_at: float = 0.0
_index_checked_at: float = 0.0

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


def _refresh_cli_available() -> bool:
    """Re-probe Obsidian CLI availability if TTL has elapsed."""
    global _cli_available, _cli_probed_at
    now = time.monotonic()
    if now - _cli_probed_at >= _CLI_PROBE_TTL:
        _cli_available = obsidian_cli.check_available()
        _cli_probed_at = now
    return _cli_available


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

    compiled_at = data.get("meta", {}).get("compiled_at")
    sources = data.get("meta", {}).get("sources", {})
    if not compiled_at or not sources:
        return True, None

    try:
        compiled_ts = datetime.fromisoformat(compiled_at).timestamp()
    except (ValueError, TypeError):
        return True, None

    for rel_path in sources:
        abs_path = os.path.join(vault_root, rel_path)
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
        for rel_path in build_index.find_md_files(vault_root, type_info):
            count += 1
            if count > expected_count:
                return True  # new files — short-circuit
            try:
                if os.path.getmtime(os.path.join(vault_root, rel_path)) > threshold:
                    return True
            except OSError:
                continue
    return count != expected_count  # catches deletions


def _check_router_resource_counts(vault_root: str, router: dict) -> bool:
    """Return True if any resource count on disk differs from the cached router.

    Complements ``_check_router`` (mtime-based): mtime checks detect edits to
    *existing* sources, while count checks detect *new or deleted* resources
    that were never in the manifest.
    """
    for key, fs_count in compile_router.resource_counts(vault_root).items():
        if fs_count != len(router.get(key, [])):
            return True
    return False


def _ensure_router_fresh() -> None:
    """Auto-recompile if the router is stale (new types or modified sources).

    Filesystem staleness checks are throttled by _STALENESS_CHECK_TTL to
    avoid per-call I/O overhead. External changes are still detected within
    a few seconds.
    """
    global _router, _router_checked_at
    if _vault_root is None or _router is None:
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


def _mark_index_dirty() -> None:
    """Flag the index for a full rebuild (e.g. version drift, unknown scope of change)."""
    global _index_dirty
    _index_dirty = True


def _mark_embeddings_dirty() -> None:
    """Flag doc embeddings as out of sync with the index."""
    global _embeddings_dirty
    _embeddings_dirty = True


def _ensure_embeddings_fresh() -> None:
    """Rebuild doc embeddings if they're out of sync with the index.

    Called lazily before brain_process operations that use embeddings.
    Only rebuilds if deps are available and the router is loaded.
    """
    global _embeddings_dirty, _doc_embeddings, _embeddings_meta
    if not _embeddings_dirty or _vault_root is None or _index is None:
        return
    if _router is not None:
        meta = build_index.build_embeddings(_vault_root, _router, _index["documents"])
        if meta is not None:
            _embeddings_meta = meta
            _load_embeddings(_vault_root)
    _embeddings_dirty = False


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


def _refresh_session_mirror() -> None:
    """Refresh `.brain/local/session.md` from the current session model."""
    if _vault_root is None or _router is None:
        return
    model = session.build_session_model(
        _router,
        _vault_root,
        obsidian_cli_available=_cli_available,
        config=_config,
        active_profile=_session_profile,
        load_config_if_missing=False,
    )
    session.persist_session_markdown(model, _vault_root)


def _compile_and_save(vault_root: str) -> dict:
    """Compile router and colours, write to disk, return compiled data.

    Resets the router staleness-check TTL so callers don't need to.
    """
    global _router_checked_at
    compiled = compile_router.compile(vault_root)
    _save_json(compiled, vault_root, _router_rel())
    compile_colours.generate(vault_root, compiled)
    _set_router(compiled)
    try:
        _refresh_session_mirror()
    except Exception as e:
        if _logger:
            _logger.error("session mirror refresh failed after compile: %s", e, exc_info=True)
    _router_checked_at = time.monotonic()
    return compiled


def _build_index_and_save(vault_root: str) -> dict:
    """Build retrieval index, write to disk, return index data.

    Always clears _index_dirty, _index_pending, and resets the staleness-check
    TTL so that callers don't need to remember to do it themselves.
    """
    global _type_embeddings, _embeddings_meta, _doc_embeddings, _index_dirty, _index_checked_at, _embeddings_dirty
    index = build_index.build_index(vault_root)
    _save_json(index, vault_root, _index_rel())
    _index_dirty = False
    _embeddings_dirty = False
    with _index_pending_lock:
        _index_pending.clear()
    _index_checked_at = time.monotonic()
    # Build embeddings if deps available and router is loaded
    if _router is not None:
        meta = build_index.build_embeddings(vault_root, _router, index["documents"])
        if meta is not None:
            _embeddings_meta = meta
            _load_embeddings(vault_root)
    return index


def _load_embeddings(vault_root: str) -> None:
    """Load pre-built embeddings from disk if available."""
    global _type_embeddings, _embeddings_meta, _doc_embeddings
    try:
        import numpy as np
    except ImportError:
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
        pass  # embeddings unavailable — graceful degradation


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

    # Load vault config (three-layer merge: template → vault → local)
    try:
        _config = config_mod.load_config(_vault_root)
    except Exception as e:
        _logger.error("startup: config load failed: %s", e, exc_info=True)

    # Auto-compile router if stale (reuse parsed data when fresh)
    # Timeout guard: compile writes CSS + graph.json to the vault, which can
    # hang indefinitely on iCloud-synced vaults if files are mid-upload.
    compiled_this_startup = False
    try:
        stale, data = _check_router(_vault_root)
        if stale:
            t0 = time.monotonic()
            _router = _run_with_timeout("router compile",
                                        lambda: _compile_and_save(_vault_root))
            compiled_this_startup = True
            _logger.info("router compile (stale) %.1fs", time.monotonic() - t0)
        else:
            _router = data
            _logger.info("router compile (fresh)")
    except Exception as e:
        _logger.error("startup: router compile failed: %s", e, exc_info=True)

    # Auto-build index if stale (same iCloud timeout concern)
    try:
        stale, data = _check_index(_vault_root)
        if stale:
            t0 = time.monotonic()
            _index = _run_with_timeout("index build",
                                       lambda: _build_index_and_save(_vault_root))
            _logger.info("index build (stale) %.1fs", time.monotonic() - t0)
        else:
            _index = data
            _logger.info("index build (fresh)")
    except Exception as e:
        _logger.error("startup: index build failed: %s", e, exc_info=True)

    # Load pre-built embeddings if available
    try:
        _load_embeddings(_vault_root)
    except Exception as e:
        _logger.error("startup: embeddings load failed: %s", e, exc_info=True)

    # Load workspace registry
    try:
        _workspace_registry = workspace_registry.load_registry(_vault_root)
    except Exception as e:
        _logger.error("startup: workspace registry failed: %s", e, exc_info=True)

    # _compile_and_save already refreshes the session mirror; only refresh
    # here when the router was fresh and no compile happened.
    if not compiled_this_startup:
        try:
            _refresh_session_mirror()
        except Exception as e:
            _logger.error("startup: session mirror refresh failed: %s", e, exc_info=True)

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
        compile_and_save=_compile_and_save,
        build_index_and_save=_build_index_and_save,
        surrounding_headings=_surrounding_headings,
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


def _surrounding_headings(vault_root, rel_path, target):
    """Return (prev_heading, next_heading) around a target section.

    Re-reads the file after a write to reflect the final state.
    Returns heading text (e.g. "## Alpha") or None for start/end of document.
    Uses collect_headings for a single scan, then locates the target in
    the heading list to find its neighbors (no second scan via find_section).
    """
    try:
        abs_path = os.path.join(vault_root, rel_path)
        with open(abs_path, "r", encoding="utf-8") as f:
            content = f.read()
        _, body = parse_frontmatter(content)

        # Callout targets don't appear in the heading list — fall back to find_section
        stripped = target.strip()
        if stripped.startswith("[!"):
            headings = collect_headings(body)
            heading_start, sec_end = find_section(body, target, include_heading=True)
            prev_heading = None
            next_heading = None
            for pos, _level, _text, raw in headings:
                if pos < heading_start:
                    prev_heading = raw
                elif pos >= sec_end:
                    next_heading = raw
                    break
            return prev_heading, next_heading

        # For heading targets: find in collected headings (single scan, no find_section)
        headings = collect_headings(body)

        # Parse target to match: level-aware if # markers present, else text-only
        if stripped.startswith("#"):
            markers = stripped.split()[0]
            target_level = len(markers)
            target_text = stripped[len(markers):].strip().lower()
        else:
            target_level = None
            target_text = stripped.lower()

        target_idx = None
        for idx, (pos, level, text, raw) in enumerate(headings):
            if text.lower() != target_text:
                continue
            if target_level is not None and level != target_level:
                continue
            target_idx = idx
            break

        if target_idx is None:
            return None, None

        # Find end of section: next heading at same or higher level
        target_level_actual = headings[target_idx][1]
        sec_end_idx = None
        for j in range(target_idx + 1, len(headings)):
            if headings[j][1] <= target_level_actual:
                sec_end_idx = j
                break

        prev_heading = headings[target_idx - 1][3] if target_idx > 0 else None
        next_heading = headings[sec_end_idx][3] if sec_end_idx is not None else None

        return prev_heading, next_heading
    except Exception as e:
        if _logger:
            _logger.warning("_surrounding_headings failed: %s", e, exc_info=True)
        return None, None


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
               type: str | None = None, since: str | None = None,
               until: str | None = None, tag: str | None = None,
               top_k: int = 500,
               sort: Literal["date_desc", "date_asc", "title"] = "date_desc"):
    """List vault artefacts by type, date range, or tag. Exhaustive — not relevance-ranked.

    Unlike brain_search, returns all matching artefacts up to top_k (default 500).
    Optional filters: type (e.g. 'temporal/research'), since/until (ISO dates e.g.
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
def brain_create(type: str = "", title: str = "", body: str = "", body_file: str = "", frontmatter: dict | None = None, parent: str | None = None, resource: str = "artefact", name: str = ""):
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
      parent     — optional project name to group artefacts under (e.g. "Brain").
                   Living types only; ignored for temporal types and non-artefact resources.

    Returns: confirmation message with path.
    """
    with _trace_tool("brain_create", resource=resource, type=type, title=title, name=name):
        try:
            return _server_artefacts.handle_brain_create(
                type=type,
                title=title,
                body=body,
                body_file=body_file,
                frontmatter=frontmatter,
                parent=parent,
                resource=resource,
                name=name,
                runtime=_runtime(),
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
def brain_edit(operation: Literal["edit", "append", "prepend", "delete_section"], path: str = "", body: str = "", body_file: str = "", frontmatter: dict | None = None, target: str | None = None, resource: str = "artefact", name: str = ""):
    """Modify an existing vault resource. Single-file mutation.

    For bodies over ~1 KB, prefer body_file over body to save tokens in the tool call.

    Parameters:
      resource   — resource kind (default "artefact"). Editable: artefact, skill,
                   memory, style, template.
      operation  — "edit" (replace body/section), "append" (add after), "prepend" (insert before),
                   or "delete_section" (remove a section including its heading; requires target)
      path       — relative path or basename for artefacts (resolves like wikilinks).
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
      target     — optional heading, callout title, ":entire_body", or
                   ":body_preamble".
                   When given: edit replaces that section's content; append inserts at end
                   of the section; prepend inserts before the section's heading line.
                   Include # markers to disambiguate duplicate headings (e.g. "### Notes").
                   For callouts, use the [!type] prefix (e.g. "[!note] Implementation status").
                   Use target=":entire_body" to explicitly target the full markdown body
                   after frontmatter. This spelling is also valid for append/prepend.
                   Use target=":body_preamble" with edit to target the leading
                   body content before the first targetable section (heading or
                   callout).
                   target=":body" is rejected; use one of the explicit reserved targets.

    Artefact paths validated against compiled router — wrong folder or naming rejected with helpful error.
    Non-artefact resources resolve via _Config/ conventions (no terminal status auto-move).
    """
    with _trace_tool("brain_edit", resource=resource, operation=operation, path=path, name=name, target=target):
        try:
            return _server_artefacts.handle_brain_edit(
                operation=operation,
                path=path,
                body=body,
                body_file=body_file,
                frontmatter=frontmatter,
                target=target,
                resource=resource,
                name=name,
                runtime=_runtime(),
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
      fix-links            — scan/fix broken wikilinks (optional: {fix} to apply)
      sync_definitions     — sync artefact library definitions to vault (optional: {dry_run, force, types, preference})
      archive              — archive an artefact to _Archive/ (params: {path}). Must have terminal status.
      unarchive            — restore an archived artefact to its original type folder (params: {path})
    """
    with _trace_tool("brain_action", action=action, params=params):
        try:
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
