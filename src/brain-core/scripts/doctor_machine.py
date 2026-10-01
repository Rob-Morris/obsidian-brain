#!/usr/bin/env python3
"""Launcher-safe machine-level diagnosis behind `brain doctor`."""

from __future__ import annotations

import argparse
import json

from _common import join_argv
from _machine._labels import brain_label
from _machine.maintenance import collect_machine_summary
from _machine.process_footprint import format_bytes


def _counted_label(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


def register_guidance(vault_root: str) -> str:
    """The launcher command that registers an unregistered Brain."""
    request = json.dumps({"vault_root": vault_root}, separators=(",", ":"), sort_keys=True)
    return join_argv(["brain", "register", "--request-json", request])


def _render_repair_findings(findings: list[dict]) -> list[str]:
    lines: list[str] = []
    for finding in findings:
        repair = finding.get("repair")
        message = finding["message"]
        if repair is None:
            lines.append(f"finding: {finding['check']} — {message}")
            continue
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
    registry = summary["registry"]
    stale_label = _counted_label(counts["stale_registry_entries"], "entry", "entries")
    orphan_label = _counted_label(counts["orphan_candidates"], "orphan candidate", "orphan candidates")
    findings_label = _counted_label(counts["brains_with_repair_findings"], "Brain with findings", "Brains with findings")
    lines = [
        "brains:    "
        f"{counts['brains']} discovered "
        f"({counts['stale_registry_entries']} stale vault-registry {stale_label}, "
        f"{counts['unregistered_brains']} unregistered, "
        f"{counts['brains_with_repair_findings']} {findings_label})",
        "registry:  "
        f"{registry['path']} "
        f"({registry['brains_count']} registered, {'stale' if registry['stale'] else 'current'})",
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

    if summary["unregistered_brains"]:
        lines.append("unregistered brains:")
        for path in summary["unregistered_brains"]:
            lines.append(f"  {path}")
            lines.append(f"    register: {register_guidance(path)}")

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
        measure_memory=True,
    )
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        _render_human(summary)
    return 0 if summary["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
