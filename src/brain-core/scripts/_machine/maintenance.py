"""Machine-level runtime topology analysis and mutation beneath the CLI family."""

from __future__ import annotations

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
from _repair_common import build_repair_argv, build_repair_command

from ._labels import brain_label
from .topology import (
    classify_brain_runtime,
    find_live_brain_runtime_processes,
    list_central_runtimes,
)


DELEGATED_REPAIR_TIMEOUT = 300
_DELEGATED_OK_STATUSES = {"planned", "noop", "changed"}


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
        repair_findings = bootstrap_diagnostics.collect_registry_check_findings(brain["path"])
        repair_findings.extend(bootstrap_diagnostics.collect_mcp_check_findings(brain["path"]))
        record = dict(brain)
        record["runtime"] = runtime
        record["repair_findings"] = repair_findings
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
            "machine_registry_brains": machine_registry["brains"],
            "stale_machine_registry_entries": len(machine_registry["stale_machine_registry_entries"]),
            "stale_registry_entries": len(discovery["stale_registry_entries"]),
            "runtimes": len(runtime_rows),
            "selected_runtimes": len(selected_runtimes),
            "orphan_candidates": len(orphan_candidates),
        },
    }


def _match_brain_selector(brain: dict[str, Any], selector: str) -> bool:
    candidate = brain["path"]
    if selector == candidate:
        return True
    alias = brain.get("alias")
    return alias == selector


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
        return {
            "step": _step(
                scope,
                "error",
                f"Could not run target Brain repair scope {scope}: {exc}",
                command=command,
            ),
            "payload": None,
        }

    payload = None
    stdout = result.stdout.strip()
    if stdout:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            payload = None

    if payload is None:
        message = result.stderr.strip() or stdout or f"repair.py {scope} exited {result.returncode}"
        return {
            "step": _step(
                scope,
                "error",
                f"Target Brain repair scope {scope} did not produce valid JSON: {message}",
                command=command,
            ),
            "payload": None,
        }

    delegated_status = payload.get("status")
    if delegated_status == "planned":
        status = "planned"
        message = f"Would run target Brain repair scope {scope}."
    elif delegated_status == "noop":
        status = "noop"
        message = f"Target Brain repair scope {scope} is already clean."
    elif delegated_status == "ok":
        status = "changed"
        message = f"Ran target Brain repair scope {scope}."
    elif delegated_status == "partial":
        status = "partial"
        message = f"Target Brain repair scope {scope} completed partially."
    elif delegated_status == "error":
        status = "error"
        message = f"Target Brain repair scope {scope} failed."
    else:
        status = "error"
        message = f"Target Brain repair scope {scope} returned unknown status {delegated_status!r}."

    return {
        "step": _step(
            scope,
            status,
            message,
            command=command,
            delegated_status=delegated_status,
            delegated_result=payload,
        ),
        "payload": payload,
    }


def _legacy_targets(summary: dict[str, Any], selector: str | None) -> tuple[list[dict[str, Any]] | None, str | None]:
    brains = summary["brains"]
    if selector is None:
        return [brain for brain in brains if brain["runtime"]["status"] == "legacy_vault_venv"], None

    matching = [brain for brain in brains if _match_brain_selector(brain, selector)]
    if not matching:
        return None, f"No discovered Brain matches {selector!r}."

    legacy = [brain for brain in matching if brain["runtime"]["status"] == "legacy_vault_venv"]
    if not legacy:
        return [], f"Selected Brain {selector!r} is not currently using a legacy vault-local .venv."
    return legacy, None


