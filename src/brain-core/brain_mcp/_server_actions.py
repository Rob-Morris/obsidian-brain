from __future__ import annotations

import json
import os

import edit
import fix_links
import migrate_naming
import obsidian_cli
import rename
import shape_presentation
import start_shaping
import sync_definitions
import workspace_registry

from ._server_runtime import ServerRuntime


def _action_compile(runtime: ServerRuntime, params: dict | None):
    del params
    state = runtime.get_state()
    if state.vault_root is None:
        return runtime.fmt_error("server not initialized")
    try:
        router = runtime.compile_and_save(state.vault_root)
        runtime.set_router(router)
        art_count = len(router["artefacts"])
        configured = sum(1 for a in router["artefacts"] if a["configured"])
        trigger_count = len(router["triggers"])
        skill_count = len(router["skills"])
        memory_count = len(router.get("memories", []))
        return (
            f"**Compiled:** {art_count} artefacts ({configured} configured), "
            f"{trigger_count} triggers, {skill_count} skills, "
            f"{memory_count} memories"
        )
    except (ValueError, OSError) as e:
        return runtime.fmt_error(str(e))


def _action_build_index(runtime: ServerRuntime, params: dict | None):
    del params
    state = runtime.get_state()
    if state.vault_root is None:
        return runtime.fmt_error("server not initialized")
    try:
        index = runtime.build_index_and_save(state.vault_root)
        runtime.set_index(index)
        doc_count = index["meta"]["document_count"]
        term_count = len(index["corpus_stats"]["df"])
        return f"**Built index:** {doc_count} documents, {term_count} unique terms"
    except (ValueError, OSError) as e:
        return runtime.fmt_error(str(e))


def _action_rename(runtime: ServerRuntime, params: dict | None):
    state = runtime.get_state()
    if state.vault_root is None:
        return runtime.fmt_error("server not initialized")
    if not params or "source" not in params or "dest" not in params:
        return runtime.fmt_error("rename requires params: {source, dest} (relative paths)")

    source = params["source"]
    dest = params["dest"]

    runtime.refresh_cli_available()
    state = runtime.get_state()
    if state.cli_available and state.vault_name:
        abs_dest = os.path.join(state.vault_root, dest)
        os.makedirs(os.path.dirname(abs_dest), exist_ok=True)

        result = obsidian_cli.move(state.vault_name, source, dest)
        if result is True:
            runtime.mark_index_dirty()
            return f"**Renamed** (obsidian_cli): {source} → {dest} (wikilinks auto-updated)"

    try:
        links_updated = rename.rename_and_update_links(state.vault_root, source, dest)
        runtime.mark_index_dirty()
        return f"**Renamed** (grep_replace): {source} → {dest}, {links_updated} links updated"
    except FileNotFoundError as e:
        return runtime.fmt_error(str(e))


def _action_delete(runtime: ServerRuntime, params: dict | None):
    state = runtime.get_state()
    if state.vault_root is None:
        return runtime.fmt_error("server not initialized")
    if not params or "path" not in params:
        return runtime.fmt_error("delete requires params: {path} (relative path)")
    try:
        links_replaced = rename.delete_and_clean_links(state.vault_root, params["path"])
        runtime.mark_index_dirty()
        return f"**Deleted:** {params['path']}, {links_replaced} links replaced"
    except FileNotFoundError as e:
        return runtime.fmt_error(str(e))


def _action_convert(runtime: ServerRuntime, params: dict | None):
    state = runtime.get_state()
    if state.vault_root is None or state.router is None:
        return runtime.fmt_error("server not initialized")
    if not params or "path" not in params or "target_type" not in params:
        return runtime.fmt_error("convert requires params: {path, target_type}")
    try:
        result = edit.convert_artefact(
            state.vault_root,
            state.router,
            params["path"],
            params["target_type"],
            parent=params.get("parent"),
        )
        runtime.mark_index_dirty()
        return json.dumps(
            {
                "status": "ok",
                "old_path": result["old_path"],
                "new_path": result["new_path"],
                "type": result["type"],
                "links_updated": result["links_updated"],
            },
            indent=2,
        )
    except (ValueError, FileNotFoundError) as e:
        return runtime.fmt_error(str(e))


