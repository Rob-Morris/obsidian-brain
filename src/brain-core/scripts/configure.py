#!/usr/bin/env python3
"""configure.py — manage explicit Brain configuration surfaces."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from _bootstrap import mcp_transport
from _bootstrap.mcp_state import CLAUDE_MD_BOOTSTRAP_VAULT, CLAUDE_MD_FILE, bootstrap_line_for_target
from _bootstrap.runtime import (
    handoff_current_script_to_managed_runtime,
    required_modules_for_scope,
    step as _step,
)
from _bootstrap.vaults import find_vault_root, make_vault_parent_parser
from _bootstrap.workspace_binding import (
    WorkspaceBindingError,
    converge_workspace_binding,
    load_workspace_manifest_state,
    resolve_local_brain_vault,
    resolve_local_brain_alias,
    resolve_workspace_dir,
    save_workspace_manifest_data,
)
from _common import find_root_bootstrap_file, safe_write
from _lifecycle_common import (
    emit_lifecycle_result,
    exit_code_for_result,
    make_result_envelope,
    render_human_result,
)

BOOTSTRAP_TIMEOUT = 300


def _result_envelope(action: str, vault_root: Path, steps: list[dict], *, notes: list[str] | None = None) -> dict:
    return make_result_envelope(
        action=action,
        vault_root=vault_root,
        managed_python=sys.executable,
        steps=steps,
        notes=notes,
    )


def _render_human(result: dict) -> str:
    return render_human_result(result, subject_label="Configure action", subject_key="action")


def _emit_result(result: dict, *, as_json: bool) -> int:
    emit_lifecycle_result(result, as_json=as_json, render_human=_render_human)
    return exit_code_for_result(result)


def _managed_runtime_error_result(action: str, vault_root: Path, message: str) -> dict:
    return _result_envelope(
        action,
        vault_root,
        [_step("managed_runtime", "error", message)],
    )


def _resolve_binding_brain(vault_root: Path, brain_id: str | None) -> str:
    if brain_id is None:
        return resolve_local_brain_alias(vault_root)
    if resolve_local_brain_vault(brain_id) is None:
        raise WorkspaceBindingError(
            f"unknown local Brain ID '{brain_id}'. Register or upgrade that Brain first, or pick a known vault alias."
        )
    return brain_id


def configure_workspace_binding_action(
    vault_root: Path,
    *,
    workspace_dir: Path,
    brain_id: str | None,
    slug: str | None,
    force: bool,
) -> dict:
    try:
        resolved_brain = _resolve_binding_brain(vault_root, brain_id)
        convergence = converge_workspace_binding(
            workspace_dir,
            brain=resolved_brain,
            slug=slug,
            allow_rebind=force,
        )
        step = _step("workspace_binding", convergence.status, convergence.message)
        notes = [f"workspace brain: {convergence.brain}", f"workspace slug: {convergence.slug}"]
        return _result_envelope("workspace_binding", vault_root, [step], notes=notes)
    except WorkspaceBindingError as exc:
        return _result_envelope(
            "workspace_binding",
            vault_root,
            [_step("workspace_binding", "error", str(exc), reason=getattr(exc, "code", None))],
        )


def _parse_link_args(entries: list[str]) -> dict[str, str]:
    links: dict[str, str] = {}
    for entry in entries:
        if "=" not in entry:
            raise WorkspaceBindingError(
                f"invalid --link value '{entry}'; expected NAME=VALUE"
            )
        key, value = entry.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise WorkspaceBindingError(
                f"invalid --link value '{entry}'; expected NAME=VALUE"
            )
        links[key] = value
    return links


def configure_workspace_metadata_action(
    vault_root: Path,
    *,
    workspace_dir: Path,
    tags: list[str],
    clear_tags: bool,
    links: list[str],
    clear_links: bool,
) -> dict:
    if not tags and not links and not clear_tags and not clear_links:
        return _result_envelope(
            "workspace_metadata",
            vault_root,
            [_step("workspace_metadata", "error", "No metadata changes requested.")],
        )

    try:
        state = load_workspace_manifest_state(workspace_dir)
        if state.data is None:
            raise WorkspaceBindingError(
                "workspace binding is missing; run `brain setup workspace` or `brain configure workspace binding` first."
            )
        manifest = dict(state.data)

        defaults = manifest.get("defaults")
        if defaults is None:
            defaults = {}
        if not isinstance(defaults, dict):
            raise WorkspaceBindingError("workspace manifest defaults must be a mapping")
        defaults = dict(defaults)

        if clear_tags:
            defaults.pop("tags", None)
        if tags:
            current_tags = defaults.get("tags")
            if current_tags is None:
                current_tags = []
            if not isinstance(current_tags, list) or not all(isinstance(item, str) for item in current_tags):
                raise WorkspaceBindingError("workspace manifest defaults.tags must be a list of strings")
            merged_tags = list(current_tags)
            for tag in tags:
                if tag not in merged_tags:
                    merged_tags.append(tag)
            defaults["tags"] = merged_tags
        if defaults:
            manifest["defaults"] = defaults
        else:
            manifest.pop("defaults", None)

        parsed_links = _parse_link_args(links)
        current_links = manifest.get("links")
        if current_links is None:
            current_links = {}
        if not isinstance(current_links, dict):
            raise WorkspaceBindingError("workspace manifest links must be a mapping")
        current_links = {} if clear_links else dict(current_links)
        current_links.update(parsed_links)
        if current_links:
            manifest["links"] = current_links
        else:
            manifest.pop("links", None)

        write = save_workspace_manifest_data(workspace_dir, manifest)
        return _result_envelope(
            "workspace_metadata",
            vault_root,
            [_step("workspace_metadata", write.status, write.message)],
        )
    except WorkspaceBindingError as exc:
        return _result_envelope(
            "workspace_metadata",
            vault_root,
            [_step("workspace_metadata", "error", str(exc))],
        )


def _ensure_bootstrap_file(path: Path, bootstrap: str) -> tuple[str, str]:
    try:
        existing = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing = ""
    except OSError as exc:
        raise WorkspaceBindingError(f"failed to read {path}: {exc}") from exc

    if not existing:
        safe_write(path, f"{bootstrap}\n")
        return "changed", f"Created {path.name} with Brain bootstrap instructions."

    if bootstrap in existing:
        return "noop", f"{path.name} already includes Brain bootstrap instructions."

    separator = "\n" if existing.endswith("\n") else "\n\n"
    safe_write(path, f"{existing}{separator}{bootstrap}\n")
    return "changed", f"Appended Brain bootstrap instructions to {path.name}."


def configure_workspace_bootstrap_action(
    vault_root: Path,
    *,
    workspace_dir: Path,
    surface: str,
) -> dict:
    try:
        surfaces = ["agents", "claude"] if surface == "all" else [surface]
        steps: list[dict] = []
        if "agents" in surfaces:
            agents_path = find_root_bootstrap_file(workspace_dir, "AGENTS.md") or (workspace_dir / "AGENTS.md")
            status, message = _ensure_bootstrap_file(agents_path, CLAUDE_MD_BOOTSTRAP_VAULT)
            steps.append(_step("workspace_bootstrap_agents", status, message))
        if "claude" in surfaces:
            claude_path = workspace_dir / CLAUDE_MD_FILE
            status, message = _ensure_bootstrap_file(
                claude_path,
                bootstrap_line_for_target(workspace_dir),
            )
            steps.append(_step("workspace_bootstrap_claude", status, message))
        return _result_envelope("workspace_bootstrap", vault_root, steps)
    except WorkspaceBindingError as exc:
        return _result_envelope(
            "workspace_bootstrap",
            vault_root,
            [_step("workspace_bootstrap", "error", str(exc))],
        )


def _apply_semantic_flag(vault_root: Path, steps: list[dict]) -> None:
    import _semantic.config as semantic_config

    changed = semantic_config.set_semantic_retrieval_enabled(vault_root, enabled=True)
    steps.append(
        _step(
            "semantic_config",
            "changed" if changed else "noop",
            (
                "Enabled defaults.flags.semantic_retrieval in .brain/local/config.yaml."
                if changed
                else "defaults.flags.semantic_retrieval is already enabled."
            ),
        )
    )


def _provision_runtime_or_record_error(vault_root: Path, steps: list[dict], notes: list[str]) -> dict | None:
    import _semantic.provision as semantic_provision

    try:
        outcome = semantic_provision.provision_semantic_runtime(
            vault_root,
            python_executable=sys.executable,
            refresh_assets=True,
        )
    except semantic_provision.SemanticProvisionError as exc:
        steps.append(_step("semantic_runtime", "error", str(exc)))
        notes.append(
            "Semantic retrieval is configured on, but the managed runtime could not be provisioned on this machine."
        )
        return _result_envelope("semantic_enable", vault_root, steps, notes=notes)
    semantic_provision.append_runtime_steps(steps, outcome)
    semantic_provision.append_asset_step(steps, notes, outcome)
    semantic_provision.append_marker_step(steps, outcome)
    if outcome.assets_error:
        notes.append(
            "Run `python3 .brain-core/scripts/repair.py semantic` after resolving the underlying vault or runtime issue."
        )
        return _result_envelope("semantic_enable", vault_root, steps, notes=notes)
    return None


def _configure_semantic_enable(vault_root: Path, *, provision: bool, bootstrap_steps: list[dict]) -> dict:
    steps = list(bootstrap_steps)
    notes: list[str] = []

    _apply_semantic_flag(vault_root, steps)

    if not provision:
        notes.append(
            "Runtime provisioning was skipped (--no-provision). "
            "Run `python3 .brain-core/scripts/check.py --actionable` or "
            "`python3 .brain-core/scripts/repair.py semantic` later if this vault remains unavailable for semantic search."
        )
        return _result_envelope("semantic_enable", vault_root, steps, notes=notes)

    error_result = _provision_runtime_or_record_error(vault_root, steps, notes)
    if error_result is not None:
        return error_result
    return _result_envelope("semantic_enable", vault_root, steps, notes=notes)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    vault_parent = make_vault_parent_parser()
    parser = argparse.ArgumentParser(
        description="Configure explicit Brain workspace and capability surfaces.",
        parents=[vault_parent],
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    semantic = subparsers.add_parser(
        "semantic",
        help="Configure semantic retrieval support for this vault.",
        parents=[make_vault_parent_parser()],
    )
    semantic.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    semantic.add_argument(
        "--enable",
        action="store_true",
        required=True,
        help="Enable semantic retrieval in local config and provision runtime support.",
    )
    semantic.add_argument(
        "--no-provision",
        action="store_true",
        help="Write config only; skip semantic runtime provisioning and asset refresh.",
    )

    workspace = subparsers.add_parser(
        "workspace",
        help="Configure workspace-owned binding, metadata, and bootstrap state.",
    )
    workspace_subparsers = workspace.add_subparsers(dest="workspace_command", required=True)

    binding = workspace_subparsers.add_parser(
        "binding",
        help="Create or update the workspace-to-Brain binding.",
        parents=[make_vault_parent_parser()],
    )
    binding.add_argument("--path", help="Workspace directory to bind (default: current directory).")
    binding.add_argument("--brain", help="Symbolic local Brain ID to bind to (default: current vault's alias).")
    binding.add_argument("--slug", help="Explicit workspace slug (default: existing slug or derived from folder name).")
    binding.add_argument("--force", action="store_true", help="Allow rebinding or slug changes when the workspace is already bound.")
    binding.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    metadata = workspace_subparsers.add_parser(
        "metadata",
        help="Update optional workspace metadata such as defaults and links.",
        parents=[make_vault_parent_parser()],
    )
    metadata.add_argument("--path", help="Workspace directory to update (default: current directory).")
    metadata.add_argument("--tag", action="append", default=[], help="Add one defaults.tags entry (repeatable).")
    metadata.add_argument("--clear-tags", action="store_true", help="Clear defaults.tags before applying any --tag values.")
    metadata.add_argument("--link", action="append", default=[], help="Set one workspace link as NAME=VALUE (repeatable).")
    metadata.add_argument("--clear-links", action="store_true", help="Clear the links mapping before applying any --link values.")
    metadata.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    bootstrap_sub = workspace_subparsers.add_parser(
        "bootstrap",
        help="Install optional agent bootstrap instructions into the workspace.",
        parents=[make_vault_parent_parser()],
    )
    bootstrap_sub.add_argument("--path", help="Workspace directory to update (default: current directory).")
    bootstrap_sub.add_argument(
        "--surface",
        choices=("agents", "claude", "all"),
        default="all",
        help="Which bootstrap surfaces to manage (default: all).",
    )
    bootstrap_sub.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    mcp = subparsers.add_parser(
        "mcp",
        help="Configure MCP transport policy explicitly.",
        parents=[make_vault_parent_parser()],
    )
    mcp.add_argument(
        "--client",
        choices=("claude", "codex", "all"),
        default="all",
        help="Which client config to write (default: all).",
    )
    mcp.add_argument("--user", action="store_true", help="Register as the default Brain route for all projects (user scope).")
    mcp.add_argument(
        "--local",
        action="store_true",
        help="Use Claude local scope (.claude/settings.local.json). Unsupported for Codex.",
    )
    mcp.add_argument(
        "--workspace",
        "--project",
        dest="project",
        help="Target workspace directory to configure (default: current directory).",
    )
    mcp.add_argument("--remove", action="store_true", help="Remove only recorded Brain-managed entries for the requested scope.")
    mcp.add_argument("--force", action="store_true", help="Skip the confirmation prompt for --remove.")
    mcp.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    return parser.parse_args(argv)


def _mcp_scope(*, user: bool, local: bool) -> str:
    if user:
        return "user"
    if local:
        return "local"
    return "project"


def _mcp_followup_notes(*, client: str, scope: str, workspace_dir: Path | None) -> list[str]:
    notes: list[str] = []
    if scope == "project" and workspace_dir is not None:
        if client in {"all", "claude"}:
            notes.extend(mcp_transport.claude_project_followup_notes(workspace_dir))
            notes.append("In Claude Code for that directory: run /mcp and approve `brain` if prompted.")
            notes.append("Verify in Claude: call `brain_session` and confirm `environment.vault_root`.")
        if client in {"all", "codex"}:
            notes.append("In Codex for that directory: trust the project and ensure the project-scoped `brain` MCP is enabled.")
            notes.append("Verify in Codex: call `brain_session` and confirm `environment.vault_root`.")
            notes.append("Health check: `codex mcp list`.")
    elif scope == "user":
        if client in {"all", "claude"}:
            notes.append("Verify in Claude: `claude mcp list`.")
        if client in {"all", "codex"}:
            notes.append("Verify in Codex: `codex mcp list`.")
    return notes


def configure_mcp_action(
    vault_root: Path,
    *,
    client: str,
    user: bool,
    local: bool,
    workspace_dir: Path | None,
    remove: bool,
    force: bool,
) -> dict:
    scope = _mcp_scope(user=user, local=local)
    action = "mcp_remove" if remove else "mcp_configure"

    try:
        clients, _warnings = mcp_transport._resolve_clients_or_error(client, scope)
    except mcp_transport.InitTransportError as exc:
        return _result_envelope(action, vault_root, [_step("mcp_transport", "error", str(exc))])

    if remove and not force and not mcp_transport._confirm_removal(mcp_transport._scope_label(scope, workspace_dir), clients):
        return _result_envelope(
            action,
            vault_root,
            [_step("mcp_transport", "noop", "Removal cancelled. No changes made.")],
        )

    try:
        mcp_result = mcp_transport.apply_mcp_transport_action(
            vault_root,
            client_arg=client,
            scope=scope,
            target_dir=workspace_dir,
            remove=remove,
        )
    except mcp_transport.InitTransportError as exc:
        return _result_envelope(action, vault_root, [_step("mcp_transport", "error", str(exc))])

    if remove:
        if mcp_result["status"] == "noop":
            status = "noop"
            message = "No recorded Brain-managed MCP entries matched this request."
        else:
            status = "changed"
            message = f"Removed recorded Brain-managed MCP entries for {client} ({scope})."
    else:
        status = "changed"
        message = f"Configured Brain MCP transport for {client} ({scope})."

    notes = _mcp_followup_notes(client=client, scope=scope, workspace_dir=workspace_dir)
    for warning in mcp_result.get("warnings", []):
        if warning not in notes:
            notes.append(warning)
    return _result_envelope(action, vault_root, [_step("mcp_transport", status, message)], notes=notes)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.command == "semantic":
        vault_root = find_vault_root(getattr(args, "vault", None))
        forwarded_args = list(argv) if argv is not None else sys.argv[1:]

        try:
            summary = handoff_current_script_to_managed_runtime(
                vault_root,
                dependency_owner="configure.py",
                required_modules=required_modules_for_scope("semantic"),
                forwarded_args=forwarded_args,
                script_path=str(Path(__file__).resolve()),
                timeout=BOOTSTRAP_TIMEOUT,
            )
        except RuntimeError as exc:
            result = _managed_runtime_error_result("semantic_enable", vault_root, str(exc))
            return _emit_result(result, as_json=args.json)

        result = _configure_semantic_enable(
            Path(vault_root),
            provision=not args.no_provision,
            bootstrap_steps=summary["steps"],
        )
        return _emit_result(result, as_json=args.json)

    if args.command == "mcp":
        vault_root = find_vault_root(getattr(args, "vault", None))
        workspace_dir = None
        if not args.user:
            try:
                workspace_dir = resolve_workspace_dir(args.project)
            except WorkspaceBindingError as exc:
                result = _result_envelope(
                    "mcp_configure",
                    vault_root,
                    [_step("mcp_transport", "error", str(exc))],
                )
                return _emit_result(result, as_json=args.json)
        result = configure_mcp_action(
            vault_root,
            client=args.client,
            user=args.user,
            local=args.local,
            workspace_dir=workspace_dir,
            remove=args.remove,
            force=args.force,
        )
        return _emit_result(result, as_json=args.json)

    vault_root = find_vault_root(getattr(args, "vault", None))
    try:
        workspace_dir = resolve_workspace_dir(getattr(args, "path", None))
    except WorkspaceBindingError as exc:
        result = _result_envelope(
            f"workspace_{args.workspace_command}",
            vault_root,
            [_step(f"workspace_{args.workspace_command}", "error", str(exc))],
        )
        return _emit_result(result, as_json=args.json)

    if args.workspace_command == "binding":
        result = configure_workspace_binding_action(
            vault_root,
            workspace_dir=workspace_dir,
            brain_id=args.brain,
            slug=args.slug,
            force=args.force,
        )
        return _emit_result(result, as_json=args.json)

    if args.workspace_command == "metadata":
        result = configure_workspace_metadata_action(
            vault_root,
            workspace_dir=workspace_dir,
            tags=args.tag,
            clear_tags=args.clear_tags,
            links=args.link,
            clear_links=args.clear_links,
        )
        return _emit_result(result, as_json=args.json)

    result = configure_workspace_bootstrap_action(
        vault_root,
        workspace_dir=workspace_dir,
        surface=args.surface,
    )
    return _emit_result(result, as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