def _legacy_venv_step(
    *,
    dry_run: bool,
    live_scan_available: bool,
    live_processes: list[dict[str, Any]],
    delegated_cleanup_safe: bool,
    legacy_dir: Path,
) -> tuple[dict[str, Any] | None, bool]:
    if dry_run:
        if not live_scan_available:
            return (
                _step(
                    "legacy_venv",
                    "planned",
                    "Would remove the legacy vault-local .venv after proving no live process still uses it.",
                    path=str(legacy_dir),
                ),
                False,
            )
        if live_processes:
            return (
                _step(
                    "legacy_venv",
                    "planned",
                    "Would remove the legacy vault-local .venv once no live process is using it.",
                    path=str(legacy_dir),
                    live_processes=live_processes,
                ),
                False,
            )
        return (
            _step(
                "legacy_venv",
                "planned",
                "Would remove the legacy vault-local .venv after delegated repairs succeed.",
                path=str(legacy_dir),
            ),
            False,
        )

    if not live_scan_available:
        return (
            _step(
                "legacy_venv",
                "error",
                "Cannot remove the legacy vault-local .venv because live-process detection is unavailable.",
                path=str(legacy_dir),
            ),
            False,
        )
    if live_processes:
        return (
            _step(
                "legacy_venv",
                "error",
                "Cannot remove the legacy vault-local .venv because it is still in use by a live process.",
                path=str(legacy_dir),
                live_processes=live_processes,
            ),
            False,
        )
    if not delegated_cleanup_safe:
        return (
            _step(
                "legacy_venv",
                "noop",
                "Left the legacy vault-local .venv in place because delegated repairs did not complete cleanly.",
                path=str(legacy_dir),
            ),
            False,
        )
    if not legacy_dir.exists():
        return (
            _step(
                "legacy_venv",
                "noop",
                "Legacy vault-local .venv was already absent.",
                path=str(legacy_dir),
            ),
            False,
        )
    return None, True


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
    targets, selection_error = _legacy_targets(summary, selector)
    if targets is None:
        steps = [_step("selection", "error", selection_error or "No matching Brain found.")]
        return {
            "action": "migrate-legacy",
            "dry_run": dry_run,
            "status": derive_step_status(steps, dry_run=dry_run),
            "steps": steps,
            "targets": [],
            "counts": {
                "targets": 0,
                "changed_targets": 0,
                "planned_targets": 0,
                "error_targets": 1,
            },
        }

    if not targets:
        steps = [_step("selection", "noop", selection_error or "No discovered legacy Brains need migration.")]
        return {
            "action": "migrate-legacy",
            "dry_run": dry_run,
            "status": derive_step_status(steps, dry_run=dry_run),
            "steps": steps,
            "targets": [],
            "counts": {
                "targets": 0,
                "changed_targets": 0,
                "planned_targets": 0,
                "error_targets": 0,
            },
        }

    legacy_usage = find_live_brain_runtime_processes(
        brain["runtime"]["legacy_runtime_python"]
        for brain in targets
    )

    target_rows: list[dict[str, Any]] = []
    top_level_steps: list[dict[str, Any]] = [
        _step(
            "selection",
            "planned" if dry_run else "noop",
            f"Selected {len(targets)} legacy Brain(s) for migration.",
        )
    ]
    flat_steps: list[dict[str, Any]] = []

    for brain in targets:
        steps: list[dict[str, Any]] = []
        runtime_result = _run_repair_scope(
            brain["path"],
            "runtime",
            launcher_python=launcher_python,
            dry_run=dry_run,
        )
        steps.append(runtime_result["step"])

        delegated_cleanup_safe = runtime_result["step"]["status"] in _DELEGATED_OK_STATUSES
        scopes = sorted(
            {
                finding["repair"]["scope"]
                for finding in brain["repair_findings"]
                if finding["repair"]["scope"] in {"mcp", "registry"}
            }
        )
        for scope in scopes:
            repair_result = _run_repair_scope(
                brain["path"],
                scope,
                launcher_python=launcher_python,
                dry_run=dry_run,
            )
            steps.append(repair_result["step"])
            delegated_cleanup_safe = delegated_cleanup_safe and repair_result["step"]["status"] in _DELEGATED_OK_STATUSES

        legacy_dir = Path(brain["runtime"]["legacy_runtime_dir"])
        legacy_python = brain["runtime"]["legacy_runtime_python"]
        live_processes = legacy_usage["processes"].get(legacy_python, [])
        legacy_step, should_remove_legacy = _legacy_venv_step(
            dry_run=dry_run,
            live_scan_available=legacy_usage["available"],
            live_processes=live_processes,
            delegated_cleanup_safe=delegated_cleanup_safe,
            legacy_dir=legacy_dir,
        )
        if should_remove_legacy:
            legacy_step = _execute_removal_step(
                name="legacy_venv",
                target_path=legacy_dir,
                success_message="Removed the legacy vault-local .venv after central-runtime migration.",
            )
        else:
            assert legacy_step is not None
        steps.append(legacy_step)

        if not dry_run:
            steps.append(_verify_migrated_runtime(brain, launcher_python=launcher_python))

        target_status = derive_step_status(steps, dry_run=dry_run)
        target_rows.append(
            {
                "brain": {"alias": brain.get("alias"), "path": brain["path"]},
                "label": brain_label(brain),
                "status": target_status,
                "steps": steps,
            }
        )
        top_level_steps.append(
            _step(
                "target",
                target_status,
                f"{brain_label(brain)}: {target_status}",
                brain_path=brain["path"],
            )
        )
        flat_steps.extend(steps)

    return {
        "action": "migrate-legacy",
        "dry_run": dry_run,
        "status": derive_step_status(flat_steps, dry_run=dry_run),
        "steps": top_level_steps,
        "targets": target_rows,
        "counts": {
            "targets": len(target_rows),
            "changed_targets": sum(1 for row in target_rows if row["status"] == "ok"),
            "planned_targets": sum(1 for row in target_rows if row["status"] == "planned"),
            "error_targets": sum(1 for row in target_rows if row["status"] in {"error", "partial"}),
        },
    }


