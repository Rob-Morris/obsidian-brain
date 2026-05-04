from __future__ import annotations

import json
import os
from typing import Literal

from mcp.types import TextContent

import _semantic.runtime as _semantic
import _common
from _common import is_archived_path
import build_index
import list_artefacts
import obsidian_cli
import read as read_mod
import search_index
import workspace_registry

from ._server_runtime import ServerRuntime


def _fmt_environment(env):
    return "\n".join(f"{k}={v}" for k, v in env.items())


def _fmt_workspace_list(workspaces):
    lines = []
    for ws in workspaces:
        status = ws.get("status", "")
        status_part = f"\t[{status}]" if status else ""
        lines.append(f"{ws['slug']}\t{ws['mode']}\t{ws['path']}{status_part}")
    return "\n".join(lines)


def _fmt_workspace_single(ws):
    return f"{ws['slug']}\t{ws['mode']}\t{ws['path']}"


def _read_formatter(resource: str, result):
    if resource == "environment":
        return _fmt_environment(result)
    return json.dumps(result, indent=2, ensure_ascii=False)


def _reload_workspace_registry(runtime: ServerRuntime) -> dict:
    state = runtime.get_state()
    if state.vault_root is None:
        return {}
    registry = workspace_registry.load_registry(state.vault_root)
    runtime.set_workspace_registry(registry)
    return registry


def _transform_cli_results(
    cli_results: list[str],
    type_filter: str | None,
    tag_filter: str | None,
    status_filter: str | None,
    top_k: int,
    index: dict | None,
) -> list[dict]:
    index_by_path = {}
    if index:
        index_by_path = {
            doc["path"]: doc for doc in index.get("documents", []) if "path" in doc
        }
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

        transformed.append(
            {
                "path": path,
                "title": doc_meta.get(
                    "title",
                    os.path.splitext(os.path.basename(path))[0],
                ),
                "type": doc_type,
                "status": doc_status,
            }
        )

    return transformed[:top_k]


def _fmt_search(source, results):
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
    type_part = f" (type: {type_filter})" if type_filter else ""
    meta = f"**Listed:** {len(results)} results{type_part}"
    if not results:
        return [TextContent(type="text", text=meta)]
    lines = []
    for r in results:
        status_part = f"\t{r['status']}" if r.get("status") else ""
        extras = []
        if r.get("key"):
            extras.append(f"key={r['key']}")
        if r.get("parent"):
            extras.append(f"parent={r['parent']}")
        if "children_count" in r:
            extras.append(f"children={r['children_count']}")
        extras_part = f"\t{', '.join(extras)}" if extras else ""
        lines.append(
            f"{r['date']}\t{r['title']}\t{r['path']}\t{r['type']}{status_part}{extras_part}"
        )
    return [
        TextContent(type="text", text=meta),
        TextContent(type="text", text="\n".join(lines)),
    ]


def handle_brain_read(
    resource: str,
    params: dict,
    runtime: ServerRuntime,
):
    """Handle a validated brain_read request.

    Args:
        resource: The resource discriminator (already validated by _build_brain_read_params).
        params:   Validated field dict from _build_brain_read_params (absent fields omitted).
        runtime:  Server runtime instance.
    """
    runtime.check_version_drift()

    denied = runtime.enforce_profile("brain_read")
    if denied:
        return denied

    runtime.ensure_router_fresh()

    state = runtime.get_state()
    if state.router is None:
        return runtime.fmt_error("server not initialized")

    name = params.get("name")

    if resource == "workspace":
        try:
            registry = _reload_workspace_registry(runtime)
            result = workspace_registry.resolve_workspace(
                state.vault_root,
                name,
                registry=registry,
            )
            return _fmt_workspace_single(result)
        except ValueError as e:
            return runtime.fmt_error(str(e))

    result = read_mod.read_resource(state.router, state.vault_root, resource, name)

    if isinstance(result, str):
        return result

    if isinstance(result, dict):
        if "error" in result:
            return runtime.fmt_error(result["error"])
        if resource == "environment":
            runtime.refresh_cli_available()
            state = runtime.get_state()
            result["obsidian_cli_available"] = state.cli_available
            result["has_config"] = state.config is not None
            result["active_profile"] = state.session_profile

    return _read_formatter(resource, result)


