from __future__ import annotations

from _common import (
    cleanup_temp_body_file,
    is_archived_path,
    resolve_body_file,
    temp_body_file_cleanup_path,
)
import create
import edit

from ._server_runtime import ServerRuntime

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


def handle_brain_create(
    resource: str,
    params: dict,
    cleanup_path: str | None,
    runtime: ServerRuntime,
):
    """Execute a validated brain_create request.

    *params* is the pre-validated dict from _build_brain_create_params — only
    the fields accepted by the resource's Spec are present.
    """
    runtime.check_version_drift()

    denied = runtime.enforce_profile("brain_create")
    if denied:
        return denied

    runtime.ensure_warmup_started("brain_create")

    state = runtime.get_state()
    if state.router is None or state.vault_root is None:
        return runtime.fmt_progress("brain_create", ("router",))

    runtime.ensure_router_fresh()
    state = runtime.get_state()

    body = params.get("body") or ""
    body_file = params.get("body_file") or ""
    frontmatter = params.get("frontmatter")

    cleanup_path = cleanup_path or temp_body_file_cleanup_path(body_file)
    try:
        body, cleanup_path = resolve_body_file(
            body,
            body_file,
            vault_root=state.vault_root,
            cleanup_path=cleanup_path,
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
                fix_links=bool(params.get("fix_links")),
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
            )
            label = f"**Created** {result['resource']}: {result['path']}"

        fixes = format_wikilink_fixes(result.get("wikilink_fixes"))
        if fixes:
            label = f"{label}\n{fixes}"
        warnings = format_wikilink_warnings(result.get("wikilink_warnings"))
        if warnings:
            label = f"{label}\n{warnings}"
        return label
    finally:
        cleanup_temp_body_file(cleanup_path)


def handle_brain_edit(
    resource: str,
    operation: str,
    params: dict,
    cleanup_path: str | None,
    runtime: ServerRuntime,
):
    """Execute a validated brain_edit request.

    *params* is the pre-validated dict from _build_brain_edit_params — only
    the fields accepted by the (resource, operation) Spec are present.
    """
    runtime.check_version_drift()

    denied = runtime.enforce_profile("brain_edit")
    if denied:
        return denied

    runtime.ensure_warmup_started("brain_edit")

    state = runtime.get_state()
    if state.router is None or state.vault_root is None:
        return runtime.fmt_progress("brain_edit", ("router",))

    runtime.ensure_router_fresh()
    state = runtime.get_state()

    path = params.get("path") or ""
    body = params.get("body") or ""
    body_file = params.get("body_file") or ""
    frontmatter = params.get("frontmatter")
    target = params.get("target")
    selector = params.get("selector")
    scope = params.get("scope")
    name = params.get("name") or ""
    fix_links = bool(params.get("fix_links"))

    cleanup_path = cleanup_path or temp_body_file_cleanup_path(body_file)
    try:
        if resource == "artefact" and path and is_archived_path(path):
            return runtime.fmt_error(
                f"'{path}' is archived. "
                "Use brain_move(op='unarchive', path='...') to restore it first."
            )

        # preflight_request_contract is the inter-field rule layer (stays here,
        # after spec presence validation has already passed).
        try:
            edit.preflight_request_contract(
                operation,
                has_body=bool(body or body_file),
                frontmatter_changes=frontmatter,
                target=target,
                selector=selector,
                scope=scope,
            )
        except edit.ScopeValidationError as e:
            return runtime.fmt_error(e.detailed_message())

        body, cleanup_path = resolve_body_file(
            body,
            body_file,
            vault_root=state.vault_root,
            cleanup_path=cleanup_path,
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
            msg += (
                f"\n**Moved:** {result['resolved_path']} → {result['path']} "
                "(terminal status)"
            )
        if structural:
            msg += f" ({structural['display']})"
        if fixes:
            msg += f"\n{fixes}"
        if warnings:
            msg += f"\n{warnings}"
        return msg
    finally:
        cleanup_temp_body_file(cleanup_path)
