from __future__ import annotations

import os
from typing import Literal

from _common import is_archived_path, resolve_body_file
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
    type: str,
    title: str,
    body: str,
    body_file: str,
    frontmatter: dict | None,
    parent: str | None,
    key: str | None,
    resource: str,
    name: str,
    runtime: ServerRuntime,
    fix_links: bool = False,
):
    runtime.check_version_drift()
    runtime.ensure_router_fresh()

    denied = runtime.enforce_profile("brain_create")
    if denied:
        return denied

    state = runtime.get_state()
    if state.router is None or state.vault_root is None:
        return runtime.fmt_error("server not initialized")

    body, cleanup_path = resolve_body_file(body, body_file, vault_root=state.vault_root)

    if resource == "artefact":
        if not type:
            return runtime.fmt_error("type is required when resource='artefact'")
        if not title:
            return runtime.fmt_error("title is required when resource='artefact'")
        result = create.create_artefact(
            state.vault_root,
            state.router,
            type,
            title,
            body=body,
            frontmatter_overrides=frontmatter,
            parent=parent,
            key=key,
            fix_links=fix_links,
        )
        runtime.mark_router_dirty()
        runtime.mark_index_pending(result["path"], type_hint=result["type"])
        label = f"**Created** {result['type']}: {result['path']}"
    else:
        result = create.create_resource(
            state.vault_root,
            state.router,
            resource=resource,
            name=name,
            body=body,
            frontmatter=frontmatter,
        )
        label = f"**Created** {result['resource']}: {result['path']}"

    if cleanup_path:
        try:
            os.remove(cleanup_path)
        except OSError:
            pass
    fixes = format_wikilink_fixes(result.get("wikilink_fixes"))
    if fixes:
        label = f"{label}\n{fixes}"
    warnings = format_wikilink_warnings(result.get("wikilink_warnings"))
    if warnings:
        label = f"{label}\n{warnings}"
    return label


def handle_brain_edit(
    operation: Literal["edit", "append", "prepend", "delete_section"],
    path: str,
    body: str,
    body_file: str,
    frontmatter: dict | None,
    target: str | None,
    selector: dict | None,
    scope: str | None,
    resource: str,
    name: str,
    runtime: ServerRuntime,
    fix_links: bool = False,
):
    runtime.check_version_drift()
    runtime.ensure_router_fresh()

    denied = runtime.enforce_profile("brain_edit")
    if denied:
        return denied

    state = runtime.get_state()
    if state.router is None or state.vault_root is None:
        return runtime.fmt_error("server not initialized")

    body, cleanup_path = resolve_body_file(body, body_file, vault_root=state.vault_root)

    if resource == "artefact" and path and is_archived_path(path):
        return runtime.fmt_error(
            f"'{path}' is archived. "
            "Use brain_action('unarchive') to restore it first."
        )

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
    moved = result["path"] != result["resolved_path"]
    if resource == "artefact":
        runtime.mark_router_dirty()
        if moved:
            runtime.mark_index_dirty()
        else:
            runtime.mark_index_pending(result["path"])
    elif resource == "memory":
        runtime.mark_router_dirty()
    if cleanup_path:
        try:
            os.remove(cleanup_path)
        except OSError:
            pass
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
