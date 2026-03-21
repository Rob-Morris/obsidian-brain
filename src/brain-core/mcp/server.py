#!/usr/bin/env python3
"""
Brain MCP Server — exposes brain-core tools via the Model Context Protocol.

Wraps compile_router, build_index, and search_index as 3 MCP tools:
  brain_read   — read compiled router resources (safe, no side effects)
  brain_search — BM25 keyword search, with optional Obsidian CLI live search
  brain_action — mutations: compile router, rebuild index, rename files

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
import re
import sys
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Script imports — add scripts dir to sys.path
# ---------------------------------------------------------------------------

SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, os.path.abspath(SCRIPTS_DIR))

from compile_router import (
    compile as compile_router,
    find_vault_root,
    is_system_dir,
    scan_living_types,
    scan_temporal_types,
    OUTPUT_PATH as COMPILED_ROUTER_REL,
)
from build_index import (
    build_index,
    find_md_files,
    OUTPUT_PATH as RETRIEVAL_INDEX_REL,
)
from search_index import search as search_index
from check import run_checks

import obsidian_cli

# ---------------------------------------------------------------------------
# Server state
# ---------------------------------------------------------------------------

mcp = FastMCP(name="brain")

_vault_root: str | None = None
_router: dict | None = None
_index: dict | None = None
_cli_available: bool = False
_vault_name: str | None = None


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
    all_types = scan_living_types(vault_root) + scan_temporal_types(vault_root)
    for type_info in all_types:
        for rel_path in find_md_files(vault_root, type_info):
            try:
                if os.path.getmtime(os.path.join(vault_root, rel_path)) > threshold:
                    return True
            except OSError:
                continue
    return False


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
    """Compile router, write to disk, return compiled data."""
    compiled = compile_router(vault_root)
    _save_json(compiled, vault_root, COMPILED_ROUTER_REL)
    return compiled


def _build_index_and_save(vault_root: str) -> dict:
    """Build retrieval index, write to disk, return index data."""
    index = build_index(vault_root)
    _save_json(index, vault_root, RETRIEVAL_INDEX_REL)
    return index


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

def startup(vault_root: str | None = None) -> None:
    """Initialize server state: find vault, compile/build if stale, load data."""
    global _vault_root, _router, _index, _cli_available, _vault_name

    if vault_root is None:
        vault_root = os.environ.get("BRAIN_VAULT_ROOT")
    if vault_root is None:
        _vault_root = str(find_vault_root())
    else:
        _vault_root = str(vault_root)

    # Auto-compile router if stale (reuse parsed data when fresh)
    stale, data = _check_router(_vault_root)
    _router = _compile_and_save(_vault_root) if stale else data

    # Auto-build index if stale
    stale, data = _check_index(_vault_root)
    _index = _build_index_and_save(_vault_root) if stale else data

    # Probe Obsidian CLI availability
    _cli_available = obsidian_cli.check_available()
    _vault_name = os.environ.get("BRAIN_VAULT_NAME") or os.path.basename(_vault_root)


# ---------------------------------------------------------------------------
# brain_read — safe, no side effects
# ---------------------------------------------------------------------------

def _read_file_content(vault_root: str, rel_path: str) -> str:
    """Read a file's content given a relative path from vault root."""
    abs_path = os.path.join(vault_root, rel_path)
    # Resolve wikilink-style paths (no extension → try .md)
    if not os.path.isfile(abs_path) and not rel_path.endswith(".md"):
        abs_path += ".md"
    if not os.path.isfile(abs_path):
        return f"Error: file not found: {rel_path}"
    with open(abs_path, "r", encoding="utf-8") as f:
        return f.read()


