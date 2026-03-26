#!/usr/bin/env python3
"""
Brain MCP Server — thin MCP wrapper over brain-core scripts.

All logic lives in `.brain-core/scripts/` as importable functions.
The server imports them, holds the compiled router and search index in memory,
and exposes 6 MCP tools:
  brain_session — bootstrap an agent session (compiled payload, one call)
  brain_read    — read compiled router resources (safe, no side effects)
  brain_search  — BM25 keyword search, with optional Obsidian CLI live search
  brain_create  — create new vault artefacts (additive, safe to auto-approve)
  brain_edit    — modify existing vault artefacts (single-file mutation)
  brain_action  — vault-wide/destructive ops: compile, build_index, rename, delete, convert

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
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

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
import workspace_registry

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
_cli_available: bool = False
_vault_name: str | None = None
_loaded_version: str | None = None
_workspace_registry: dict | None = None


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
    "search_index", "read", "create", "edit", "rename", "obsidian_cli",
    "session", "shape_presentation", "upgrade", "workspace_registry",
]


def _check_and_reload() -> None:
    """Reload script modules if brain-core on disk has been upgraded."""
    global _loaded_version
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
        print(f"brain-core reloaded ({old} → {disk_version})", file=sys.stderr)


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
    """Auto-recompile if the router is stale (new types or modified sources)."""
    global _router
    if _vault_root is None or _router is None:
        return
    stale, data = _check_router(_vault_root)
    if not stale and not _check_router_type_count(_vault_root, _router):
        return
    _router = _compile_and_save(_vault_root)


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
    """Compile router and colours, write to disk, return compiled data."""
    compiled = compile_router.compile(vault_root)
    _save_json(compiled, vault_root, COMPILED_ROUTER_REL)
    compile_colours.generate(vault_root, compiled)
    return compiled


def _build_index_and_save(vault_root: str) -> dict:
    """Build retrieval index, write to disk, return index data."""
    index = build_index.build_index(vault_root)
    _save_json(index, vault_root, RETRIEVAL_INDEX_REL)
    return index


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

    # Auto-compile router if stale (reuse parsed data when fresh)
    stale, data = _check_router(_vault_root)
    _router = _compile_and_save(_vault_root) if stale else data

    # Auto-build index if stale
    stale, data = _check_index(_vault_root)
    _index = _build_index_and_save(_vault_root) if stale else data

    # Load workspace registry
    _workspace_registry = workspace_registry.load_registry(_vault_root)

    # Probe Obsidian CLI availability
    _cli_available = obsidian_cli.check_available()
    _vault_name = os.environ.get("BRAIN_VAULT_NAME") or os.path.basename(_vault_root)


# ---------------------------------------------------------------------------
# brain_session — agent bootstrap, one-call session setup
# ---------------------------------------------------------------------------

@mcp.tool()
def brain_session(context: str | None = None) -> str:
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
        return "Error: server not initialized"

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
def brain_read(resource: str, name: str | None = None) -> str:
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
        return "Error: server not initialized"

    # Workspace resource: handled by server (registry is server state, not router state)
    if resource == "workspace":
        if name:
            try:
                result = workspace_registry.resolve_workspace(
                    _vault_root, name, registry=_workspace_registry,
                )
                return json.dumps(result, indent=2, ensure_ascii=False)
            except ValueError as e:
                return json.dumps({"error": str(e)})
        else:
            result = workspace_registry.list_workspaces(
                _vault_root, registry=_workspace_registry,
            )
            return json.dumps(result, indent=2, ensure_ascii=False)

    result = read_mod.read_resource(_router, _vault_root, resource, name)

    # Environment resource: MCP server enriches with CLI availability
    if resource == "environment" and isinstance(result, dict) and "error" not in result:
        result["obsidian_cli_available"] = _cli_available

    # Return strings as-is (file content), dicts/lists as JSON
    if isinstance(result, str):
        return result
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


@mcp.tool()
def brain_search(query: str, type: str | None = None, tag: str | None = None,
                 status: str | None = None, top_k: int = 10) -> str:
    """Search vault content. Uses Obsidian CLI live index when available, BM25 fallback.

    Returns ranked results with path, title, type, status, score, snippet, and source.
    Optional filters: type (e.g. 'living/wiki'), tag, status (e.g. 'shaping'), top_k (default 10).
    """
    _check_and_reload()
    _ensure_router_fresh()

    if _index is None:
        return "Error: server not initialized"

    # CLI-first: Obsidian's live index is always current
    if _cli_available and _vault_name and query:
        cli_results = obsidian_cli.search(_vault_name, query)
        if cli_results is not None:
            results = _transform_cli_results(cli_results, type, tag, status, top_k)
            return json.dumps({"source": "obsidian_cli", "results": results}, indent=2)

    # BM25 fallback
    results = search_index.search(_index, query, _vault_root, type_filter=type, tag_filter=tag,
                                  status_filter=status, top_k=top_k)
    return json.dumps({"source": "bm25", "results": results}, indent=2)


# ---------------------------------------------------------------------------
# brain_create — additive, safe to auto-approve
# ---------------------------------------------------------------------------

@mcp.tool()
def brain_create(type: str, title: str, body: str = "", frontmatter: dict | None = None) -> str:
    """Create a new vault artefact. Additive — creates a file, cannot destroy existing work.

    Parameters:
      type       — artefact type key (e.g. "idea") or full type (e.g. "living/idea")
      title      — human-readable title, used for filename generation
      body       — markdown body content (optional, template body used if empty)
      frontmatter — optional frontmatter field overrides (e.g. {"status": "developing"})

    Returns JSON: {path, type, title}
    """
    _check_and_reload()
    _ensure_router_fresh()

    if _router is None or _vault_root is None:
        return "Error: server not initialized"

    try:
        result = create.create_artefact(
            _vault_root, _router, type, title,
            body=body, frontmatter_overrides=frontmatter,
        )
        return json.dumps(result, indent=2)
    except ValueError as e:
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# brain_edit — single-file mutation
# ---------------------------------------------------------------------------

@mcp.tool()
def brain_edit(operation: str, path: str, body: str = "", frontmatter: dict | None = None, target: str | None = None) -> str:
    """Modify an existing vault artefact. Single-file mutation.

    Parameters:
      operation  — "edit" (replace body) or "append" (add to body)
      path       — relative path from vault root (e.g. "Ideas/my-idea.md")
      body       — new body content (edit) or content to append (append)
      frontmatter — optional frontmatter changes (edit only, merged with existing)
      target     — optional heading text (e.g. "Outstanding Work"). When given,
                   edit replaces only that section's content; append inserts at
                   the end of that section instead of EOF. Include # markers to
                   disambiguate duplicate headings (e.g. "### Notes").

    Path validated against compiled router — wrong folder or naming rejected with helpful error.
    """
    _check_and_reload()
    _ensure_router_fresh()

    if _router is None or _vault_root is None:
        return "Error: server not initialized"

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
            return json.dumps({"error": f"Unknown operation '{operation}'. Valid: edit, append"})
        return json.dumps(result, indent=2)
    except (ValueError, FileNotFoundError) as e:
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# brain_action — vault-wide/destructive ops, gated by approval
# ---------------------------------------------------------------------------

@mcp.tool()
def brain_action(action: str, params: dict | None = None) -> str:
    """Perform vault-wide actions. Mutations — may modify multiple files.

    Actions:
      compile              — recompile the router from source files
      build_index          — rebuild the BM25 retrieval index
      rename               — rename/move a file (params: {source, dest} as relative paths)
      delete               — delete a file and clean wikilinks (params: {path})
      convert              — convert artefact to different type (params: {path, target_type})
      shape-presentation   — create presentation + launch live preview (params: {source, slug})
      upgrade              — upgrade brain-core from source (params: {source}, optional: {dry_run, force})
      register_workspace   — register a linked workspace (params: {slug, path})
      unregister_workspace — remove a linked workspace registration (params: {slug})
    """
    global _router, _index, _workspace_registry

    _check_and_reload()

    if _vault_root is None:
        return "Error: server not initialized"

    if action == "compile":
        _router = _compile_and_save(_vault_root)
        art_count = len(_router["artefacts"])
        configured = sum(1 for a in _router["artefacts"] if a["configured"])
        trigger_count = len(_router["triggers"])
        skill_count = len(_router["skills"])
        memory_count = len(_router.get("memories", []))
        living_count = sum(1 for a in _router["artefacts"] if a["classification"] == "living")
        temporal_count = sum(1 for a in _router["artefacts"] if a["classification"] == "temporal")
        return json.dumps({
            "status": "ok",
            "summary": f"Compiled: {art_count} artefacts ({configured} configured), "
                       f"{trigger_count} triggers, {skill_count} skills, "
                       f"{memory_count} memories, "
                       f"{living_count + temporal_count} colours",
            "compiled_at": _router["meta"]["compiled_at"],
        }, indent=2)

    elif action == "build_index":
        _index = _build_index_and_save(_vault_root)
        doc_count = _index["meta"]["document_count"]
        term_count = len(_index["corpus_stats"]["df"])
        return json.dumps({
            "status": "ok",
            "summary": f"Built index: {doc_count} documents, {term_count} unique terms",
            "built_at": _index["meta"]["built_at"],
        }, indent=2)

    elif action == "rename":
        if not params or "source" not in params or "dest" not in params:
            return json.dumps({"error": "rename requires params: {source, dest} (relative paths)"})

        source = params["source"]
        dest = params["dest"]

        # CLI-first: Obsidian auto-updates wikilinks
        if _cli_available and _vault_name:
            result = obsidian_cli.move(_vault_name, source, dest)
            if result is not None:
                return json.dumps({
                    "status": "ok",
                    "method": "obsidian_cli",
                    "links_updated": result.get("links_updated", -1),
                }, indent=2)

        # Fallback: grep-and-replace wikilinks + os.rename
        try:
            links_updated = rename.rename_and_update_links(_vault_root, source, dest)
            return json.dumps({
                "status": "ok",
                "method": "grep_replace",
                "links_updated": links_updated,
            }, indent=2)
        except FileNotFoundError as e:
            return json.dumps({"error": str(e)})

    elif action == "delete":
        if not params or "path" not in params:
            return json.dumps({"error": "delete requires params: {path} (relative path)"})
        try:
            links_replaced = rename.delete_and_clean_links(_vault_root, params["path"])
            return json.dumps({
                "status": "ok",
                "path": params["path"],
                "links_replaced": links_replaced,
            }, indent=2)
        except FileNotFoundError as e:
            return json.dumps({"error": str(e)})

    elif action == "convert":
        if not params or "path" not in params or "target_type" not in params:
            return json.dumps({"error": "convert requires params: {path, target_type}"})
        try:
            result = edit.convert_artefact(
                _vault_root, _router, params["path"], params["target_type"]
            )
            return json.dumps({
                "status": "ok",
                "old_path": result["old_path"],
                "new_path": result["new_path"],
                "type": result["type"],
                "links_updated": result["links_updated"],
            }, indent=2)
        except (ValueError, FileNotFoundError) as e:
            return json.dumps({"error": str(e)})

    elif action == "shape-presentation":
        result = shape_presentation.shape(_vault_root, params)
        return json.dumps(result, indent=2)

    elif action == "register_workspace":
        if not params or "slug" not in params or "path" not in params:
            return json.dumps({"error": "register_workspace requires params: {slug, path}"})
        try:
            result = workspace_registry.register_workspace(
                _vault_root, params["slug"], params["path"],
            )
            _workspace_registry = workspace_registry.load_registry(_vault_root)
            return json.dumps(result, indent=2)
        except ValueError as e:
            return json.dumps({"error": str(e)})

    elif action == "unregister_workspace":
        if not params or "slug" not in params:
            return json.dumps({"error": "unregister_workspace requires params: {slug}"})
        try:
            result = workspace_registry.unregister_workspace(
                _vault_root, params["slug"],
            )
            _workspace_registry = workspace_registry.load_registry(_vault_root)
            return json.dumps(result, indent=2)
        except ValueError as e:
            return json.dumps({"error": str(e)})

    elif action == "upgrade":
        if not params or "source" not in params:
            return json.dumps({"error": "upgrade requires params: {source} (path to source brain-core dir)"})
        source = params["source"]
        dry_run = params.get("dry_run", False)
        force = params.get("force", False)
        result = upgrade.upgrade(_vault_root, source, force=force, dry_run=dry_run)
        if result["status"] == "ok" and not dry_run:
            _check_and_reload()
            _router = _compile_and_save(_vault_root)
            _index = _build_index_and_save(_vault_root)
            result["post_upgrade"] = "Reloaded modules, recompiled router, rebuilt index."
        return json.dumps(result, indent=2)

    else:
        valid = ["compile", "build_index", "rename", "delete", "convert",
                 "shape-presentation", "upgrade", "register_workspace", "unregister_workspace"]
        return json.dumps({"error": f"Unknown action '{action}'. Valid: {', '.join(valid)}"})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    startup()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
