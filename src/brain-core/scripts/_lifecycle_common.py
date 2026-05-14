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


from _common import (
    REQUIREMENTS_REL,
    ensure_central_venv,
    resolve_or_provision_central_venv,
    resolve_vault_venv_python,
)
from _common import _venv as _venv_module


DEFAULT_MANAGED_RUNTIME_LAUNCHER = "python3.12"
MANAGED_RUNTIME_REQUIRED_MODULES = ("mcp", "yaml")
# These tuples describe extra managed-runtime module requirements for each
# lifecycle scope. An empty tuple does not bypass bootstrap or runtime repair:
# every scope still goes through the shared managed-runtime owner so it can
# recover a usable interpreter path before packageful work runs.
BOOTSTRAP_SCOPE_MODULES = {
    "runtime": MANAGED_RUNTIME_REQUIRED_MODULES,
    # MCP repair writes config/state only, but those registrations must point
    # at a usable managed Brain runtime that can actually host the MCP server.
    # Keep the package requirement in lockstep with `runtime` so `mcp`
    # composes runtime repair instead of silently assuming it already happened.
    "mcp": MANAGED_RUNTIME_REQUIRED_MODULES,
    "router": (),
    "lexical": (),
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
    """Ensure the central managed runtime is ready, reusing a compatible-minor
    venv before creating a new exact-tag one.

    Delegates the entire resolve/reuse/sync/create decision to
    `_common._venv.resolve_or_provision_central_venv` so every lifecycle
    entry point — repair, configure, upgrade, the `brain` CLI dispatch
    helper — picks the same runtime for the same vault. Callers see a
    uniform envelope with named ``managed_runtime`` and
    ``managed_dependencies`` steps regardless of whether the venv was
    reused, synced in place, or freshly created.

    Path rule comes from `_common._venv` — see DD-048 plus the v0.38.7
    lookup clarification. The convergence here closes the residual
    duplicate-venv risk: pre-v0.38.8 each direct-script entry point
    resolved an exact-tag path of its own choosing, so after Python minor
    churn `repair.py` / `configure.py` / `upgrade.py` could each plan or
    create a new runtime even when an existing compatible one already
    served the vault.
    """
    launcher_path = Path(launcher_python) if launcher_python else None
    if launcher_path is None:
        launcher_str = find_repair_launcher()
        if not launcher_str:
            raise RuntimeError(
                "No compatible Python 3.12+ launcher was found. "
                "Install Python 3.12 or 3.13 and rerun."
            )
        launcher_path = Path(launcher_str)

    requirements = vault_root / REQUIREMENTS_REL
    result = resolve_or_provision_central_venv(
        vault_root,
        launcher=launcher_path,
        required_modules=required_modules,
        dry_run=dry_run,
        timeout=timeout,
    )

    outcome = result["outcome"]
    managed_python_str = result.get("python") or ""
    steps: list[dict] = []

    # ------- managed_runtime step ----------------------------------------
    if outcome == _venv_module.RUNTIME_REUSED:
        steps.append(step(
            "managed_runtime", "noop",
            "Central managed runtime is already present.",
            python=managed_python_str,
        ))
    elif outcome == _venv_module.RUNTIME_SYNCED:
        # The venv already existed; only deps were touched. The runtime step
        # itself is a noop from the caller's perspective.
        steps.append(step(
            "managed_runtime", "noop",
            "Central managed runtime is already present.",
            python=managed_python_str,
        ))
    elif outcome == _venv_module.RUNTIME_CREATED:
        steps.append(step(
            "managed_runtime", "changed",
            "Created or repaired the central managed runtime.",
            python=managed_python_str,
        ))
    elif outcome == _venv_module.RUNTIME_PLANNED:
        if result.get("planned_action") == "create":
            steps.append(step(
                "managed_runtime", "planned",
                "Would create or repair the central managed runtime.",
                python=managed_python_str,
            ))
        else:
            # planned_action == "sync" — runtime itself is fine
            steps.append(step(
                "managed_runtime", "noop",
                "Central managed runtime is already present.",
                python=managed_python_str,
            ))
    elif outcome == _venv_module.RUNTIME_ERROR:
        steps.append(step(
            "managed_runtime", "error",
            result.get("message", "Failed to resolve or provision central managed runtime."),
            python=managed_python_str or "",
        ))

    # ------- managed_dependencies step -----------------------------------
    owner_sentence = dependency_owner[0].upper() + dependency_owner[1:]
    if not required_modules:
        steps.append(step(
            "managed_dependencies", "noop",
            f"{owner_sentence} does not require additional managed runtime dependencies.",
            requirements=str(requirements),
        ))
    elif outcome == _venv_module.RUNTIME_REUSED:
        steps.append(step(
            "managed_dependencies", "noop",
            f"Managed runtime dependencies required by {dependency_owner} are already available.",
            requirements=str(requirements),
        ))
    elif outcome == _venv_module.RUNTIME_CREATED:
        # `ensure_central_venv` ran pip during creation when modules were required.
        steps.append(step(
            "managed_dependencies", "noop",
            f"Managed runtime dependencies required by {dependency_owner} are already available.",
            requirements=str(requirements),
        ))
    elif outcome == _venv_module.RUNTIME_SYNCED:
        steps.append(step(
            "managed_dependencies", "changed",
            f"Synced the managed runtime dependencies required by {dependency_owner}.",
            requirements=str(requirements),
            synced=list(result.get("synced_modules", ())),
        ))
    elif outcome == _venv_module.RUNTIME_PLANNED:
        steps.append(step(
            "managed_dependencies", "planned",
            f"Would sync the managed runtime dependencies required by {dependency_owner}.",
            requirements=str(requirements),
            missing=list(result.get("missing_modules", ())),
        ))
    elif outcome == _venv_module.RUNTIME_ERROR:
        steps.append(step(
            "managed_dependencies", "error",
            f"Could not sync managed runtime dependencies required by {dependency_owner}: "
            + result.get("message", "unknown error"),
            requirements=str(requirements),
            missing=list(result.get("missing_modules", ())),
        ))

    ready = outcome in (_venv_module.RUNTIME_REUSED, _venv_module.RUNTIME_SYNCED, _venv_module.RUNTIME_CREATED) \
        and bool(managed_python_str)
    if outcome == _venv_module.RUNTIME_PLANNED:
        status = "planned"
    elif ready:
        status = "ready"
    else:
        status = "error"

    return {
        "checked_at": iso_now(),
        "launcher_python": launcher_python,
        "managed_python": managed_python_str,
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
    """Return True when the path is a usable Python 3.12+ launcher.

    Defensive against probe payloads that are not the expected dict shape
    (e.g. a stub binary that prints a bare version string parseable as JSON
    but lacking the "compatible" field). Anything we cannot affirmatively
    verify as compatible is treated as incompatible.
    """
    try:
        probe = probe_python(python_path)
    except RuntimeError:
        return False
    if not isinstance(probe, dict):
        return False
    return bool(probe.get("compatible"))


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
