from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass

import _common
import edit
import fix_links
import obsidian_cli
import rename
import shape_printable
import shape_presentation
import start_shaping_session

from . import _server_readiness
from ._server_runtime import ServerRuntime
from ._server_contracts import contract_hint, validate_spec


@dataclass(frozen=True)
class MoveSpec:
    required_fields: tuple[str, ...]
    optional_fields: tuple[str, ...] = ()
    handler: Callable[[ServerRuntime, dict], str] | None = None
    requires_router_refresh: bool = True


@dataclass(frozen=True)
class ActionSpec:
    required_fields: tuple[str, ...] = ()
    optional_fields: tuple[str, ...] = ()
    handler: Callable[[ServerRuntime, dict], str] | None = None
    requires_router_refresh: bool = False


def move_contract_hint(op: str) -> str:
    return contract_hint(MOVE_SPECS[op], f"op='{op}'")


def action_contract_hint(action: str) -> str:
    spec = ACTION_SPECS[action]
    required = ", ".join(spec.required_fields)
    hint = f"action='{action}' requires params: {{{required}}}"
    if spec.optional_fields:
        hint += f". Optional: {', '.join(spec.optional_fields)}."
    else:
        hint += "."
    return hint


def _validate_action_params(action: str, params: dict | None):
    spec = ACTION_SPECS[action]
    payload = params or {}

    if not isinstance(payload, dict):
        raise ValueError(
            f"Action '{action}' expects params to be an object. "
            f"{action_contract_hint(action)}"
        )

    if action == "reparent-children":
        allowed = {"source", "to"}
        extras = [name for name in payload if name not in allowed]
        if extras:
            raise ValueError(
                f"Action '{action}' does not accept params field '{extras[0]}'. "
                f"{action_contract_hint(action)}"
            )
        if payload.get("source") in (None, ""):
            raise ValueError(
                f"Action '{action}' requires params field 'source'. "
                f"{action_contract_hint(action)}"
            )
        result = {"source": payload["source"]}
        if "to" in payload:
            result["to"] = payload.get("to")
        return result

    # Delegate to the shared validator; map its generic error terms to the
    # brain_action-specific wording ("Action 'X' does not accept params field 'Y'").
    return validate_spec(
        spec,
        payload,
        label=f"Action '{action}'",
        hint=action_contract_hint(action),
        field_term="params field",
    )


def _validate_artefact_path(vault_root: str, router: dict, path: str, *, label: str):
    if _common.is_archived_path(path):
        raise ValueError(
            f"{label} cannot target _Archive/. "
            "Use brain_move(op='archive'/'unarchive') for archive transitions."
        )
    return _common.validate_artefact_folder(vault_root, router, path)


def _format_partial_apply_error(runtime: ServerRuntime, exc: _common.PartialApplyError):
    runtime.mark_router_dirty()
    runtime.mark_index_dirty()
    return runtime.fmt_error(str(exc))


def _action_rename(runtime: ServerRuntime, params: dict):
    state = runtime.get_state()
    if state.vault_root is None or state.router is None:
        return runtime.fmt_error("server not initialized")

    source = params["source"]
    dest = params["dest"]
    try:
        source_art = _validate_artefact_path(
            state.vault_root, state.router, source, label="Rename source",
        )
        dest_art = _validate_artefact_path(
            state.vault_root, state.router, dest, label="Rename destination",
        )
        if source_art["key"] != dest_art["key"]:
            raise ValueError(
                "Rename cannot move an artefact between different type folders. "
                "Use brain_move(op='convert', ...) for type changes."
            )
        rename.validate_rename_request(
            state.vault_root,
            source,
            dest,
            router=state.router,
        )
        rename.validate_destination_parent_directory(state.vault_root, dest)
    except (OSError, ValueError) as e:
        return runtime.fmt_error(str(e))

    runtime.refresh_cli_available()
    state = runtime.get_state()
    if state.cli_available and state.vault_name:
        try:
            abs_dest = os.path.join(state.vault_root, dest)
            os.makedirs(os.path.dirname(abs_dest), exist_ok=True)
        except OSError as e:
            return runtime.fmt_error(str(e))

        result = obsidian_cli.move(state.vault_name, source, dest)
        if result is True:
            runtime.mark_router_dirty()
            runtime.mark_index_dirty()
            return f"**Renamed** (obsidian_cli): {source} → {dest} (wikilinks auto-updated)"

    try:
        links_updated = rename.rename_and_update_links(
            state.vault_root,
            source,
            dest,
            router=state.router,
        )
        runtime.mark_router_dirty()
        runtime.mark_index_dirty()
        return f"**Renamed** (grep_replace): {source} → {dest}, {links_updated} links updated"
    except _common.ParentChainError as e:
        return runtime.fmt_error(_common.parent_chain_error_message(e))
    except _common.PartialApplyError as e:
        return _format_partial_apply_error(runtime, e)
    except (OSError, ValueError) as e:
        return runtime.fmt_error(str(e))


