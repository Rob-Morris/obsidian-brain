#!/usr/bin/env python3
"""setup.py — public setup owner for vaults and workspaces."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import configure
from _bootstrap.vaults import find_vault_root, make_vault_parent_parser
from _bootstrap.workspace_scaffold import GitInspectionError, ensure_brain_ignore_rules
from _bootstrap.runtime import step as _step
from _bootstrap.workspace_binding import (
    WORKSPACE_REASON_ALREADY_BOUND,
    WorkspaceBindingError,
    resolve_local_brain_alias,
    resolve_workspace_dir,
    workspace_slug,
)
from _lifecycle_common import (
    emit_lifecycle_result,
    exit_code_for_result,
    make_result_envelope,
    render_human_result,
)


def _result_envelope(action: str, vault_root: Path, steps: list[dict], *, notes: list[str] | None = None) -> dict:
    return make_result_envelope(
        action=action,
        vault_root=vault_root,
        managed_python=sys.executable,
        steps=steps,
        notes=notes,
    )


def _render_human(result: dict) -> str:
    return render_human_result(result, subject_label="Setup action", subject_key="action")


def _emit_result(result: dict, *, as_json: bool) -> int:
    emit_lifecycle_result(result, as_json=as_json, render_human=_render_human)
    return exit_code_for_result(result)


def _merge_result(into_steps: list[dict], into_notes: list[str], result: dict) -> None:
    into_steps.extend(result.get("steps", []))
    for note in result.get("notes", []):
        if note not in into_notes:
            into_notes.append(note)


def _setup_workspace_core(
    vault_root: Path,
    *,
    workspace_dir: Path,
    brain_id: str | None,
    slug: str | None,
    force: bool,
) -> dict:
    binding_result = configure.configure_workspace_binding_action(
        vault_root,
        workspace_dir=workspace_dir,
        brain_id=brain_id,
        slug=slug,
        force=force,
    )
    steps = list(binding_result["steps"])
    notes = list(binding_result.get("notes", []))
    if binding_result["status"] == "error":
        return _result_envelope("workspace_setup", vault_root, steps, notes=notes)

    try:
        ignore_message = ensure_brain_ignore_rules(workspace_dir, "project", [], skip_mcp=True)
        if ignore_message:
            steps.append(
                _step(
                    "workspace_local_scaffold",
                    "noop",
                    ignore_message,
                )
            )
        else:
            steps.append(
                _step(
                    "workspace_local_scaffold",
                    "noop",
                    "Workspace-local Brain ignore rules did not need changes.",
                )
            )
    except GitInspectionError as exc:
        steps.append(_step("workspace_local_scaffold", "error", str(exc)))

    notes.append(
        "Optional next steps: `brain configure mcp ...`, `brain configure workspace bootstrap ...`, `brain configure workspace metadata ...`."
    )
    return _result_envelope("workspace_setup", vault_root, steps, notes=notes)


def _prompt(prompt: str, *, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or (default or "")


def _prompt_yes_no(prompt: str, *, default: bool = False) -> bool:
    suffix = " [Y/n]" if default else " [y/N]"
    value = input(f"{prompt}{suffix}: ").strip().lower()
    if not value:
        return default
    return value in {"y", "yes"}


def _prompt_choice(prompt: str, choices: tuple[str, ...], *, default: str) -> str:
    options = "/".join(choices)
    while True:
        value = _prompt(f"{prompt} ({options})", default=default).strip().lower()
        if value in choices:
            return value
        print(f"Choose one of: {', '.join(choices)}")


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _run_guided_workspace_setup(
    vault_root: Path,
    *,
    workspace_dir: Path,
    brain_id: str | None,
    slug: str | None,
) -> dict:
    default_brain = brain_id or resolve_local_brain_alias(vault_root)
    default_slug = slug or workspace_slug(workspace_dir.name)

    chosen_brain = _prompt("Brain ID", default=default_brain)
    chosen_slug = _prompt("Workspace slug", default=default_slug)

    result = _setup_workspace_core(
        vault_root,
        workspace_dir=workspace_dir,
        brain_id=chosen_brain,
        slug=chosen_slug,
        force=False,
    )
    if result["status"] == "error" and any(step.get("reason") == WORKSPACE_REASON_ALREADY_BOUND for step in result["steps"]):
        if _prompt_yes_no("This workspace is already bound differently. Rebind it now?", default=False):
            result = _setup_workspace_core(
                vault_root,
                workspace_dir=workspace_dir,
                brain_id=chosen_brain,
                slug=chosen_slug,
                force=True,
            )

    steps = list(result.get("steps", []))
    notes = list(result.get("notes", []))
    if result["status"] == "error":
        return _result_envelope("workspace_setup", vault_root, steps, notes=notes)

    if _prompt_yes_no("Configure MCP transport now?", default=True):
        mcp_scope = _prompt_choice("MCP scope", ("user", "project", "local"), default="user")
        mcp_client = _prompt_choice("MCP client", ("all", "claude", "codex"), default="all")
        mcp_result = configure.configure_mcp_action(
            vault_root,
            client=mcp_client,
            user=mcp_scope == "user",
            local=mcp_scope == "local",
            workspace_dir=None if mcp_scope == "user" else workspace_dir,
            remove=False,
            force=False,
        )
        _merge_result(steps, notes, mcp_result)

    if _prompt_yes_no("Add agent bootstrap instructions now?", default=False):
        bootstrap_surface = _prompt_choice("Bootstrap surface", ("all", "agents", "claude"), default="all")
        bootstrap_result = configure.configure_workspace_bootstrap_action(
            vault_root,
            workspace_dir=workspace_dir,
            surface=bootstrap_surface,
        )
        _merge_result(steps, notes, bootstrap_result)

    if _prompt_yes_no("Add optional workspace metadata now?", default=False):
        tag_entries = _split_csv(_prompt("Defaults tags (comma-separated)", default=""))
        link_entries = _split_csv(_prompt("Workspace links as name=value pairs (comma-separated)", default=""))
        if tag_entries or link_entries:
            metadata_result = configure.configure_workspace_metadata_action(
                vault_root,
                workspace_dir=workspace_dir,
                tags=tag_entries,
                clear_tags=False,
                links=link_entries,
                clear_links=False,
            )
            _merge_result(steps, notes, metadata_result)
        else:
            steps.append(_step("workspace_metadata", "noop", "No optional metadata changes were requested."))

    return _result_envelope("workspace_setup", vault_root, steps, notes=notes)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Set up Brain vaults and workspaces.",
        parents=[make_vault_parent_parser()],
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    workspace = subparsers.add_parser(
        "workspace",
        help="Ensure a workspace is usable with an existing Brain.",
        parents=[make_vault_parent_parser()],
    )
    workspace.add_argument("path", nargs="?", help="Workspace directory to set up (default: current directory).")
    workspace.add_argument("--brain", help="Explicit local Brain ID to bind to (default: current vault's alias).")
    workspace.add_argument("--slug", help="Explicit workspace slug (default: existing slug or folder-derived slug).")
    workspace.add_argument("--guided", action="store_true", help="Run the interactive guided workspace setup flow.")
    workspace.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    workspace.add_argument("--force", action="store_true", help="Allow rebinding when the workspace is already bound.")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    vault_root = find_vault_root(getattr(args, "vault", None))

    try:
        workspace_dir = resolve_workspace_dir(args.path)
    except WorkspaceBindingError as exc:
        result = _result_envelope(
            "workspace_setup",
            vault_root,
            [_step("workspace_binding", "error", str(exc))],
        )
        return _emit_result(result, as_json=args.json)

    if args.guided:
        result = _run_guided_workspace_setup(
            vault_root,
            workspace_dir=workspace_dir,
            brain_id=args.brain,
            slug=args.slug,
        )
    else:
        result = _setup_workspace_core(
            vault_root,
            workspace_dir=workspace_dir,
            brain_id=args.brain,
            slug=args.slug,
            force=args.force,
        )
    return _emit_result(result, as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
