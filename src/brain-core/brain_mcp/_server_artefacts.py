from __future__ import annotations

from collections.abc import Callable

from mcp.types import CallToolResult, TextContent

from _common import (
    build_vault_file_index,
    is_archived_path,
    ParentChainError,
    PartialApplyError,
    parent_chain_error_message,
)
import create
import edit
from _staging import finalise_staged_body, resolve_mutation_body

from . import _server_readiness
from ._server_runtime import ServerRuntime


def prepare_fix_links_file_index(tool_name, fix_links, runtime):
    """Prepare a lazy authoritative wikilink index for mutation-time fixes.

    MCP create/edit used to pass the retrieval index's document list through to
    the script layer. That is cheap, but it can be stale for externally-created
    files and therefore unsafe for deciding whether to warn or rewrite links.
    The returned factory is created inside the mutation boundary and invoked
    by the script layer only after the changed file is known to contain a
    wikilink. This keeps the eventual filesystem scan inside the same exclusive
    cross-process snapshot as the mutation without penalising link-free files.
    """
    if not fix_links:
        return None, None

    state, progress = _server_readiness.require_router(runtime, tool_name)
    if progress is not None:
        return None, progress
    if state.vault_root is None:
        return None, runtime.fmt_progress(tool_name, ("router",))
    return lambda: build_vault_file_index(state.vault_root), None


def require_prepared_fix_links_index(tool_name, fix_links, file_index):
    """Enforce the MCP handler contract for mutation-time link fixing."""
    if fix_links and file_index is None:
        raise ValueError(
            f"{tool_name} fix_links requires a prepared filesystem wikilink index"
        )


def format_wikilink_fixes(fixes):
    """Format applied-fix summary into a markdown block.

    ``fixes`` is the ``wikilink_fixes`` dict attached by create/edit scripts.
    Returns an empty string when no fixes were applied.
    """
    if not fixes:
        return ""
    entries = fixes.get("fixes") or []
    if not entries:
        return ""
    lines = [f"✔ Wikilink fixes applied ({fixes.get('applied', 0)}):"]
    for item in entries:
        lines.append(f"  [[{item['target']}]] → [[{item['resolved_to']}]]")
    return "\n".join(lines)


def format_wikilink_warnings(findings):
    """Format wikilink check findings into markdown warning lines.

    Groups findings by status and emits one block per category. Returns an
    empty string when *findings* is empty or None, so callers can concatenate
    unconditionally.

    Output shape:
        ⚠ Broken wikilinks: [[Helix]], [[Skogarmaor]]
        ⚠ Resolvable wikilinks (use fix-links to fix all or selected):
          [[old stem]] → [[new stem]]
        ⚠ Ambiguous wikilinks: [[dup]] matches 2 files
    """
    if not findings:
        return ""

    broken = [f for f in findings if f["status"] == "broken"]
    resolvable = [f for f in findings if f["status"] == "resolvable"]
    ambiguous = [f for f in findings if f["status"] == "ambiguous"]

    lines = []
    if broken:
        stems = ", ".join(f"[[{f['stem']}]]" for f in broken)
        lines.append(f"⚠ Broken wikilinks: {stems}")
    if resolvable:
        lines.append(
            "⚠ Resolvable wikilinks (use fix-links to fix all or selected):"
        )
        for f in resolvable:
            lines.append(f"  [[{f['stem']}]] → [[{f['resolved_to']}]]")
    if ambiguous:
        for f in ambiguous:
            n = len(f.get("candidates") or [])
            lines.append(
                f"⚠ Ambiguous wikilinks: [[{f['stem']}]] matches {n} files"
            )

    return "\n".join(lines)


def _structured_text_result(text: str, payload: dict) -> CallToolResult:
    """Return concise prose and the script-layer result without dropping fields."""
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structuredContent=payload,
    )


def handle_brain_create(
    resource: str,
    params: dict,
    runtime: ServerRuntime,
    file_index: dict | Callable[[], dict] | None = None,
):
    """Execute a validated brain_create request.

    *params* is the pre-validated dict from _build_brain_create_params — only
    the fields accepted by the resource's Spec are present.
    """
    runtime.check_version_drift()

    denied = runtime.enforce_profile("brain_create")
    if denied:
        return denied

    state, progress = _server_readiness.require_router(runtime, "brain_create")
    if progress is not None:
        return progress
    if state.vault_root is None:
        return runtime.fmt_progress("brain_create", ("router",))

    fix_links = bool(params.get("fix_links"))
    require_prepared_fix_links_index("brain_create", fix_links, file_index)

    body = params.get("body") or ""
    body_file = params.get("body_file") or ""
    body_handle = params.get("body_handle") or ""
    frontmatter = params.get("frontmatter")

    body, staged_handle = resolve_mutation_body(
        state.vault_root,
        body=body,
        body_file=body_file,
        body_handle=body_handle,
    )

    if resource == "artefact":
        result = create.create_artefact(
            state.vault_root,
            state.router,
            params["type"],
            params["title"],
            body=body,
            frontmatter_overrides=frontmatter,
            parent=params.get("parent"),
            key=params.get("key"),
            fix_links=fix_links,
            file_index=file_index,
        )
        runtime.mark_router_dirty()
        runtime.mark_index_pending(result["path"], type_hint=result["type"])
        label = f"**Created** {result['type']}: {result['path']}"
    else:
        result = create.create_resource(
            state.vault_root,
            state.router,
            resource=resource,
            name=params["name"],
            body=body,
            frontmatter=frontmatter,
            file_index=file_index,
        )
        label = f"**Created** {result['resource']}: {result['path']}"

    fixes = format_wikilink_fixes(result.get("wikilink_fixes"))
    if fixes:
        label = f"{label}\n{fixes}"
    warnings = format_wikilink_warnings(result.get("wikilink_warnings"))
    if warnings:
        label = f"{label}\n{warnings}"
    staging_warning = finalise_staged_body(state.vault_root, staged_handle)
    if staging_warning:
        label += f"\nWarning: {staging_warning}"
    payload = dict(result)
    payload.setdefault("resource", resource)
    if staging_warning:
        payload["staging_warning"] = staging_warning
    return _structured_text_result(label, payload)


