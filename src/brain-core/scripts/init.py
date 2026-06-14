#!/usr/bin/env python3
"""
init.py - Legacy compatibility shell for Brain MCP transport registrations.

This script preserves the old flag-compatible entry point over the shared
configure/setup transport owners. New workflows should use configure.py and
setup.py directly; init.py remains only for legacy installer/removal paths.

Examples:
  1. Current directory:  python3 /vault/.brain-core/scripts/init.py
  2. Claude local only:  python3 /vault/.brain-core/scripts/init.py --client claude --local
  3. User default:       python3 /vault/.brain-core/scripts/init.py --client all --user
  4. Specific folder:    python3 /vault/.brain-core/scripts/init.py --project /path/to/project
  5. Explicit removal:   python3 /vault/.brain-core/scripts/init.py --remove --client all --project /path/to/project

Launcher-safe and dependency-light. Idempotent — safe to re-run. Never
clobbers non-brain MCP config.

Claude uses native project/local/user config surfaces.
Codex uses native project/user config surfaces only.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import List, Optional, Tuple

import _bootstrap.mcp_state as _mcp_state
from _bootstrap.mcp_state import (
    BRAIN_SERVER_NAME,
    CLAUDE_LOCAL_MD_FILE,
    CLAUDE_LOCAL_SETTINGS_FILE,
    CLAUDE_MD_BOOTSTRAP_PROJECT,
    CLAUDE_MD_BOOTSTRAP_VAULT,
    CLAUDE_MD_FILE,
    CLAUDE_PROJECT_CONFIG_FILE,
    CLAUDE_USER_CONFIG_FILE,
    CODEX_CONFIG_REL,
    INIT_STATE_REL,
    bootstrap_line_for_target,
    build_mcp_config,
    build_session_hook_command,
    config_targets_vault,
    configured_vault_root,
    _load_init_state,
    matching_records,
    read_codex_server_config,
    record_init_target,
    remove_codex_server,
    remove_init_records as _remove_init_records,
    write_codex_config,
)
from _bootstrap.mcp_transport import (
    SUPPORTED_CLIENTS,
    SUPPORTED_SCOPES,
    InitTransportError,
    _confirm_removal,
    _resolve_clients,
    _resolve_clients_or_error,
    _scope_label,
    apply_mcp_transport_action,
    claude_project_followup_notes,
    cleanup_claude_bootstrap,
    ensure_claude_md,
    ensure_session_start_hook,
    ensure_workspace_manifest,
    fatal,
    find_python,
    header,
    info,
    register_claude,
    register_codex,
    write_local_settings_json,
    write_project_mcp_json,
    write_user_claude_json,
)
from _bootstrap.vaults import find_vault_root
from _bootstrap.workspace_binding import (
    WORKSPACE_MANIFEST_LEGACY_REL as WORKSPACE_MANIFEST_LEGACY_FILE,
    WORKSPACE_MANIFEST_REL as WORKSPACE_MANIFEST_FILE,
    WorkspaceBindingError,
    is_brain_vault,
)
from _bootstrap.workspace_scaffold import GitInspectionError, ensure_brain_ignore_rules


# ---------------------------------------------------------------------------
# Main helpers
# ---------------------------------------------------------------------------

def _scope_from_args(args: argparse.Namespace) -> Tuple[str, Optional[Path], str]:
    if args.user:
        return "user", None, "user (all projects)"

    target_dir = Path(args.project).resolve() if args.project else Path.cwd().resolve()
    if not target_dir.is_dir():
        fatal(f"Not a directory: {target_dir}")

    scope = "local" if args.local else "project"
    return scope, target_dir, f"{scope} ({target_dir})"


def _ensure_brain_ignore_rules_or_fatal(
    target_dir: Path,
    scope: str,
    clients: List[str],
    *,
    skip_mcp: bool,
) -> None:
    try:
        message = ensure_brain_ignore_rules(target_dir, scope, clients, skip_mcp=skip_mcp)
    except GitInspectionError as exc:
        fatal(f"Failed to install Brain ignore rules: {exc}")
    if message:
        info(message)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Legacy compatibility CLI for Brain MCP transport registrations.",
        epilog=(
            "Examples:\n"
            "  cd /my/project && python3 /vault/.brain-core/scripts/init.py\n"
            "      Configure Claude and Codex for the current directory\n\n"
            "  python3 /vault/.brain-core/scripts/init.py --client claude --local\n"
            "      Configure Claude local scope only (.claude/settings.local.json)\n\n"
            "  python3 /vault/.brain-core/scripts/init.py --client all --user\n"
            "      Register as the default brain for all supported clients\n\n"
            "  python3 /vault/.brain-core/scripts/init.py --client codex --project /my/project\n"
            "      Configure Codex for a specific project without cd-ing into it\n\n"
            "  python3 /vault/.brain-core/scripts/init.py --skip-mcp --project /my/project\n"
            "      Scaffold Brain folder bootstrap only, without writing MCP/client config\n\n"
            "  python3 /vault/.brain-core/scripts/init.py --remove --client all --project /my/project\n"
            "      Remove only recorded Brain-managed project registrations for that folder\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--vault",
        help="Path to Brain vault (default: auto-detect from script location)",
    )
    parser.add_argument(
        "--client",
        choices=("claude", "codex", "all"),
        default="all",
        help="Which client config to write (default: all)",
    )
    parser.add_argument(
        "--user",
        action="store_true",
        help="Register as default brain for all projects (user scope)",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Use Claude local scope (.claude/settings.local.json). Unsupported for Codex.",
    )
    parser.add_argument(
        "--project",
        help="Target folder to configure (default: current directory)",
    )
    parser.add_argument(
        "--skip-mcp",
        action="store_true",
        help="Scaffold folder bootstrap only; skip runtime discovery and MCP/client registration",
    )
    parser.add_argument(
        "--remove",
        action="store_true",
        help="Remove only recorded Brain-managed entries for the requested scope",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip the confirmation prompt for --remove",
    )
    parser.add_argument(
        "--vault-self",
        action="store_true",
        dest="vault_self",
        help=(
            "Register using vault-self mode: set BRAIN_WORKSPACE_DIR to the target "
            "directory (which must be the vault root) and skip workspace binding. "
            "Intended for installing a vault's own project-scope MCP registration."
        ),
    )
    parser.add_argument(
        "--cleanup-bootstrap",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)

    if args.user and (args.local or args.project):
        fatal("--user cannot be combined with --local or --project")
    if args.skip_mcp and args.user:
        fatal("--skip-mcp cannot be combined with --user")
    if args.skip_mcp and args.remove:
        fatal("--skip-mcp cannot be combined with --remove")
    if args.vault_self and args.user:
        fatal("--vault-self cannot be combined with --user")
    if args.vault_self and args.remove:
        fatal("--vault-self cannot be combined with --remove")

    scope, target_dir, scope_label = _scope_from_args(args)
    clients, warnings = _resolve_clients(args.client, scope)

    vault_root = find_vault_root(args.vault)
    header(f"Brain vault: {vault_root}")
    info(f"Scope:   {scope_label}")
    info(f"Client:  {', '.join(clients)}")

    for warning in warnings:
        info(f"Warning: {warning}")

    if args.cleanup_bootstrap:
        if target_dir is None:
            fatal("--cleanup-bootstrap requires a target directory")
        header("Cleaning Claude bootstrap")
        cleanup_claude_bootstrap(target_dir, local=scope == "local")
        header("Done")
        info(f"Target:   {target_dir}")
        print(file=sys.stderr)
        return

    if args.skip_mcp:
        header("Folder bootstrap")
        if target_dir is None:
            fatal("--skip-mcp requires a target directory")

        if not is_brain_vault(target_dir):
            # Only bind non-vault-root directories; a vault root is a Brain, not
            # a workspace of itself (the refuse-guard would raise). Use the narrow
            # `.brain-core/VERSION` predicate (the single source of truth) so an
            # AGENTS.md-only workspace is still bound, not mistaken for a vault.
            ensure_workspace_manifest(target_dir, vault_root=vault_root)
        _ensure_brain_ignore_rules_or_fatal(target_dir, scope, clients, skip_mcp=True)

        header("Done")
        info(f"Vault:    {vault_root}")
        info(f"Target:   {target_dir}")
        info("MCP:      skipped (--skip-mcp)")
        print(file=sys.stderr)
        return

    if args.remove:
        header("Removing MCP registrations")
        if not args.force and not _confirm_removal(scope_label, clients):
            print(file=sys.stderr)
            info("Removal cancelled. No changes made.")
            print(file=sys.stderr)
            return
    try:
        result = apply_mcp_transport_action(
            vault_root,
            client_arg=args.client,
            scope=scope,
            target_dir=target_dir,
            remove=args.remove,
            vault_self=args.vault_self,
        )
    except InitTransportError as exc:
        fatal(str(exc))

    if args.remove:
        if result["status"] == "noop":
            info("No recorded Brain-managed entries matched this request.")
        header("Done")
        info(f"Removed:  {result['removed_count']} recorded registration(s)")
        info(f"Retained: {result['retained_count']} record(s)")
        print(file=sys.stderr)
        return

    header("Done")
    info(f"Vault:    {vault_root}")
    info(f"Scope:    {scope_label}")
    info(f"Clients:  {', '.join(clients)}")
    info(f"Python:   {result['python_path']}")
    for registration in result["results"]:
        info(f"{registration['client'].title()}: {registration['method']}")
    print(file=sys.stderr)

    if result["claude_project_notes"]:
        header("Claude project approval")
        for note in result["claude_project_notes"]:
            info(note)
        print(file=sys.stderr)

    for note in result["verification_notes"]:
        info(note)
    info(f"Remove:   {result['remove_command']}")
    print(file=sys.stderr)


if __name__ == "__main__":
    main()
