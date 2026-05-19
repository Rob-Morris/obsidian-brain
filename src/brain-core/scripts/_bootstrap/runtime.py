#!/usr/bin/env python3
"""Shared launcher-safe managed-runtime helpers."""

from __future__ import annotations

from datetime import datetime, timezone
import functools
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

from _common import REQUIREMENTS_REL, resolve_or_provision_central_venv, resolve_vault_venv_python
from _common import _venv as _venv_module


DEFAULT_MANAGED_RUNTIME_LAUNCHER = "python3.12"
MANAGED_RUNTIME_ENV = "BRAIN_MANAGED_RUNTIME"
BOOTSTRAP_SUMMARY_ENV = "BRAIN_BOOTSTRAP_SUMMARY"
MANAGED_RUNTIME_REQUIRED_MODULES = ("mcp",)
BOOTSTRAP_SCOPE_MODULES = {
    "runtime": MANAGED_RUNTIME_REQUIRED_MODULES,
    "mcp": MANAGED_RUNTIME_REQUIRED_MODULES,
    "router": (),
    "lexical": (),
    "registry": (),
    "frontmatter": (),
    "semantic": MANAGED_RUNTIME_REQUIRED_MODULES,
}


def iso_now() -> str:
    """Return the current local timestamp in ISO 8601 form."""
    return datetime.now(timezone.utc).astimezone().isoformat()


def step(name: str, status: str, message: str, **extra: Any) -> dict:
    """Build a structured bootstrap step record."""
    payload = {"name": name, "status": status, "message": message}
    payload.update(extra)
    return payload


def required_modules_for_scope(scope: str) -> tuple[str, ...]:
    """Return third-party modules required by the named lifecycle scope."""
    return BOOTSTRAP_SCOPE_MODULES.get(scope, ())


def probe_python(python_path: str, *, modules: tuple[str, ...] = ()) -> dict:
    """Probe a Python path for compatibility and importable modules."""
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
    try:
        probe = probe_python(python_path)
    except RuntimeError:
        return False
    if not isinstance(probe, dict):
        return False
    return bool(probe.get("compatible"))


@functools.lru_cache(maxsize=2)
def find_launcher_python(*, prefer_path_binaries: bool = False) -> str | None:
    """Return the best available Python launcher for bootstrap work."""
    candidates: list[str] = []
    if prefer_path_binaries:
        candidates.extend(
            path
            for name in ("python3.13", "python3.12", "python3")
            if (path := shutil.which(name))
        )
        if sys.executable:
            candidates.append(sys.executable)
    else:
        if sys.executable:
            candidates.append(sys.executable)
        candidates.extend(
            path
            for name in ("python3.13", "python3.12", "python3")
            if (path := shutil.which(name))
        )

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate not in seen:
            deduped.append(candidate)
            seen.add(candidate)

    for candidate in deduped:
        if candidate and is_compatible_python(candidate):
            return candidate
    return None


def current_process_in_managed_runtime(vault_root: str | Path) -> bool:
    """Return whether this process already runs in the canonical managed runtime."""
    if os.environ.get(MANAGED_RUNTIME_ENV) == "1":
        return True
    managed_python = resolve_vault_venv_python(vault_root)
    return os.path.realpath(sys.executable) == os.path.realpath(str(managed_python))


def bootstrap_managed_runtime(
    vault_root: Path,
    *,
    required_modules: tuple[str, ...],
    dependency_owner: str,
    launcher_python: str | None = None,
    dry_run: bool = False,
    timeout: int = 300,
) -> dict:
    """Ensure the central managed runtime is ready for substantive work."""
    launcher_path = Path(launcher_python) if launcher_python else None
    if launcher_path is None:
        launcher_str = find_launcher_python()
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

    if outcome == _venv_module.RUNTIME_REUSED:
        steps.append(step(
            "managed_runtime", "noop",
            "Central managed runtime is already present.",
            python=managed_python_str,
        ))
    elif outcome == _venv_module.RUNTIME_SYNCED:
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

    ready = (
        outcome in (
            _venv_module.RUNTIME_REUSED,
            _venv_module.RUNTIME_SYNCED,
            _venv_module.RUNTIME_CREATED,
        )
        and bool(managed_python_str)
    )
    if outcome == _venv_module.RUNTIME_PLANNED:
        status = "planned"
    elif ready:
        status = "ready"
    else:
        status = "error"

    return {
        "checked_at": iso_now(),
        "launcher_python": str(launcher_path),
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
) -> None:
    """Re-exec the current script inside the canonical managed runtime."""
    env = os.environ.copy()
    env[MANAGED_RUNTIME_ENV] = "1"
    env[BOOTSTRAP_SUMMARY_ENV] = json.dumps(summary)
    argv = [managed_python, script_path, *forwarded_args]
    os.execve(managed_python, argv, env)


def handoff_current_script_to_managed_runtime(
    vault_root: str | Path,
    *,
    dependency_owner: str,
    forwarded_args: list[str],
    script_path: str,
    required_modules: tuple[str, ...] = MANAGED_RUNTIME_REQUIRED_MODULES,
    launcher_python: str | None = None,
    timeout: int = 300,
) -> dict:
    """Ensure the current wrapper executes inside the canonical managed runtime.

    The caller may continue only when this function returns. If the current
    process is not already the managed runtime, this function either re-execs
    into it or raises `RuntimeError` when bootstrap could not produce a usable
    managed interpreter.
    """
    if current_process_in_managed_runtime(vault_root):
        return {
            "checked_at": iso_now(),
            "managed_python": sys.executable,
            "status": "ready",
            "steps": load_bootstrap_steps(),
            "managed_runtime_ready": True,
        }

    summary = bootstrap_managed_runtime(
        Path(vault_root),
        required_modules=required_modules,
        dependency_owner=dependency_owner,
        launcher_python=launcher_python,
        timeout=timeout,
    )
    if not summary["managed_runtime_ready"]:
        raise RuntimeError("Managed runtime bootstrap did not produce a usable central venv.")

    managed_python = summary["managed_python"]
    if os.path.realpath(sys.executable) != os.path.realpath(managed_python):
        exec_managed_runtime(
            managed_python=managed_python,
            script_path=script_path,
            forwarded_args=forwarded_args,
            summary=summary,
        )
    return summary


def load_bootstrap_steps() -> list[dict]:
    """Read bootstrap steps from the managed-runtime re-exec environment."""
    payload = os.environ.get(BOOTSTRAP_SUMMARY_ENV)
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


def runtime_dependency_missing(python_path: str, module: str) -> bool:
    """Return whether one named module is missing from a Python executable."""
    probe = probe_python(python_path, modules=(module,))
    return module in probe.get("missing", [])