def handle_brain_edit(
    resource: str,
    operation: str,
    params: dict,
    runtime: ServerRuntime,
    file_index: dict | Callable[[], dict] | None = None,
):
    """Execute a validated brain_edit request.

    *params* is the pre-validated dict from _build_brain_edit_params — only
    the fields accepted by the (resource, operation) Spec are present.
    """
    runtime.check_version_drift()

    denied = runtime.enforce_profile("brain_edit")
    if denied:
        return denied

    state, progress = _server_readiness.require_router(runtime, "brain_edit")
    if progress is not None:
        return progress
    if state.vault_root is None:
        return runtime.fmt_progress("brain_edit", ("router",))

    path = params.get("path") or ""
    body = params.get("body") or ""
    body_file = params.get("body_file") or ""
    body_handle = params.get("body_handle") or ""
    frontmatter = params.get("frontmatter")
    target = params.get("target")
    selector = params.get("selector")
    scope = params.get("scope")
    name = params.get("name") or ""
    fix_links = bool(params.get("fix_links"))
    require_prepared_fix_links_index("brain_edit", fix_links, file_index)

    if resource == "artefact" and path and is_archived_path(path):
        return runtime.fmt_error(
            f"'{path}' is archived. "
            "Use brain_move(op='unarchive', path='...') to restore it first."
        )

    if operation != "replace_text":
        try:
            edit.preflight_request_contract(
                operation,
                has_body=bool(body or body_file or body_handle),
                frontmatter_changes=frontmatter,
                target=target,
                selector=selector,
                scope=scope,
            )
        except edit.ScopeValidationError as e:
            return runtime.fmt_error(e.detailed_message())

    body, staged_handle = resolve_mutation_body(
        state.vault_root,
        body=body,
        body_file=body_file,
        body_handle=body_handle,
    )

    try:
        result = edit.edit_resource(
            state.vault_root,
            state.router,
            resource=resource,
            operation=operation,
            path=path,
            name=name,
            body=body,
            frontmatter_changes=frontmatter,
            target=target,
            selector=selector,
            scope=scope,
            fix_links=fix_links,
            file_index=file_index,
            old_text=params.get("old_text"),
            new_text=params.get("new_text"),
            match_occurrence=params.get("match_occurrence"),
            replace_all=bool(params.get("replace_all")),
        )
    except edit.ScopeValidationError as e:
        return runtime.fmt_error(e.detailed_message())
    moved = result["path"] != result["resolved_path"]
    if resource == "artefact":
        runtime.mark_router_dirty()
        if moved:
            runtime.mark_index_dirty()
        else:
            runtime.mark_index_pending(result["path"])
    elif resource == "memory":
        runtime.mark_router_dirty()
    past = edit.OPERATION_LABELS[result["operation"]]
    structural = result.get("structural_target")
    fixes = format_wikilink_fixes(result.get("wikilink_fixes"))
    warnings = format_wikilink_warnings(result.get("wikilink_warnings"))
    msg = f"**{past}:** {result['path']}"
    if moved:
        move_reason = result.get("move_reason", "lifecycle projection")
        msg += f"\n**Moved:** {result['resolved_path']} → {result['path']} ({move_reason})"
    if structural:
        msg += f" ({structural['display']})"
    if fixes:
        msg += f"\n{fixes}"
    if warnings:
        msg += f"\n{warnings}"
    staging_warning = finalise_staged_body(state.vault_root, staged_handle)
    if staging_warning:
        msg += f"\nWarning: {staging_warning}"
        result["staging_warning"] = staging_warning
    return _structured_text_result(msg, result)


def handle_brain_lifecycle(
    *,
    tool_name: str,
    path: str,
    field: str,
    value,
    runtime: ServerRuntime,
):
    """Run an explicit lifecycle field handler and return its change set."""
    runtime.check_version_drift()
    denied = runtime.enforce_profile(tool_name)
    if denied:
        return denied
    state, progress = _server_readiness.require_router(runtime, tool_name)
    if progress is not None:
        return progress
    try:
        result = edit.update_lifecycle_field(
            state.vault_root, state.router, path, field, value
        )
    except ParentChainError as exc:
        return runtime.fmt_error(parent_chain_error_message(exc))
    except PartialApplyError as exc:
        runtime.mark_router_dirty()
        runtime.mark_index_dirty()
        return runtime.fmt_error(str(exc))
    except (ValueError, FileNotFoundError) as exc:
        return runtime.fmt_error(str(exc))
    runtime.mark_router_dirty()
    if result["path"] != result["resolved_path"]:
        runtime.mark_index_dirty()
    else:
        runtime.mark_index_pending(result["path"])
    summary = (
        f"**Updated {field}:** {result['old_value']!r} → {result['new_value']!r}\n"
        f"**Path:** {result['resolved_path']} → {result['path']}"
    )
    return _structured_text_result(summary, result)
