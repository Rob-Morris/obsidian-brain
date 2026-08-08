#!/usr/bin/env python3
"""
Brain MCP Server — thin MCP wrapper over brain-core scripts.

All logic lives in `.brain-core/scripts/` as importable functions.
The server imports them, holds the compiled router and search index in memory,
and exposes the MCP tool surface:
  brain_init    — additive bootstrap/orientation snapshot, cheap and idempotent
  brain_session — bootstrap an agent session (compiled payload, one call)
  brain_read    — read compiled router resources (safe, no side effects)
  brain_outline — list structural edit targets (safe, no side effects)
  brain_check   — run structured vault checks (safe, no side effects)
  brain_search  — relevance-ranked lexical, semantic, or hybrid search
  brain_list    — exhaustive enumeration by type, date range, or tag (not relevance-ranked)
  brain_upload_attachment — add a binary file to the Obsidian attachment namespace
  brain_create  — create new vault artefacts (additive, safe to auto-approve)
  brain_edit    — modify existing vault artefacts (single-file mutation)
  brain_define  — guarded type, trigger, and plugin definition authoring
  brain_reparent / brain_set_* — explicit lifecycle mutations
  brain_classify / brain_resolve — experimental read-only content processing
  brain_ingest  — experimental content ingestion that may create/update files
  brain_move    — destructive content-move ops: rename, convert, archive, unarchive
  brain_action  — workflow/utility bucket: delete, reparent, shaping helpers, fix-links

Why this pattern: scripts are the source of truth for all vault operations.
The MCP server gets in-memory caching for free (router/index loaded once at
startup). Standalone scripts pay a cold-start cost reading JSON from disk.
Agents without MCP use the scripts directly — same logic, same results.

Optional native Obsidian CLI integration (Obsidian 1.12+ IPC socket):
  - Search: CLI-first with BM25 fallback (CLI uses Obsidian's live index)
  - Rename: CLI-first with grep-and-replace fallback (CLI auto-updates wikilinks)
  - Requires Obsidian to be running with CLI enabled (communicates via ~/.obsidian-cli.sock)

Startup sequence:
  1. Find vault root (the proxy/runtime launch path sets BRAIN_VAULT_ROOT after resolving the active workspace binding)
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
from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Script imports — add scripts dir to sys.path
# ---------------------------------------------------------------------------

SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, os.path.abspath(SCRIPTS_DIR))

import compile_router
from _common import mutation_lock_error_message
from _lifecycle.document_parts import EmbeddingParts
import _lifecycle.retrieval_assets as retrieval_assets
import _lifecycle.retrieval_errors as retrieval_errors
import _semantic.model as semantic_model
import _search.index as search_index
import _search.paths as search_paths
from _common import (
    ParentChainError,
    PartialApplyError,
    MutationLockError,
    SELECTOR_OCCURRENCE_DESCRIPTION,
    SELECTOR_WITHIN_DESCRIPTION,
    SELECTOR_WITHIN_OCCURRENCE_DESCRIPTION,
    SELECTOR_WITHIN_TARGET_DESCRIPTION,
    iter_artefact_paths,
    parent_chain_error_message,
    safe_write_json,
    vault_mutation_lock,
)
import edit
import define as definition_workflows
from _staging import discard_staged_body, stage_body
from _resource_contract import RESOURCE_KINDS
import obsidian_cli
import retrieval_embeddings as _retrieval_embeddings
import session
import upload_attachment as attachment_upload
from start_shaping_session import SHAPING_MODES
import workspace_registry
import config as config_mod
from . import _server_actions
from . import _server_artefacts
from . import _server_config_state
from . import _server_init
from . import _server_content
from . import _server_readiness
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
from ._server_runtime import ReadinessInfo, ServerRuntime, ServerState

# Path constants — read from script modules (single source of truth).
def _router_rel() -> str:
    return compile_router.OUTPUT_PATH

def _index_rel() -> str:
    return search_paths.OUTPUT_PATH

# ---------------------------------------------------------------------------
# Server state
# ---------------------------------------------------------------------------

mcp = FastMCP(name="brain")

_vault_root: str | None = None
# `_config` answers "what is the current config?" — the last successfully
# published merged config dict (lingers as last-good during an error state).
# `_config_state` answers "is it fresh/healthy?" — see _server_config_state.
# Readers must reach config via `_current_config()` (fail-closed), never `_config`
# directly; `_publish_config_reload` is the sole exception (it reads the outgoing
# config to diff semantic enablement before swapping).
_config: dict | None = None
_config_state: _server_config_state.ConfigState = _server_config_state.ConfigProbeError(
    None, "config not loaded"
)
_config_lock = threading.RLock()
_session_profile: str | None = None
_router: dict | None = None
_index: dict | None = None
_last_index_error: str | None = None
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
_semantic_warmup_state: str = "disabled"
_warmup_active_phase: str | None = None
_last_warmup_error: str | None = None
_last_warmup_reason: str | None = None
_last_semantic_warmup_error: str | None = None
_warmup_generation: int = 0
_semantic_enablement_generation: int = 0
_warmup_thread: threading.Thread | None = None
_semantic_warmup_thread: threading.Thread | None = None
_warmup_lock = threading.Lock()
_router_ready_event = threading.Event()

_INDEX_STATE_ERROR_TYPES = (
    retrieval_errors.UnreadableRetrievalSourceError,
    retrieval_errors.CompiledRouterUnavailableError,
    retrieval_errors.RetrievalPersistenceError,
    retrieval_errors.SemanticRuntimeUnavailableError,
    semantic_model.SemanticModelError,
)
_type_embeddings = None
_embeddings_meta = None
_doc_embeddings = None
_embedding_parts_by_path: dict[str, EmbeddingParts] | None = None
# _doc_embeddings_pending tracks specific paths whose doc embeddings are stale.
# _doc_embeddings_dirty is a full-rebuild signal that overrides the set.
# _type_embeddings_dirty signals taxonomy churn (router change) requiring type re-encode.
# Refresh fires if any of these are non-default OR any in-memory cache is None.
_doc_embeddings_pending: set[str] = set()
_doc_embeddings_pending_lock = threading.Lock()
_doc_embeddings_dirty: bool = False
_type_embeddings_dirty: bool = False


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
        "%(asctime)s [pid=%(process)d] [%(levelname)s] %(message)s",
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
    global _config, _config_state
    global _last_index_error
    global _index_dirty, _router_dirty, _router_checked_at, _index_checked_at
    global _resource_mtime_cache, _warmup_generation, _warmup_thread
    global _semantic_warmup_thread, _readiness, _warmup_state
    global _semantic_warmup_state, _warmup_active_phase
    global _last_warmup_error, _last_warmup_reason, _last_semantic_warmup_error
    global _doc_embeddings_dirty, _type_embeddings_dirty
    global _router_ready_event

    _session_profile = None
    with _config_lock:
        _config = None
        _config_state = _server_config_state.ConfigProbeError(None, "config not loaded")
    _index = None
    _last_index_error = None
    _workspace_registry = None
    _index_dirty = False
    _router_dirty = False
    _router_checked_at = 0.0
    _index_checked_at = 0.0
    _resource_mtime_cache = None
    with _warmup_lock:
        old_router_ready_event = _router_ready_event
        _warmup_generation += 1
        _router_ready_event = threading.Event()
        _warmup_thread = None
        _semantic_warmup_thread = None
        _readiness = "cold"
        _warmup_state = "not_started"
        _semantic_warmup_state = "disabled"
        _warmup_active_phase = None
        _last_warmup_error = None
        _last_warmup_reason = None
        _last_semantic_warmup_error = None
        generation = _warmup_generation
    old_router_ready_event.set()
    _set_router(None)
    _doc_embeddings_dirty = False
    _type_embeddings_dirty = False
    with _index_pending_lock:
        _index_pending.clear()
    with _doc_embeddings_pending_lock:
        _doc_embeddings_pending.clear()
    return generation


def _config_path_signature(path: str) -> tuple:
    """Return a cheap signature for one config input path."""
    try:
        stat = os.stat(path)
    except FileNotFoundError:
        return (os.path.abspath(path), False, None, None, None, None, None)
    return (
        os.path.abspath(path),
        True,
        stat.st_mtime_ns,
        stat.st_size,
        stat.st_ino,
        stat.st_dev,
        stat.st_mode,
    )


def _format_config_error(exc: BaseException, *, prefix: str = "config reload failed") -> str:
    return f"{prefix}: {exc}"


def _probe_config(
    vault_root: str, *, error_prefix: str
) -> _server_config_state.ProbeOutcome:
    """Resolve config input paths and stat them into a change signature.

    Wraps only expected boundary failures. A programmer bug (e.g. a mis-stubbed
    path helper raising RuntimeError) propagates untouched to the unexpected-error
    path and is never reported as a user config error. The signature is computed
    exactly once here and threaded onward, so the load path never re-probes.
    """
    try:
        paths = config_mod.config_input_paths(vault_root)
        signature = tuple(_config_path_signature(path) for path in paths)
    except (FileNotFoundError, OSError) as exc:
        return _server_config_state.ProbeFailed(
            _format_config_error(exc, prefix=error_prefix)
        )
    return _server_config_state.ProbeOk(paths, signature)


def _load_config(
    paths: tuple, *, error_prefix: str
) -> _server_config_state.LoadOutcome:
    """Load and merge config for already-resolved input paths.

    Wraps only expected boundary failures (missing/unreadable files, malformed
    YAML, invalid config shape, invalid text encoding); other exceptions
    propagate as programmer bugs.
    """
    try:
        config = config_mod.load_config_from_paths(paths)
    except (OSError, config_mod.YamlError, config_mod.ConfigError, UnicodeDecodeError) as exc:
        return _server_config_state.LoadFailed(
            _format_config_error(exc, prefix=error_prefix)
        )
    return _server_config_state.LoadOk(config)


def _config_embeddings_enabled(config: dict | None) -> bool:
    if _vault_root is None or config is None:
        return False
    return retrieval_assets.embeddings_should_refresh(_vault_root, config=config)


def _set_vault_name_from_config(config: dict | None) -> None:
    global _vault_name
    config_brain_name = (config or {}).get("vault", {}).get("brain_name", "")
    _vault_name = (
        config_brain_name
        or os.environ.get("BRAIN_VAULT_NAME")
        or os.path.basename(_vault_root or "")
    )


def _current_config() -> dict | None:
    """Return the published config only when it is fresh, else None (fail closed).

    Returns the SAME dict object held by `_config` — never a copy — so callers
    (and tests) that mutate the live config in place keep working. During any
    error state this returns None, so every config-derived feature fails closed.
    """
    return _config if isinstance(_config_state, _server_config_state.ConfigFresh) else None


def _derived_work_config() -> dict | None:
    """Return config for background derived work.

    A transient probe error means we could not stat config inputs, not that the
    last-good config is structurally bad. Background work such as semantic warmup
    may keep using that last-good config so a stat blip does not permanently
    degrade derived state. Sticky load errors still fail closed.
    """
    if isinstance(
        _config_state,
        (_server_config_state.ConfigFresh, _server_config_state.ConfigProbeError),
    ):
        return _config
    return None


def _config_error_message() -> str | None:
    """Operator-facing config error for the current state, or None when fresh."""
    state = _config_state
    if isinstance(
        state,
        (_server_config_state.ConfigProbeError, _server_config_state.ConfigLoadError),
    ):
        return state.error
    return None


def _semantic_warmup_enablement_matches(
    warmup_generation: int,
    semantic_enablement_generation: int,
) -> bool:
    with _warmup_lock:
        return (
            warmup_generation == _warmup_generation
            and semantic_enablement_generation == _semantic_enablement_generation
        )


def _reconcile_semantic_config_change(
    *,
    was_enabled: bool,
    is_enabled: bool,
    reason: str,
) -> None:
    """Update semantic warmup state after config enablement changes."""
    global _semantic_enablement_generation, _semantic_warmup_state
    global _semantic_warmup_thread, _last_semantic_warmup_error
    if was_enabled == is_enabled:
        return
    with _warmup_lock:
        _semantic_enablement_generation += 1
        if not is_enabled:
            _semantic_warmup_state = "disabled"
            _semantic_warmup_thread = None
            _last_semantic_warmup_error = None
    if not is_enabled:
        _clear_loaded_embeddings()
        _retrieval_embeddings.clear_query_encoder()
        with _doc_embeddings_pending_lock:
            _doc_embeddings_pending.clear()
        return
    _ensure_semantic_warmup_started(reason)


def _publish_config_reload(
    config: dict,
    *,
    reason: str,
    enqueue_mirror: bool,
) -> None:
    """Publish a freshly loaded config and all derived runtime state.

    Single side-effect point for a successful reload. Owns `_config` and every
    config-derived effect; the `_config_state` transition is owned by the caller
    (`_refresh_config`). The semantic-enablement diff is computed against the
    OUTGOING `_config` *before* reassigning it — this is the one place that reads
    `_config` directly rather than via `_current_config()`.
    """
    global _config
    was_enabled = _config_embeddings_enabled(_config)
    _config = config
    _set_vault_name_from_config(config)
    is_enabled = _config_embeddings_enabled(config)
    _reconcile_semantic_config_change(
        was_enabled=was_enabled,
        is_enabled=is_enabled,
        reason=reason,
    )
    if enqueue_mirror:
        _refresh_session_mirror_best_effort()


def _refresh_config(reason: str, *, error_prefix: str, enqueue_mirror: bool) -> str | None:
    """Probe → decide → (load) → commit the config-freshness state.

    The read-only probe runs outside the lock; the load and the state commit run
    inside `_config_lock` so two concurrent refreshes cannot both load and race
    to publish. Returns an operator-facing error string, or None when fresh.
    Shared by live reload and startup (which differ only by error prefix and
    whether a session-mirror refresh is enqueued).
    """
    global _config_state
    if _vault_root is None:
        return None
    probe_outcome = _probe_config(_vault_root, error_prefix=error_prefix)
    with _config_lock:
        decision = _server_config_state.on_probe(_config_state, probe_outcome)
        if isinstance(decision, _server_config_state.NeedsLoad):
            load_outcome = _load_config(decision.paths, error_prefix=error_prefix)
            commit = _server_config_state.on_load(decision.signature, load_outcome)
            # Commit the state BEFORE publishing. Publish's side effects
            # (semantic reconcile, mirror enqueue) read config via
            # `_current_config()`, which gates on `ConfigFresh`. Committing after
            # would make those readers observe the pre-reload state and fail
            # closed on a *successful* reload — notably leaving semantic warmup
            # stuck "disabled" when recovering from an error into a config that
            # enables semantic. `was_enabled` is unaffected: publish reads the
            # outgoing `_config` explicitly before swapping it.
            _config_state = commit.next_state
            if commit.publish:
                _publish_config_reload(
                    load_outcome.config,
                    reason=reason,
                    enqueue_mirror=enqueue_mirror,
                )
            elif commit.error and _logger:
                _logger.error("%s", commit.error)
            return commit.error
        _config_state = decision.next_state
        return decision.error


def _ensure_config_fresh(reason: str) -> str | None:
    """Reload merged config when its input signature changes (live reload).

    Returns an operator-facing error string when a changed config cannot be
    loaded; sticky until a later signature change reloads cleanly. A transient
    probe failure is reported but clears on the next successful same-signature
    probe without requiring a file change.
    """
    return _refresh_config(
        reason, error_prefix="config reload failed", enqueue_mirror=True
    )


def _warmup_generation_matches(generation: int) -> bool:
    with _warmup_lock:
        return generation == _warmup_generation


def _record_index_state_error(exc: BaseException) -> None:
    """Record typed index state errors that should block index-backed reads."""
    global _last_index_error
    if isinstance(exc, _INDEX_STATE_ERROR_TYPES):
        _last_index_error = str(exc)


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
            _router_ready_event.set()


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


def _ensure_semantic_warmup_started(reason: str | None = None) -> None:
    """Ensure the background semantic warmup track is running when enabled."""
    global _semantic_warmup_state, _semantic_warmup_thread, _last_semantic_warmup_error

    if _vault_root is None:
        return

    if not _embeddings_enabled():
        with _warmup_lock:
            _semantic_warmup_state = "disabled"
            _semantic_warmup_thread = None
            _last_semantic_warmup_error = None
        return

    with _warmup_lock:
        if _semantic_warmup_state == "ready":
            return
        if _semantic_warmup_thread is not None and _semantic_warmup_thread.is_alive():
            return
        generation = _warmup_generation
        semantic_enablement_generation = _semantic_enablement_generation
        _semantic_warmup_state = "warming"
        _last_semantic_warmup_error = None
        _semantic_warmup_thread = threading.Thread(
            target=_run_semantic_warmup,
            args=(generation, _vault_root, semantic_enablement_generation),
            daemon=True,
            name="brain-semantic-warmup",
        )
        _semantic_warmup_thread.start()
        if _logger:
            _logger.info("semantic warmup started (%s)", reason or "unspecified")


def _semantic_ready() -> bool:
    """Return True when semantic work should proceed instead of reporting progress."""
    with _warmup_lock:
        return _semantic_warmup_state == "ready"


def _semantic_warmup_restart_message(detail: str) -> str:
    """Return a consistent operator-facing recovery hint for warmup failures."""
    return f"{detail}; restart the server to retry semantic warmup"


def _ensure_semantic_ready(tool_name: str) -> CallToolResult | None:
    """Return progress/error until semantic requests are safe to serve."""
    return _server_readiness.require_semantic(_runtime(), tool_name)


def _wait_for_warmup(timeout: float = 5.0) -> bool:
    """Join the current warmup thread for tests and bounded shutdown paths."""
    thread = _warmup_thread
    if thread is None:
        return True
    thread.join(timeout=timeout)
    return not thread.is_alive()


def _wait_for_semantic_warmup(timeout: float = 5.0) -> bool:
    """Join the current semantic warmup thread for tests and bounded shutdown paths."""
    thread = _semantic_warmup_thread
    if thread is None:
        return True
    thread.join(timeout=timeout)
    return not thread.is_alive()


def _finish_semantic_warmup(
    generation: int,
    *,
    state: Literal["disabled", "ready", "deferred"],
    error: str | None = None,
    semantic_enablement_generation: int | None = None,
) -> None:
    global _semantic_warmup_state, _semantic_warmup_thread, _last_semantic_warmup_error
    with _warmup_lock:
        if generation != _warmup_generation:
            return
        if (
            semantic_enablement_generation is not None
            and semantic_enablement_generation != _semantic_enablement_generation
        ):
            return
        _semantic_warmup_state = state
        _semantic_warmup_thread = None
        _last_semantic_warmup_error = error


def _get_readiness_info() -> ReadinessInfo:
    with _warmup_lock:
        return ReadinessInfo(
            readiness=_readiness,
            warmup_state=_warmup_state,
            semantic_warmup_state=_semantic_warmup_state,
            active_phase=_warmup_active_phase,
            last_error=_last_warmup_error,
            last_reason=_last_warmup_reason,
            last_semantic_error=_last_semantic_warmup_error,
        )


def _readiness_snapshot(debug: bool = False, *, tool_name: str | None = None) -> dict:
    state = _get_state()
    info = _get_readiness_info()
    return _server_readiness.build_snapshot(
        state=state,
        info=info,
        debug=debug,
        tool_name=tool_name,
    )


def _fmt_progress(tool_name: str, needs: tuple[str, ...] = ()) -> CallToolResult:
    snapshot = _server_readiness.build_snapshot(
        state=_get_state(),
        info=_get_readiness_info(),
        debug=False,
        tool_name=tool_name,
        needs=needs,
    )
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
    """Serialize vault mutations across MCP, CLI, and other Brain processes.

    The script layer remains the source of truth for mutation behaviour. The
    thread lock protects shared in-memory state; the vault file lock extends
    the same mutation boundary to public Brain CLI processes.
    """
    if _logger:
        _logger.debug("mutation wait: %s", label)
    with _mutation_lock:
        lock = vault_mutation_lock(_vault_root) if _vault_root else contextlib.nullcontext()
        with contextlib.ExitStack() as stack:
            try:
                stack.enter_context(lock)
            except MutationLockError as exc:
                raise ValueError(mutation_lock_error_message(exc)) from exc
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
    _log_phase_begin(kind, phase_key)
    try:
        result = fn()
    except Exception as e:
        _log_phase_failure(kind, phase_key, started_at, str(e), exc_info=True)
        if on_error is not None:
            on_error(str(e))
        return None
    _log_phase_success(kind, phase_key, started_at)
    return result


def _log_phase_begin(kind: str, phase_key: str) -> None:
    if _logger:
        _logger.info("%s phase begin: %s", kind, phase_key)


def _log_phase_success(kind: str, phase_key: str, started_at: float) -> None:
    if _logger:
        _logger.info(
            "%s phase success: %s %.3fs",
            kind,
            phase_key,
            time.monotonic() - started_at,
        )


def _log_phase_failure(
    kind: str,
    phase_key: str,
    started_at: float,
    error: str,
    *,
    exc_info: bool = False,
) -> None:
    if _logger:
        _logger.error(
            "%s phase failure: %s %.3fs: %s",
            kind,
            phase_key,
            time.monotonic() - started_at,
            error,
            exc_info=exc_info,
        )


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
            _set_router(compiled)
            _router_checked_at = time.monotonic()
            _router_dirty = False
        return compiled

    if _logger:
        _logger.info("router compile (fresh)")
    if _warmup_generation_matches(generation):
        _set_router(data)
        _router_checked_at = time.monotonic()
        _router_dirty = False
    return data


def _load_index_for_warmup(vault_root: str, generation: int) -> dict | None:
    """Load or build the retrieval index for the active warmup generation."""
    global _index, _index_dirty, _index_checked_at, _embedding_parts_by_path
    global _last_index_error

    stale, data = _check_index(vault_root)
    if stale:
        t0 = time.monotonic()
        try:
            build_result = _run_with_timeout(
                "index build",
                lambda: search_index.build_index(vault_root),
            )
            index = build_result.index
            search_index.persist_retrieval_index(vault_root, index)
        except Exception as exc:
            if _warmup_generation_matches(generation):
                _record_index_state_error(exc)
            raise
        if _logger:
            _logger.info("index build (stale) %.1fs", time.monotonic() - t0)
        if _warmup_generation_matches(generation):
            _index = index
            _embedding_parts_by_path = build_result.embedding_parts_by_path
            _index_dirty = False
            _last_index_error = None
            with _index_pending_lock:
                _index_pending.clear()
            _index_checked_at = time.monotonic()
        return index

    if _logger:
        _logger.info("index build (fresh)")
    if _warmup_generation_matches(generation):
        _index = data
        _embedding_parts_by_path = None
        _index_dirty = False
        _last_index_error = None
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


def _run_semantic_warmup(
    generation: int,
    vault_root: str,
    semantic_enablement_generation: int,
) -> None:
    """Warm semantic embeddings state in parallel with the main warmup track."""
    if not _semantic_warmup_enablement_matches(generation, semantic_enablement_generation):
        return

    if not _embeddings_enabled():
        _finish_semantic_warmup(
            generation,
            state="disabled",
            semantic_enablement_generation=semantic_enablement_generation,
        )
        return

    with _warmup_lock:
        router_ready_event = _router_ready_event
    if not router_ready_event.wait(timeout=_STARTUP_OP_TIMEOUT):
        if _logger:
            _logger.error(
                "semantic warmup timed out waiting for router readiness after %.1fs",
                _STARTUP_OP_TIMEOUT,
            )
        if _semantic_warmup_enablement_matches(generation, semantic_enablement_generation):
            _mark_embeddings_dirty()
        _finish_semantic_warmup(
            generation,
            state="deferred",
            error=_semantic_warmup_restart_message(
                "semantic warmup timed out waiting for router readiness"
            ),
            semantic_enablement_generation=semantic_enablement_generation,
        )
        return
    if not _semantic_warmup_enablement_matches(generation, semantic_enablement_generation):
        return

    with _warmup_lock:
        main_warmup_state = _warmup_state
        router = _router
        last_warmup_error = _last_warmup_error
    if main_warmup_state == "failed":
        detail = "semantic warmup could not start because startup warmup failed"
        if last_warmup_error:
            detail = f"{detail}: {last_warmup_error}"
        _finish_semantic_warmup(
            generation,
            state="deferred",
            error=_semantic_warmup_restart_message(detail),
            semantic_enablement_generation=semantic_enablement_generation,
        )
        return
    if router is None:
        _finish_semantic_warmup(
            generation,
            state="deferred",
            error=_semantic_warmup_restart_message(
                "semantic warmup could not start because the router never became available"
            ),
            semantic_enablement_generation=semantic_enablement_generation,
        )
        return

    expected_router_source_hash = _retrieval_embeddings.router_source_hash(router)

    try:
        loaded = _load_current_embeddings_from_disk(vault_root, router)
    except _retrieval_embeddings.SemanticEmbeddingsLoadError as exc:
        if _logger:
            _logger.warning(
                "semantic embeddings sidecars are unreadable during warmup; scheduling rebuild: %s",
                exc,
            )
        if _semantic_warmup_enablement_matches(generation, semantic_enablement_generation):
            _mark_embeddings_dirty()
    except _INDEX_STATE_ERROR_TYPES as exc:
        if _logger:
            _logger.error("semantic warmup failed: %s", exc, exc_info=True)
        if _semantic_warmup_enablement_matches(generation, semantic_enablement_generation):
            _mark_embeddings_dirty()
        _finish_semantic_warmup(
            generation,
            state="deferred",
            error=_semantic_warmup_restart_message(str(exc)),
            semantic_enablement_generation=semantic_enablement_generation,
        )
        return
    except Exception as exc:
        if _logger:
            _logger.error("semantic warmup failed: %s", exc, exc_info=True)
        if _semantic_warmup_enablement_matches(generation, semantic_enablement_generation):
            _mark_embeddings_dirty()
        _finish_semantic_warmup(
            generation,
            state="deferred",
            error=_semantic_warmup_restart_message(
                f"semantic warmup failed unexpectedly: {exc}"
            ),
            semantic_enablement_generation=semantic_enablement_generation,
        )
        return
    else:
        if _semantic_warmup_enablement_matches(generation, semantic_enablement_generation):
            if loaded is None:
                _mark_embeddings_dirty()
            else:
                applied = _apply_loaded_embeddings_snapshot(
                    loaded,
                    generation=generation,
                    semantic_enablement_generation=semantic_enablement_generation,
                    expected_router_source_hash=expected_router_source_hash,
                )
                if not applied:
                    _mark_embeddings_dirty()

    _finish_semantic_warmup(
        generation,
        state="ready",
        semantic_enablement_generation=semantic_enablement_generation,
    )


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
            "config": _derived_work_config(),
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

    try:
        source_hash = _retrieval_embeddings.router_source_hash(data)
    except _retrieval_embeddings.RouterMetadataError:
        return True, None
    meta = data.get("meta", {})
    compiled_at = meta.get("compiled_at")
    sources = meta.get("sources", {})
    if not compiled_at or source_hash is None or not sources:
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

    if data.get("meta", {}).get("index_version") != search_paths.INDEX_VERSION:
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
    current_index_source_count = compile_router.count_living_artefact_index_entries(
        vault_root, router.get("artefacts", [])
    )
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
            _compile_and_save(_vault_root)
        except Exception as e:
            if _logger:
                _logger.error("router recompile failed: %s", e, exc_info=True)
            if isinstance(e, _INDEX_STATE_ERROR_TYPES):
                _record_index_state_error(e)
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
        _compile_and_save(_vault_root)
    except Exception as e:
        if _logger:
            _logger.error("router recompile failed: %s", e, exc_info=True)
        if isinstance(e, _INDEX_STATE_ERROR_TYPES):
            _record_index_state_error(e)
        return
    _refresh_session_mirror_best_effort()


def _refresh_session_mirror_best_effort() -> None:
    """Enqueue a background mirror refresh; never blocks or raises.

    Used on mid-session recompile paths where a failed or slow refresh
    must not impact the triggering tool call. The actual build + persist
    runs in the session-mirror worker; failures are logged there.
    """
    _enqueue_mirror_refresh()


def _embeddings_enabled() -> bool:
    """Return True when any enabled feature needs embedding sidecars."""
    return _config_embeddings_enabled(_derived_work_config())


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
    _retrieval_embeddings.clear_embeddings_outputs(_vault_root)


def _mark_index_dirty() -> None:
    """Flag the index for a full rebuild (e.g. version drift, unknown scope of change).

    Index rebuild may add/remove docs, so doc embeddings need full re-encode.
    Type embeddings are sourced from the router and are not affected here.
    """
    global _index_dirty, _doc_embeddings_dirty
    _index_dirty = True
    _doc_embeddings_dirty = True
    _clear_loaded_embeddings()


def _mark_router_dirty() -> None:
    """Flag the router for recompile on the next _ensure_router_fresh call.

    Router holds taxonomy → type embeddings dirty. Each doc's embedding text
    includes its type description, so doc embeddings are also stale.
    """
    global _router_dirty, _doc_embeddings_dirty, _type_embeddings_dirty
    _router_dirty = True
    _doc_embeddings_dirty = True
    _type_embeddings_dirty = True
    _clear_loaded_embeddings()


def _mark_embeddings_dirty() -> None:
    """Mark all embeddings dirty (doc + type). Use sparingly — prefer per-path."""
    global _doc_embeddings_dirty, _type_embeddings_dirty
    _doc_embeddings_dirty = True
    _type_embeddings_dirty = True
    _clear_loaded_embeddings()


def _apply_loaded_embeddings_snapshot(
    loaded,
    *,
    generation: int | None = None,
    semantic_enablement_generation: int | None = None,
    expected_router_source_hash: str | None = None,
) -> bool:
    """Apply a fully-loaded embeddings snapshot when it still matches runtime state.

    Returns ``True`` when the snapshot was published to in-memory state.
    Returns ``False`` when the active warmup generation, current router, or
    pending per-path mutations no longer match the snapshot's assumptions.
    """
    global _type_embeddings, _doc_embeddings, _embeddings_meta
    global _doc_embeddings_dirty, _type_embeddings_dirty

    with _warmup_lock:
        if generation is not None and generation != _warmup_generation:
            return False
        if (
            semantic_enablement_generation is not None
            and semantic_enablement_generation != _semantic_enablement_generation
        ):
            return False
        if _router is None:
            return False
        if expected_router_source_hash is not None:
            current_router_source_hash = _retrieval_embeddings.router_source_hash(_router)
            if current_router_source_hash != expected_router_source_hash:
                return False
        with _doc_embeddings_pending_lock:
            if _doc_embeddings_pending:
                return False
            _type_embeddings, _doc_embeddings, _embeddings_meta = loaded
            _doc_embeddings_dirty = False
            _type_embeddings_dirty = False
            _doc_embeddings_pending.clear()
    return True


def _load_current_embeddings_from_disk(vault_root: str, router: dict):
    """Return a complete current embeddings snapshot from disk when available.

    Returns `(type_embeddings, doc_embeddings, meta)` only when all three are
    present and the persisted metadata matches the current router fingerprint.
    Returns `None` when sidecars are missing, partial, or stale. Propagates
    `SemanticEmbeddingsLoadError` for present-but-unreadable sidecars so the
    caller can choose between rebuild or deferred recovery.
    """
    type_embeddings, doc_embeddings, meta = _retrieval_embeddings.load_embeddings_state(
        vault_root
    )
    if (
        meta is None
        or type_embeddings is None
        or doc_embeddings is None
        or not _retrieval_embeddings.embeddings_meta_matches_router(meta, router)
    ):
        return None
    return (type_embeddings, doc_embeddings, meta)


def _ensure_embeddings_fresh() -> None:
    """Rebuild doc embeddings if they're out of sync with the index.

    Called lazily before search/process operations that need embeddings. Only
    rebuilds if deps are available and the router is loaded.
    """
    global _doc_embeddings, _embeddings_meta, _type_embeddings
    global _doc_embeddings_dirty, _type_embeddings_dirty
    if _vault_root is None:
        return
    if not _embeddings_enabled():
        _clear_loaded_embeddings()
        _retrieval_embeddings.clear_query_encoder()
        _invalidate_embeddings_disk_state()
        _doc_embeddings_dirty = False
        _type_embeddings_dirty = False
        with _doc_embeddings_pending_lock:
            _doc_embeddings_pending.clear()
        return
    if _index is None or _router is None:
        return
    with _doc_embeddings_pending_lock:
        has_pending = bool(_doc_embeddings_pending)
    has_missing_loaded_embeddings = any(
        value is None for value in (_type_embeddings, _embeddings_meta, _doc_embeddings)
    )
    needs_rebuild = _doc_embeddings_dirty or _type_embeddings_dirty or has_pending
    if not needs_rebuild and not has_missing_loaded_embeddings:
        return
    if not needs_rebuild:
        try:
            loaded = _load_current_embeddings_from_disk(_vault_root, _router)
        except _retrieval_embeddings.SemanticEmbeddingsLoadError as exc:
            if _logger:
                _logger.warning(
                    "semantic embeddings sidecars are unreadable; rebuilding lazily: %s",
                    exc,
                )
        else:
            if loaded is not None:
                if _apply_loaded_embeddings_snapshot(loaded):
                    return
    result = retrieval_assets.refresh_embeddings_for_loaded_state(
        _vault_root,
        _router,
        _index["documents"],
        embedding_parts_by_path=_embedding_parts_by_path,
        config=_derived_work_config(),
    )
    if result is not None:
        _apply_loaded_embeddings_snapshot(result)
    else:
        _clear_loaded_embeddings()
        _doc_embeddings_dirty = False
        _type_embeddings_dirty = False
        with _doc_embeddings_pending_lock:
            _doc_embeddings_pending.clear()


def _mark_index_pending(rel_path: str, type_hint: str | None = None) -> None:
    """Queue a single file for incremental index update on the next search.

    Also queues the same path for doc-embedding refresh. Sidecars are NOT
    eager-deleted — the next refresh overwrites them. Stale-but-loadable sidecars
    are preferred over missing-and-failing-fallback.
    """
    with _index_pending_lock:
        _index_pending.append((rel_path, type_hint))
    with _doc_embeddings_pending_lock:
        _doc_embeddings_pending.add(rel_path)
    _clear_loaded_embeddings()


def _apply_pending_index_updates() -> None:
    """Drain queued path updates into the in-memory index.

    ``index_update`` maintains corpus stats incrementally and leaves
    ``meta['built_at']`` untouched, so the external-change sweep still measures
    freshness against the last full build.
    """
    global _index, _index_checked_at, _embedding_parts_by_path, _last_index_error
    if _index is None or not _index_pending:
        return

    with _index_pending_lock:
        pending = _index_pending[:]
        _index_pending.clear()

    try:
        for rel_path, type_hint in pending:
            updated = search_index.index_update(
                _index,
                _vault_root,
                rel_path,
                type_hint=type_hint,
            )
            if updated is None and _embedding_parts_by_path is not None:
                _embedding_parts_by_path.pop(rel_path, None)
            elif updated is not None and _embedding_parts_by_path is not None:
                _embedding_parts_by_path[rel_path] = updated.embedding_parts
        _save_json(_index, _vault_root, _index_rel())
        _index_checked_at = time.monotonic()
        _last_index_error = None
    except Exception as e:
        if _logger:
            _logger.error("index incremental update failed: %s", e)
        # Typed retrieval-state failures block reads with an explicit index error;
        # untyped failures still mark the index dirty so the next refresh can retry.
        _record_index_state_error(e)
        _mark_index_dirty()


def _try_rebuild_index(
    error_message: str,
    *,
    clear_dirty_on_failure: bool,
) -> bool:
    """Run a full index rebuild and apply shared failure semantics."""
    global _index, _index_checked_at, _index_dirty, _last_index_error
    try:
        _index = _build_index_and_save(_vault_root)
        _last_index_error = None
        return True
    except Exception as e:
        if _logger:
            _logger.error("%s: %s", error_message, e)
        # Typed retrieval-state failures surface immediately; everything else
        # stays on the generic dirty/retry path instead of becoming user-facing.
        _record_index_state_error(e)
        if clear_dirty_on_failure:
            _index_dirty = False  # prevent tight retry loop; staleness TTL will re-detect
            _index_checked_at = time.monotonic()
        return False


def _ensure_index_fresh() -> None:
    """Update the index if needed: incremental for queued paths, full rebuild
    if dirty flag is set, filesystem staleness check on TTL for external changes.
    """
    global _index, _index_checked_at, _index_dirty
    if _vault_root is None:
        return

    # Full rebuild takes priority over incremental
    if _index_dirty:
        _try_rebuild_index(
            "index full rebuild failed",
            clear_dirty_on_failure=True,
        )
        return

    # Incremental updates for paths queued by brain_create/brain_edit
    if _index_pending and _index is not None:
        _apply_pending_index_updates()
        # Fall through to TTL-gated staleness check (detects external files)

    # Filesystem staleness check for external changes (throttled)
    now = time.monotonic()
    if now - _index_checked_at < _INDEX_CHECK_TTL:
        return
    _index_checked_at = now
    stale, data = _check_index(_vault_root)
    if not stale:
        return
    _try_rebuild_index(
        "index staleness rebuild failed",
        clear_dirty_on_failure=False,
    )


def _ensure_mutation_index_ready() -> None:
    """Prepare the index for mutation-time link checks without a TTL sweep.

    ``brain_create`` / ``brain_edit`` need the in-memory index to reflect MCP
    writes queued in ``_index_pending``, but they should not pay the periodic
    vault-wide external staleness scan that read/search paths use.
    """
    global _index, _index_checked_at, _index_dirty
    if _vault_root is None or _index is None:
        return

    if _index_dirty:
        _try_rebuild_index(
            "index full rebuild failed",
            clear_dirty_on_failure=True,
        )
        return

    if _index_pending:
        _apply_pending_index_updates()
        if _index_dirty:
            _try_rebuild_index(
                "index full rebuild failed",
                clear_dirty_on_failure=True,
            )


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
    global _doc_embeddings_dirty, _type_embeddings_dirty
    compiled = compile_router.compile(vault_root)
    compile_router.persist_compiled_router(vault_root, compiled)
    _set_router(compiled)
    _router_checked_at = time.monotonic()
    _router_dirty = False
    if _embeddings_enabled():
        _doc_embeddings_dirty = True
        _type_embeddings_dirty = True
    return compiled


def _build_index_and_save(vault_root: str) -> dict:
    """Build retrieval index, write to disk, return index data.

    Always clears _index_dirty, _index_pending, and resets the staleness-check
    TTL so that callers don't need to remember to do it themselves.
    """
    global _index_dirty, _index_checked_at, _embeddings_meta, _type_embeddings, _doc_embeddings
    global _embedding_parts_by_path
    global _doc_embeddings_dirty, _type_embeddings_dirty, _last_index_error
    build_result = search_index.build_index(vault_root)
    index = build_result.index
    result = retrieval_assets.persist_retrieval_outputs(
        vault_root,
        index,
        router=_router,
        embedding_parts_by_path=build_result.embedding_parts_by_path,
        config=_derived_work_config(),
    )
    _index_dirty = False
    _doc_embeddings_dirty = False
    _type_embeddings_dirty = False
    with _index_pending_lock:
        _index_pending.clear()
    with _doc_embeddings_pending_lock:
        _doc_embeddings_pending.clear()
    _index_checked_at = time.monotonic()
    _embedding_parts_by_path = build_result.embedding_parts_by_path
    _last_index_error = None
    if result is not None:
        _type_embeddings, _doc_embeddings, _embeddings_meta = result
    else:
        _clear_loaded_embeddings()
    return index


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

def startup(vault_root: str | None = None) -> None:
    """Initialize the minimal server skeleton and start background warmup."""
    global _vault_root, _vault_name, _loaded_version, _logger

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

    # Load vault config via the same probe→load primitive as live reload. An
    # expected config boundary failure (missing/malformed config) becomes a known
    # config error visible in brain_init(debug=true) and the server proceeds
    # degraded (_config stays None). A programmer bug propagates and is fatal.
    started_at = time.monotonic()
    _log_phase_begin("startup", "config_load")
    startup_config_error = _refresh_config(
        "startup",
        error_prefix="config reload failed during startup",
        enqueue_mirror=False,
    )
    if startup_config_error is not None:
        _set_vault_name_from_config(None)
        _log_phase_failure("startup", "config_load", started_at, startup_config_error)
    else:
        _log_phase_success("startup", "config_load", started_at)
    # CLI availability is probed lazily on first tool call via _refresh_cli_available()
    # to avoid blocking startup (the Obsidian IPC socket check is fast but we defer entirely).

    _ensure_warmup_started("startup")
    _ensure_semantic_warmup_started("startup")
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
        config=_current_config(),
        config_error=_config_error_message(),
        session_profile=_session_profile,
        router=_router,
        index=_index,
        index_error=_last_index_error,
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
    with _warmup_lock:
        _clear_loaded_embeddings()
        _router = router
        if router is None:
            _router_ready_event.clear()
        else:
            _router_ready_event.set()


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
        get_readiness_info=_get_readiness_info,
        set_router=_set_router,
        set_index=_set_index,
        set_workspace_registry=_set_workspace_registry,
        set_session_profile=_set_session_profile,
        fmt_error=_fmt_error,
        fmt_progress=_fmt_progress,
        enforce_profile=_enforce_profile,
        ensure_config_fresh=_ensure_config_fresh,
        refresh_cli_available=_refresh_cli_available,
        ensure_warmup_started=_ensure_warmup_started,
        ensure_router_fresh=_ensure_router_fresh,
        ensure_index_fresh=_ensure_index_fresh,
        ensure_mutation_index_ready=_ensure_mutation_index_ready,
        get_readiness_snapshot=_readiness_snapshot,
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
    Config freshness is checked before enforcement; config errors fail closed.
    No enforcement if there is fresh config but no active session profile.
    """
    config_error = _ensure_config_fresh(tool_name)
    if config_error is not None:
        return _fmt_error(config_error)
    config = _current_config()
    if config is None or _session_profile is None:
        return None
    profiles = config.get("vault", {}).get("profiles", {})
    profile = profiles.get(_session_profile)
    if profile is None:
        return _fmt_error(
            f"active operator profile '{_session_profile}' is no longer defined; "
            "run brain_session again or fix config"
        )
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


