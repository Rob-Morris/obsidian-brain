from __future__ import annotations

import os
from typing import Literal

from _common import is_archived_path, resolve_body_file
import create
import edit

from _server_runtime import ServerRuntime


def handle_brain_create(
    type: str,
    title: str,
    body: str,
    body_file: str,
    frontmatter: dict | None,
    parent: str | None,
    resource: str,
    name: str,
    runtime: ServerRuntime,
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
        )
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
    return label


def handle_brain_edit(
    operation: Literal["edit", "append", "prepend", "delete_section"],
    path: str,
    body: str,
    body_file: str,
    frontmatter: dict | None,
    target: str | None,
    resource: str,
    name: str,
    runtime: ServerRuntime,
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

    if operation == "delete_section":
        if not target:
            return runtime.fmt_error("delete_section requires a target heading.")
    elif not body and not frontmatter and not target:
        return runtime.fmt_error(
            f"{operation} with no body and no frontmatter changes is a no-op. "
            "Pass body content, frontmatter changes, or both."
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
    )

    moved = result["path"] != result["resolved_path"]
    if moved:
        runtime.mark_index_dirty()
    else:
        runtime.mark_index_pending(result["path"])
    if cleanup_path:
        try:
            os.remove(cleanup_path)
        except OSError:
            pass
    past = edit.OPERATION_LABELS[result["operation"]]
    msg = f"**{past}:** {result['path']}"
    if moved:
        msg += (
            f"\n**Moved:** {result['resolved_path']} → {result['path']} "
            "(terminal status)"
        )
    if target:
        msg += f" (target: {target})"
        prev_h, next_h = runtime.surrounding_headings(state.vault_root, result["path"], target)
        if prev_h or next_h:
            prev_label = prev_h or "(start)"
            next_label = next_h or "(end)"
            msg += f"\n**Context:** prev={prev_label} | next={next_label}"
    return msg
