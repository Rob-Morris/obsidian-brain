#!/usr/bin/env python3
"""Launcher-safe machine-level maintenance entry point beneath the CLI family."""

from __future__ import annotations

import argparse
import json

from _lifecycle_common import exit_code_for_result, render_step_label
from _machine.discovery import discover_brains, sync_machine_registry
from _machine.maintenance import (
    inspect_machine_runtime_state,
    migrate_legacy_brains,
    prune_orphaned_runtimes,
)


def _render_human(result: dict) -> str:
    lines = [
        f"Machine action: {result['action']}",
        f"Source Brain: {result['source_vault']}",
        f"Current Brain: {result.get('current_vault') or '(none)'}",
        f"Status: {result['status']}",
    ]
    for entry in result.get("steps", []):
        lines.append(f"  {render_step_label(entry['status'])}  {entry['name']}: {entry['message']}")

    targets = result.get("targets", [])
    if not targets:
        return "\n".join(lines)

    lines.append("Targets:")
    for target in targets:
        lines.append(f"  {render_step_label(target['status'])}  {target['label']}")
        for entry in target.get("steps", []):
            lines.append(f"    {render_step_label(entry['status'])}  {entry['name']}: {entry['message']}")
            command = entry.get("command")
            if command:
                lines.append(f"      command: {command}")
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Machine-level Brain runtime maintenance")
    parser.add_argument("--vault", required=True, help="Vault providing the machine helper code")
    parser.add_argument("--current-vault", help="Current vault in scope for this invocation, if any")
    parser.add_argument("--launcher", help="Launcher Python path already chosen by the CLI")
    subparsers = parser.add_subparsers(dest="action", required=True)

    migrate = subparsers.add_parser(
        "migrate-legacy",
        help="Converge discovered legacy Brains onto shared central runtimes.",
    )
    migrate.add_argument("--brain", help="Optional discovered Brain alias or absolute path to target.")
    migrate.add_argument("--dry-run", action="store_true")
    migrate.add_argument("--json", action="store_true")

    prune = subparsers.add_parser(
        "prune-runtimes",
        help="Prune orphaned shared central runtimes.",
    )
    prune.add_argument("--dry-run", action="store_true")
    prune.add_argument("--json", action="store_true")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    discovery = discover_brains(current_vault=args.current_vault)
    machine_registry = sync_machine_registry(discovery["brains"])
    summary = inspect_machine_runtime_state(
        launcher_python=args.launcher,
        discovery=discovery,
        machine_registry=machine_registry,
    )

    if args.action == "migrate-legacy":
        result = migrate_legacy_brains(
            summary,
            launcher_python=args.launcher,
            dry_run=args.dry_run,
            selector=args.brain,
        )
    else:
        result = prune_orphaned_runtimes(summary, dry_run=args.dry_run)

    result["source_vault"] = args.vault
    result["current_vault"] = args.current_vault
    result["launcher_python"] = args.launcher

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(_render_human(result))
    return exit_code_for_result(result)


if __name__ == "__main__":
    raise SystemExit(main())