# ---------------------------------------------------------------------------
# Shared parameter descriptions — used by multiple @mcp.tool() registrations.
# Kept here (not duplicated at each call site) so the prose doesn't drift.
# ---------------------------------------------------------------------------

_BODY_FILE_DESCRIPTION = (
    "Legacy caller-owned body file inside the vault or system temp directory. "
    "Mutually exclusive with body/body_handle and never deleted by Brain. Prefer "
    "a retry-safe handle from brain_stage."
)

_BODY_HANDLE_DESCRIPTION = (
    "Opaque retry-safe handle returned by brain_stage. Mutually exclusive with "
    "body and body_file. Brain consumes it only after a successful mutation; "
    "failed calls may be retried with the same handle."
)

_NAME_DESCRIPTION = (
    "Resource name for skill, memory, style, or template. Required when "
    "resource is one of those kinds. For templates, use the artefact type key, "
    "e.g. 'wiki'."
)

_FIX_LINKS_DESCRIPTION = "Repair resolvable broken wikilinks in this file."

_MUTATION_CONTENT_DESCRIPTION = (
    "Body content from one inline, staged, or caller-file source."
)

_STRUCTURAL_SELECTOR_DESCRIPTION = (
    "Optional occurrence and ancestor-chain selector for duplicate structural targets."
)

