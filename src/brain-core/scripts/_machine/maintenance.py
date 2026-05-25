"""Machine-level runtime topology analysis beneath the CLI family."""

from __future__ import annotations

from typing import Any

from _common import central_venvs_root

from .topology import (
    classify_brain_runtime,
    find_live_brain_runtime_processes,
    list_central_runtimes,
)


def inspect_machine_runtime_state(
    *,
    launcher_python: str | None = None,
    discovery: dict[str, Any],
    machine_registry: dict[str, Any],
) -> dict[str, Any]:
    """Inspect discovered Brains and shared runtimes on this machine."""
    brains: list[dict[str, Any]] = []
    selected_runtimes: set[str] = set()

    for brain in discovery["brains"]:
        runtime = classify_brain_runtime(brain["path"], launcher_python=launcher_python)
        record = dict(brain)
        record["runtime"] = runtime
        brains.append(record)
        if runtime["selected_runtime"] is not None:
            selected_runtimes.add(runtime["selected_runtime"])

    runtimes = list_central_runtimes()
    live_usage = find_live_brain_runtime_processes(rt["python"] for rt in runtimes)
    runtime_rows: list[dict[str, Any]] = []
    orphan_candidates: list[str] = []

    for runtime in runtimes:
        selected_by = [
            brain["path"]
            for brain in brains
            if brain["runtime"]["selected_runtime"] == runtime["python"]
        ]
        live_processes = live_usage["processes"].get(runtime["python"], [])
        orphan_candidate = None
        if live_usage["available"]:
            orphan_candidate = not selected_by and not live_processes
            if orphan_candidate:
                orphan_candidates.append(runtime["python"])
        row = dict(runtime)
        row["selected_by"] = selected_by
        row["live_processes"] = live_processes
        row["orphan_candidate"] = orphan_candidate
        runtime_rows.append(row)

    healthy = (
        not discovery["stale_registry_entries"]
        and not machine_registry["stale_machine_registry_entries"]
        and not machine_registry["blocked"]
        and not machine_registry["malformed_rewritten"]
        and all(
            brain["runtime"]["healthy_runtime"] and not brain["runtime"]["legacy_runtime_present"]
            for brain in brains
        )
    )
    tidy = healthy and live_usage["available"] and not orphan_candidates

    return {
        "healthy": healthy,
        "tidy": tidy,
        "launcher_python": launcher_python,
        "live_process_scan_available": live_usage["available"],
        "machine_registry": machine_registry,
        "venvs_root": str(central_venvs_root()),
        "brains": brains,
        "stale_machine_registry_entries": machine_registry["stale_machine_registry_entries"],
        "stale_registry_entries": discovery["stale_registry_entries"],
        "runtimes": runtime_rows,
        "counts": {
            "brains": len(brains),
            "machine_registry_brains": machine_registry["brains"],
            "stale_machine_registry_entries": len(machine_registry["stale_machine_registry_entries"]),
            "stale_registry_entries": len(discovery["stale_registry_entries"]),
            "runtimes": len(runtime_rows),
            "selected_runtimes": len(selected_runtimes),
            "orphan_candidates": len(orphan_candidates),
        },
    }
