#!/usr/bin/env python3
"""
Brain MCP Server — thin MCP wrapper over brain-core scripts.

All logic lives in `.brain-core/scripts/` as importable functions.
The server imports them, holds the compiled router and search index in memory,
and exposes 7 MCP tools:
  brain_session — bootstrap an agent session (compiled payload, one call)
  brain_read    — read compiled router resources (safe, no side effects)
  brain_search  — BM25 keyword search, with optional Obsidian CLI live search
  brain_create  — create new vault artefacts (additive, safe to auto-approve)
  brain_edit    — modify existing vault artefacts (single-file mutation)
  brain_action  — vault-wide/destructive ops: compile, build_index, rename, delete, convert
  brain_process — content processing: classify, resolve duplicates, ingest

Why this pattern: scripts are the source of truth for all vault operations.
The MCP server gets in-memory caching for free (router/index loaded once at
startup). Standalone scripts pay a cold-start cost reading JSON from disk.
Agents without MCP use the scripts directly — same logic, same results.

Optional Obsidian CLI integration (dsebastien/obsidian-cli-rest):
  - Search: CLI-first with BM25 fallback (CLI index is always current)
  - Rename: CLI-first with grep-and-replace fallback (CLI updates wikilinks)

Startup sequence:
  1. Find vault root (server always runs from vault via .mcp.json)
  2. Auto-compile router if stale
  3. Auto-build index if stale
  4. Load both into memory
  5. Probe Obsidian CLI availability
  6. Serve via stdio

Requires Python >=3.10 and the `mcp` SDK (see requirements.txt).
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent

# ---------------------------------------------------------------------------
# Script imports — add scripts dir to sys.path
# ---------------------------------------------------------------------------

SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, os.path.abspath(SCRIPTS_DIR))

import importlib
import _common
import compile_router
import compile_colours
import build_index
import search_index
import read as read_mod
import rename
import create
import edit
import obsidian_cli
import session
import shape_presentation
import upgrade
import migrate_naming
import workspace_registry
import process

# Constants that don't change between versions
COMPILED_ROUTER_REL = compile_router.OUTPUT_PATH
RETRIEVAL_INDEX_REL = build_index.OUTPUT_PATH

# ---------------------------------------------------------------------------
# Server state
# ---------------------------------------------------------------------------

mcp = FastMCP(name="brain")

_vault_root: str | None = None
_router: dict | None = None
_index: dict | None = None
_index_dirty: bool = False       # set True for full rebuild (e.g. version drift)
_index_pending: list[tuple[str, str | None]] = []  # [(rel_path, type_hint), ...] for incremental updates
_cli_available: bool = False
_cli_probed_at: float = 0.0  # monotonic timestamp of last CLI probe
_vault_name: str | None = None
_loaded_version: str | None = None
_workspace_registry: dict | None = None
_type_embeddings = None    # numpy array or None
_embeddings_meta = None    # dict with "types" and "documents" keys, or None
_doc_embeddings = None     # numpy array or None
_embeddings_dirty: bool = False  # set True when doc embeddings are out of sync with index


_CLI_PROBE_TTL = 30      # seconds between CLI availability re-probes
_STALENESS_CHECK_TTL = 5  # seconds between router/index filesystem staleness checks
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


_SCRIPT_MODULES = [
    "_common", "compile_router", "compile_colours", "build_index",
    "search_index", "check", "read", "create", "edit", "rename", "obsidian_cli",
    "session", "shape_presentation", "upgrade", "migrate_naming",
    "workspace_registry", "process",
]


def _check_and_reload() -> None:
    """Reload script modules if brain-core on disk has been upgraded.

    After reloading, recompile the router (version change implies config
    changes) and mark the index dirty (new logic may affect indexing).
    """
    global _loaded_version, _router
    if _vault_root is None or _loaded_version is None:
        return
    disk_version = _read_disk_version(_vault_root)
    if disk_version is not None and disk_version != _loaded_version:
        old = _loaded_version
        for name in _SCRIPT_MODULES:
            mod = sys.modules.get(name)
            if mod is not None:
                importlib.reload(mod)
        _loaded_version = disk_version
        _router = _compile_and_save(_vault_root)
        _mark_index_dirty()
        print(f"brain-core reloaded ({old} → {disk_version}): recompiled router, index marked dirty", file=sys.stderr)


# ---------------------------------------------------------------------------
# Staleness detection
# ---------------------------------------------------------------------------

def _check_router(vault_root: str) -> tuple[bool, dict | None]:
    """Check staleness and return parsed data if fresh. (stale, data|None)"""
    router_path = os.path.join(vault_root, COMPILED_ROUTER_REL)
    if not os.path.isfile(router_path):
        return True, None

    try:
        with open(router_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
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
    index_path = os.path.join(vault_root, RETRIEVAL_INDEX_REL)
    if not os.path.isfile(index_path):
        return True, None

    try:
        with open(index_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return True, None

    built_at = data.get("meta", {}).get("built_at")
    if not built_at:
        return True, None

    try:
        threshold = datetime.fromisoformat(built_at).timestamp()
    except (ValueError, TypeError):
        return True, None

    if _has_md_newer_than(vault_root, threshold):
        return True, None

    return False, data


def _has_md_newer_than(vault_root: str, threshold: float) -> bool:
    """Return True as soon as any .md file in type folders is newer than threshold."""
    all_types = compile_router.scan_living_types(vault_root) + compile_router.scan_temporal_types(vault_root)
    for type_info in all_types:
        for rel_path in build_index.find_md_files(vault_root, type_info):
            try:
                if os.path.getmtime(os.path.join(vault_root, rel_path)) > threshold:
                    return True
            except OSError:
                continue
    return False


def _check_router_type_count(vault_root: str, router: dict) -> bool:
    """Return True if the vault has types not in the cached router."""
    cached_count = len(router.get("artefacts", []))
    fs_types = (
        compile_router.scan_living_types(vault_root)
        + compile_router.scan_temporal_types(vault_root)
    )
    return len(fs_types) != cached_count


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
    if now - _router_checked_at < _STALENESS_CHECK_TTL:
        return
    _router_checked_at = now
    stale, data = _check_router(_vault_root)
    if not stale and not _check_router_type_count(_vault_root, _router):
        return
    _router = _compile_and_save(_vault_root)


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
    _index_pending.append((rel_path, type_hint))


def _ensure_index_fresh() -> None:
    """Update the index if needed: incremental for queued paths, full rebuild
    if dirty flag is set, filesystem staleness check on TTL for external changes.
    """
    global _index, _index_checked_at
    if _vault_root is None:
        return

    # Full rebuild takes priority over incremental
    if _index_dirty:
        _index = _build_index_and_save(_vault_root)
        return

    # Incremental updates for paths queued by brain_create/brain_edit
    if _index_pending and _index is not None:
        pending = _index_pending[:]
        _index_pending.clear()
        for rel_path, type_hint in pending:
            build_index.index_update(_index, _vault_root, rel_path, type_hint=type_hint, recompute=False)
        build_index._recompute_corpus_stats(_index)
        _save_json(_index, _vault_root, RETRIEVAL_INDEX_REL)
        _mark_embeddings_dirty()
        _index_checked_at = time.monotonic()
        return

    # Filesystem staleness check for external changes (throttled)
    now = time.monotonic()
    if now - _index_checked_at < _STALENESS_CHECK_TTL:
        return
    _index_checked_at = now
    stale, data = _check_index(_vault_root)
    if not stale:
        return
    _index = _build_index_and_save(_vault_root)


# ---------------------------------------------------------------------------
# Compile & build helpers
# ---------------------------------------------------------------------------

def _save_json(data: dict, vault_root: str, rel_path: str) -> None:
    """Write a dict as JSON to vault_root/rel_path."""
    output_path = os.path.join(vault_root, rel_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _compile_and_save(vault_root: str) -> dict:
    """Compile router and colours, write to disk, return compiled data.

    Resets the router staleness-check TTL so callers don't need to.
    """
    global _router_checked_at
    compiled = compile_router.compile(vault_root)
    _save_json(compiled, vault_root, COMPILED_ROUTER_REL)
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
    _save_json(index, vault_root, RETRIEVAL_INDEX_REL)
    _index_dirty = False
    _embeddings_dirty = False
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
        if os.path.isfile(doc_path):
            _doc_embeddings = np.load(doc_path)
    except (OSError, ValueError):
        pass  # embeddings unavailable — graceful degradation


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

def startup(vault_root: str | None = None) -> None:
    """Initialize server state: find vault, compile/build if stale, load data."""
    global _vault_root, _router, _index, _cli_available, _vault_name, _loaded_version, _workspace_registry

    if vault_root is None:
        vault_root = os.environ.get("BRAIN_VAULT_ROOT")
    if vault_root is None:
        _vault_root = str(compile_router.find_vault_root())
    else:
        _vault_root = str(vault_root)

    # Record loaded version for drift detection
    _loaded_version = _read_disk_version(_vault_root)

    # Run any pending migrations (e.g. vaults upgraded via manual file copy)
    upgrade.run_pending_migrations(_vault_root)

    # Auto-compile router if stale (reuse parsed data when fresh)
    stale, data = _check_router(_vault_root)
    _router = _compile_and_save(_vault_root) if stale else data

    # Auto-build index if stale
    stale, data = _check_index(_vault_root)
    _index = _build_index_and_save(_vault_root) if stale else data

    # Load pre-built embeddings if available
    _load_embeddings(_vault_root)

    # Load workspace registry
    _workspace_registry = workspace_registry.load_registry(_vault_root)

    # Probe Obsidian CLI availability
    _cli_available = obsidian_cli.check_available()
    _cli_probed_at = time.monotonic()
    _vault_name = os.environ.get("BRAIN_VAULT_NAME") or os.path.basename(_vault_root)


# ---------------------------------------------------------------------------
# Response formatting helpers (DD-026)
# ---------------------------------------------------------------------------

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
    "artefact": lambda result, name: (
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
def brain_session(context: str | None = None):
    """Bootstrap an agent session. Returns everything needed to work with the Brain in one call.

    Args:
        context: Optional context slug for scoped sessions (e.g., "mcp-spike").
                 Context scoping is not yet implemented — parameter accepted for forward compatibility.

    Returns a compiled JSON payload: always-rules, user preferences, gotchas,
    triggers, artefact type summaries, environment, memory/skill/plugin/style indexes.
    Call this once at session start. Use brain_read for individual resources after.
    """
    _check_and_reload()
    _ensure_router_fresh()

    if _router is None or _vault_root is None:
        return _fmt_error("server not initialized")

    result = session.compile_session(
        _router, _vault_root,
        obsidian_cli_available=_cli_available,
        context=context,
    )
    return json.dumps(result, ensure_ascii=False)


# ---------------------------------------------------------------------------
# brain_read — safe, no side effects
# ---------------------------------------------------------------------------

@mcp.tool()
def brain_read(resource: str, name: str | None = None):
    """Read Brain vault resources. Safe, no side effects.

    Resources:
      artefact    — list artefact types, or filter by name
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
      file        — read any artefact file by relative path (name = path from vault root)
    """
    _check_and_reload()
    _ensure_router_fresh()

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
            result["obsidian_cli_available"] = _cli_available
    # Use formatter if available (DD-026), else fall through to JSON
    formatter = _READ_FORMATTERS.get(resource)
    if formatter:
        return formatter(result, name)
    return json.dumps(result, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# brain_search — safe, no side effects
# ---------------------------------------------------------------------------

def _transform_cli_results(cli_results: list[dict], type_filter: str | None,
                           tag_filter: str | None, status_filter: str | None,
                           top_k: int) -> list[dict]:
    """Transform Obsidian CLI search results to match brain_search schema."""
    transformed = []
    for item in cli_results:
        path = item.get("filename", item.get("path", ""))
        # Read frontmatter from the index if available
        doc_meta = {}
        if _index:
            for doc in _index.get("documents", []):
                if doc.get("path") == path:
                    doc_meta = doc
                    break
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
            "score": item.get("score", 0),
            "snippet": item.get("matches", [{}])[0].get("content", "")[:200] if item.get("matches") else "",
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
        lines.append(f"{r['title']}\t{r['path']}\t{r['type']}{status_part}\tscore={r.get('score', 0.0):.2f}")
    return [
        TextContent(type="text", text=meta),
        TextContent(type="text", text="\n".join(lines)),
    ]


@mcp.tool()
def brain_search(query: str, type: str | None = None, tag: str | None = None,
                 status: str | None = None, top_k: int = 10):
    """Search vault content. Uses Obsidian CLI live index when available, BM25 fallback.

    Returns ranked results with path, title, type, status, score, snippet, and source.
    Optional filters: type (e.g. 'living/wiki'), tag, status (e.g. 'shaping'), top_k (default 10).
    """
    _check_and_reload()
    _ensure_router_fresh()
    _ensure_index_fresh()

    if _index is None:
        return _fmt_error("server not initialized")

    # Resolve type filter to full type (e.g. "idea" → "living/ideas")
    type_filter = type
    if type_filter and _router:
        art = _common.match_artefact(_router.get("artefacts", []), type_filter)
        if art:
            type_filter = art["type"]

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


# ---------------------------------------------------------------------------
# brain_create — additive, safe to auto-approve
# ---------------------------------------------------------------------------

@mcp.tool()
def brain_create(type: str, title: str, body: str = "", frontmatter: dict | None = None, parent: str | None = None):
    """Create a new vault artefact. Additive — creates a file, cannot destroy existing work.

    Parameters:
      type       — artefact type key (e.g. "ideas") or full type (e.g. "living/ideas")
      title      — human-readable title, used for filename generation
      body       — markdown body content (optional, template body used if empty)
      frontmatter — optional frontmatter field overrides (e.g. {"status": "developing"})
      parent     — optional project name to group this artefact under (e.g. "Brain").
                   Living types only; ignored for temporal types.

    Returns JSON: {path, type, title}
    """
    _check_and_reload()
    _ensure_router_fresh()

    if _router is None or _vault_root is None:
        return _fmt_error("server not initialized")

    try:
        result = create.create_artefact(
            _vault_root, _router, type, title,
            body=body, frontmatter_overrides=frontmatter, parent=parent,
        )
        _mark_index_pending(result["path"], type_hint=result["type"])
        return f"**Created** {result['type']}: {result['path']}"
    except ValueError as e:
        return _fmt_error(str(e))
    except Exception as e:
        return _fmt_error(f"Unexpected error: {e}")


# ---------------------------------------------------------------------------
# brain_edit — single-file mutation
# ---------------------------------------------------------------------------

@mcp.tool()
def brain_edit(operation: str, path: str, body: str = "", frontmatter: dict | None = None, target: str | None = None):
    """Modify an existing vault artefact. Single-file mutation.

    Parameters:
      operation  — "edit" (replace body) or "append" (add to body)
      path       — relative path from vault root (e.g. "Ideas/my-idea.md")
      body       — new body content (edit) or content to append (append)
      frontmatter — optional frontmatter changes (edit only, merged with existing)
      target     — optional heading or callout title. When given, edit replaces
                   only that section's content; append inserts at the end of that
                   section instead of EOF. Include # markers to disambiguate
                   duplicate headings (e.g. "### Notes"). For callouts, use the
                   [!type] prefix (e.g. "[!note] Implementation status").

    Path validated against compiled router — wrong folder or naming rejected with helpful error.
    """
    _check_and_reload()
    _ensure_router_fresh()

    if _router is None or _vault_root is None:
        return _fmt_error("server not initialized")

    if operation == "edit" and body == "" and not frontmatter and not target:
        return _fmt_error("edit with empty body and no frontmatter changes would erase file content. "
                          "Pass body content, frontmatter changes, or use append instead.")

    try:
        if operation == "edit":
            result = edit.edit_artefact(
                _vault_root, _router, path, body,
                frontmatter_changes=frontmatter,
                target=target,
            )
        elif operation == "append":
            result = edit.append_to_artefact(
                _vault_root, _router, path, body,
                target=target,
            )
        else:
            return _fmt_error(f"Unknown operation '{operation}'. Valid: edit, append")
        _mark_index_pending(result["path"])
        past = "Edited" if result["operation"] == "edit" else "Appended"
        msg = f"**{past}:** {result['path']}"
        if target:
            msg += f" (target: {target})"
        return msg
    except (ValueError, FileNotFoundError) as e:
        return _fmt_error(str(e))
    except Exception as e:
        return _fmt_error(f"Unexpected error: {e}")


# ---------------------------------------------------------------------------
# brain_action — vault-wide/destructive ops, gated by approval
# ---------------------------------------------------------------------------

@mcp.tool()
def brain_action(action: str, params: dict | None = None):
    """Perform vault-wide actions. Mutations — may modify multiple files.

    Actions:
      compile              — recompile the router from source files
      build_index          — rebuild the BM25 retrieval index
      rename               — rename/move a file (params: {source, dest} as relative paths)
      delete               — delete a file and clean wikilinks (params: {path})
      convert              — convert artefact to different type (params: {path, target_type})
      shape-presentation   — create presentation + launch live preview (params: {source, slug})
      upgrade              — upgrade brain-core from source (params: {source}, optional: {dry_run, force})
      migrate_naming       — migrate vault filenames to generous naming conventions (optional: {dry_run})
      register_workspace   — register a linked workspace (params: {slug, path})
      unregister_workspace — remove a linked workspace registration (params: {slug})
    """
    global _router, _index, _workspace_registry

    _check_and_reload()

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
        except Exception as e:
            return _fmt_error(f"Unexpected error: {e}")

    elif action == "build_index":
        try:
            _index = _build_index_and_save(_vault_root)
            doc_count = _index["meta"]["document_count"]
            term_count = len(_index["corpus_stats"]["df"])
            return f"**Built index:** {doc_count} documents, {term_count} unique terms"
        except (ValueError, OSError) as e:
            return _fmt_error(str(e))
        except Exception as e:
            return _fmt_error(f"Unexpected error: {e}")

    elif action == "rename":
        if not params or "source" not in params or "dest" not in params:
            return _fmt_error("rename requires params: {source, dest} (relative paths)")

        source = params["source"]
        dest = params["dest"]

        # CLI-first: Obsidian auto-updates wikilinks
        _refresh_cli_available()
        if _cli_available and _vault_name:
            result = obsidian_cli.move(_vault_name, source, dest)
            if result is not None:
                _mark_index_dirty()
                n = result.get("links_updated", -1)
                return f"**Renamed** (obsidian_cli): {source} → {dest}, {n} links updated"

        # Fallback: grep-and-replace wikilinks + os.rename
        try:
            links_updated = rename.rename_and_update_links(_vault_root, source, dest)
            _mark_index_dirty()
            return f"**Renamed** (grep_replace): {source} → {dest}, {links_updated} links updated"
        except FileNotFoundError as e:
            return _fmt_error(str(e))
        except Exception as e:
            return _fmt_error(f"Unexpected error: {e}")

    elif action == "delete":
        if not params or "path" not in params:
            return _fmt_error("delete requires params: {path} (relative path)")
        try:
            links_replaced = rename.delete_and_clean_links(_vault_root, params["path"])
            _mark_index_dirty()
            return f"**Deleted:** {params['path']}, {links_replaced} links replaced"
        except FileNotFoundError as e:
            return _fmt_error(str(e))
        except Exception as e:
            return _fmt_error(f"Unexpected error: {e}")

    elif action == "convert":
        if not params or "path" not in params or "target_type" not in params:
            return _fmt_error("convert requires params: {path, target_type}")
        try:
            result = edit.convert_artefact(
                _vault_root, _router, params["path"], params["target_type"]
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
        except Exception as e:
            return _fmt_error(f"Unexpected error: {e}")

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
        except Exception as e:
            return _fmt_error(f"Unexpected error: {e}")

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
        except Exception as e:
            return _fmt_error(f"Unexpected error: {e}")

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
        except Exception as e:
            return _fmt_error(f"Unexpected error: {e}")

    elif action == "upgrade":
        if not params or "source" not in params:
            return _fmt_error("upgrade requires params: {source} (path to source brain-core dir)")
        try:
            source = params["source"]
            dry_run = params.get("dry_run", False)
            force = params.get("force", False)
            result = upgrade.upgrade(_vault_root, source, force=force, dry_run=dry_run)
            if result["status"] == "ok" and not dry_run:
                _check_and_reload()
                new_router = _compile_and_save(_vault_root)
                new_index = _build_index_and_save(_vault_root)
                _router = new_router
                _index = new_index
                result["post_upgrade"] = "Reloaded modules, recompiled router, rebuilt index."
            return json.dumps(result, indent=2)
        except Exception as e:
            return _fmt_error(f"Unexpected error: {e}")

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
        except Exception as e:
            return _fmt_error(f"Unexpected error: {e}")

    else:
        valid = ["compile", "build_index", "rename", "delete", "convert",
                 "shape-presentation", "upgrade", "migrate_naming",
                 "register_workspace", "unregister_workspace"]
        return _fmt_error(f"Unknown action '{action}'. Valid: {', '.join(valid)}")


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
def brain_process(operation: str, content: str,
                  type: str | None = None, title: str | None = None,
                  mode: str = "auto"):
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

    _check_and_reload()
    _ensure_router_fresh()
    _ensure_index_fresh()
    _ensure_embeddings_fresh()

    if _router is None or _vault_root is None:
        return _fmt_error("server not initialized")

    try:
        if operation == "classify":
            result = process.classify_content(
                _router, _vault_root, content,
                index=_index,
                type_embeddings=_type_embeddings,
                type_embeddings_meta=_embeddings_meta,
                mode=mode,
            )
            return _fmt_classify(result)

        elif operation == "resolve":
            if not type or not title:
                return _fmt_error("resolve requires type and title parameters")
            result = process.resolve_content(
                _router, _vault_root, type, title, content=content,
                index=_index,
                doc_embeddings=_doc_embeddings,
                doc_embeddings_meta=_embeddings_meta,
            )
            if result.get("action") == "error":
                return _fmt_error(result["reasoning"])
            return _fmt_resolve(result)

        elif operation == "ingest":
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

        else:
            return _fmt_error(
                f"Unknown operation '{operation}'. Valid: classify, resolve, ingest"
            )
    except Exception as e:
        return _fmt_error(f"Unexpected error: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    startup()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