def _read_named_resource(resource: str, name: str | None,
                         router_key: str, doc_field: str) -> str:
    """List items or read a specific item's file content by name."""
    items = _router[router_key]
    if name:
        match = next((i for i in items if i["name"] == name), None)
        if not match:
            return json.dumps({"error": f"No {resource} matching '{name}'"})
        return _read_file_content(_vault_root, match[doc_field])
    return json.dumps(items, indent=2)


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
      environment — runtime environment info
      router      — always-rules and metadata
      compliance  — run structural compliance checks (name = severity filter: error/warning/info)
    """
    if _router is None:
        return "Error: server not initialized"

    if resource == "artefact":
        artefacts = _router["artefacts"]
        if name:
            matches = [a for a in artefacts if a["key"] == name or a["type"] == name]
            if not matches:
                return json.dumps({"error": f"No artefact matching '{name}'"})
            return json.dumps(matches, indent=2)
        return json.dumps(artefacts, indent=2)

    elif resource == "trigger":
        return json.dumps(_router["triggers"], indent=2)

    elif resource == "style":
        return _read_named_resource("style", name, "styles", "style_doc")

    elif resource == "template":
        if not name:
            return json.dumps({"error": "template resource requires a name parameter (artefact type key)"})
        artefacts = _router["artefacts"]
        match = next((a for a in artefacts if a["key"] == name or a["type"] == name), None)
        if not match:
            return json.dumps({"error": f"No artefact matching '{name}'"})
        if not match.get("template_file"):
            return json.dumps({"error": f"Artefact '{name}' has no template file"})
        return _read_file_content(_vault_root, match["template_file"])

    elif resource == "skill":
        return _read_named_resource("skill", name, "skills", "skill_doc")

    elif resource == "plugin":
        return _read_named_resource("plugin", name, "plugins", "skill_doc")

    elif resource == "environment":
        env = dict(_router["environment"])
        env["obsidian_cli_available"] = _cli_available
        return json.dumps(env, indent=2)

    elif resource == "router":
        return json.dumps({
            "always_rules": _router["always_rules"],
            "meta": _router["meta"],
        }, indent=2)

    elif resource == "compliance":
        result = run_checks(str(_vault_root), _router)
        if name:  # name parameter doubles as severity filter
            result["findings"] = [f for f in result["findings"] if f["severity"] == name]
            result["summary"] = {
                "errors": sum(1 for f in result["findings"] if f["severity"] == "error"),
                "warnings": sum(1 for f in result["findings"] if f["severity"] == "warning"),
                "info": sum(1 for f in result["findings"] if f["severity"] == "info"),
            }
        return json.dumps(result, indent=2, ensure_ascii=False)

    else:
        valid = ["artefact", "trigger", "style", "template", "skill", "plugin", "environment", "router", "compliance"]
        return json.dumps({"error": f"Unknown resource '{resource}'. Valid: {', '.join(valid)}"})


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
    if _index is None:
        return "Error: server not initialized"

    # CLI-first: Obsidian's live index is always current
    if _cli_available and _vault_name and query:
        cli_results = obsidian_cli.search(_vault_name, query)
        if cli_results is not None:
            results = _transform_cli_results(cli_results, type, tag, status, top_k)
            return json.dumps({"source": "obsidian_cli", "results": results}, indent=2)

    # BM25 fallback
    results = search_index(_index, query, _vault_root, type_filter=type, tag_filter=tag,
                           status_filter=status, top_k=top_k)
    return json.dumps({"source": "bm25", "results": results}, indent=2)


# ---------------------------------------------------------------------------
# brain_action — mutations, gated by approval
# ---------------------------------------------------------------------------

def _rename_grep_replace(vault_root: str, source: str, dest: str) -> int:
    """Rename a file and update wikilinks via grep-and-replace. Returns links updated."""
    abs_source = os.path.join(vault_root, source)
    abs_dest = os.path.join(vault_root, dest)

    if not os.path.isfile(abs_source):
        raise FileNotFoundError(f"Source file not found: {source}")

    # Derive wikilink stems (without .md extension)
    def stem(path: str) -> str:
        return path[:-3] if path.endswith(".md") else path

    old_stem = stem(source)
    new_stem = stem(dest)

    # Find and replace wikilinks in all .md files
    links_updated = 0
    wikilink_pattern = re.compile(
        r'\[\['
        + re.escape(old_stem)
        + r'(\|[^\]]*)?'  # optional alias
        + r'\]\]'
    )

    for dirpath, _dirnames, filenames in os.walk(vault_root):
        # Skip system directories
        rel_dir = os.path.relpath(dirpath, vault_root)
        if rel_dir != "." and is_system_dir(os.path.basename(dirpath)):
            if not rel_dir.startswith("_Temporal"):
                continue

        for fname in filenames:
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(dirpath, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read()
            except OSError:
                continue

            new_content, count = wikilink_pattern.subn(
                lambda m: f"[[{new_stem}{m.group(1) or ''}]]", content
            )
            if count > 0:
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(new_content)
                links_updated += count

    # Create destination directory if needed and rename the file
    os.makedirs(os.path.dirname(abs_dest), exist_ok=True)
    os.rename(abs_source, abs_dest)

    return links_updated


@mcp.tool()
def brain_action(action: str, params: dict | None = None) -> str:
    """Perform vault actions. Mutations — may modify files.

    Actions:
      compile     — recompile the router from source files
      build_index — rebuild the BM25 retrieval index
      rename      — rename/move a file (params: source, dest as relative paths)
    """
    global _router, _index

    if _vault_root is None:
        return "Error: server not initialized"

    if action == "compile":
        _router = _compile_and_save(_vault_root)
        art_count = len(_router["artefacts"])
        configured = sum(1 for a in _router["artefacts"] if a["configured"])
        trigger_count = len(_router["triggers"])
        skill_count = len(_router["skills"])
        return json.dumps({
            "status": "ok",
            "summary": f"Compiled: {art_count} artefacts ({configured} configured), "
                       f"{trigger_count} triggers, {skill_count} skills",
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
            links_updated = _rename_grep_replace(_vault_root, source, dest)
            return json.dumps({
                "status": "ok",
                "method": "grep_replace",
                "links_updated": links_updated,
            }, indent=2)
        except FileNotFoundError as e:
            return json.dumps({"error": str(e)})

    else:
        valid = ["compile", "build_index", "rename"]
        return json.dumps({"error": f"Unknown action '{action}'. Valid: {', '.join(valid)}"})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    startup()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