def _action_delete(runtime: ServerRuntime, params: dict):
    state = runtime.get_state()
    if state.vault_root is None or state.router is None:
        return runtime.fmt_error("server not initialized")
    try:
        _validate_artefact_path(
            state.vault_root, state.router, params["path"], label="Delete path",
        )
        links_replaced = rename.delete_and_clean_links(
            state.vault_root,
            params["path"],
            router=state.router,
            recursive=bool(params.get("recursive")),
        )
        runtime.mark_router_dirty()
        runtime.mark_index_dirty()
        return f"**Deleted:** {params['path']}, {links_replaced} links replaced"
    except _common.ParentChainError as e:
        return runtime.fmt_error(_common.parent_chain_error_message(e))
    except _common.PartialApplyError as e:
        return _format_partial_apply_error(runtime, e)
    except (FileNotFoundError, ValueError) as e:
        return runtime.fmt_error(str(e))


def _action_convert(runtime: ServerRuntime, params: dict):
    state = runtime.get_state()
    if state.vault_root is None or state.router is None:
        return runtime.fmt_error("server not initialized")
    try:
        result = edit.convert_artefact(
            state.vault_root,
            state.router,
            params["path"],
            params["target_type"],
            parent=params.get("parent"),
            recursive=bool(params.get("recursive")),
        )
        runtime.mark_router_dirty()
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
    except _common.ParentChainError as e:
        return runtime.fmt_error(_common.parent_chain_error_message(e))
    except _common.PartialApplyError as e:
        return _format_partial_apply_error(runtime, e)
    except (ValueError, FileNotFoundError, OSError) as e:
        return runtime.fmt_error(str(e))


def _action_shape_presentation(runtime: ServerRuntime, params: dict):
    state = runtime.get_state()
    if state.vault_root is None:
        return runtime.fmt_error("server not initialized")
    try:
        result = shape_presentation.shape(state.vault_root, params)
        if isinstance(result, dict) and "error" in result:
            return runtime.fmt_error(result["error"])
        if isinstance(result, dict) and result.get("created") and result.get("path"):
            runtime.mark_index_pending(result["path"], type_hint="temporal/presentation")
        return json.dumps(result, indent=2)
    except (ValueError, FileNotFoundError) as e:
        return runtime.fmt_error(str(e))


def _action_shape_printable(runtime: ServerRuntime, params: dict):
    state = runtime.get_state()
    if state.vault_root is None:
        return runtime.fmt_error("server not initialized")
    try:
        result = shape_printable.shape(state.vault_root, params)
        if isinstance(result, dict) and "error" in result:
            return runtime.fmt_error(result["error"])
        if isinstance(result, dict) and result.get("created") and result.get("path"):
            runtime.mark_index_pending(result["path"], type_hint="temporal/printable")
        return json.dumps(result, indent=2)
    except (ValueError, FileNotFoundError) as e:
        return runtime.fmt_error(str(e))


