#!/usr/bin/env python3
"""Shared lifecycle helpers for configure/repair-style CLI flows."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _bootstrap.runtime import (
    BOOTSTRAP_SCOPE_MODULES,
    BOOTSTRAP_SUMMARY_ENV,
    DEFAULT_MANAGED_RUNTIME_LAUNCHER,
    MANAGED_RUNTIME_ENV,
    MANAGED_RUNTIME_REQUIRED_MODULES,
    bootstrap_managed_runtime,
    current_process_in_managed_runtime,
    exec_managed_runtime,
    find_launcher_python,
    is_compatible_python,
    load_bootstrap_steps,
    probe_python,
    required_modules_for_scope,
)


def iso_now() -> str:
    """Return the current local timestamp in ISO 8601 form."""
    return datetime.now(timezone.utc).astimezone().isoformat()


def step(name: str, status: str, message: str, **extra: Any) -> dict:
    """Build a structured lifecycle step record."""
    payload = {"name": name, "status": status, "message": message}
    payload.update(extra)
    return payload


def make_result_envelope(
    *,
    vault_root: str | Path,
    managed_python: str,
    steps: list[dict],
    scope: str | None = None,
    action: str | None = None,
    dry_run: bool | None = None,
    status: str | None = None,
    checked_at: str | None = None,
    notes: list[str] | None = None,
) -> dict:
    """Build a canonical lifecycle result envelope for configure and repair flows."""
    if (scope is None) == (action is None):
        raise ValueError("make_result_envelope requires exactly one of scope or action")
    if status is None:
        status = derive_step_status(steps, dry_run=bool(dry_run))
    result = {
        "vault_root": str(vault_root),
        "managed_python": managed_python,
        "status": status,
        "steps": steps,
    }
    if scope is not None:
        result["scope"] = scope
    if action is not None:
        result["action"] = action
    if checked_at is not None:
        result["checked_at"] = checked_at
    if dry_run is not None:
        result["dry_run"] = dry_run
    if notes:
        result["notes"] = notes
    return result


def derive_step_status(steps: list[dict], *, dry_run: bool = False) -> str:
    """Return the canonical status for a list of step records."""
    statuses = {entry["status"] for entry in steps}
    if "error" in statuses:
        success_like = {"changed", "noop", "planned"}
        return "partial" if any(entry["status"] in success_like for entry in steps) else "error"
    if dry_run and "planned" in statuses:
        return "planned"
    if "changed" in statuses:
        return "ok"
    return "noop"


def render_step_label(status: str) -> str:
    """Return a padded step label for human output."""
    label = {
        "changed": "CHANGED",
        "noop": "OK",
        "planned": "PLAN",
        "error": "ERROR",
    }.get(status, status.upper())
    return label[:7].ljust(7)


def render_human_result(result: dict, *, subject_label: str, subject_key: str) -> str:
    """Render a lifecycle result envelope to the shared human-readable format."""
    lines = [
        f"{subject_label}: {result[subject_key]}",
        f"Vault: {result['vault_root']}",
        f"Status: {result['status']}",
    ]
    for entry in result.get("steps", []):
        label = render_step_label(entry["status"])
        lines.append(f"  {label}  {entry['name']}: {entry['message']}")
    for note in result.get("notes", []):
        lines.append(f"  NOTE    {note}")
    return "\n".join(lines)


def exit_code_for_result(result: dict) -> int:
    """Return the CLI exit code for a lifecycle result envelope."""
    status = result.get("status")
    if status == "error":
        return 2
    if status == "partial":
        return 1
    return 0


def find_repair_launcher() -> str | None:
    """Return the best available Python launcher for lifecycle guidance."""
    return find_launcher_python()