_ARTEFACT_TYPE_FILTER_DESCRIPTION = (
    "Artefact type filter, e.g. 'living/wiki' or 'temporal/research'."
)

_ARTEFACT_TAG_FILTER_DESCRIPTION = (
    "Exact artefact tag filter."
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


class _InlineMutationContent(BaseModel):
    """Inline markdown supplied directly in a mutation request."""

    model_config = ConfigDict(extra="forbid")
    source: Annotated[
        Literal["inline"],
        Field(description="Select inline markdown supplied in this request."),
    ]
    content: Annotated[str, Field(description="Markdown content supplied inline.")]


class _StagedMutationContent(BaseModel):
    """Retry-safe content previously stored by brain_stage."""

    model_config = ConfigDict(extra="forbid")
    source: Annotated[
        Literal["stage"],
        Field(description="Select a retry-safe body staged by brain_stage."),
    ]
    handle: Annotated[str, Field(description=_BODY_HANDLE_DESCRIPTION)]


class _FileMutationContent(BaseModel):
    """Legacy caller-owned content file."""

    model_config = ConfigDict(extra="forbid")
    source: Annotated[
        Literal["file"],
        Field(description="Select a legacy caller-owned body file."),
    ]
    path: Annotated[str, Field(description=_BODY_FILE_DESCRIPTION)]


_MutationContent = Annotated[
    _InlineMutationContent | _StagedMutationContent | _FileMutationContent,
    Field(discriminator="source"),
]


class _BrainCreateRequestBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _explain_legacy_looking_content_shape(cls, value):
        if not isinstance(value, dict):
            return value

        if "body" in value:
            body = value["body"]
            if isinstance(body, dict) and "kind" in body:
                raise ValueError(
                    "brain_create: use 'content', not 'body', and use its "
                    "'source' discriminator, not 'kind'; for example, "
                    "'content': {'source': 'inline', 'content': '...'}"
                )
            raise ValueError(
                "brain_create: use 'content', not 'body'; for example, "
                "'content': {'source': 'inline', 'content': '...'}"
            )

        content = value.get("content")
        if isinstance(content, dict) and "kind" in content:
            raise ValueError(
                "brain_create content uses the 'source' discriminator, not "
                "'kind'; for example, "
                "'content': {'source': 'inline', 'content': '...'}"
            )
        return value


class _BrainCreateArtefactRequest(_BrainCreateRequestBase):
    resource: Annotated[
        Literal["artefact"],
        Field(description="Select creation of a typed vault artefact."),
    ]
    type: Annotated[str, Field(description="Artefact type key, such as living/idea.")]
    title: Annotated[str, Field(description="Artefact title used by the type naming contract.")]
    content: Annotated[
        _MutationContent | None,
        Field(description=_MUTATION_CONTENT_DESCRIPTION),
    ] = None
    frontmatter: Annotated[dict | None, Field(description="Non-lifecycle frontmatter overrides.")] = None
    parent: Annotated[str | None, Field(description="Optional parent artefact reference.")] = None
    key: Annotated[str | None, Field(description="Optional living-artefact key override.")] = None
    fix_links: Annotated[bool, Field(description=_FIX_LINKS_DESCRIPTION)] = False


class _BrainCreateNamedRequestBase(_BrainCreateRequestBase):
    name: Annotated[str, Field(description=_NAME_DESCRIPTION)]
    content: Annotated[
        _MutationContent,
        Field(description=_MUTATION_CONTENT_DESCRIPTION),
    ]


class _BrainCreateNamedWithFrontmatterRequestBase(_BrainCreateNamedRequestBase):
    frontmatter: Annotated[dict | None, Field(description="Optional resource frontmatter.")] = None


class _BrainCreateSkillRequest(_BrainCreateNamedWithFrontmatterRequestBase):
    resource: Annotated[
        Literal["skill"],
        Field(description="Select creation of a named skill resource."),
    ]


class _BrainCreateMemoryRequest(_BrainCreateNamedWithFrontmatterRequestBase):
    resource: Annotated[
        Literal["memory"],
        Field(description="Select creation of a named memory resource."),
    ]


class _BrainCreateStyleRequest(_BrainCreateNamedWithFrontmatterRequestBase):
    resource: Annotated[
        Literal["style"],
        Field(description="Select creation of a named style resource."),
    ]


class _BrainCreateTemplateRequest(_BrainCreateNamedRequestBase):
    resource: Annotated[
        Literal["template"],
        Field(description="Select creation of a named template resource."),
    ]


_BrainCreateRequest = Annotated[
    _BrainCreateArtefactRequest
    | _BrainCreateSkillRequest
    | _BrainCreateMemoryRequest
    | _BrainCreateStyleRequest
    | _BrainCreateTemplateRequest,
    Field(discriminator="resource"),
]


class _BrainEditArtefactSubject(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource: Annotated[
        Literal["artefact"],
        Field(description="Select an artefact subject identified by path or key."),
    ]
    path: Annotated[str, Field(description="Artefact key, relative path, or resolvable name.")]
    fix_links: Annotated[bool, Field(description=_FIX_LINKS_DESCRIPTION)] = False


class _BrainEditNamedSubjectBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(description=_NAME_DESCRIPTION)]


class _BrainEditSkillSubject(_BrainEditNamedSubjectBase):
    resource: Annotated[
        Literal["skill"],
        Field(description="Select a named skill subject."),
    ]


class _BrainEditMemorySubject(_BrainEditNamedSubjectBase):
    resource: Annotated[
        Literal["memory"],
        Field(description="Select a named memory subject."),
    ]


class _BrainEditStyleSubject(_BrainEditNamedSubjectBase):
    resource: Annotated[
        Literal["style"],
        Field(description="Select a named style subject."),
    ]


class _BrainEditTemplateSubject(_BrainEditNamedSubjectBase):
    resource: Annotated[
        Literal["template"],
        Field(description="Select a named template subject."),
    ]


_BrainEditSubject = Annotated[
    _BrainEditArtefactSubject
    | _BrainEditSkillSubject
    | _BrainEditMemorySubject
    | _BrainEditStyleSubject
    | _BrainEditTemplateSubject,
    Field(discriminator="resource"),
]


class _BrainStructuralMutationBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: Annotated[
        _MutationContent | None,
        Field(description=_MUTATION_CONTENT_DESCRIPTION),
    ] = None
    frontmatter: Annotated[dict | None, Field(description="Non-lifecycle frontmatter changes.")] = None
    target: Annotated[str | None, Field(description="Optional heading, callout, or :body target.")] = None
    selector: Annotated[
        _StructuralSelector | None,
        Field(description=_STRUCTURAL_SELECTOR_DESCRIPTION),
    ] = None
    scope: Annotated[
        Literal["section", "intro", "body", "heading", "header"] | None,
        Field(description=edit.brain_edit_scope_description()),
    ] = None


class _BrainEditMutation(_BrainStructuralMutationBase):
    operation: Annotated[
        Literal["edit"],
        Field(description="Replace the selected body range and merge frontmatter."),
    ]


class _BrainAppendMutation(_BrainStructuralMutationBase):
    operation: Annotated[
        Literal["append"],
        Field(description="Append content to the selected body range."),
    ]


class _BrainPrependMutation(_BrainStructuralMutationBase):
    operation: Annotated[
        Literal["prepend"],
        Field(description="Prepend content to the selected body range."),
    ]


class _BrainDeleteSectionMutation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Annotated[
        Literal["delete_section"],
        Field(description="Delete one selected heading section or callout block."),
    ]
    target: Annotated[str, Field(description="Heading or callout section to remove.")]
    selector: Annotated[
        _StructuralSelector | None,
        Field(description=_STRUCTURAL_SELECTOR_DESCRIPTION),
    ] = None
    frontmatter: Annotated[dict | None, Field(description="Non-lifecycle frontmatter changes.")] = None


class _BrainReplaceTextMutation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Annotated[
        Literal["replace_text"],
        Field(description="Replace one or more exact text matches."),
    ]
    old_text: Annotated[str, Field(description="Exact text to find.")]
    new_text: Annotated[str, Field(description="Replacement text; empty deletes the match.")]
    target: Annotated[str | None, Field(description="Optional structural narrowing target.")] = None
    selector: Annotated[
        _StructuralSelector | None,
        Field(description=_STRUCTURAL_SELECTOR_DESCRIPTION),
    ] = None
    scope: Annotated[
        Literal["section", "intro", "body", "heading", "header"] | None,
        Field(description=edit.brain_edit_scope_description()),
    ] = None
    match_occurrence: Annotated[int | None, Field(description="One-based match occurrence.", ge=1)] = None
    replace_all: Annotated[bool, Field(description="Replace every exact match.")] = False


_BrainEditMutationRequest = Annotated[
    _BrainEditMutation
    | _BrainAppendMutation
    | _BrainPrependMutation
    | _BrainDeleteSectionMutation
    | _BrainReplaceTextMutation,
    Field(discriminator="operation"),
]


class _BrainEditRequest(BaseModel):
    """Schema-valid edit composed from an exact subject and mutation variant."""

    model_config = ConfigDict(extra="forbid")
    subject: Annotated[
        _BrainEditSubject,
        Field(description="Resource-specific subject to edit."),
    ]
    mutation: Annotated[
        _BrainEditMutationRequest,
        Field(description="Operation-specific mutation to apply."),
    ]


class _CreateDefinitionMutation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Annotated[
        Literal["create"],
        Field(description="Create a definition that does not already exist."),
    ]
    definition: Annotated[str, Field(description="Complete markdown definition document.")]


class _ReplaceDefinitionMutation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Annotated[
        Literal["replace"],
        Field(description="Replace a definition after verifying its reviewed hash."),
    ]
    definition: Annotated[str, Field(description="Complete replacement markdown document.")]
    expected_sha256: Annotated[
        str,
        Field(description="SHA-256 of the reviewed current definition; stale replacements fail."),
    ]


_DefinitionDocumentMutation = Annotated[
    _CreateDefinitionMutation | _ReplaceDefinitionMutation,
    Field(discriminator="operation"),
]


class _CreateTypeDefinitionMutation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Annotated[
        Literal["create"],
        Field(description="Create a taxonomy definition and matching template."),
    ]
    definition: Annotated[str, Field(description="Complete taxonomy markdown document.")]
    template: Annotated[str, Field(description="Complete markdown template linked by the taxonomy.")]


class _ReplaceTypeDefinitionMutation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Annotated[
        Literal["replace"],
        Field(description="Replace a taxonomy and template after verifying both hashes."),
    ]
    definition: Annotated[str, Field(description="Complete replacement taxonomy document.")]
    template: Annotated[str, Field(description="Complete replacement type template.")]
    expected_sha256: Annotated[str, Field(description="Reviewed current taxonomy SHA-256.")]
    expected_template_sha256: Annotated[str, Field(description="Reviewed current template SHA-256.")]


_TypeDefinitionMutation = Annotated[
    _CreateTypeDefinitionMutation | _ReplaceTypeDefinitionMutation,
    Field(discriminator="operation"),
]


class _BrainDefineTypeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Annotated[
        Literal["type"],
        Field(description="Select a type-definition mutation."),
    ]
    name: Annotated[str, Field(description="Lowercase hyphenated taxonomy filename stem.")]
    classification: Annotated[
        Literal["living", "temporal"],
        Field(description="Taxonomy classification and destination folder."),
    ]
    mutation: Annotated[
        _TypeDefinitionMutation,
        Field(description="Create or guarded-replace mutation for the type bundle."),
    ]


class _BrainDefinePluginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Annotated[
        Literal["plugin"],
        Field(description="Select a plugin-definition mutation."),
    ]
    name: Annotated[str, Field(description="Safe plugin data-directory name, preserving display case.")]
    mutation: Annotated[
        _DefinitionDocumentMutation,
        Field(description="Create or guarded-replace mutation for the plugin definition."),
    ]


class _CreateTriggerMutation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Annotated[
        Literal["create"],
        Field(description="Create a trigger with a unique condition."),
    ]
    condition: Annotated[str, Field(description="Unique one-line trigger condition.")]
    target: Annotated[str, Field(description="Existing vault-relative wikilink target.")]


class _ReplaceTriggerMutation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Annotated[
        Literal["replace"],
        Field(description="Replace a trigger using its exact current values."),
    ]
    condition: Annotated[str, Field(description="Exact current trigger condition.")]
    target: Annotated[str, Field(description="Exact current target used as an optimistic precondition.")]
    new_condition: Annotated[str | None, Field(description="Optional replacement condition.")] = None
    new_target: Annotated[str | None, Field(description="Optional replacement target.")] = None


class _DeleteTriggerMutation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Annotated[
        Literal["delete"],
        Field(description="Delete a trigger using its exact current condition."),
    ]
    condition: Annotated[str, Field(description="Exact current trigger condition.")]
    target: Annotated[
        str | None,
        Field(description="Optional exact-current-target precondition."),
    ] = None


_TriggerDefinitionMutation = Annotated[
    _CreateTriggerMutation | _ReplaceTriggerMutation | _DeleteTriggerMutation,
    Field(discriminator="operation"),
]


class _BrainDefineTriggerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Annotated[
        Literal["trigger"],
        Field(description="Select a trigger mutation."),
    ]
    mutation: Annotated[
        _TriggerDefinitionMutation,
        Field(description="Create, replace, or delete mutation for one trigger."),
    ]


_BrainDefineRequest = Annotated[
    _BrainDefineTypeRequest | _BrainDefineTriggerRequest | _BrainDefinePluginRequest,
    Field(discriminator="kind"),
]


def _flatten_mutation_content(content) -> dict:
    """Translate a public content variant to the script-layer flat contract."""
    if content is None:
        return {}
    payload = content.model_dump()
    if payload["source"] == "inline":
        return {"body": payload["content"]}
    if payload["source"] == "stage":
        return {"body_handle": payload["handle"]}
    return {"body_file": payload["path"]}

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
    recursive: bool | None,
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
        "recursive": recursive,
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
    body_handle: str | None,
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
        "body_handle": body_handle,
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
    body_handle: str | None,
    frontmatter: dict | None,
    target: str | None,
    selector: dict | None,
    scope: str | None,
    name: str | None,
    fix_links: bool | None,
    old_text: str | None,
    new_text: str | None,
    match_occurrence: int | None,
    replace_all: bool | None,
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
        "body_handle": body_handle,
        "frontmatter": frontmatter,
        "target": target,
        "selector": selector,
        "scope": scope,
        "name": name,
        "fix_links": fix_links,
        "old_text": old_text,
        "new_text": new_text,
        "match_occurrence": match_occurrence,
        "replace_all": replace_all,
    }
    params = validate_spec(
        spec,
        payload,
        label=f"Resource '{resource}' op '{operation}'",
        hint=edit_contract_hint(resource, operation),
        field_term="top-level field",
    )
    if operation == "replace_text":
        if new_text is None:
            raise ValueError(
                f"Resource '{resource}' op 'replace_text' requires top-level field "
                "'new_text'. Pass an empty string to delete the matched text."
            )
        params["new_text"] = new_text
    return params


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
    modified_since: str | None,
    modified_until: str | None,
    tag: str | None,
    top_k: int | None,
    sort: str | None,
    cursor: str | None,
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
        "modified_since": modified_since,
        "modified_until": modified_until,
        "tag": tag,
        "top_k": top_k,
        "sort": sort,
        "cursor": cursor,
    }
    return validate_spec(
        spec,
        payload,
        label=f"Resource '{resource}'",
        hint=list_contract_hint(resource),
        field_term="top-level field",
    )