def handle_brain_search(
    query: str,
    resource: Literal[
        "artefact",
        "skill",
        "trigger",
        "style",
        "memory",
        "plugin",
    ],
    type: str | None,
    tag: str | None,
    status: str | None,
    mode: Literal["lexical", "semantic", "hybrid"] | None,
    top_k: int,
    runtime: ServerRuntime,
):
    runtime.check_version_drift()

    denied = runtime.enforce_profile("brain_search")
    if denied:
        return denied

    runtime.ensure_router_fresh()

    state = runtime.get_state()
    if state.router is None:
        return runtime.fmt_error("server not initialized")

    if resource != "artefact":
        if mode not in (None, "lexical"):
            return runtime.fmt_error(
                "brain_search mode applies only to artefact search; "
                "non-artefact resources support lexical text matching only"
            )
        results = search_index.search_resource(
            state.router,
            state.vault_root,
            resource,
            query,
            top_k=top_k,
        )
        return _fmt_search("text", results)

    runtime.ensure_index_fresh()
    state = runtime.get_state()
    if state.index is None:
        return runtime.fmt_error("server not initialized")

    type_filter = type
    if type_filter and state.router:
        art = _common.match_artefact(state.router.get("artefacts", []), type_filter)
        if art:
            type_filter = art["frontmatter_type"]

    try:
        resolved_mode = search_index.resolve_search_mode(
            state.vault_root,
            mode,
            config=state.config,
            doc_embeddings=state.doc_embeddings,
            embeddings_meta=state.embeddings_meta,
        )
    except search_index.SearchModeUnavailableError as e:
        return runtime.fmt_error(str(e))

    if resolved_mode in {"semantic", "hybrid"}:
        runtime.ensure_embeddings_fresh()
        state = runtime.get_state()
        if not _semantic.semantic_engine_available(
            state.vault_root,
            config=state.config,
            doc_embeddings=state.doc_embeddings,
            embeddings_meta=state.embeddings_meta,
        ):
            return runtime.fmt_error(
                "semantic retrieval is unavailable: embeddings sidecars are "
                "missing or dependencies are not installed"
            )

    if resolved_mode == "lexical":
        runtime.refresh_cli_available()
        state = runtime.get_state()
        if state.cli_available and state.vault_name and query:
            cli_results = obsidian_cli.search(state.vault_name, query)
            if cli_results is not None:
                results = _transform_cli_results(
                    cli_results,
                    type_filter,
                    tag,
                    status,
                    top_k,
                    state.index,
                )
                return _fmt_search("obsidian_cli", results)

        results = search_index.search(
            state.index,
            query,
            state.vault_root,
            type_filter=type_filter,
            tag_filter=tag,
            status_filter=status,
            top_k=top_k,
        )
        return _fmt_search("bm25", results)

    try:
        if resolved_mode == "semantic":
            results = search_index.search_semantic(
                query,
                state.vault_root,
                type_filter=type_filter,
                tag_filter=tag,
                status_filter=status,
                top_k=top_k,
                doc_embeddings=state.doc_embeddings,
                embeddings_meta=state.embeddings_meta,
            )
            return _fmt_search("semantic", results)

        results = search_index.search_hybrid(
            state.index,
            query,
            state.vault_root,
            type_filter=type_filter,
            tag_filter=tag,
            status_filter=status,
            top_k=top_k,
            doc_embeddings=state.doc_embeddings,
            embeddings_meta=state.embeddings_meta,
        )
        return _fmt_search("hybrid", results)
    except search_index.SearchModeUnavailableError as e:
        return runtime.fmt_error(str(e))


def handle_brain_list(
    resource: str,
    params: dict,
    runtime: ServerRuntime,
):
    """Handle a validated brain_list request.

    Args:
        resource: The resource discriminator (already validated by _build_brain_list_params).
        params:   Validated field dict from _build_brain_list_params (absent fields omitted).
        runtime:  Server runtime instance.
    """
    runtime.check_version_drift()

    denied = runtime.enforce_profile("brain_list")
    if denied:
        return denied

    runtime.ensure_router_fresh()

    state = runtime.get_state()
    if state.router is None:
        return runtime.fmt_error("server not initialized")

    query = params.get("query")
    type_filter = params.get("type")
    parent = params.get("parent")
    since = params.get("since")
    until = params.get("until")
    tag = params.get("tag")
    top_k = params.get("top_k", 500)
    sort = params.get("sort", "date_desc")

    if resource == "workspace":
        registry = _reload_workspace_registry(runtime)
        results = workspace_registry.list_workspaces(
            state.vault_root,
            registry=registry,
        )
        return _fmt_workspace_list(results)

    if resource == "artefact":
        runtime.ensure_index_fresh()
        state = runtime.get_state()
        if state.index is None:
            return runtime.fmt_error("server not initialized")

    results = list_artefacts.list_resources(
        state.index,
        state.router,
        state.vault_root,
        resource=resource,
        query=query,
        type_filter=type_filter,
        parent=parent,
        since=since,
        until=until,
        tag=tag,
        top_k=top_k,
        sort=sort,
    )

    if resource == "artefact":
        return _fmt_list(results, type_filter)

    meta = f"**Listed:** {len(results)} {resource}(s)"
    if query:
        meta += f" matching '{query}'"
    if not results:
        return [TextContent(type="text", text=meta)]
    lines = []
    for r in results:
        if isinstance(r, dict):
            lines.append(r.get("name", r.get("path", str(r))))
        else:
            lines.append(str(r))
    return [
        TextContent(type="text", text=meta),
        TextContent(type="text", text="\n".join(lines)),
    ]