def prune_orphaned_runtimes(
    summary: dict[str, Any],
    *,
    dry_run: bool,
) -> dict[str, Any]:
    if not summary["live_process_scan_available"]:
        steps = [
            _step(
                "selection",
                "error",
                "Cannot prune shared runtimes because live-process detection is unavailable.",
            )
        ]
        return {
            "action": "prune-runtimes",
            "dry_run": dry_run,
            "status": derive_step_status(steps, dry_run=dry_run),
            "steps": steps,
            "targets": [],
            "counts": {
                "targets": 0,
                "changed_targets": 0,
                "planned_targets": 0,
                "error_targets": 1,
            },
        }

    targets = [runtime for runtime in summary["runtimes"] if runtime["orphan_candidate"]]
    if not targets:
        steps = [_step("selection", "noop", "No orphaned shared runtimes need pruning.")]
        return {
            "action": "prune-runtimes",
            "dry_run": dry_run,
            "status": derive_step_status(steps, dry_run=dry_run),
            "steps": steps,
            "targets": [],
            "counts": {
                "targets": 0,
                "changed_targets": 0,
                "planned_targets": 0,
                "error_targets": 0,
            },
        }

    target_rows: list[dict[str, Any]] = []
    top_level_steps: list[dict[str, Any]] = [
        _step(
            "selection",
            "planned" if dry_run else "noop",
            f"Selected {len(targets)} orphaned shared runtime(s) for pruning.",
        )
    ]
    flat_steps: list[dict[str, Any]] = []
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
        target_rows.append(
            {
                "runtime": {
                    "name": runtime["name"],
                    "dir": runtime["dir"],
                    "python": runtime["python"],
                },
                "label": runtime["python"],
                "status": target_status,
                "steps": steps,
            }
        )
        top_level_steps.append(
            _step(
                "target",
                target_status,
                f"{runtime['python']}: {target_status}",
                python=runtime["python"],
            )
        )
        flat_steps.extend(steps)

    return {
        "action": "prune-runtimes",
        "dry_run": dry_run,
        "status": derive_step_status(flat_steps, dry_run=dry_run),
        "steps": top_level_steps,
        "targets": target_rows,
        "counts": {
            "targets": len(target_rows),
            "changed_targets": sum(1 for row in target_rows if row["status"] == "ok"),
            "planned_targets": sum(1 for row in target_rows if row["status"] == "planned"),
            "error_targets": sum(1 for row in target_rows if row["status"] in {"error", "partial"}),
        },
    }
