#!/usr/bin/env python3
"""configure.py — manage explicit Brain configuration surfaces."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from _bootstrap import agent_skills, mcp_transport
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


def _mcp_error(action: str, vault_root: Path | None, message: str, *, committed_effects=()) -> dict:
    return _result_envelope(
        action,
        vault_root,
        [_step("mcp_transport", "error", message, committed_effects=list(committed_effects))],
    )


def configure_agent_skills_action(
    vault_root: Path,
    *,
    client: str,
    replace: bool,
    remove: bool,
    home_dir: Path | None = None,
) -> dict:
    action = "agent_skills_remove" if remove else "agent_skills_configure"
    try:
        steps = agent_skills.configure_agent_skill_adapters(
            home_dir=home_dir or Path.home(),
            client=client,
            replace=replace,
            remove=remove,
        )
    except ValueError as exc:
        steps = [_step("agent_skills", "error", str(exc))]
    notes = []
    if any(step["status"] == "changed" for step in steps):
        notes.append(
            "Restart the affected agent client so it reloads the shaping skill adapter."
        )
    return _result_envelope(action, vault_root, steps, notes=notes)


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
    before_write=None,
) -> dict:
    try:
        resolved_brain = _resolve_binding_brain(vault_root, brain_id)
        convergence = converge_workspace_binding(
            workspace_dir,
            brain=resolved_brain,
            slug=slug,
            allow_rebind=force,
            before_write=before_write,
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
    parent: str | None = None,
    clear_parent: bool = False,
    before_write=None,
) -> dict:
    from _lifecycle.derived_cache_state import RouterCacheUnavailable
    if not tags and not links and not clear_tags and not clear_links and parent is None and not clear_parent:
        return _result_envelope(
            "workspace_metadata",
            vault_root,
            [_step("workspace_metadata", "error", "No metadata changes requested.")],
        )

    try:
        state = load_workspace_manifest_state(workspace_dir)
        if state.data is None:
            raise WorkspaceBindingError(
                "workspace binding is missing; run `brain workspace setup` or `brain configure workspace binding` first."
            )
        manifest = dict(state.data)

        defaults = manifest.get("defaults")
        if defaults is None:
            defaults = {}
        if not isinstance(defaults, dict):
            raise WorkspaceBindingError("workspace manifest defaults must be a mapping")
        defaults = dict(defaults)
        if parent is not None and clear_parent:
            raise WorkspaceBindingError("parent and clear_parent are mutually exclusive")
        if clear_parent:
            defaults.pop("parent", None)
        elif parent is not None:
            defaults["parent"] = parent

        if clear_tags:
            defaults.pop("tags", None)
        if tags:
            from _common._workspace import merge_metadata_tags
            defaults["tags"] = list(merge_metadata_tags(defaults.get("tags", []), tags))
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
        from _common._workspace import update_metadata_links
        current_links = update_metadata_links(current_links, parsed_links, clear=clear_links)
        if current_links:
            manifest["links"] = current_links
        else:
            manifest.pop("links", None)

        if defaults.get("parent") is not None:
            from _common._workspace import manifest_workspace_reference, require_workspace, workspace_policy
            from _lifecycle.derived_cache_state import require_fresh_compiled_router
            router = require_fresh_compiled_router(str(vault_root))
            reference = manifest_workspace_reference(manifest)
            require_workspace(router, reference, active=True)
            policy = workspace_policy(router, reference, defaults, local=True)
            if policy.parent is not None:
                defaults["parent"] = policy.parent
                manifest["defaults"] = defaults
        write = save_workspace_manifest_data(workspace_dir, manifest, before_write=before_write)
        return _result_envelope(
            "workspace_metadata",
            vault_root,
            [_step("workspace_metadata", write.status, write.message)],
        )
    except (WorkspaceBindingError, ValueError, RouterCacheUnavailable) as exc:
        return _result_envelope(
            "workspace_metadata",
            vault_root,
            [_step("workspace_metadata", "error", str(exc))],
        )


def _ensure_bootstrap_file(path: Path, bootstrap: str, *, before_write=None) -> tuple[str, str]:
    try:
        existing = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing = ""
    except OSError as exc:
        raise WorkspaceBindingError(f"failed to read {path}: {exc}") from exc

    if not existing:
        if before_write is not None:
            before_write()
        safe_write(path, f"{bootstrap}\n")
        return "changed", f"Created {path.name} with Brain bootstrap instructions."

    if bootstrap in existing:
        return "noop", f"{path.name} already includes Brain bootstrap instructions."

    separator = "\n" if existing.endswith("\n") else "\n\n"
    if before_write is not None:
        before_write()
    safe_write(path, f"{existing}{separator}{bootstrap}\n")
    return "changed", f"Appended Brain bootstrap instructions to {path.name}."


def configure_workspace_bootstrap_action(
    vault_root: Path,
    *,
    workspace_dir: Path,
    surface: str,
    remove: bool = False,
    before_write=None,
) -> dict:
    steps: list[dict] = []
    try:
        surfaces = ["agents", "claude", "grok"] if surface == "all" else [surface]
        if "grok" in surfaces:
            from _bootstrap.grok_mcp import plan_rule
            from _bootstrap.file_transaction import FilePlan

            plan_rule(FilePlan(), workspace_dir, remove=remove)
        if remove and "agents" in surfaces:
            status = "noop" if surface == "all" else "error"
            steps.append(_step("workspace_bootstrap_agents", status, "AGENTS.md bootstrap removal is not supported."))
        elif "agents" in surfaces:
            agents_path = find_root_bootstrap_file(workspace_dir, "AGENTS.md") or (workspace_dir / "AGENTS.md")
            status, message = _ensure_bootstrap_file(agents_path, CLAUDE_MD_BOOTSTRAP_VAULT, before_write=before_write)
            steps.append(_step("workspace_bootstrap_agents", status, message))
        if "claude" in surfaces:
            if remove:
                if before_write is not None:
                    before_write()
                removed = mcp_transport.cleanup_claude_bootstrap(workspace_dir)
                status = "changed" if removed else "noop"
                message = (
                    "Removed Brain bootstrap instructions from CLAUDE.md."
                    if removed
                    else "CLAUDE.md has no Brain bootstrap instructions to remove."
                )
            else:
                claude_path = workspace_dir / CLAUDE_MD_FILE
                status, message = _ensure_bootstrap_file(
                    claude_path,
                    bootstrap_line_for_target(workspace_dir),
                    before_write=before_write,
                )
            steps.append(_step("workspace_bootstrap_claude", status, message))
        if "grok" in surfaces:
            from _bootstrap.grok_mcp import configure_rule

            changed = configure_rule(workspace_dir, remove=remove, before_write=before_write)
            steps.append(
                _step(
                    "workspace_bootstrap_grok",
                    "changed" if changed else "noop",
                    "Reconciled native Grok Brain startup rule.",
                )
            )
        return _result_envelope("workspace_bootstrap", vault_root, steps)
    except WorkspaceBindingError as exc:
        return _result_envelope(
            "workspace_bootstrap",
            vault_root,
            [*steps, _step("workspace_bootstrap", "error", str(exc))],
        )
    except (mcp_transport.InitTransportError, OSError, ValueError, RuntimeError) as exc:
        surviving = getattr(exc, "surviving_paths", ())
        if surviving:
            steps.append(
                _step(
                    "workspace_bootstrap_grok",
                    "changed",
                    f"Bootstrap rollback left files requiring recovery: {', '.join(map(str, surviving))}",
                )
            )
        return _result_envelope(
            "workspace_bootstrap",
            vault_root,
            [*steps, _step("workspace_bootstrap", "error", str(exc))],
        )


def _configure_semantic_enable(
    vault_root: Path, *, provision: bool, bootstrap_steps: list[dict]
) -> dict:
    from _lifecycle.semantic_enable import enable_semantic

    return enable_semantic(
        vault_root,
        provision=provision,
        bootstrap_steps=bootstrap_steps,
    )


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
    metadata.add_argument("--clear-links", action="store_true", help="Clear descriptive links, preserving setup-owned links.workspace, before applying --link values.")
    metadata.add_argument("--parent", help="Set defaults.parent to a living artefact in this workspace.")
    metadata.add_argument("--clear-parent", action="store_true", help="Clear the local default parent.")
    metadata.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    bootstrap_sub = workspace_subparsers.add_parser(
        "bootstrap",
        help="Install optional agent bootstrap instructions into the workspace.",
        parents=[make_vault_parent_parser()],
    )
    bootstrap_sub.add_argument(
        "--path",
        "--workspace",
        dest="path",
        help="Workspace directory to update (default: current directory).",
    )
    bootstrap_sub.add_argument(
        "--surface",
        choices=("agents", "claude", "grok", "all"),
        default="all",
        help="Which bootstrap surfaces to manage (default: all).",
    )
    bootstrap_sub.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    bootstrap_sub.add_argument("--remove", action="store_true", help="Remove managed bootstrap instructions.")

    mcp = subparsers.add_parser(
        "mcp",
        help="Configure MCP transport policy explicitly.",
        parents=[make_vault_parent_parser()],
    )
    mcp.add_argument(
        "--client",
        choices=("claude", "codex", "grok", "all"),
        required=True,
        help="Explicit client selection; all selects all currently supported clients.",
    )
    mcp.add_argument("--user", action="store_true", help="Register as the default Brain route for all projects (user scope).")
    mcp.add_argument(
        "--local",
        action="store_true",
        help="Use Claude local scope (.claude/settings.local.json). Unsupported for Codex and Grok.",
    )
    mcp.add_argument(
        "--workspace",
        "--project",
        dest="project",
        help="Target workspace directory to configure (default: current directory).",
    )
    mcp.add_argument("--remove", action="store_true", help="Remove only recorded Brain-managed entries for the requested scope.")
    mcp.add_argument("--force", action="store_true", help="Skip the confirmation prompt for --remove.")
    mcp.add_argument(
        "--vault-self",
        action="store_true",
        dest="vault_self",
        help="Use explicit project-scope vault-self mode for registering this vault's own MCP transport.",
    )
    mcp.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    agent_skill_parser = subparsers.add_parser(
        "agent-skills",
        help="Configure active-Brain skill adapters for Claude and Codex.",
        parents=[make_vault_parent_parser()],
    )
    agent_skill_parser.add_argument(
        "--client",
        choices=("claude", "codex", "grok", "all"),
        default="all",
        help="Which client skill directory to configure (default: all).",
    )
    agent_skill_parser.add_argument(
        "--replace",
        action="store_true",
        help="Archive an existing unmanaged shaping skill before installing the adapter.",
    )
    agent_skill_parser.add_argument(
        "--remove",
        action="store_true",
        help="Remove only an unmodified Brain-managed shaping adapter.",
    )
    agent_skill_parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON."
    )

    return parser.parse_args(argv)


def _mcp_scope(*, user: bool, local: bool) -> str:
    if user:
        return "user"
    if local:
        return "local"
    return "project"


def configure_mcp_action(
    vault_root: Path,
    *,
    client: str,
    user: bool,
    local: bool,
    workspace_dir: Path | None,
    remove: bool,
    force: bool,
    vault_self: bool = False,
) -> dict:
    scope = _mcp_scope(user=user, local=local)
    action = "mcp_remove" if remove else "mcp_configure"

    if user and local:
        return _mcp_error(action, vault_root, "--user cannot be combined with --local")
    if user and workspace_dir is not None:
        return _mcp_error(action, vault_root, "--user cannot be combined with --workspace/--project")

    conflict = "--user" if user else "--local" if local else "--remove" if remove else None
    if vault_self and conflict is not None:
        return _mcp_error(action, vault_root, f"--vault-self cannot be combined with {conflict}")

    try:
        clients, _warnings = mcp_transport._resolve_clients_or_error(client, scope)
    except mcp_transport.InitTransportError as exc:
        return _mcp_error(action, vault_root, str(exc), committed_effects=getattr(exc, "committed_effects", ()))

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
            vault_self=vault_self,
        )
    except mcp_transport.InitTransportError as exc:
        return _mcp_error(action, vault_root, str(exc), committed_effects=getattr(exc, "committed_effects", ()))

    if remove:
        if mcp_result["status"] == "noop":
            status = "noop"
            message = "No recorded Brain-managed MCP entries matched this request."
        else:
            status = "changed"
            message = f"Removed recorded Brain-managed MCP entries for {client} ({scope})."
    else:
        status = mcp_result["status"]
        message = f"Configured Brain MCP transport for {client} ({scope})."

    notes = list(mcp_result.get("verification_notes") or [])
    if not remove and not notes:
        notes = mcp_transport.mcp_followup_notes(clients, scope, workspace_dir)
    for warning in mcp_result.get("warnings", []):
        if warning not in notes:
            notes.append(warning)
    return _result_envelope(action, vault_root, [_step("mcp_transport", status, message,
                            committed_effects=mcp_result.get("committed_effects", []))], notes=notes)


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
        vault_root = None if args.user else find_vault_root(getattr(args, "vault", None))
        action = "mcp_remove" if args.remove else "mcp_configure"
        if args.vault_self and args.user:
            return _emit_result(_mcp_error(action, vault_root, "--vault-self cannot be combined with --user"), as_json=args.json)
        if args.vault_self and args.local:
            return _emit_result(_mcp_error(action, vault_root, "--vault-self cannot be combined with --local"), as_json=args.json)
        if args.vault_self and args.remove:
            return _emit_result(_mcp_error(action, vault_root, "--vault-self cannot be combined with --remove"), as_json=args.json)
        if args.vault_self and not args.project:
            return _emit_result(_mcp_error(action, vault_root, "--vault-self requires --workspace/--project"), as_json=args.json)
        if args.user and args.local:
            return _emit_result(_mcp_error(action, vault_root, "--user cannot be combined with --local"), as_json=args.json)
        if args.user and args.project:
            return _emit_result(_mcp_error(action, vault_root, "--user cannot be combined with --workspace/--project"), as_json=args.json)
        workspace_dir = None
        if not args.user:
            try:
                workspace_dir = resolve_workspace_dir(args.project)
            except WorkspaceBindingError as exc:
                return _emit_result(_mcp_error(action, vault_root, str(exc)), as_json=args.json)
        result = configure_mcp_action(
            vault_root,
            client=args.client,
            user=args.user,
            local=args.local,
            workspace_dir=workspace_dir,
            remove=args.remove,
            force=args.force,
            vault_self=args.vault_self,
        )
        return _emit_result(result, as_json=args.json)

    if args.command == "agent-skills":
        vault_root = find_vault_root(getattr(args, "vault", None))
        result = configure_agent_skills_action(
            Path(vault_root),
            client=args.client,
            replace=args.replace,
            remove=args.remove,
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
            parent=args.parent,
            clear_parent=args.clear_parent,
        )
        return _emit_result(result, as_json=args.json)

    result = configure_workspace_bootstrap_action(
        vault_root,
        workspace_dir=workspace_dir,
        surface=args.surface,
        remove=args.remove,
    )
    return _emit_result(result, as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
