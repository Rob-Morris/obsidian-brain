#!/usr/bin/env python3
"""
migrate_to_0_48_2.py — Converge Brain MCP project registration state.

Repairs project-scoped Brain MCP config that may have been written with a
launcher/system Python, plus stale Claude SessionStart hooks that predate the
managed-runtime hook command.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from _bootstrap import diagnostics as bootstrap_diagnostics
from _bootstrap import mcp_transport
from _bootstrap.diagnostics import local_mcp_state_present
from _bootstrap.runtime import iso_now, step
from _common import find_vault_root
from _lifecycle_common import exit_code_for_result, make_result_envelope


VERSION = "0.48.2"


def converge_mcp(vault_root: str, *, dry_run: bool = False) -> dict:
    """Converge Brain-managed MCP project registration state."""
    vault = Path(find_vault_root(vault_root))
    if not local_mcp_state_present(vault):
        return make_result_envelope(
            scope="mcp",
            vault_root=vault,
            dry_run=dry_run,
            managed_python=sys.executable,
            checked_at=iso_now(),
            steps=[
                step("claude_project", "noop", "Claude project MCP is not installed for this vault."),
                step("codex_project", "noop", "Codex project MCP is not installed for this vault."),
            ],
        )
    state = bootstrap_diagnostics.inspect_mcp(vault)
    server_config = state["server_config"]
    steps = []
    try:
        steps.append(_repair_claude(vault, server_config, state["claude"], dry_run))
    except (OSError, ValueError) as exc:
        steps.append(step("claude_project", "error", str(exc)))
    try:
        steps.append(_repair_codex(vault, server_config, state["codex"], dry_run))
    except (OSError, ValueError) as exc:
        steps.append(step("codex_project", "error", str(exc)))
    return make_result_envelope(
        scope="mcp",
        vault_root=vault,
        dry_run=dry_run,
        managed_python=sys.executable,
        checked_at=iso_now(),
        steps=steps,
    )


def _record_claude_direct(vault_root: Path, server_config: dict) -> None:
    config_path = vault_root / mcp_transport.CLAUDE_PROJECT_CONFIG_FILE
    mcp_transport.write_project_mcp_json(server_config, vault_root)
    bootstrap_path = mcp_transport.ensure_claude_md(vault_root)
    hook_python = mcp_transport.session_hook_python(server_config)
    hook_path = mcp_transport.ensure_session_start_hook(vault_root, vault_root, python_path=hook_python)
    record = {
        "client": "claude",
        "scope": "project",
        "target_path": str(vault_root),
        "config_path": str(config_path),
        "server_name": mcp_transport.BRAIN_SERVER_NAME,
        "server_config": server_config,
        "bootstrap_path": str(bootstrap_path),
        "bootstrap_line": mcp_transport.bootstrap_line_for_target(vault_root),
        "hook_path": str(hook_path),
        "hook_command": mcp_transport.build_session_hook_command(
            vault_root, vault_root, python_path=hook_python
        ),
        "method": f"{config_path} (direct migration repair)",
    }
    mcp_transport.record_init_target(vault_root, record)


def _repair_claude(vault_root: Path, server_config: dict, claude_state: dict, dry_run: bool) -> dict:
    if not claude_state["present"]:
        return step("claude_project", "noop", "Claude project MCP is not installed for this vault.")
    if claude_state["healthy"]:
        return step("claude_project", "noop", "Claude project MCP state is already healthy.")
    if dry_run:
        return step("claude_project", "planned", "Would repair .mcp.json, CLAUDE.md, session hook, and init-state record.")
    _record_claude_direct(vault_root, server_config)
    return step("claude_project", "changed", "Repaired Claude project MCP config, bootstrap, hook, and init-state record.")


def _repair_codex(vault_root: Path, server_config: dict, codex_state: dict, dry_run: bool) -> dict:
    if not codex_state["present"]:
        return step("codex_project", "noop", "Codex project MCP is not installed for this vault.")
    if codex_state["healthy"]:
        return step("codex_project", "noop", "Codex project MCP state is already healthy.")
    if dry_run:
        return step("codex_project", "planned", "Would repair .codex/config.toml and the init-state record.")
    record = mcp_transport.register_codex(server_config, "project", vault_root)
    mcp_transport.record_init_target(vault_root, record)
    return step("codex_project", "changed", "Repaired Codex project MCP config and init-state record.")


def _strict_repair_result(result: dict) -> dict:
    """Treat any repair step error as migration failure so upgrade can retry."""
    errored_steps = [
        step for step in result.get("steps", [])
        if isinstance(step, dict) and step.get("status") == "error"
    ]
    if not errored_steps:
        return result
    strict = dict(result)
    strict["status"] = "error"
    return strict


def migrate(vault_root: str) -> dict:
    """Upgrade runner entry point."""
    return _strict_repair_result(converge_mcp(vault_root, dry_run=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Converge Brain MCP registration state.")
    parser.add_argument("--vault", help="Path to the Brain vault (default: auto-detect).")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing them.")
    args = parser.parse_args(argv)

    vault_root = find_vault_root(args.vault)
    result = (
        converge_mcp(vault_root, dry_run=True)
        if args.dry_run
        else migrate(vault_root)
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return exit_code_for_result(result)


if __name__ == "__main__":
    raise SystemExit(main())
