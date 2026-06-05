"""Runtime-topology classification for machine-level Brain maintenance."""

from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Callable, Iterable

from _common import (
    central_venvs_root,
    find_existing_central_venv,
    find_runnable_python,
    legacy_vault_venv_dir,
    legacy_vault_venv_python,
    resolve_vault_venv_python,
    same_executable_path,
)


# Performance gate only: exact matching still belongs to _python_family_process_key().
_PYTHON_PROCESS_GATE = re.compile(r"/python[^/\s]*(?=\s|$)")


def _same_path(left: str | Path | None, right: str | Path | None) -> bool:
    if left is None or right is None:
        return False
    return same_executable_path(left, right)


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


def _python_family_process_key(
    path: str | Path,
    *,
    parent_resolver: Callable[[str], str] | None = None,
) -> str | None:
    resolve_parent = parent_resolver or os.path.realpath
    candidate = Path(str(path))
    if not candidate.is_absolute():
        return None
    if not candidate.name.startswith("python"):
        return None
    return resolve_parent(str(candidate.parent))


def _tracked_runtime_processes(
    runtime_pythons: Iterable[str | Path],
) -> dict[str, dict[str, Any]]:
    tracked: dict[str, dict[str, Any]] = {}
    for path in runtime_pythons:
        runtime_path = str(Path(path))
        key = _python_family_process_key(runtime_path)
        if key is None:
            continue
        tracked.setdefault(key, {"runtime": runtime_path, "processes": []})
    return tracked


def _tracked_process_map(tracked: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    return {
        entry["runtime"]: list(entry["processes"])
        for entry in tracked.values()
    }


def _command_prefixes(command: str) -> Iterable[str]:
    for index, char in enumerate(command):
        if char == " ":
            yield command[:index]
    yield command


def _match_runtime_process(
    command: str,
    tracked: dict[str, dict[str, Any]],
    *,
    parent_resolver: Callable[[str], str] | None = None,
) -> str | None:
    if not _PYTHON_PROCESS_GATE.search(command):
        return None
    for prefix in _command_prefixes(command):
        key = _python_family_process_key(prefix, parent_resolver=parent_resolver)
        if key is not None and key in tracked:
            return key
    return None


def find_live_brain_runtime_processes(
    runtime_pythons: Iterable[str | Path],
) -> dict[str, Any]:
    """Return live processes currently executing one of the supplied runtime paths."""
    tracked = _tracked_runtime_processes(runtime_pythons)
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
        return {"available": False, "processes": _tracked_process_map(tracked)}

    if result.returncode != 0:
        return {"available": False, "processes": _tracked_process_map(tracked)}

    @lru_cache(maxsize=None)
    def real_runtime_parent(path: str) -> str:
        return os.path.realpath(path)

    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        pid_text, _, command = stripped.partition(" ")
        if not pid_text or not command:
            continue
        tracked_key = _match_runtime_process(command, tracked, parent_resolver=real_runtime_parent)
        if tracked_key is None:
            continue
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        tracked[tracked_key]["processes"].append({"pid": pid, "command": command})

    return {
        "available": True,
        "processes": _tracked_process_map(tracked),
    }
