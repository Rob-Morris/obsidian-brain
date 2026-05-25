"""Runtime-topology classification for machine-level Brain maintenance."""

from __future__ import annotations

import os
from pathlib import Path
import shlex
import subprocess
from typing import Any, Iterable

from _common import (
    central_venvs_root,
    find_existing_central_venv,
    find_runnable_python,
    legacy_vault_venv_dir,
    legacy_vault_venv_python,
    resolve_vault_venv_python,
)


def _same_path(left: str | Path | None, right: str | Path | None) -> bool:
    if left is None or right is None:
        return False
    return os.path.realpath(str(left)) == os.path.realpath(str(right))


def classify_brain_runtime(
    vault_root: str | Path,
    *,
    launcher_python: str | None = None,
) -> dict[str, Any]:
    """Classify how a discovered Brain currently resolves its runtime."""
    vault_path = Path(vault_root)
    launcher_path = Path(launcher_python) if launcher_python else None

    expected_runtime_path = resolve_vault_venv_python(vault_path, launcher=launcher_path)
    selected_runtime = find_existing_central_venv(vault_path, launcher=launcher_path)
    runnable_runtime = find_runnable_python(vault_path, launcher=launcher_path)
    legacy_runtime_dir = legacy_vault_venv_dir(vault_path)
    legacy_runtime_python = legacy_vault_venv_python(vault_path)
    legacy_runtime_present = legacy_runtime_dir.exists()

    if selected_runtime is not None and _same_path(selected_runtime, expected_runtime_path):
        status = "central_exact"
        message = "Brain resolves to its expected shared central runtime."
    elif selected_runtime is not None:
        status = "central_compatible"
        message = "Brain resolves to a compatible shared central runtime for this requirements hash."
    elif legacy_runtime_python.is_file():
        status = "legacy_vault_venv"
        message = "Brain still falls back to its legacy vault-local .venv."
    elif runnable_runtime is not None and launcher_path is not None and _same_path(runnable_runtime, launcher_path):
        status = "launcher_fallback"
        message = "Brain is falling back to the bare launcher because no managed runtime is available."
    else:
        status = "missing_runtime"
        message = "Brain has no central runtime and no runnable legacy or launcher fallback."

    return {
        "status": status,
        "message": message,
        "healthy_runtime": status in {"central_exact", "central_compatible"},
        "expected_runtime": str(expected_runtime_path),
        "selected_runtime": str(selected_runtime) if selected_runtime is not None else None,
        "runnable_runtime": str(runnable_runtime) if runnable_runtime is not None else None,
        "legacy_runtime_dir": str(legacy_runtime_dir),
        "legacy_runtime_python": str(legacy_runtime_python),
        "legacy_runtime_present": legacy_runtime_present,
    }


def list_central_runtimes() -> list[dict[str, str]]:
    """Return every central runtime that currently exists on this machine."""
    root = central_venvs_root()
    if not root.is_dir():
        return []

    runtimes: list[dict[str, str]] = []
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        python_path = entry / "bin" / "python"
        if entry.is_dir() and python_path.is_file():
            runtimes.append(
                {
                    "name": entry.name,
                    "dir": str(entry),
                    "python": str(python_path),
                }
            )
    return runtimes


def find_live_brain_runtime_processes(
    runtime_pythons: Iterable[str | Path],
) -> dict[str, Any]:
    """Return live processes currently executing one of the supplied runtime paths."""
    tracked = {str(Path(path)): [] for path in runtime_pythons}
    if not tracked:
        return {"available": True, "processes": {}}

    try:
        result = subprocess.run(
            ["ps", "-Ao", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"available": False, "processes": tracked}

    if result.returncode != 0:
        return {"available": False, "processes": tracked}

    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        pid_text, _, command = stripped.partition(" ")
        if not pid_text or not command:
            continue
        try:
            parts = shlex.split(command)
        except ValueError:
            parts = command.split(" ", 1)
        if not parts:
            continue
        executable = parts[0]
        if executable not in tracked:
            continue
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        tracked[executable].append({"pid": pid, "command": command})
    return {"available": True, "processes": tracked}
