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
  1. Find vault root (server always runs from vault via .mcp.json)
  2. Auto-compile router if stale
  3. Auto-build index if stale
  4. Load both into memory
  5. Probe Obsidian CLI availability
  6. Serve via stdio

Single-file by design: splitting into sub-modules (formatters, lifecycle, etc.)
was considered but adds cross-module state coupling with no hot-reload, reuse,
or meaningful agent-efficiency gains. Keep vault logic in scripts/, glue here.

Requires Python >=3.10 and the `mcp` SDK (see requirements.txt).
"""

import json
import os
import signal
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
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
    parse_frontmatter,
    resolve_body_file,
    safe_write_json,
)
import edit
import obsidian_cli
import session
import shape_presentation
import start_shaping
import migrate_naming
import workspace_registry
import process
import list_artefacts
import fix_links
import sync_definitions
import config as config_mod

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
_router_checked_at: float = 0.0
_index_checked_at: float = 0.0


def _refresh_cli_available():
    """Re-probe Obsidian CLI availability if TTL has elapsed."""
    global _cli_available, _cli_probed_at
    now = time.monotonic()
    if now - _cli_probed_at >= _CLI_PROBE_TTL:
        _cli_available = obsidian_cli.check_available()
        _cli_probed_at = now


# ---------------------------------------------------------------------------
# Version drift — reload script modules if brain-core was upgraded
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


def _check_and_reload() -> None:
    """Exit if brain-core on disk has been upgraded.

    The MCP client will restart the server, loading the new code.
    """
    if _vault_root is None or _loaded_version is None:
        return
    try:
        disk_version = _read_disk_version(_vault_root)
    except Exception:
        return
    if disk_version is None or disk_version == _loaded_version:
        return
    print(f"brain-core upgraded ({_loaded_version} → {disk_version}), exiting for restart", file=sys.stderr)
    sys.exit(0)


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
        print(f"brain-core router recompile failed: {e}", file=sys.stderr)


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
            print(f"brain-core index full rebuild failed: {e}", file=sys.stderr)
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
            print(f"brain-core index incremental update failed: {e}", file=sys.stderr)
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
        print(f"brain-core index staleness rebuild failed: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Compile & build helpers
# ---------------------------------------------------------------------------

def _save_json(data: dict, vault_root: str, rel_path: str) -> None:
    """Write a dict as JSON to vault_root/rel_path (atomic via safe_write_json)."""
    output_path = os.path.join(vault_root, rel_path)
    safe_write_json(output_path, data, bounds=vault_root)


def _compile_and_save(vault_root: str) -> dict:
    """Compile router and colours, write to disk, return compiled data.

    Resets the router staleness-check TTL so callers don't need to.
    """
    global _router_checked_at
    compiled = compile_router.compile(vault_root)
    _save_json(compiled, vault_root, _router_rel())
    compile_colours.generate(vault_root, compiled)
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
    global _vault_root, _config, _router, _index, _vault_name, _loaded_version, _workspace_registry

    if vault_root is None:
        vault_root = os.environ.get("BRAIN_VAULT_ROOT")
    if vault_root is None:
        _vault_root = str(compile_router.find_vault_root())
    else:
        _vault_root = str(vault_root)

    # Record loaded version for drift detection
    _loaded_version = _read_disk_version(_vault_root)

    # Load vault config (three-layer merge: template → vault → local)
    try:
        _config = config_mod.load_config(_vault_root)
    except Exception as e:
        print(f"brain-core startup: config load failed: {e}", file=sys.stderr)

    # Auto-compile router if stale (reuse parsed data when fresh)
    try:
        stale, data = _check_router(_vault_root)
        _router = _compile_and_save(_vault_root) if stale else data
    except Exception as e:
        print(f"brain-core startup: router compile failed: {e}", file=sys.stderr)

    # Auto-build index if stale
    try:
        stale, data = _check_index(_vault_root)
        _index = _build_index_and_save(_vault_root) if stale else data
    except Exception as e:
        print(f"brain-core startup: index build failed: {e}", file=sys.stderr)

    # Load pre-built embeddings if available
    try:
        _load_embeddings(_vault_root)
    except Exception as e:
        print(f"brain-core startup: embeddings load failed: {e}", file=sys.stderr)

    # Load workspace registry
    try:
        _workspace_registry = workspace_registry.load_registry(_vault_root)
    except Exception as e:
        print(f"brain-core startup: workspace registry failed: {e}", file=sys.stderr)

    # CLI availability is probed lazily on first tool call via _refresh_cli_available()
    # to avoid blocking startup (the Obsidian IPC socket check is fast but we defer entirely).
    # Vault name: config > env var > directory basename
    config_brain_name = (_config or {}).get("vault", {}).get("brain_name", "")
    _vault_name = config_brain_name or os.environ.get("BRAIN_VAULT_NAME") or os.path.basename(_vault_root)


# ---------------------------------------------------------------------------
# Response formatting helpers (DD-026)
# ---------------------------------------------------------------------------

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
    except Exception:
        return None, None


def _fmt_error(msg):
    """Format an error as a CallToolResult with isError flag."""
    return CallToolResult(
        content=[TextContent(type="text", text=f"Error: {msg}")],
        isError=True,
    )


def _fmt_artefact_list(artefacts):
    """Format artefact list as readable plain text."""
    lines = []
    for a in artefacts:
        status = "configured" if a["configured"] else "unconfigured"
        naming = (a.get("naming") or {}).get("pattern", "")
        lines.append(f"{a['type']}\t{a['key']}\t{a['path']}/\t{naming}\t[{status}]")
    return "\n".join(lines)


def _fmt_trigger_list(triggers):
    """Format trigger list as readable plain text."""
    lines = []
    for t in triggers:
        detail = f" — {t['detail']}" if t.get("detail") else ""
        lines.append(f"[{t['category']}] {t['condition']}{detail} → {t['target']}")
    return "\n".join(lines)


def _fmt_named_list(items, doc_key="skill_doc"):
    """Format a list of {name, doc_path} items as plain text."""
    lines = []
    for item in items:
        doc = item.get(doc_key) or ""
        lines.append(f"{item['name']}\t{doc}")
    return "\n".join(lines)


def _fmt_memory_list(memories):
    """Format memory list as readable plain text."""
    lines = []
    for m in memories:
        triggers = ", ".join(m.get("triggers", []))
        lines.append(f"{m['name']}\t[{triggers}]\t{m['memory_doc']}")
    return "\n".join(lines)


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


# Dispatch table for brain_read list resources
_READ_FORMATTERS = {
    "type": lambda result, name: (
        json.dumps(result, indent=2, ensure_ascii=False) if name
        else _fmt_artefact_list(result)
    ),
    "trigger": lambda result, name: _fmt_trigger_list(result),
    "style": lambda result, name: _fmt_named_list(result, "style_doc"),
    "skill": lambda result, name: _fmt_named_list(result),
    "plugin": lambda result, name: _fmt_named_list(result),
    "memory": lambda result, name: (
        # Multiple matches with name → keep JSON for disambiguation
        json.dumps(result, indent=2, ensure_ascii=False) if name
        else _fmt_memory_list(result)
    ),
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
    global _session_profile

    try:
        _check_and_reload()
        _ensure_router_fresh()
        _refresh_cli_available()

        if _router is None or _vault_root is None:
            return _fmt_error("server not initialized")

        if _config is not None:
            try:
                profile, op_id = config_mod.authenticate_operator(operator_key, _config)
                _session_profile = profile
            except ValueError as e:
                return _fmt_error(str(e))
        else:
            _session_profile = None

        result = session.compile_session(
            _router, _vault_root,
            obsidian_cli_available=_cli_available,
            context=context,
            config=_config,
        )

        if _session_profile:
            result["active_profile"] = _session_profile

        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        print(traceback.format_exc(), file=sys.stderr)
        return _fmt_error(f"Unexpected error: {e}")


# ---------------------------------------------------------------------------
# brain_read — safe, no side effects
# ---------------------------------------------------------------------------

@mcp.tool()
def brain_read(
    resource: Literal[
        "type", "trigger", "style", "template", "skill", "plugin",
        "memory", "workspace", "environment", "router", "compliance",
        "artefact", "file",
    ],
    name: str | None = None,
):
    """Read Brain vault resources. Safe, no side effects.

    Resources:
      type        — list artefact types, or filter by name
      trigger     — list all triggers
      style       — list styles, or read a specific style file by name
      template    — read a template file (name = artefact type key)
      skill       — list skills, or read a specific skill file by name
      plugin      — list plugins, or read a specific plugin file by name
      memory      — list memories, or search by trigger/name (case-insensitive substring)
      workspace   — list workspaces, or resolve a specific workspace by slug (name = slug)
      environment — runtime environment info
      router      — always-rules and metadata
      compliance  — run structural compliance checks (name = severity filter: error/warning/info)
      artefact    — read an artefact file (name = relative path or basename; resolves like wikilinks)
      file        — read any vault file by name (resolves and delegates to the correct resource handler)
    """
    try:
        _check_and_reload()
        _ensure_router_fresh()

        denied = _enforce_profile("brain_read")
        if denied:
            return denied

        if _router is None:
            return _fmt_error("server not initialized")

        # Workspace resource: handled by server (registry is server state, not router state)
        if resource == "workspace":
            if name:
                try:
                    result = workspace_registry.resolve_workspace(
                        _vault_root, name, registry=_workspace_registry,
                    )
                    return _fmt_workspace_single(result)
                except ValueError as e:
                    return _fmt_error(str(e))
            else:
                result = workspace_registry.list_workspaces(
                    _vault_root, registry=_workspace_registry,
                )
                return _fmt_workspace_list(result)

        result = read_mod.read_resource(_router, _vault_root, resource, name)

        # Return strings as-is (file content)
        if isinstance(result, str):
            return result
        # Dict results: check for errors, enrich environment
        if isinstance(result, dict):
            if "error" in result:
                return _fmt_error(result["error"])
            if resource == "environment":
                _refresh_cli_available()
                result["obsidian_cli_available"] = _cli_available
                result["has_config"] = _config is not None
                result["active_profile"] = _session_profile
        # Use formatter if available (DD-026), else fall through to JSON
        formatter = _READ_FORMATTERS.get(resource)
        if formatter:
            return formatter(result, name)
        return json.dumps(result, indent=2, ensure_ascii=False)
    except Exception as e:
        print(traceback.format_exc(), file=sys.stderr)
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
def brain_search(query: str, type: str | None = None, tag: str | None = None,
                 status: str | None = None, top_k: int = 10):
    """Search vault content. Uses Obsidian CLI live index when available, BM25 fallback.

    Returns ranked results with path, title, type, status, and source.
    Optional filters: type (e.g. 'living/wiki'), tag, status (e.g. 'shaping'), top_k (default 10).
    """
    try:
        _check_and_reload()
        _ensure_router_fresh()
        _ensure_index_fresh()

        denied = _enforce_profile("brain_search")
        if denied:
            return denied

        if _index is None:
            return _fmt_error("server not initialized")

        # Resolve type filter to full type (e.g. "idea" → "living/ideas")
        type_filter = type
        if type_filter and _router:
            art = _common.match_artefact(_router.get("artefacts", []), type_filter)
            if art:
                type_filter = art["frontmatter_type"]

        _refresh_cli_available()

        # CLI-first: Obsidian's live index is always current
        if _cli_available and _vault_name and query:
            cli_results = obsidian_cli.search(_vault_name, query)
            if cli_results is not None:
                results = _transform_cli_results(cli_results, type_filter, tag, status, top_k)
                return _fmt_search("obsidian_cli", results)

        # BM25 fallback
        results = search_index.search(_index, query, _vault_root, type_filter=type_filter, tag_filter=tag,
                                      status_filter=status, top_k=top_k)
        return _fmt_search("bm25", results)
    except Exception as e:
        print(traceback.format_exc(), file=sys.stderr)
        return _fmt_error(f"Unexpected error: {e}")


# ---------------------------------------------------------------------------
# brain_list — exhaustive enumeration, not relevance-ranked
# ---------------------------------------------------------------------------

@mcp.tool()
def brain_list(type: str | None = None, since: str | None = None,
               until: str | None = None, tag: str | None = None,
               top_k: int = 500,
               sort: Literal["date_desc", "date_asc", "title"] = "date_desc"):
    """List vault artefacts by type, date range, or tag. Exhaustive — not relevance-ranked.

    Unlike brain_search, returns all matching artefacts up to top_k (default 500).
    Optional filters: type (e.g. 'temporal/research'), since/until (ISO dates e.g.
    '2026-03-20'), tag, top_k, sort ('date_desc', 'date_asc', 'title').
    """
    try:
        _check_and_reload()
        _ensure_router_fresh()
        _ensure_index_fresh()

        denied = _enforce_profile("brain_list")
        if denied:
            return denied

        if _index is None:
            return _fmt_error("server not initialized")

        results = list_artefacts.list_artefacts(
            _index, _router, type_filter=type, since=since, until=until,
            tag=tag, top_k=top_k, sort=sort,
        )
        return _fmt_list(results, type)
    except Exception as e:
        print(traceback.format_exc(), file=sys.stderr)
        return _fmt_error(f"Unexpected error: {e}")


# ---------------------------------------------------------------------------
# brain_create — additive, safe to auto-approve
# ---------------------------------------------------------------------------

@mcp.tool()
def brain_create(type: str, title: str, body: str = "", body_file: str = "", frontmatter: dict | None = None, parent: str | None = None):
    """Create a new vault artefact. Additive — creates a file, cannot destroy existing work.

    For bodies over ~1 KB, prefer body_file over body to save tokens in the tool call.

    Parameters:
      type       — artefact type key (e.g. "ideas") or full type (e.g. "living/ideas")
      title      — human-readable title, used for filename generation
      body       — markdown body content (optional, template body used if empty).
                   Mutually exclusive with body_file.
      body_file  — absolute path to a file containing the body content (optional).
                   Must be inside the vault or the system temp directory.
                   Temp files are deleted after reading; vault files are left in place.
                   Use for large content to keep MCP call displays compact.
                   Mutually exclusive with body.
      frontmatter — optional frontmatter field overrides (e.g. {"status": "shaping"})
      parent     — optional project name to group this artefact under (e.g. "Brain").
                   Living types only; ignored for temporal types.

    Returns JSON: {path, type, title}
    """
    try:
        _check_and_reload()
        _ensure_router_fresh()

        denied = _enforce_profile("brain_create")
        if denied:
            return denied

        if _router is None or _vault_root is None:
            return _fmt_error("server not initialized")

        body, cleanup_path = resolve_body_file(body, body_file, vault_root=_vault_root)

        result = create.create_artefact(
            _vault_root, _router, type, title,
            body=body, frontmatter_overrides=frontmatter, parent=parent,
        )
        _mark_index_pending(result["path"], type_hint=result["type"])
        if cleanup_path:
            try:
                os.remove(cleanup_path)
            except OSError:
                pass
        return f"**Created** {result['type']}: {result['path']}"
    except (ValueError, FileNotFoundError) as e:
        return _fmt_error(str(e))
    except Exception as e:
        print(traceback.format_exc(), file=sys.stderr)
        return _fmt_error(f"Unexpected error: {e}")


# ---------------------------------------------------------------------------
# brain_edit — single-file mutation
# ---------------------------------------------------------------------------

@mcp.tool()
def brain_edit(operation: Literal["edit", "append", "prepend"], path: str, body: str = "", body_file: str = "", frontmatter: dict | None = None, target: str | None = None):
    """Modify an existing vault artefact. Single-file mutation.

    For bodies over ~1 KB, prefer body_file over body to save tokens in the tool call.

    Parameters:
      operation  — "edit" (replace body/section), "append" (add after), or "prepend" (insert before)
      path       — relative path or basename (resolves like wikilinks; e.g. "Ideas/my-idea.md" or "my-idea")
      body       — new body content (edit), content to append (append), or content to prepend (prepend).
                   Mutually exclusive with body_file.
                   Omit body for frontmatter-only changes.
      body_file  — absolute path to a file containing the body content (optional).
                   Must be inside the vault or the system temp directory.
                   Temp files are deleted after reading; vault files are left in place.
                   Use for large content to keep MCP call displays compact.
                   Mutually exclusive with body.
      frontmatter — optional frontmatter changes. Merge strategy depends on operation:
                   edit overwrites fields; append/prepend extend list fields (with dedup)
                   and overwrite scalars. All operations can be used for frontmatter-only
                   changes by omitting body.
      target     — optional heading, callout title, or ":body" for whole-body targeting.
                   When given: edit replaces that section's content; append inserts at end
                   of the section; prepend inserts before the section's heading line.
                   Include # markers to disambiguate duplicate headings (e.g. "### Notes").
                   For callouts, use the [!type] prefix (e.g. "[!note] Implementation status").
                   Use target=":body" to explicitly target the entire body.

    Path validated against compiled router — wrong folder or naming rejected with helpful error.
    """
    try:
        _check_and_reload()
        _ensure_router_fresh()

        denied = _enforce_profile("brain_edit")
        if denied:
            return denied

        if _router is None or _vault_root is None:
            return _fmt_error("server not initialized")

        body, cleanup_path = resolve_body_file(body, body_file, vault_root=_vault_root)

        if not body and not frontmatter and not target:
            return _fmt_error(f"{operation} with no body and no frontmatter changes is a no-op. "
                              "Pass body content, frontmatter changes, or both.")

        if operation == "edit":
            result = edit.edit_artefact(
                _vault_root, _router, path, body,
                frontmatter_changes=frontmatter,
                target=target,
            )
        elif operation == "append":
            result = edit.append_to_artefact(
                _vault_root, _router, path, body,
                frontmatter_changes=frontmatter,
                target=target,
            )
        elif operation == "prepend":
            result = edit.prepend_to_artefact(
                _vault_root, _router, path, body,
                frontmatter_changes=frontmatter,
                target=target,
            )
        else:
            return _fmt_error(f"Unknown operation '{operation}'. Valid: edit, append, prepend")
        moved = result["path"] != result["resolved_path"]
        if moved:
            _mark_index_dirty()  # file moved, full rebuild needed
        else:
            _mark_index_pending(result["path"])
        if cleanup_path:
            try:
                os.remove(cleanup_path)
            except OSError:
                pass
        past = {"edit": "Edited", "append": "Appended", "prepend": "Prepended"}[result["operation"]]
        msg = f"**{past}:** {result['path']}"
        if moved:
            msg += f"\n**Moved:** {result['resolved_path']} → {result['path']} (terminal status)"
        if target:
            msg += f" (target: {target})"
            prev_h, next_h = _surrounding_headings(_vault_root, result["path"], target)
            if prev_h or next_h:
                prev_label = prev_h or "(start)"
                next_label = next_h or "(end)"
                msg += f"\n**Context:** prev={prev_label} | next={next_label}"
        return msg
    except (ValueError, FileNotFoundError) as e:
        return _fmt_error(str(e))
    except Exception as e:
        print(traceback.format_exc(), file=sys.stderr)
        return _fmt_error(f"Unexpected error: {e}")


# ---------------------------------------------------------------------------
# brain_action — vault-wide/destructive ops, gated by approval
# ---------------------------------------------------------------------------

@mcp.tool()
def brain_action(
    action: Literal[
        "compile", "build_index", "rename", "delete", "convert",
        "shape-presentation", "start-shaping", "migrate_naming",
        "register_workspace", "unregister_workspace", "fix-links",
        "sync_definitions",
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
      shape-presentation   — create presentation + launch live preview (params: {source, slug})
      start-shaping        — bootstrap shaping session (params: {target}, optional: {title})
      migrate_naming       — migrate vault filenames to generous naming conventions (optional: {dry_run})
      register_workspace   — register a linked workspace (params: {slug, path})
      unregister_workspace — remove a linked workspace registration (params: {slug})
      fix-links            — scan/fix broken wikilinks (optional: {fix} to apply)
      sync_definitions     — sync artefact library definitions to vault (optional: {dry_run, force, types})
    """
    global _router, _index, _workspace_registry

    try:
        _check_and_reload()

        denied = _enforce_profile("brain_action")
        if denied:
            return denied

        if _vault_root is None:
            return _fmt_error("server not initialized")

        if action == "compile":
            try:
                _router = _compile_and_save(_vault_root)
                art_count = len(_router["artefacts"])
                configured = sum(1 for a in _router["artefacts"] if a["configured"])
                trigger_count = len(_router["triggers"])
                skill_count = len(_router["skills"])
                memory_count = len(_router.get("memories", []))
                living_count = sum(1 for a in _router["artefacts"] if a["classification"] == "living")
                temporal_count = sum(1 for a in _router["artefacts"] if a["classification"] == "temporal")
                return (f"**Compiled:** {art_count} artefacts ({configured} configured), "
                        f"{trigger_count} triggers, {skill_count} skills, "
                        f"{memory_count} memories, "
                        f"{living_count + temporal_count} colours")
            except (ValueError, OSError) as e:
                return _fmt_error(str(e))

        elif action == "build_index":
            try:
                _index = _build_index_and_save(_vault_root)
                doc_count = _index["meta"]["document_count"]
                term_count = len(_index["corpus_stats"]["df"])
                return f"**Built index:** {doc_count} documents, {term_count} unique terms"
            except (ValueError, OSError) as e:
                return _fmt_error(str(e))

        elif action == "rename":
            if not params or "source" not in params or "dest" not in params:
                return _fmt_error("rename requires params: {source, dest} (relative paths)")

            source = params["source"]
            dest = params["dest"]

            # CLI-first: Obsidian auto-updates wikilinks
            _refresh_cli_available()
            if _cli_available and _vault_name:
                # Ensure destination directory exists (Obsidian CLI won't create it)
                abs_dest = os.path.join(_vault_root, dest)
                os.makedirs(os.path.dirname(abs_dest), exist_ok=True)

                result = obsidian_cli.move(_vault_name, source, dest)
                if result is True:  # False (CLI error) and None (connection) fall through
                    _mark_index_dirty()
                    return f"**Renamed** (obsidian_cli): {source} → {dest} (wikilinks auto-updated)"

            # Fallback: grep-and-replace wikilinks + os.rename
            try:
                links_updated = rename.rename_and_update_links(_vault_root, source, dest)
                _mark_index_dirty()
                return f"**Renamed** (grep_replace): {source} → {dest}, {links_updated} links updated"
            except FileNotFoundError as e:
                return _fmt_error(str(e))

        elif action == "delete":
            if not params or "path" not in params:
                return _fmt_error("delete requires params: {path} (relative path)")
            try:
                links_replaced = rename.delete_and_clean_links(_vault_root, params["path"])
                _mark_index_dirty()
                return f"**Deleted:** {params['path']}, {links_replaced} links replaced"
            except FileNotFoundError as e:
                return _fmt_error(str(e))

        elif action == "convert":
            if not params or "path" not in params or "target_type" not in params:
                return _fmt_error("convert requires params: {path, target_type}")
            try:
                result = edit.convert_artefact(
                    _vault_root, _router, params["path"], params["target_type"],
                    parent=params.get("parent"),
                )
                _mark_index_dirty()
                return json.dumps({
                    "status": "ok",
                    "old_path": result["old_path"],
                    "new_path": result["new_path"],
                    "type": result["type"],
                    "links_updated": result["links_updated"],
                }, indent=2)
            except (ValueError, FileNotFoundError) as e:
                return _fmt_error(str(e))

        elif action == "shape-presentation":
            if not params or "source" not in params or "slug" not in params:
                return _fmt_error("shape-presentation requires params: {source, slug}")
            try:
                result = shape_presentation.shape(_vault_root, params)
                if isinstance(result, dict) and "error" in result:
                    return _fmt_error(result["error"])
                return json.dumps(result, indent=2)
            except (ValueError, FileNotFoundError) as e:
                return _fmt_error(str(e))

        elif action == "start-shaping":
            try:
                result = start_shaping.start_shaping(_vault_root, _router, params)
                if isinstance(result, dict) and "error" in result:
                    return _fmt_error(result["error"])
                _mark_index_pending(result["transcript_path"], type_hint=result.get("type"))
                return json.dumps(result, indent=2)
            except (ValueError, FileNotFoundError) as e:
                return _fmt_error(str(e))

        elif action == "register_workspace":
            if not params or "slug" not in params or "path" not in params:
                return _fmt_error("register_workspace requires params: {slug, path}")
            try:
                workspace_registry.register_workspace(
                    _vault_root, params["slug"], params["path"],
                )
                _workspace_registry = workspace_registry.load_registry(_vault_root)
                return f"**Workspace registered:** {params['slug']} → {params['path']}"
            except ValueError as e:
                return _fmt_error(str(e))

        elif action == "unregister_workspace":
            if not params or "slug" not in params:
                return _fmt_error("unregister_workspace requires params: {slug}")
            try:
                workspace_registry.unregister_workspace(
                    _vault_root, params["slug"],
                )
                _workspace_registry = workspace_registry.load_registry(_vault_root)
                return f"**Workspace unregistered:** {params['slug']}"
            except ValueError as e:
                return _fmt_error(str(e))

        elif action == "sync_definitions":
            try:
                p = params or {}
                result = sync_definitions.sync_definitions(
                    _vault_root,
                    dry_run=p.get("dry_run", False),
                    force=p.get("force", False),
                    types=p.get("types", None),
                )
                if result["status"] == "ok" and not p.get("dry_run") and result["updated"]:
                    _router = _compile_and_save(_vault_root)
                    result["post_sync"] = "Recompiled router."
                return json.dumps(result, indent=2)
            except (OSError, json.JSONDecodeError) as e:
                return _fmt_error(str(e))

        elif action == "migrate_naming":
            if _router is None:
                return _fmt_error("router not initialized")
            try:
                dry_run = (params or {}).get("dry_run", False)
                result = migrate_naming.migrate_vault(_vault_root, router=_router, dry_run=dry_run)
                if isinstance(result, dict) and "error" in result:
                    return _fmt_error(result["error"])
                if not dry_run and result.get("renamed", 0) > 0:
                    new_router = _compile_and_save(_vault_root)
                    new_index = _build_index_and_save(_vault_root)
                    _router = new_router
                    _index = new_index
                return json.dumps(result, indent=2)
            except (ValueError, FileNotFoundError, OSError) as e:
                return _fmt_error(str(e))

        elif action == "fix-links":
            if _router is None:
                return _fmt_error("router not initialized")
            try:
                do_fix = (params or {}).get("fix", False)
                result = fix_links.scan_and_resolve(_vault_root, router=_router)
                if do_fix and result["fixed"]:
                    total = fix_links.apply_fixes(_vault_root, result["fixed"])
                    result["substitutions"] = total
                    _mark_index_dirty()
                result["mode"] = "fix" if do_fix else "dry_run"
                return json.dumps(result, indent=2)
            except (ValueError, OSError) as e:
                return _fmt_error(str(e))

        else:
            valid = ["compile", "build_index", "rename", "delete", "convert",
                     "shape-presentation", "start-shaping",
                     "migrate_naming", "register_workspace",
                     "unregister_workspace", "fix-links", "sync_definitions"]
            return _fmt_error(f"Unknown action '{action}'. Valid: {', '.join(valid)}")

    except Exception as e:
        print(traceback.format_exc(), file=sys.stderr)
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
    global _index

    try:
        _check_and_reload()
        _ensure_router_fresh()
        _ensure_index_fresh()
        _ensure_embeddings_fresh()

        denied = _enforce_profile("brain_process")
        if denied:
            return denied

        if _router is None or _vault_root is None:
            return _fmt_error("server not initialized")

        if operation == "classify":
            try:
                result = process.classify_content(
                    _router, _vault_root, content,
                    index=_index,
                    type_embeddings=_type_embeddings,
                    type_embeddings_meta=_embeddings_meta,
                    mode=mode,
                )
                return _fmt_classify(result)
            except OSError as e:
                return _fmt_error(str(e))

        elif operation == "resolve":
            if not type or not title:
                return _fmt_error("resolve requires type and title parameters")
            try:
                result = process.resolve_content(
                    _router, _vault_root, type, title, content=content,
                    index=_index,
                    doc_embeddings=_doc_embeddings,
                    doc_embeddings_meta=_embeddings_meta,
                )
                if result.get("action") == "error":
                    return _fmt_error(result["reasoning"])
                return _fmt_resolve(result)
            except (ValueError, OSError) as e:
                return _fmt_error(str(e))

        elif operation == "ingest":
            try:
                result = process.ingest_content(
                    _router, _vault_root, content,
                    title=title, type_hint=type,
                    index=_index,
                    type_embeddings=_type_embeddings,
                    type_embeddings_meta=_embeddings_meta,
                    doc_embeddings=_doc_embeddings,
                    doc_embeddings_meta=_embeddings_meta,
                )
                formatted = _fmt_ingest(result)
                if formatted is None:
                    return _fmt_error(result.get("message", "Unknown error"))
                # Queue incremental index update after successful mutation
                if result.get("action_taken") in ("created", "updated") and result.get("path"):
                    _mark_index_pending(result["path"], type_hint=result.get("type"))
                    _ensure_index_fresh()
                return formatted
            except (ValueError, FileNotFoundError, OSError) as e:
                return _fmt_error(str(e))

        else:
            return _fmt_error(
                f"Unknown operation '{operation}'. Valid: classify, resolve, ingest"
            )
    except Exception as e:
        print(traceback.format_exc(), file=sys.stderr)
        return _fmt_error(f"Unexpected error: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _shutdown(reason: str) -> None:
    """Log a clean shutdown message to stderr and exit."""
    print(f"brain-core shutdown: {reason}", file=sys.stderr)
    sys.exit(0)


def _handle_signal(signum: int, _frame) -> None:
    """Handle SIGTERM/SIGINT per MCP stdio lifecycle spec."""
    name = signal.Signals(signum).name
    _shutdown(f"received {name}")


def main():
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        startup()
    except Exception as e:
        print(f"brain-core fatal startup error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        mcp.run(transport="stdio")
    except Exception as e:
        print(f"brain-core unexpected error: {e}", file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)
        sys.exit(1)

    _shutdown("stdin closed")


if __name__ == "__main__":
    main()