def _build_process_params(
    operation: str,
    *,
    content: str | None,
    type: str | None,
    title: str | None,
    mode: str | None,
):
    """Validate one split content-processing tool request."""
    spec = _server_content.PROCESS_SPECS.get(operation)
    if spec is None:
        raise ValueError(
            f"Unknown process operation '{operation}'. "
            f"Valid: {', '.join(_server_content.PROCESS_SPECS)}"
        )
    payload = {
        "content": content,
        "type": type,
        "title": title,
        "mode": mode,
    }
    params = validate_spec(
        spec,
        payload,
        label=f"Process op '{operation}'",
        hint=_server_content.process_contract_hint(operation),
        field_term="top-level field",
    )
    if operation in ("classify", "ingest") and "mode" not in params:
        params["mode"] = "auto"
    return params


class _BrainActionDeleteParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: Annotated[
        str,
        Field(description="Vault-relative path to the artefact file to delete."),
    ]
    recursive: Annotated[
        bool | None,
        Field(description="When true, delete the living descendant subtree too."),
    ] = None


class _BrainActionReparentParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Annotated[
        str,
        Field(description="Artefact whose direct living children should be reparented."),
    ]
    to: Annotated[
        str | None,
        Field(
            description=(
                "New parent reference. Omit to lift children to source's parent; "
                "pass null or an empty string to clear children to top-level."
            )
        ),
    ] = None


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