def _action_shape_presentation(runtime: ServerRuntime, params: dict | None):
    state = runtime.get_state()
    if state.vault_root is None:
        return runtime.fmt_error("server not initialized")
    if not params or "source" not in params or "slug" not in params:
        return runtime.fmt_error("shape-presentation requires params: {source, slug}")
    try:
        result = shape_presentation.shape(state.vault_root, params)
        if isinstance(result, dict) and "error" in result:
            return runtime.fmt_error(result["error"])
        return json.dumps(result, indent=2)
    except (ValueError, FileNotFoundError) as e:
        return runtime.fmt_error(str(e))


def _action_start_shaping(runtime: ServerRuntime, params: dict | None):
    state = runtime.get_state()
    if state.vault_root is None or state.router is None:
        return runtime.fmt_error("server not initialized")
    try:
        result = start_shaping.start_shaping(state.vault_root, state.router, params)
        if isinstance(result, dict) and "error" in result:
            return runtime.fmt_error(result["error"])
        runtime.mark_index_pending(result["transcript_path"], type_hint=result.get("type"))
        return json.dumps(result, indent=2)
    except (ValueError, FileNotFoundError) as e:
        return runtime.fmt_error(str(e))


def _action_register_workspace(runtime: ServerRuntime, params: dict | None):
    state = runtime.get_state()
    if state.vault_root is None:
        return runtime.fmt_error("server not initialized")
    if not params or "slug" not in params or "path" not in params:
        return runtime.fmt_error("register_workspace requires params: {slug, path}")
    try:
        workspace_registry.register_workspace(
            state.vault_root,
            params["slug"],
            params["path"],
        )
        registry = workspace_registry.load_registry(state.vault_root)
        runtime.set_workspace_registry(registry)
        return f"**Workspace registered:** {params['slug']} → {params['path']}"
    except ValueError as e:
        return runtime.fmt_error(str(e))


def _action_unregister_workspace(runtime: ServerRuntime, params: dict | None):
    state = runtime.get_state()
    if state.vault_root is None:
        return runtime.fmt_error("server not initialized")
    if not params or "slug" not in params:
        return runtime.fmt_error("unregister_workspace requires params: {slug}")
    try:
        workspace_registry.unregister_workspace(state.vault_root, params["slug"])
        registry = workspace_registry.load_registry(state.vault_root)
        runtime.set_workspace_registry(registry)
        return f"**Workspace unregistered:** {params['slug']}"
    except ValueError as e:
        return runtime.fmt_error(str(e))


def _action_sync_definitions(runtime: ServerRuntime, params: dict | None):
    state = runtime.get_state()
    if state.vault_root is None:
        return runtime.fmt_error("server not initialized")
    try:
        p = params or {}
        result = sync_definitions.sync_definitions(
            state.vault_root,
            dry_run=p.get("dry_run", False),
            force=p.get("force", False),
            types=p.get("types", None),
        )
        if result["status"] == "ok" and not p.get("dry_run") and result["updated"]:
            router = runtime.compile_and_save(state.vault_root)
            runtime.set_router(router)
            result["post_sync"] = "Recompiled router."
        return json.dumps(result, indent=2)
    except (OSError, json.JSONDecodeError) as e:
        return runtime.fmt_error(str(e))


def _action_migrate_naming(runtime: ServerRuntime, params: dict | None):
    state = runtime.get_state()
    if state.router is None:
        return runtime.fmt_error("router not initialized")
    if state.vault_root is None:
        return runtime.fmt_error("server not initialized")
    try:
        dry_run = (params or {}).get("dry_run", False)
        result = migrate_naming.migrate_vault(
            state.vault_root,
            router=state.router,
            dry_run=dry_run,
        )
        if isinstance(result, dict) and "error" in result:
            return runtime.fmt_error(result["error"])
        if not dry_run and result.get("renamed", 0) > 0:
            router = runtime.compile_and_save(state.vault_root)
            index = runtime.build_index_and_save(state.vault_root)
            runtime.set_router(router)
            runtime.set_index(index)
        return json.dumps(result, indent=2)
    except (ValueError, FileNotFoundError, OSError) as e:
        return runtime.fmt_error(str(e))


