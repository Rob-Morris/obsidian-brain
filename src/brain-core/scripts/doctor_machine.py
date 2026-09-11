#!/usr/bin/env python3
"""Launcher-safe machine-level diagnosis behind `brain doctor`."""

from __future__ import annotations

import argparse
import json

from _machine._labels import brain_label
from _machine.discovery import (
    DEFAULT_MACHINE_REGISTRY_BLOCK_MESSAGE,
    MACHINE_REGISTRY_BLOCK_MESSAGES,
)
from _machine.maintenance import collect_machine_summary
from _machine.process_footprint import format_bytes


def _counted_label(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


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
                DEFAULT_MACHINE_REGISTRY_BLOCK_MESSAGE,
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


def _render_repair_findings(findings: list[dict]) -> list[str]:
    lines: list[str] = []
    for finding in findings:
        repair = finding["repair"]
        message = finding["message"]
        lines.append(f"repair: {repair['scope']} — {message}")
        lines.append(f"command: {repair['command']}")
    return lines


def _memory_lines(summary: dict) -> list[str]:
    memory = summary.get("memory")
    if memory is None:
        return []
    if not summary["live_process_scan_available"]:
        return ["memory:    runtime footprint unavailable (process scan failed)"]
    if memory["process_count"] == 0:
        return ["memory:    no live runtime processes"]
    unmeasured = memory["process_count"] - memory["measured_count"]
    if unmeasured:
        coverage = (
            f"{memory['measured_count']} of {memory['process_count']} live runtime processes "
            f"({unmeasured} unmeasured)"
        )
    else:
        coverage = (
            f"{memory['process_count']} live "
            f"{_counted_label(memory['process_count'], 'runtime process', 'runtime processes')}"
        )
    lines = [f"memory:    {format_bytes(memory['total_bytes'])} across {coverage}"]
    if memory["total_over_threshold"]:
        lines.append(
            f"  total exceeds {format_bytes(memory['total_warn_bytes'])}; "
            "restart idle MCP sessions to reclaim it"
        )
    for process in memory["heavy_processes"]:
        lines.append(
            f"  pid {process['pid']} holds {format_bytes(process['footprint_bytes'])} "
            f"(over {format_bytes(memory['process_warn_bytes'])}): {process['command']}"
        )
    return lines


def render_human_lines(summary: dict) -> list[str]:
    counts = summary["counts"]
    registry = summary["machine_registry"]
    stale_label = _counted_label(counts["stale_registry_entries"], "entry", "entries")
    orphan_label = _counted_label(counts["orphan_candidates"], "orphan candidate", "orphan candidates")
    registry_brain_label = _counted_label(registry["brains_count"], "brain", "brains")
    drifted_label = _counted_label(counts["brains_with_repair_findings"], "Brain with drift", "Brains with drift")
    lines = [
        "brains:    "
        f"{counts['brains']} discovered "
        f"({counts['stale_registry_entries']} stale vault-registry {stale_label}, "
        f"{counts['brains_with_repair_findings']} {drifted_label})",
        "registry:  "
        f"{registry['path']} "
        f"({registry['brains_count']} {registry_brain_label}, {_machine_registry_state_label(registry)})",
    ]
    if summary["live_process_scan_available"]:
        lines.append(
            "runtimes:  "
            f"{summary['venvs_root']} "
            f"({counts['runtimes']} present, {counts['orphan_candidates']} {orphan_label})"
        )
    else:
        lines.append(
            "runtimes:  "
            f"{summary['venvs_root']} "
            f"({counts['runtimes']} present, orphan detection unavailable)"
        )

    lines.extend(_memory_lines(summary))

    if summary["stale_registry_entries"]:
        lines.append("stale vault registry:")
        for entry in summary["stale_registry_entries"]:
            lines.append(f"  {entry['alias']}: {entry['path']}")

    if summary["stale_machine_registry_entries"]:
        lines.append("stale machine registry:")
        for entry in summary["stale_machine_registry_entries"]:
            label = entry["alias"] or "(unaliased)"
            lines.append(f"  {label}: {entry['path']}")

    note_lines = _machine_registry_note_lines(summary)
    if note_lines:
        lines.append("registry note:")
        for line in note_lines:
            lines.append(f"  {line}")

    if not summary["live_process_scan_available"]:
        lines.extend(["runtime note:", "  ps failed; orphan detection skipped"])

    if summary["brains"]:
        lines.append("brain routes:")
        for brain in summary["brains"]:
            runtime = brain["runtime"]
            lines.append(f"  {brain_label(brain)}")
            lines.append(f"    route: {runtime['status']} — {runtime['message']}")
            if runtime["selected_runtime"] is not None:
                lines.append(f"    runtime: {runtime['selected_runtime']}")
            else:
                lines.append(f"    expected runtime: {runtime['expected_runtime']}")
            if runtime["legacy_runtime_present"]:
                lines.append(f"    legacy .venv: {runtime['legacy_runtime_dir']}")
            if brain["repair_findings"]:
                for line in _render_repair_findings(brain["repair_findings"]):
                    lines.append(f"    {line}")

    orphan_candidates = [runtime for runtime in summary["runtimes"] if runtime["orphan_candidate"]]
    if orphan_candidates:
        lines.append("orphan candidates:")
        for runtime in orphan_candidates:
            lines.append(f"  {runtime['python']}")

    return lines


def _render_human(summary: dict) -> None:
    for line in render_human_lines(summary):
        print(line)


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

    summary = collect_machine_summary(
        current_vault=args.current_vault,
        launcher_python=args.launcher,
        synchronise_registry=True,
        measure_memory=True,
    )
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        _render_human(summary)
    return 0 if summary["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