class _BrainActionShapeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: Annotated[
        str,
        Field(description="Existing artefact path or resolvable name to shape."),
    ]
    mode: Annotated[
        Literal[*SHAPING_MODES],
        Field(description="Shaping mode selected by the shaping skill."),
    ]


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


class _BrainActionDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Annotated[
        Literal["delete"],
        Field(description="Delete one artefact or an explicitly recursive subtree."),
    ]
    params: Annotated[
        _BrainActionDeleteParams,
        Field(description="Artefact deletion parameters."),
    ]


class _BrainActionReparentChildrenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Annotated[
        Literal["reparent-children"],
        Field(description="Move the direct children of one living artefact."),
    ]
    params: Annotated[
        _BrainActionReparentParams,
        Field(description="Source and optional destination parent for the child move."),
    ]


class _BrainActionShapePrintableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Annotated[
        Literal["shape-printable"],
        Field(description="Create and optionally render a printable from an artefact."),
    ]
    params: Annotated[
        _BrainActionShapePrintableParams,
        Field(description="Printable shaping and rendering parameters."),
    ]


class _BrainActionShapePresentationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Annotated[
        Literal["shape-presentation"],
        Field(description="Create and optionally render a presentation from an artefact."),
    ]
    params: Annotated[
        _BrainActionShapePresentationParams,
        Field(description="Presentation shaping, rendering, and preview parameters."),
    ]


