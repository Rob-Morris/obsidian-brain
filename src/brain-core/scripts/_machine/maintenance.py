"""Machine-level runtime topology analysis and mutation beneath the CLI family."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from typing import Any

from _bootstrap import diagnostics as bootstrap_diagnostics
from _bootstrap.runtime import step as _step
from _common import central_venvs_root
from _lifecycle_common import derive_step_status
from _repair_common import build_repair_argv

from ._labels import brain_label
from .discovery import discover_brains, sync_machine_registry
from .topology import (
    classify_brain_runtime,
    find_live_brain_runtime_processes,
    list_central_runtimes,
)


DELEGATED_REPAIR_TIMEOUT = 300
_DELEGATED_OK_STATUSES = {"planned", "noop", "changed"}


@dataclass(frozen=True)
class _LegacyTargetSelection:
    targets: list[dict[str, Any]]
    step: dict[str, Any]




def collect_machine_summary(*, current_vault: str | None = None, launcher_python: str | None = None) -> dict[str, Any]:
    """Collect the shared machine-runtime summary for Doctor and machine actions."""
    discovery = discover_brains(current_vault=current_vault)
    machine_registry = sync_machine_registry(discovery["brains"])
    return inspect_machine_runtime_state(
        launcher_python=launcher_python,
        discovery=discovery,
        machine_registry=machine_registry,
    )


def inspect_machine_runtime_state(
    *,
    launcher_python: str | None = None,
    discovery: dict[str, Any],
    machine_registry: dict[str, Any],
) -> dict[str, Any]:
    """Inspect discovered Brains and shared runtimes on this machine."""
    brains: list[dict[str, Any]] = []

    for brain in discovery["brains"]:
        runtime = classify_brain_runtime(brain["path"], launcher_python=launcher_python)
        repair_findings = bootstrap_diagnostics.collect_registry_check_findings(brain["path"])
        repair_findings.extend(bootstrap_diagnostics.collect_mcp_check_findings(brain["path"]))
        record = dict(brain)
        record["runtime"] = runtime
        record["repair_findings"] = repair_findings
        brains.append(record)

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
        orphan_candidate = live_usage["available"] and not selected_by and not live_processes
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
            brain["runtime"]["healthy_runtime"]
            and not brain["runtime"]["legacy_runtime_present"]
            and not brain["repair_findings"]
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
            "brains_with_repair_findings": sum(1 for brain in brains if brain["repair_findings"]),
            "repair_findings": sum(len(brain["repair_findings"]) for brain in brains),
            "machine_registry_brains": machine_registry["brains_count"],
            "stale_machine_registry_entries": len(machine_registry["stale_machine_registry_entries"]),
            "stale_registry_entries": len(discovery["stale_registry_entries"]),
            "runtimes": len(runtime_rows),
            "orphan_candidates": len(orphan_candidates),
        },
    }


def _match_brain_selector(brain: dict[str, Any], selector: str) -> bool:
    candidate = brain["path"]
    if selector == candidate:
        return True
    alias = brain.get("alias")
    return alias == selector


def _build_action_result(
    action: str,
    *,
    dry_run: bool,
    steps: list[dict[str, Any]],
    targets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    target_rows = list(targets or [])
    return {
        "action": action,
        "dry_run": dry_run,
        "status": derive_step_status(steps, dry_run=dry_run),
        "steps": steps,
        "targets": target_rows,
        "counts": {
            "targets": len(target_rows),
            "changed_targets": sum(1 for row in target_rows if row["status"] == "ok"),
            "planned_targets": sum(1 for row in target_rows if row["status"] == "planned"),
            "error_targets": sum(1 for row in target_rows if row["status"] in {"error", "partial"}),
        },
    }


def _record_target(
    target_rows: list[dict[str, Any]],
    top_level_steps: list[dict[str, Any]],
    *,
    target_row: dict[str, Any],
    summary_metadata: dict[str, Any],
) -> None:
    target_rows.append(target_row)
    status = target_row["status"]
    step_status = {
        "ok": "changed",
        "planned": "planned",
        "partial": "error",
        "error": "error",
        "noop": "noop",
    }[status]
    top_level_steps.append(
        _step(
            "target",
            step_status,
            f"{target_row['label']}: {status}",
            **summary_metadata,
        )
    )


def _run_repair_scope(
    vault_root: str | Path,
    scope: str,
    *,
    launcher_python: str | None,
    dry_run: bool,
) -> dict[str, Any]:
    vault_path = Path(vault_root).resolve()
    launcher = launcher_python or sys.executable
    argv = build_repair_argv(
        vault_path,
        scope,
        launcher=launcher,
        json_mode=True,
        dry_run=dry_run,
    )
    command = shlex.join(argv)

    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=DELEGATED_REPAIR_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _step(
            scope,
            "error",
            f"Could not run target Brain repair scope {scope}: {exc}",
            command=command,
        )

    payload = None
    stdout = result.stdout.strip()
    if stdout:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            payload = None

    if payload is None:
        message = result.stderr.strip() or stdout or f"repair.py {scope} exited {result.returncode}"
        return _step(
            scope,
            "error",
            f"Target Brain repair scope {scope} did not produce valid JSON: {message}",
            command=command,
        )

    delegated_status = payload.get("status")
    delegated_map = {
        "planned": ("planned", f"Would run target Brain repair scope {scope}."),
        "noop": ("noop", f"Target Brain repair scope {scope} is already clean."),
        "ok": ("changed", f"Ran target Brain repair scope {scope}."),
        "partial": ("partial", f"Target Brain repair scope {scope} completed partially."),
        "error": ("error", f"Target Brain repair scope {scope} failed."),
    }
    status, message = delegated_map.get(
        delegated_status,
        ("error", f"Target Brain repair scope {scope} returned unknown status {delegated_status!r}."),
    )

    return _step(
        scope,
        status,
        message,
        command=command,
        delegated_status=delegated_status,
        delegated_result=payload,
    )


def _select_legacy_targets(
    summary: dict[str, Any],
    selector: str | None,
    *,
    dry_run: bool,
) -> _LegacyTargetSelection:
    brains = summary["brains"]
    if selector is None:
        targets = [brain for brain in brains if brain["runtime"]["status"] == "legacy_vault_venv"]
        if not targets:
            return _LegacyTargetSelection(
                targets=[],
                step=_step("selection", "noop", "No discovered legacy Brains need migration."),
            )
        return _LegacyTargetSelection(
            targets=targets,
            step=_step(
                "selection",
                "planned" if dry_run else "noop",
                f"Selected {len(targets)} legacy Brain(s) for migration.",
            ),
        )

    matching = [brain for brain in brains if _match_brain_selector(brain, selector)]
    if not matching:
        return _LegacyTargetSelection(
            targets=[],
            step=_step("selection", "error", f"No discovered Brain matches {selector!r}."),
        )

    legacy = [brain for brain in matching if brain["runtime"]["status"] == "legacy_vault_venv"]
    if not legacy:
        return _LegacyTargetSelection(
            targets=[],
            step=_step(
                "selection",
                "noop",
                f"Selected Brain {selector!r} is not currently using a legacy vault-local .venv.",
            ),
        )

    return _LegacyTargetSelection(
        targets=legacy,
        step=_step(
            "selection",
            "planned" if dry_run else "noop",
            f"Selected {len(legacy)} legacy Brain(s) for migration.",
        ),
    )


def _legacy_venv_step(
    *,
    dry_run: bool,
    live_scan_available: bool,
    live_processes: list[dict[str, Any]],
    delegated_cleanup_safe: bool,
    legacy_dir: Path,
) -> dict[str, Any]:
    if dry_run:
        if not live_scan_available:
            return _step(
                "legacy_venv",
                "planned",
                "Would remove the legacy vault-local .venv after proving no live process still uses it.",
                path=str(legacy_dir),
            )
        if live_processes:
            return _step(
                "legacy_venv",
                "planned",
                "Would remove the legacy vault-local .venv once no live process is using it.",
                path=str(legacy_dir),
                live_processes=live_processes,
            )
        return _step(
            "legacy_venv",
            "planned",
            "Would remove the legacy vault-local .venv after delegated repairs succeed.",
            path=str(legacy_dir),
        )

    if not live_scan_available:
        return _step(
            "legacy_venv",
            "error",
            "Cannot remove the legacy vault-local .venv because live-process detection is unavailable.",
            path=str(legacy_dir),
        )
    if live_processes:
        return _step(
            "legacy_venv",
            "error",
            "Cannot remove the legacy vault-local .venv because it is still in use by a live process.",
            path=str(legacy_dir),
            live_processes=live_processes,
        )
    if not delegated_cleanup_safe:
        return _step(
            "legacy_venv",
            "noop",
            "Left the legacy vault-local .venv in place because delegated repairs did not complete cleanly.",
            path=str(legacy_dir),
        )
    if not legacy_dir.exists():
        return _step(
            "legacy_venv",
            "noop",
            "Legacy vault-local .venv was already absent.",
            path=str(legacy_dir),
        )
    return _execute_removal_step(
        name="legacy_venv",
        target_path=legacy_dir,
        success_message="Removed the legacy vault-local .venv after central-runtime migration.",
    )


def _execute_removal_step(
    *,
    name: str,
    target_path: Path,
    success_message: str,
    **metadata: Any,
) -> dict[str, Any]:
    try:
        shutil.rmtree(target_path)
    except OSError as exc:
        return _step(
            name,
            "error",
            f"Could not remove {target_path}: {exc}",
            path=str(target_path),
            **metadata,
        )
    return _step(
        name,
        "changed",
        success_message,
        path=str(target_path),
        **metadata,
    )


def _verify_migrated_runtime(brain: dict[str, Any], *, launcher_python: str | None) -> dict[str, Any]:
    verification = classify_brain_runtime(brain["path"], launcher_python=launcher_python)
    if verification["healthy_runtime"] and not verification["legacy_runtime_present"]:
        return _step(
            "verify",
            "noop",
            "Brain now resolves to a shared central runtime without a legacy .venv.",
            runtime_status=verification["status"],
        )
    return _step(
        "verify",
        "error",
        verification["message"],
        runtime_status=verification["status"],
    )


def migrate_legacy_brains(
    summary: dict[str, Any],
    *,
    launcher_python: str | None,
    dry_run: bool,
    selector: str | None = None,
) -> dict[str, Any]:
    selection = _select_legacy_targets(summary, selector, dry_run=dry_run)
    if not selection.targets:
        return _build_action_result(
            "migrate-legacy",
            dry_run=dry_run,
            steps=[selection.step],
        )

    legacy_usage = find_live_brain_runtime_processes(
        brain["runtime"]["legacy_runtime_python"]
        for brain in selection.targets
    )

    target_rows: list[dict[str, Any]] = []
    top_level_steps: list[dict[str, Any]] = [selection.step]

    for brain in selection.targets:
        steps: list[dict[str, Any]] = []
        runtime_step = _run_repair_scope(
            brain["path"],
            "runtime",
            launcher_python=launcher_python,
            dry_run=dry_run,
        )
        steps.append(runtime_step)

        delegated_cleanup_safe = runtime_step["status"] in _DELEGATED_OK_STATUSES
        scopes = sorted(
            {
                finding["repair"]["scope"]
                for finding in brain["repair_findings"]
                if finding["repair"]["scope"] in {"mcp", "registry"}
            }
        )
        for scope in scopes:
            repair_step = _run_repair_scope(
                brain["path"],
                scope,
                launcher_python=launcher_python,
                dry_run=dry_run,
            )
            steps.append(repair_step)
            delegated_cleanup_safe = delegated_cleanup_safe and repair_step["status"] in _DELEGATED_OK_STATUSES

        legacy_python = brain["runtime"]["legacy_runtime_python"]
        live_processes = legacy_usage["processes"].get(legacy_python, [])
        steps.append(
            _legacy_venv_step(
                dry_run=dry_run,
                live_scan_available=legacy_usage["available"],
                live_processes=live_processes,
                delegated_cleanup_safe=delegated_cleanup_safe,
                legacy_dir=Path(brain["runtime"]["legacy_runtime_dir"]),
            )
        )

        if not dry_run:
            steps.append(_verify_migrated_runtime(brain, launcher_python=launcher_python))

        target_status = derive_step_status(steps, dry_run=dry_run)
        _record_target(
            target_rows,
            top_level_steps,
            target_row={
                "brain": {"alias": brain.get("alias"), "path": brain["path"]},
                "label": brain_label(brain),
                "status": target_status,
                "steps": steps,
            },
            summary_metadata={"brain_path": brain["path"]},
        )

    return _build_action_result(
        "migrate-legacy",
        dry_run=dry_run,
        steps=top_level_steps,
        targets=target_rows,
    )


def prune_orphaned_runtimes(
    summary: dict[str, Any],
    *,
    dry_run: bool,
) -> dict[str, Any]:
    if not summary["live_process_scan_available"]:
        return _build_action_result(
            "prune-runtimes",
            dry_run=dry_run,
            steps=[
                _step(
                    "selection",
                    "error",
                    "Cannot prune shared runtimes because live-process detection is unavailable.",
                )
            ],
        )

    targets = [runtime for runtime in summary["runtimes"] if runtime["orphan_candidate"]]
    if not targets:
        return _build_action_result(
            "prune-runtimes",
            dry_run=dry_run,
            steps=[_step("selection", "noop", "No orphaned shared runtimes need pruning.")],
        )

    target_rows: list[dict[str, Any]] = []
    top_level_steps: list[dict[str, Any]] = [
        _step(
            "selection",
            "planned" if dry_run else "noop",
            f"Selected {len(targets)} orphaned shared runtime(s) for pruning.",
        )
    ]
    for runtime in targets:
        runtime_dir = Path(runtime["dir"])
        if dry_run:
            prune_step = _step(
                "prune",
                "planned",
                "Would remove the orphaned shared runtime directory.",
                path=str(runtime_dir),
                python=runtime["python"],
            )
        elif not runtime_dir.exists():
            prune_step = _step(
                "prune",
                "noop",
                "Shared runtime directory was already absent.",
                path=str(runtime_dir),
                python=runtime["python"],
            )
        else:
            prune_step = _execute_removal_step(
                name="prune",
                target_path=runtime_dir,
                success_message="Removed the orphaned shared runtime directory.",
                python=runtime["python"],
            )

        steps = [prune_step]
        target_status = derive_step_status(steps, dry_run=dry_run)
        _record_target(
            target_rows,
            top_level_steps,
            target_row={
                "runtime": {
                    "name": runtime["name"],
                    "dir": runtime["dir"],
                    "python": runtime["python"],
                },
                "label": runtime["python"],
                "status": target_status,
                "steps": steps,
            },
            summary_metadata={"python": runtime["python"]},
        )

    return _build_action_result(
        "prune-runtimes",
        dry_run=dry_run,
        steps=top_level_steps,
        targets=target_rows,
    )
