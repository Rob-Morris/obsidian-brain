#!/usr/bin/env python3
"""Launcher-safe machine-level diagnosis behind `brain doctor`."""

from __future__ import annotations

import argparse
import json

from _machine.discovery import (
    MACHINE_REGISTRY_BLOCK_MESSAGES,
    discover_brains,
    sync_machine_registry,
)
from _machine.maintenance import inspect_machine_runtime_state


def _counted_label(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


def _brain_label(brain: dict) -> str:
    alias = brain.get("alias")
    if alias:
        return f"{alias} ({brain['path']})"
    return brain["path"]


def _machine_registry_state_label(registry: dict) -> str:
    if registry["blocked"]:
        return "blocked"
    if registry["changed"]:
        return "updated"
    return "current"


def _machine_registry_note_lines(summary: dict) -> list[str]:
    registry = summary["machine_registry"]
    if registry["blocked"]:
        return [
            MACHINE_REGISTRY_BLOCK_MESSAGES.get(
                registry.get("blocked_reason"),
                "machine-registry state is blocked; leaving brains.json untouched",
            )
        ]
    if registry["malformed_rewritten"]:
        lines = ["rewrote malformed machine-registry state from current discoveries"]
        if registry.get("backup_path"):
            lines.append(f"backup: {registry['backup_path']}")
        lines.append("re-run brain doctor to confirm clean machine-registry state")
        return lines
    if summary["stale_machine_registry_entries"]:
        return [
            "pruned stale derived machine-registry entries",
            "re-run brain doctor to confirm clean machine-registry state",
        ]
    return []


def _render_human(summary: dict) -> None:
    counts = summary["counts"]
    registry = summary["machine_registry"]
    stale_label = _counted_label(counts["stale_registry_entries"], "entry", "entries")
    orphan_label = _counted_label(counts["orphan_candidates"], "orphan candidate", "orphan candidates")
    registry_brain_label = _counted_label(registry["brains"], "brain", "brains")
    print(
        "brains:    "
        f"{counts['brains']} discovered "
        f"({counts['stale_registry_entries']} stale vault-registry {stale_label})"
    )
    print(
        "registry:  "
        f"{registry['path']} "
        f"({registry['brains']} {registry_brain_label}, {_machine_registry_state_label(registry)})"
    )
    if summary["live_process_scan_available"]:
        print(
            "runtimes:  "
            f"{summary['venvs_root']} "
            f"({counts['runtimes']} present, {counts['orphan_candidates']} {orphan_label})"
        )
    else:
        print(
            "runtimes:  "
            f"{summary['venvs_root']} "
            f"({counts['runtimes']} present, orphan detection unavailable)"
        )

    if summary["stale_registry_entries"]:
        print("stale vault registry:")
        for entry in summary["stale_registry_entries"]:
            print(f"  {entry['alias']}: {entry['path']}")

    if summary["stale_machine_registry_entries"]:
        print("stale machine registry:")
        for entry in summary["stale_machine_registry_entries"]:
            label = entry["alias"] or "(unaliased)"
            print(f"  {label}: {entry['path']}")

    note_lines = _machine_registry_note_lines(summary)
    if note_lines:
        print("registry note:")
        for line in note_lines:
            print(f"  {line}")

    if not summary["live_process_scan_available"]:
        print("runtime note:")
        print("  ps failed; orphan detection skipped")

    if summary["brains"]:
        print("brain routes:")
        for brain in summary["brains"]:
            runtime = brain["runtime"]
            print(f"  {_brain_label(brain)}")
            print(f"    route: {runtime['status']} — {runtime['message']}")
            if runtime["selected_runtime"] is not None:
                print(f"    runtime: {runtime['selected_runtime']}")
            else:
                print(f"    expected runtime: {runtime['expected_runtime']}")
            if runtime["legacy_runtime_present"]:
                print(f"    legacy .venv: {runtime['legacy_runtime_dir']}")

    orphan_candidates = [runtime for runtime in summary["runtimes"] if runtime["orphan_candidate"]]
    if orphan_candidates:
        print("orphan candidates:")
        for runtime in orphan_candidates:
            print(f"  {runtime['python']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Machine-level Brain runtime diagnosis")
    parser.add_argument("--vault", required=True, help="Vault providing the machine helper code")
    parser.add_argument(
        "--current-vault",
        help="Current vault in scope for this invocation, if any",
    )
    parser.add_argument("--launcher", help="Launcher Python path already chosen by the CLI")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    discovery = discover_brains(
        current_vault=args.current_vault,
    )
    machine_registry = sync_machine_registry(discovery["brains"])
    summary = inspect_machine_runtime_state(
        launcher_python=args.launcher,
        discovery=discovery,
        machine_registry=machine_registry,
    )
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        _render_human(summary)
    return 0 if summary["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