class _BrainActionShapeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Annotated[
        Literal["shape"],
        Field(description="Start or continue a schema-valid shaping session."),
    ]
    params: Annotated[
        _BrainActionShapeParams,
        Field(description="Target artefact and shaping mode."),
    ]


class _BrainActionFixLinksRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Annotated[
        Literal["fix-links"],
        Field(description="Scan for broken wikilinks and optionally repair them."),
    ]
    params: Annotated[
        _BrainActionFixLinksParams,
        Field(description="Optional scope and apply controls for link repair."),
    ] = _BrainActionFixLinksParams()


_BrainActionRequest = Annotated[
    _BrainActionDeleteRequest
    | _BrainActionReparentChildrenRequest
    | _BrainActionShapePrintableRequest
    | _BrainActionShapePresentationRequest
    | _BrainActionShapeRequest
    | _BrainActionFixLinksRequest,
    Field(discriminator="action"),
]


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
            "memory", "workspace", "environment", "router",
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
            "'workspace'; an artefact key, path, or basename for 'artefact'; "
            "and a vault-relative path for "
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


@mcp.tool()
def brain_outline(
    path: Annotated[
        str,
        Field(description="Artefact key, relative path, or resolvable name."),
    ],
):
    """List headings and callouts that can be targeted by brain_edit."""
    with _trace_tool("brain_outline", path=path):
        try:
            return _server_reading.handle_brain_outline(path, _runtime())
        except Exception as e:
            if _logger:
                _logger.error("brain_outline: %s", e, exc_info=True)
            return _fmt_error(f"Unexpected error: {e}")


@mcp.tool()
def brain_check(
    severity: Annotated[
        Literal["error", "warning", "info"] | None,
        Field(description="Optional severity filter."),
    ] = None,
    check: Annotated[
        str | None,
        Field(description="Optional exact check-name filter, such as parent_contract."),
    ] = None,
    path: Annotated[
        str | None,
        Field(description="Optional artefact path or folder-prefix filter."),
    ] = None,
    actionable: Annotated[
        bool,
        Field(description="Include deterministic fix guidance when available."),
    ] = False,
):
    """Run read-only Brain compliance checks with structured findings."""
    with _trace_tool("brain_check", severity=severity, check=check, path=path):
        try:
            return _server_reading.handle_brain_check(
                _runtime(),
                severity=severity,
                check_name=check,
                path=path,
                actionable=actionable,
            )
        except Exception as e:
            if _logger:
                _logger.error("brain_check: %s", e, exc_info=True)
            return _fmt_error(f"Unexpected error: {e}")