def _action_shape(runtime: ServerRuntime, params: dict):
    state = runtime.get_state()
    try:
        result = start_shaping_session.start_shaping_session(
            state.vault_root,
            state.router,
            params["target"],
            mode=params["mode"],
        )
        if result["status_changed"]:
            runtime.mark_router_dirty()
        if result["target_path_changed"]:
            runtime.mark_index_dirty()
        else:
            for path in result["changed_paths"]:
                type_hint = result.get("type") if path == result["transcript_path"] else None
                runtime.mark_index_pending(path, type_hint=type_hint)
        return json.dumps(result, indent=2)
    except _common.PartialApplyError as e:
        runtime.mark_router_dirty()
        runtime.mark_index_dirty()
        return runtime.fmt_error(str(e))
    except (ValueError, FileNotFoundError) as e:
        return runtime.fmt_error(str(e))


def _action_fix_links(runtime: ServerRuntime, params: dict):
    state = runtime.get_state()
    if state.router is None:
        return runtime.fmt_error("router not initialized")
    if state.vault_root is None:
        return runtime.fmt_error("server not initialized")
    try:
        do_fix = params.get("fix", False)
        path = params.get("path")
        links_filter = params.get("links")

        if path:
            result = fix_links.scan_file(
                state.vault_root, path, router=state.router,
            )
            if do_fix and result["fixed"]:
                total = fix_links.apply_fixes_to_file(
                    state.vault_root, path, result["fixed"],
                    links_filter=links_filter,
                )
                result["substitutions"] = total
                runtime.mark_index_pending(path)
        else:
            result = fix_links.scan_and_resolve(
                state.vault_root, router=state.router,
            )
            if do_fix and result["fixed"]:
                total = fix_links.apply_fixes(state.vault_root, result["fixed"])
                result["substitutions"] = total
                runtime.mark_index_dirty()
        result["mode"] = "fix" if do_fix else "dry_run"
        return json.dumps(result, indent=2)
    except (ValueError, OSError) as e:
        return runtime.fmt_error(str(e))


def _action_reparent(runtime: ServerRuntime, params: dict):
    state = runtime.get_state()
    if state.vault_root is None or state.router is None:
        return runtime.fmt_error("server not initialized")
    try:
        result = edit.reparent_children(
            state.vault_root,
            state.router,
            params["source"],
            params.get("to"),
            to_provided="to" in params,
        )
        runtime.mark_router_dirty()
        runtime.mark_index_dirty()
        return json.dumps(
            {
                "status": "ok",
                "source": result["source"],
                "to": result["to"],
                "children": result["children"],
                "moves": result["moves"],
                "links_updated": result["links_updated"],
            },
            indent=2,
        )
    except _common.ParentChainError as e:
        return runtime.fmt_error(_common.parent_chain_error_message(e))
    except _common.PartialApplyError as e:
        return _format_partial_apply_error(runtime, e)
    except (ValueError, FileNotFoundError, OSError) as e:
        return runtime.fmt_error(str(e))


def _action_archive(runtime: ServerRuntime, params: dict):
    state = runtime.get_state()
    if state.router is None:
        return runtime.fmt_error("router not initialized")
    if state.vault_root is None:
        return runtime.fmt_error("server not initialized")
    try:
        result = edit.archive_artefact(
            state.vault_root,
            state.router,
            params["path"],
            recursive=bool(params.get("recursive")),
        )
        runtime.mark_router_dirty()
        runtime.mark_index_dirty()
        return (
            f"**Archived:** {result['old_path']} → {result['new_path']}"
            f" ({result['links_updated']} links updated)"
        )
    except _common.ParentChainError as e:
        return runtime.fmt_error(_common.parent_chain_error_message(e))
    except _common.PartialApplyError as e:
        return _format_partial_apply_error(runtime, e)
    except (ValueError, FileNotFoundError, OSError) as e:
        return runtime.fmt_error(str(e))


