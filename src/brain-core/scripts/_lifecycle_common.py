#!/usr/bin/env python3
"""Shared lifecycle helpers for configure/repair-style CLI flows."""

from __future__ import annotations

from datetime import datetime, timezone
import functools
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


DEFAULT_MANAGED_RUNTIME_LAUNCHER = "python3.12"
VENV_PYTHON_REL = Path(".venv/bin/python")
REQUIREMENTS_REL = Path(".brain-core/brain_mcp/requirements.txt")
BOOTSTRAP_SCOPE_MODULES = {
    "mcp": ("mcp", "yaml"),
    "router": (),
    "index": (),
    "registry": (),
    "semantic": ("yaml",),
}


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


def required_modules_for_scope(scope: str) -> tuple[str, ...]:
    """Return third-party modules required by the named lifecycle scope."""
    return BOOTSTRAP_SCOPE_MODULES.get(scope, ())


def bootstrap_managed_runtime(
    vault_root: Path,
    *,
    required_modules: tuple[str, ...],
    dependency_owner: str,
    launcher_python: str | None = None,
    dry_run: bool = False,
    timeout: int = 300,
) -> dict:
    """Ensure the vault-local managed runtime exists and has the requested modules."""
    managed_python = vault_root / VENV_PYTHON_REL
    requirements = vault_root / REQUIREMENTS_REL
    steps: list[dict] = []

    runtime_probe = probe_python(str(managed_python))
    managed_exists = managed_python.is_file()

    if managed_exists and runtime_probe.get("compatible"):
        steps.append(step(
            "managed_runtime",
            "noop",
            "Vault-local managed runtime is already present.",
            python=str(managed_python),
        ))
    elif dry_run:
        steps.append(step(
            "managed_runtime",
            "planned",
            "Would create or repair the vault-local managed runtime.",
            python=str(managed_python),
        ))
    else:
        launcher = launcher_python or find_repair_launcher()
        if not launcher:
            raise RuntimeError(
                "No compatible Python 3.12+ launcher was found. "
                "Install Python 3.12 or 3.13 and rerun."
            )
        subprocess.run(
            [launcher, "-m", "venv", str(vault_root / ".venv")],
            check=True,
            timeout=timeout,
        )
        runtime_probe = probe_python(str(managed_python))
        if not runtime_probe.get("compatible"):
            raise AssertionError("Created vault-local .venv is not Python 3.12+")
        steps.append(step(
            "managed_runtime",
            "changed",
            "Created or repaired the vault-local managed runtime.",
            python=str(managed_python),
        ))

    if not required_modules:
        owner_sentence = dependency_owner[0].upper() + dependency_owner[1:]
        steps.append(step(
            "managed_dependencies",
            "noop",
            f"{owner_sentence} does not require additional managed runtime dependencies.",
            requirements=str(requirements),
        ))
        ready = managed_python.is_file() and runtime_probe.get("compatible", False)
    else:
        dependency_probe = probe_python(str(managed_python), modules=required_modules)
        missing = tuple(dependency_probe.get("missing", []))
        if not missing:
            steps.append(step(
                "managed_dependencies",
                "noop",
                f"Managed runtime dependencies required by {dependency_owner} are already available.",
                requirements=str(requirements),
            ))
        elif dry_run:
            steps.append(step(
                "managed_dependencies",
                "planned",
                f"Would sync the managed runtime dependencies required by {dependency_owner}.",
                requirements=str(requirements),
                missing=list(missing),
            ))
        else:
            subprocess.run(
                [str(managed_python), "-m", "pip", "install", "--quiet", "-r", str(requirements)],
                check=True,
                timeout=timeout,
            )
            dependency_probe = probe_python(str(managed_python), modules=required_modules)
            if not dependency_probe.get("ok"):
                raise RuntimeError(
                    "Vault-local dependency sync completed, but required modules are still unavailable."
                )
            steps.append(step(
                "managed_dependencies",
                "changed",
                f"Synced the managed runtime dependencies required by {dependency_owner}.",
                requirements=str(requirements),
            ))
        ready = managed_python.is_file() and dependency_probe.get("ok", False)

    if dry_run and any(current["status"] == "planned" for current in steps):
        status = "planned"
    elif ready:
        status = "ready"
    else:
        status = "error"

    return {
        "checked_at": iso_now(),
        "launcher_python": launcher_python,
        "managed_python": str(managed_python),
        "status": status,
        "steps": steps,
        "managed_runtime_ready": ready,
    }


def exec_managed_runtime(
    *,
    managed_python: str,
    script_path: str,
    forwarded_args: list[str],
    summary: dict,
    managed_runtime_env: str,
    bootstrap_summary_env: str,
) -> None:
    """Re-exec the current script inside the vault-local managed runtime."""
    env = os.environ.copy()
    env[managed_runtime_env] = "1"
    env[bootstrap_summary_env] = json.dumps(summary)
    argv = [managed_python, script_path, *forwarded_args]
    os.execve(managed_python, argv, env)


def load_bootstrap_steps(bootstrap_summary_env: str) -> list[dict]:
    """Read bootstrap steps from the managed-runtime re-exec environment."""
    payload = os.environ.get(bootstrap_summary_env)
    if not payload:
        return []
    try:
        summary = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError("internal bootstrap summary is corrupted") from exc
    if not isinstance(summary, dict):
        raise RuntimeError("internal bootstrap summary payload is invalid")
    steps = summary.get("steps", [])
    if not isinstance(steps, list):
        raise RuntimeError("internal bootstrap summary steps are invalid")
    return steps


def probe_python(python_path: str, *, modules: tuple[str, ...] = ()) -> dict:
    code = (
        "import importlib.util, json, sys; "
        f"mods = {modules!r}; "
        "missing = [name for name in mods if importlib.util.find_spec(name) is None]; "
        "payload = {"
        "  'major': sys.version_info[0],"
        "  'minor': sys.version_info[1],"
        "  'missing': missing,"
        "}; "
        "payload['compatible'] = (payload['major'], payload['minor']) >= (3, 12); "
        "payload['ok'] = payload['compatible'] and not missing; "
        "print(json.dumps(payload))"
    )
    try:
        result = subprocess.run(
            [python_path, "-c", code],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"ok": False, "compatible": False, "missing": list(modules)}
    if result.returncode != 0:
        return {"ok": False, "compatible": False, "missing": list(modules)}
    try:
        return json.loads(result.stdout.strip() or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"probe of {python_path} produced invalid JSON: {result.stdout!r}"
        ) from exc


def is_compatible_python(python_path: str) -> bool:
    """Return True when the path is a usable Python 3.12+ launcher."""
    return bool(probe_python(python_path).get("compatible"))


@functools.lru_cache(maxsize=1)
def find_repair_launcher() -> str | None:
    """Return the best available Python launcher for lifecycle guidance."""
    candidates = [sys.executable]
    for name in ("python3.13", "python3.12", "python3"):
        path = shutil.which(name)
        if path and path not in candidates:
            candidates.append(path)

    for candidate in candidates:
        if candidate and is_compatible_python(candidate):
            return candidate
    return None
