#!/usr/bin/env python3
"""Shared lifecycle result helpers."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Callable, TextIO


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


def emit_lifecycle_result(
    result: dict,
    *,
    as_json: bool,
    render_human: Callable[[dict], str],
    stream: TextIO | None = None,
) -> None:
    """Emit any result payload as JSON or human text to the chosen stream."""
    if as_json:
        target = stream or sys.stdout
        print(json.dumps(result, indent=2, ensure_ascii=False), file=target)
        return

    target = stream or sys.stdout
    print(render_human(result), file=target)