def _action_unarchive(runtime: ServerRuntime, params: dict):
    state = runtime.get_state()
    if state.router is None:
        return runtime.fmt_error("router not initialized")
    if state.vault_root is None:
        return runtime.fmt_error("server not initialized")
    try:
        result = edit.unarchive_artefact(
            state.vault_root,
            state.router,
            params["path"],
            recursive=bool(params.get("recursive")),
        )
        runtime.mark_router_dirty()
        runtime.mark_index_dirty()
        message = (
            f"**Unarchived:** {result['old_path']} → {result['new_path']}"
            f" ({result['links_updated']} links updated)"
        )
        if result.get("uninspected"):
            details = "; ".join(
                f"{item['path']}: {item['reason']}"
                for item in result["uninspected"]
            )
            message += (
                "\n**Warning:** recursive restore could not inspect archived "
                f"candidate(s): {details}"
            )
        return message
    except _common.ParentChainError as e:
        return runtime.fmt_error(_common.parent_chain_error_message(e))
    except _common.PartialApplyError as e:
        return _format_partial_apply_error(runtime, e)
    except (ValueError, FileNotFoundError, OSError) as e:
        return runtime.fmt_error(str(e))


MOVE_SPECS = {
    "rename": MoveSpec(
        required_fields=("source", "dest"),
        handler=_action_rename,
        requires_router_refresh=True,
    ),
    "convert": MoveSpec(
        required_fields=("path", "target_type"),
        optional_fields=("parent", "recursive"),
        handler=_action_convert,
        requires_router_refresh=True,
    ),
    "archive": MoveSpec(
        required_fields=("path",),
        optional_fields=("recursive",),
        handler=_action_archive,
        requires_router_refresh=True,
    ),
    "unarchive": MoveSpec(
        required_fields=("path",),
        optional_fields=("recursive",),
        handler=_action_unarchive,
        requires_router_refresh=True,
    ),
}


ACTION_SPECS = {
    "delete": ActionSpec(
        required_fields=("path",),
        optional_fields=("recursive",),
        handler=_action_delete,
        requires_router_refresh=True,
    ),
    "reparent-children": ActionSpec(
        required_fields=("source",),
        optional_fields=("to",),
        handler=_action_reparent,
        requires_router_refresh=True,
    ),
    "shape-printable": ActionSpec(
        required_fields=("source", "slug"),
        optional_fields=("render", "keep_heading_with_next", "pdf_engine"),
        handler=_action_shape_printable,
    ),
    "shape-presentation": ActionSpec(
        required_fields=("source", "slug"),
        optional_fields=("render", "preview"),
        handler=_action_shape_presentation,
    ),
    "shape": ActionSpec(
        required_fields=("target", "mode"),
        handler=_action_shape,
        requires_router_refresh=True,
    ),
    "fix-links": ActionSpec(
        optional_fields=("fix", "path", "links"),
        handler=_action_fix_links,
        requires_router_refresh=True,
    ),
}


def handle_brain_move(op: str, params: dict | None, runtime: ServerRuntime):
    runtime.check_version_drift()

    denied = runtime.enforce_profile("brain_move")
    if denied:
        return denied

    state = runtime.get_state()
    if state.vault_root is None:
        return runtime.fmt_error("server not initialized")

    spec = MOVE_SPECS.get(op)
    if spec is None or spec.handler is None:
        return runtime.fmt_error(
            f"Unknown move op '{op}'. Valid: {', '.join(MOVE_SPECS)}"
        )

    if spec.requires_router_refresh:
        _state, progress = _server_readiness.require_router(runtime, "brain_move")
        if progress is not None:
            return progress

    return spec.handler(runtime, params or {})


def handle_brain_action(action: str, params: dict | None, runtime: ServerRuntime):
    runtime.check_version_drift()

    denied = runtime.enforce_profile("brain_action")
    if denied:
        return denied

    state = runtime.get_state()
    if state.vault_root is None:
        return runtime.fmt_error("server not initialized")

    spec = ACTION_SPECS.get(action)
    if spec is None or spec.handler is None:
        return runtime.fmt_error(
            f"Unknown action '{action}'. Valid: {', '.join(sorted(ACTION_SPECS))}"
        )

    try:
        validated_params = _validate_action_params(action, params)
    except ValueError as e:
        return runtime.fmt_error(str(e))

    if spec.requires_router_refresh:
        _state, progress = _server_readiness.require_router(runtime, "brain_action")
        if progress is not None:
            return progress

    return spec.handler(runtime, validated_params)