def _action_fix_links(runtime: ServerRuntime, params: dict | None):
    state = runtime.get_state()
    if state.router is None:
        return runtime.fmt_error("router not initialized")
    if state.vault_root is None:
        return runtime.fmt_error("server not initialized")
    try:
        do_fix = (params or {}).get("fix", False)
        result = fix_links.scan_and_resolve(state.vault_root, router=state.router)
        if do_fix and result["fixed"]:
            total = fix_links.apply_fixes(state.vault_root, result["fixed"])
            result["substitutions"] = total
            runtime.mark_index_dirty()
        result["mode"] = "fix" if do_fix else "dry_run"
        return json.dumps(result, indent=2)
    except (ValueError, OSError) as e:
        return runtime.fmt_error(str(e))


def _action_archive(runtime: ServerRuntime, params: dict | None):
    state = runtime.get_state()
    if state.router is None:
        return runtime.fmt_error("router not initialized")
    if not params or "path" not in params:
        return runtime.fmt_error("archive requires params: {path}")
    if state.vault_root is None:
        return runtime.fmt_error("server not initialized")
    try:
        result = edit.archive_artefact(state.vault_root, state.router, params["path"])
        runtime.mark_index_dirty()
        return (
            f"**Archived:** {result['old_path']} → {result['new_path']}"
            f" ({result['links_updated']} links updated)"
        )
    except (ValueError, FileNotFoundError, OSError) as e:
        return runtime.fmt_error(str(e))


def _action_unarchive(runtime: ServerRuntime, params: dict | None):
    state = runtime.get_state()
    if state.router is None:
        return runtime.fmt_error("router not initialized")
    if not params or "path" not in params:
        return runtime.fmt_error("unarchive requires params: {path}")
    if state.vault_root is None:
        return runtime.fmt_error("server not initialized")
    try:
        result = edit.unarchive_artefact(state.vault_root, state.router, params["path"])
        runtime.mark_index_dirty()
        return (
            f"**Unarchived:** {result['old_path']} → {result['new_path']}"
            f" ({result['links_updated']} links updated)"
        )
    except (ValueError, FileNotFoundError, OSError) as e:
        return runtime.fmt_error(str(e))


_ACTION_HANDLERS = {
    "compile": _action_compile,
    "build_index": _action_build_index,
    "rename": _action_rename,
    "delete": _action_delete,
    "convert": _action_convert,
    "shape-presentation": _action_shape_presentation,
    "start-shaping": _action_start_shaping,
    "register_workspace": _action_register_workspace,
    "unregister_workspace": _action_unregister_workspace,
    "sync_definitions": _action_sync_definitions,
    "migrate_naming": _action_migrate_naming,
    "fix-links": _action_fix_links,
    "archive": _action_archive,
    "unarchive": _action_unarchive,
}


def handle_brain_action(action: str, params: dict | None, runtime: ServerRuntime):
    runtime.check_version_drift()

    denied = runtime.enforce_profile("brain_action")
    if denied:
        return denied

    state = runtime.get_state()
    if state.vault_root is None:
        return runtime.fmt_error("server not initialized")

    handler = _ACTION_HANDLERS.get(action)
    if handler is None:
        valid = [
            "compile",
            "build_index",
            "rename",
            "delete",
            "convert",
            "shape-presentation",
            "start-shaping",
            "migrate_naming",
            "register_workspace",
            "unregister_workspace",
            "fix-links",
            "sync_definitions",
            "archive",
            "unarchive",
        ]
        return runtime.fmt_error(f"Unknown action '{action}'. Valid: {', '.join(valid)}")

    return handler(runtime, params)