@mcp.tool()
def brain_search(
    query: Annotated[
        str,
        Field(description=(
            "Search text. Artefacts search indexed content; other resources "
            "search name + body text."
        )),
    ],
    resource: Annotated[
        Literal["artefact", "skill", "trigger", "style", "memory", "plugin"],
        Field(description=(
            "Collection to search. Type/tag/status/mode apply only to artefacts."
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
        Field(description="Artefact status filter."),
    ] = None,
    mode: Annotated[
        Literal["lexical", "semantic", "hybrid"] | None,
        Field(description=(
            "Artefact retrieval mode. 'lexical' may use Obsidian CLI; "
            "'semantic' uses vectors; 'hybrid' fuses BM25 + vectors. Omit for "
            "the best default."
        )),
    ] = None,
    top_k: Annotated[
        int,
        Field(description="Maximum results to return."),
    ] = 10,
):
    """Search vault content, relevance-ranked.

    Artefact search supports lexical, semantic, and hybrid retrieval. Lexical
    mode may use the Obsidian CLI live index in MCP when available; semantic
    and hybrid use the local script-layer retrieval stack. For exhaustive
    enumeration (not relevance-ranked), use brain_list.
    """
    with _trace_tool("brain_search", query=query, resource=resource, type=type, tag=tag, mode=mode):
        try:
            return _server_reading.handle_brain_search(
                query=query,
                resource=resource,
                type=type,
                tag=tag,
                status=status,
                mode=mode,
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
    modified_since: Annotated[
        str | None,
        Field(description=(
            "Inclusive ISO start date on artefact modified metadata. Artefact lists only."
        )),
    ] = None,
    modified_until: Annotated[
        str | None,
        Field(description=(
            "Inclusive ISO end date on artefact modified metadata. Artefact lists only."
        )),
    ] = None,
    tag: Annotated[
        str | None,
        Field(description=_ARTEFACT_TAG_FILTER_DESCRIPTION),
    ] = None,
    top_k: Annotated[
        int | None,
        Field(description="Page size. Artefact lists only; default 500.", ge=1),
    ] = None,
    sort: Annotated[
        Literal["date_desc", "date_asc", "modified_desc", "modified_asc", "title"] | None,
        Field(description=(
            "Artefact list sort order by created date, modified date, or title. "
            "Artefact lists only; default date_desc."
        )),
    ] = None,
    cursor: Annotated[
        str | None,
        Field(description="Continuation cursor returned by a prior artefact list page."),
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
                modified_since=modified_since,
                modified_until=modified_until,
                tag=tag,
                top_k=top_k,
                sort=sort,
                cursor=cursor,
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
def brain_stage(
    content: Annotated[
        str,
        Field(description="Body content to hold for a later create/edit call."),
    ],
):
    """Create a Brain-owned, retry-safe body handle for a later mutation."""
    with _trace_tool("brain_stage", bytes=len(content.encode("utf-8"))):
        denied = _enforce_profile("brain_stage")
        if denied:
            return denied
        if _vault_root is None:
            return _fmt_error("server not initialized")
        try:
            with _serialize_mutation("brain_stage"):
                result = stage_body(_vault_root, content)
            return CallToolResult(
                content=[TextContent(type="text", text=f"**Staged:** {result['handle']}")],
                structuredContent=result,
            )
        except (ValueError, OSError) as e:
            return _fmt_error(str(e))


@mcp.tool()
def brain_discard_stage(
    handle: Annotated[
        str,
        Field(description="Unused opaque body handle previously returned by brain_stage."),
    ],
):
    """Discard an unused staged body explicitly before its automatic expiry."""
    with _trace_tool("brain_discard_stage", handle=handle):
        denied = _enforce_profile("brain_discard_stage")
        if denied:
            return denied
        if _vault_root is None:
            return _fmt_error("server not initialized")
        try:
            with _serialize_mutation("brain_discard_stage"):
                discarded = discard_staged_body(_vault_root, handle)
            result = {"handle": handle, "discarded": discarded}
            return CallToolResult(
                content=[TextContent(type="text", text=(
                    "**Discarded staged body**" if discarded else "**Staged body already absent**"
                ))],
                structuredContent=result,
            )
        except (ValueError, OSError) as e:
            return _fmt_error(str(e))


@mcp.tool()
def brain_upload_attachment(
    destination_key: Annotated[
        str,
        Field(description=(
            "Required destination identifier: an active living artefact key "
            "in type/key or type~key form, or a standalone Brain key."
        )),
    ],
    name: Annotated[
        str,
        Field(description=(
            "Destination filename beneath the derived attachment folder."
        )),
    ],
    content_base64: Annotated[
        str,
        Field(description=(
            "Base64-encoded attachment bytes. Maximum decoded size is "
            f"{attachment_upload.MAX_ATTACHMENT_BYTES // (1024 * 1024)} MiB."
        )),
    ],
):
    """Add a retry-safe binary or text attachment to the Obsidian attachment folder."""
    with _trace_tool(
        "brain_upload_attachment", destination_key=destination_key, name=name
    ):
        denied = _enforce_profile("brain_upload_attachment")
        if denied:
            return denied
        if _vault_root is None:
            return _fmt_error("server not initialized")
        try:
            state = _get_state()
            if attachment_upload.attachment_destination_requires_router(
                destination_key
            ):
                state, progress = _server_readiness.require_router(
                    _runtime(), "brain_upload_attachment"
                )
                if progress is not None:
                    return progress
            attachment_upload.resolve_attachment_destination(
                state.router, destination_key
            )
            attachment_upload.validate_attachment_name(name)
            content = attachment_upload.decode_attachment_base64(content_base64)
            with _serialize_mutation(
                f"brain_upload_attachment:{destination_key}:{name}"
            ):
                result = attachment_upload.upload_attachment(
                    state.vault_root,
                    state.router,
                    destination_key=destination_key,
                    name=name,
                    content=content,
                )
            state = "Uploaded" if result["created"] else "Attachment already present"
            return CallToolResult(
                content=[TextContent(
                    type="text",
                    text=f"**{state}:** {result['path']}\n{result['embed']}",
                )],
                structuredContent=result,
            )
        except (FileExistsError, ValueError, OSError) as e:
            return _fmt_error(str(e))


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
    body_handle: Annotated[str, Field(description=_BODY_HANDLE_DESCRIPTION)] = "",
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
            "children use owner-scoped date folders."
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
    with _trace_tool("brain_create", resource=resource, type=type, title=title, name=name):
        try:
            params = _build_brain_create_params(
                resource,
                type=type or None,
                title=title or None,
                body=body or None,
                body_file=body_file or None,
                body_handle=body_handle or None,
                frontmatter=frontmatter,
                parent=parent,
                key=key,
                name=name or None,
                fix_links=fix_links,
            )
            with _serialize_mutation(f"brain_create:{resource}:{type or name or title}"):
                file_index, progress = _server_artefacts.prepare_fix_links_file_index(
                    "brain_create", params.get("fix_links"), _runtime()
                )
                if progress:
                    return progress
                return _server_artefacts.handle_brain_create(
                    resource=resource,
                    params=params,
                    runtime=_runtime(),
                    file_index=file_index,
                )
        except ParentChainError as e:
            return _fmt_error(parent_chain_error_message(e))
        except (ValueError, FileNotFoundError) as e:
            return _fmt_error(str(e))
        except Exception as e:
            if _logger:
                _logger.error("brain_create: %s", e, exc_info=True)
            return _fmt_error(f"Unexpected error: {e}")


@mcp.tool(name="brain_create")
def _brain_create_tool(
    request: Annotated[
        _BrainCreateRequest,
        Field(description="Resource-discriminated create request with one explicit content source."),
    ],
):
    """Create a vault resource from a schema-valid resource variant.

    For an inline artefact, pass `{"resource": "artefact", "type": "...",
    "title": "...", "content": {"source": "inline", "content": "..."}}`
    inside `request`. Staged and caller-file-backed content use the same
    `content.source` discriminator. Invalid combinations fail before mutation.
    """
    payload = request.model_dump(exclude_unset=True, exclude_none=True)
    payload.pop("content", None)
    payload.update(_flatten_mutation_content(request.content))
    return brain_create(**payload)


# ---------------------------------------------------------------------------
# brain_edit — single-file mutation
# ---------------------------------------------------------------------------

def brain_edit(
    operation: Annotated[
        Literal["edit", "append", "prepend", "delete_section", "replace_text"],
        Field(description=(
            "Mutation kind. edit replaces, append/prepend insert, "
            "delete_section removes a heading/callout section, and replace_text "
            "performs fail-safe exact replacement."
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
    body_handle: Annotated[str, Field(description=_BODY_HANDLE_DESCRIPTION)] = "",
    old_text: Annotated[
        str | None,
        Field(description="Exact text to find. Required only for replace_text."),
    ] = None,
    new_text: Annotated[
        str | None,
        Field(description=(
            "Replacement text. Required only for replace_text; pass an empty "
            "string to delete the exact match."
        )),
    ] = None,
    match_occurrence: Annotated[
        int | None,
        Field(description=(
            "One-based exact-match occurrence to replace when multiple matches "
            "exist. Mutually exclusive with replace_all."
        ), ge=1),
    ] = None,
    replace_all: Annotated[
        bool | None,
        Field(description="Replace every exact match. Mutually exclusive with match_occurrence."),
    ] = None,
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
                body_handle=body_handle or None,
                frontmatter=frontmatter,
                target=target,
                selector=selector_payload,
                scope=scope,
                name=name or None,
                fix_links=fix_links or None,
                old_text=old_text,
                new_text=new_text,
                match_occurrence=match_occurrence,
                replace_all=replace_all,
            )
            with _serialize_mutation(f"brain_edit:{resource}:{path or name}"):
                file_index, progress = _server_artefacts.prepare_fix_links_file_index(
                    "brain_edit", params.get("fix_links"), _runtime()
                )
                if progress:
                    return progress
                return _server_artefacts.handle_brain_edit(
                    resource=resource,
                    operation=operation,
                    params=params,
                    runtime=_runtime(),
                    file_index=file_index,
                )
        except ParentChainError as e:
            return _fmt_error(parent_chain_error_message(e))
        except PartialApplyError as e:
            _mark_router_dirty()
            _mark_index_dirty()
            return _fmt_error(str(e))
        except (ValueError, FileNotFoundError) as e:
            return _fmt_error(str(e))
        except Exception as e:
            if _logger:
                _logger.error("brain_edit: %s", e, exc_info=True)
            return _fmt_error(f"Unexpected error: {e}")


@mcp.tool(name="brain_edit")
def _brain_edit_tool(
    request: Annotated[
        _BrainEditRequest,
        Field(description="Edit request composed from a resource subject and operation variant."),
    ],
):
    """Modify one vault resource through schema-valid subject and mutation variants.

    For an artefact, pass `"subject": {"resource": "artefact", "path": "..."}`
    plus a mutation such as `{"operation": "replace_text", "old_text": "...",
    "new_text": "..."}` inside `request`. Other subject and mutation variants
    remain strict; handler-owned lifecycle fields require their dedicated tool.
    """
    subject = request.subject.model_dump(exclude_unset=True, exclude_none=True)
    mutation = request.mutation.model_dump(exclude_unset=True, exclude_none=True)
    content = getattr(request.mutation, "content", None)
    mutation.pop("content", None)
    mutation.update(_flatten_mutation_content(content))
    return brain_edit(**subject, **mutation)


@mcp.tool()
def brain_define(
    request: Annotated[
        _BrainDefineRequest,
        Field(description="Guarded type, trigger, or plugin definition mutation."),
    ],
):
    """Author runtime definitions through guarded, schema-valid workflows.

    A trigger create uses `"kind": "trigger", "mutation": {"operation": "create",
    "condition": "...", "target": "..."}` inside `request`. Type/plugin
    replacements require reviewed SHA-256 preconditions. This operator-only
    tool dirties compiled state after a successful mutation.
    """
    with _trace_tool("brain_define", kind=request.kind):
        denied = _enforce_profile("brain_define")
        if denied:
            return denied
        if _vault_root is None:
            return _fmt_error("server not initialized")
        try:
            mutation = request.mutation.model_dump(exclude_unset=True, exclude_none=True)
            with _serialize_mutation(f"brain_define:{request.kind}"):
                if request.kind == "trigger":
                    result = definition_workflows.update_trigger(_vault_root, **mutation)
                else:
                    result = definition_workflows.write_definition(
                        _vault_root,
                        kind=request.kind,
                        name=request.name,
                        classification=getattr(request, "classification", None),
                        **mutation,
                    )
                _mark_router_dirty()
                _mark_index_dirty()
            return CallToolResult(
                content=[TextContent(
                    type="text",
                    text=f"**{result['operation'].capitalize()}d {result['kind']}:** {result['path']}",
                )],
                structuredContent=result,
            )
        except (OSError, ValueError) as e:
            return _fmt_error(str(e))
        except Exception as e:
            if _logger:
                _logger.error("brain_define: %s", e, exc_info=True)
            return _fmt_error(f"Unexpected error: {e}")


def _run_lifecycle_mutation(tool_name, path, field, value):
    try:
        with _serialize_mutation(f"{tool_name}:{path}"):
            return _server_artefacts.handle_brain_lifecycle(
                tool_name=tool_name,
                path=path,
                field=field,
                value=value,
                runtime=_runtime(),
            )
    except ValueError as e:
        return _fmt_error(str(e))
    except Exception as e:
        if _logger:
            _logger.error("%s: %s", tool_name, e, exc_info=True)
        return _fmt_error(f"Unexpected error: {e}")


@mcp.tool()
def brain_reparent(
    path: Annotated[
        str,
        Field(description="Artefact key, relative path, or resolvable name."),
    ],
    parent: Annotated[
        str | None,
        Field(description="New parent reference. Pass null to clear the parent."),
    ],
):
    """Change an artefact's authoritative parent and reconcile derived structure."""
    with _trace_tool("brain_reparent", path=path, parent=parent):
        return _run_lifecycle_mutation("brain_reparent", path, "parent", parent)


@mcp.tool()
def brain_set_status(
    path: Annotated[str, Field(description="Artefact key, relative path, or resolvable name.")],
    status: Annotated[str, Field(description="New status from the artefact type's status enum.")],
):
    """Change lifecycle status and reconcile status folders/naming hooks."""
    with _trace_tool("brain_set_status", path=path, status=status):
        return _run_lifecycle_mutation("brain_set_status", path, "status", status)


@mcp.tool()
def brain_set_key(
    path: Annotated[str, Field(description="Living artefact key, relative path, or resolvable name.")],
    key: Annotated[str, Field(description="New canonical key slug for the living artefact.")],
):
    """Change a living artefact key and rewrite ownership references."""
    with _trace_tool("brain_set_key", path=path, key=key):
        return _run_lifecycle_mutation("brain_set_key", path, "key", key)


@mcp.tool()
def brain_set_naming_field(
    path: Annotated[str, Field(description="Artefact key, relative path, or resolvable name.")],
    field: Annotated[str, Field(description="Type-defined frontmatter field that drives naming.")],
    value: Annotated[str, Field(description="New naming-field value.")],
):
    """Change a type-defined naming driver and reconcile the filename."""
    with _trace_tool("brain_set_naming_field", path=path, field=field):
        return _run_lifecycle_mutation("brain_set_naming_field", path, field, value)


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
    recursive: Annotated[
        bool | None,
        Field(description=(
            "When true, archive the living descendant subtree, or allow convert "
            "from a living parent to a temporal type by deparenting descendants, "
            "or restore an archived living subtree during unarchive."
        )),
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
        ("recursive", recursive),
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
                recursive=recursive,
            )
            with _serialize_mutation(f"brain_move:{op}"):
                return _server_actions.handle_brain_move(
                    op=op,
                    params=params,
                    runtime=_runtime(),
                )
        except ParentChainError as e:
            return _fmt_error(parent_chain_error_message(e))
        except ValueError as e:
            return _fmt_error(str(e))
        except Exception as e:
            if _logger:
                _logger.error("brain_move: %s", e, exc_info=True)
            return _fmt_error(f"Unexpected error: {e}")


# ---------------------------------------------------------------------------
# brain_action — workflow/utility bucket, gated by approval
# ---------------------------------------------------------------------------

@mcp.tool(name="brain_action")
def _brain_action_tool(
    request: Annotated[
        _BrainActionRequest,
        Field(description="Discriminated workflow request; action determines the exact params schema."),
    ],
):
    """Perform a workflow or utility action that may touch multiple files.

    A delete uses `"action": "delete", "params": {"path": "..."}` inside
    `request`. Every action is paired with its exact parameter shape before
    handler execution.
    """
    action = request.action
    params_payload = request.params.model_dump(exclude_unset=True)
    with _trace_tool("brain_action", action=action, params=params_payload):
        try:
            with _serialize_mutation(f"brain_action:{action}"):
                return _server_actions.handle_brain_action(
                    action=action,
                    params=params_payload,
                    runtime=_runtime(),
                )
        except ParentChainError as e:
            return _fmt_error(parent_chain_error_message(e))
        except ValueError as e:
            return _fmt_error(str(e))
        except Exception as e:
            if _logger:
                _logger.error("brain_action: %s", e, exc_info=True)
            return _fmt_error(f"Unexpected error: {e}")


def brain_action(action: str, params: dict | BaseModel | None = None):
    """Compatibility entry point for in-process callers; MCP uses a discriminated request."""
    if action == "reparent":
        action = "reparent-children"
    params_payload = (
        params.model_dump(exclude_unset=True)
        if isinstance(params, BaseModel)
        else params
    )
    with _trace_tool("brain_action", action=action, params=params_payload):
        try:
            with _serialize_mutation(f"brain_action:{action}"):
                return _server_actions.handle_brain_action(
                    action=action,
                    params=params_payload,
                    runtime=_runtime(),
                )
        except ParentChainError as e:
            return _fmt_error(parent_chain_error_message(e))
        except ValueError as e:
            return _fmt_error(str(e))
        except Exception as e:
            if _logger:
                _logger.error("brain_action: %s", e, exc_info=True)
            return _fmt_error(f"Unexpected error: {e}")


# ---------------------------------------------------------------------------
# Experimental content classification, resolution, and ingestion
# ---------------------------------------------------------------------------

def _run_process_tool(operation, *, content, type=None, title=None, mode=None, tool_name):
    params = _build_process_params(
        operation,
        content=content or None,
        type=type,
        title=title,
        mode=mode,
    )
    mutation = (
        _serialize_mutation(tool_name)
        if operation == "ingest"
        else contextlib.nullcontext()
    )
    with mutation:
        return _server_content.handle_brain_process(
            operation=operation,
            params=params,
            runtime=_runtime(),
            tool_name=tool_name,
        )


@mcp.tool()
def brain_classify(
    content: Annotated[
        str,
        Field(description="Source content to classify against the Brain taxonomy."),
    ],
    mode: Annotated[
        Literal["auto", "embedding", "bm25_only", "context_assembly"] | None,
        Field(description="Classification strategy. Defaults to auto with graceful fallbacks."),
    ] = None,
):
    """Classify content against the Brain taxonomy without changing the vault."""
    with _trace_tool("brain_classify", mode=mode):
        try:
            return _run_process_tool(
                "classify", content=content, mode=mode, tool_name="brain_classify"
            )
        except ValueError as e:
            return _fmt_error(str(e))
        except Exception as e:
            if _logger:
                _logger.error("brain_classify: %s", e, exc_info=True)
            return _fmt_error(f"Unexpected error: {e}")


@mcp.tool()
def brain_resolve(
    content: Annotated[str, Field(description="Source content to compare with existing artefacts.")],
    type: Annotated[str, Field(description="Resolved Brain type key for the candidate content.")],
    title: Annotated[str, Field(description="Candidate title used for duplicate resolution.")],
):
    """Resolve whether classified content should create or update an artefact, without changing the vault."""
    with _trace_tool("brain_resolve", type=type, title=title):
        try:
            return _run_process_tool(
                "resolve", content=content, type=type, title=title, tool_name="brain_resolve"
            )
        except ValueError as e:
            return _fmt_error(str(e))
        except Exception as e:
            if _logger:
                _logger.error("brain_resolve: %s", e, exc_info=True)
            return _fmt_error(f"Unexpected error: {e}")


@mcp.tool()
def brain_ingest(
    content: Annotated[str, Field(description="Source content to classify, resolve, and create or update.")],
    type: Annotated[str | None, Field(description="Optional Brain type-key hint.")] = None,
    title: Annotated[str | None, Field(description="Optional title hint.")] = None,
    mode: Annotated[
        Literal["auto", "embedding", "bm25_only", "context_assembly"] | None,
        Field(description="Classification strategy. Defaults to auto with graceful fallbacks."),
    ] = None,
):
    """Ingest content through classification and duplicate resolution; may create or update an artefact."""
    with _trace_tool("brain_ingest", type=type, title=title, mode=mode):
        try:
            return _run_process_tool(
                "ingest", content=content, type=type, title=title, mode=mode,
                tool_name="brain_ingest",
            )
        except ValueError as e:
            return _fmt_error(str(e))
        except Exception as e:
            if _logger:
                _logger.error("brain_ingest: %s", e, exc_info=True)
            return _fmt_error(f"Unexpected error: {e}")


def brain_process(operation, content, type=None, title=None, mode=None):
    """Compatibility entry point for the former combined in-process surface."""
    with _trace_tool("brain_process", operation=operation, type=type, title=title, mode=mode):
        try:
            return _run_process_tool(
                operation, content=content, type=type, title=title, mode=mode,
                tool_name={
                    "classify": "brain_classify",
                    "resolve": "brain_resolve",
                    "ingest": "brain_ingest",
                }.get(operation, "brain_process"),
            )
        except ValueError as e:
            return _fmt_error(str(e))
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
