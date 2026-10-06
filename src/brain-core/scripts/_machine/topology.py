"""Runtime-topology classification for machine-level Brain maintenance."""

from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Callable, Iterable

from _bootstrap.runtime import target_runtime_contract
from _common import (
    central_venvs_root,
    join_argv,
    legacy_vault_venv_dir,
    legacy_vault_venv_python,
    same_executable_path,
    venv_python,
)


# Performance gate only: exact matching still belongs to _python_family_process_key().
_PYTHON_PROCESS_GATE = re.compile(r"/python[^/\s]*(?=\s|$)")


def _same_path(left: str | Path | None, right: str | Path | None) -> bool:
    if left is None or right is None:
        return False
    return same_executable_path(left, right)


RUNTIME_CONTRACT_UNAVAILABLE = "runtime_contract_unavailable"


def upgrade_guidance(vault_root: str | Path) -> str:
    """The launcher command that upgrades the Brain at ``vault_root`` from the installed distribution."""
    return join_argv(["brain", "--vault", str(vault_root), "upgrade"])


def classify_brain_runtime(
    vault_root: str | Path,
    *,
    launcher_python: str | None = None,
) -> dict[str, Any]:
    """Classify how a discovered Brain currently resolves its runtime.

    Each Brain is judged by its own Core's runtime contract, the one its MCP
    launch and runtime repair use, so an older Brain is not misjudged by this
    Core's rule. A Brain with no supported resolver, or whose resolver fails
    (missing or damaged exports), is ``runtime_contract_unavailable`` with no
    runtime paths: its runtime cannot be named without guessing.
    """
    vault_path = Path(vault_root)
    launcher_path = Path(launcher_python) if launcher_python else None
    legacy_runtime_dir = legacy_vault_venv_dir(vault_path)
    legacy_runtime_python = legacy_vault_venv_python(vault_path)
    legacy_runtime = {
        "legacy_runtime_dir": str(legacy_runtime_dir),
        "legacy_runtime_python": str(legacy_runtime_python),
        "legacy_runtime_present": legacy_runtime_dir.exists(),
    }

    try:
        contract = target_runtime_contract(vault_path)
        expected_runtime_path = contract.resolve_vault_venv_python(vault_path, launcher=launcher_path)
        selected_runtime = contract.find_existing_central_venv(vault_path, launcher=launcher_path)
        runnable_runtime = contract.find_runnable_python(vault_path, launcher=launcher_path)
    except Exception as exc:
        # The resolver is the Brain's own code: whatever it raises, this Brain's runtime cannot be named.
        return {
            "status": RUNTIME_CONTRACT_UNAVAILABLE,
            "message": (
                f"The Brain's own runtime contract could not be evaluated ({type(exc).__name__}: {exc}). "
                "Its Core may predate the runtime resolver or be damaged; "
                f"upgrade or recover it with `{upgrade_guidance(vault_path)}`."
            ),
            "healthy_runtime": False,
            "expected_runtime": None,
            "selected_runtime": None,
            "runnable_runtime": None,
            **legacy_runtime,
        }

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
        **legacy_runtime,
    }


def list_central_runtimes() -> list[dict[str, str]]:
    """Return every central runtime that currently exists on this machine."""
    root = central_venvs_root()
    if not root.is_dir():
        return []

    runtimes: list[dict[str, str]] = []
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        python_path = venv_python(entry)
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


def scan_processes() -> dict[str, Any]:
    """Return every process as ``{pid, ppid, command}``, or ``available: False``.

    One full-width scan serves both live-runtime matching and orphan
    detection; the parent process ID is what tells an orphan from a child.
    """
    try:
        result = subprocess.run(
            # Unlimited width: procps truncates to the display width (such as
            # an inherited COLUMNS), which would hide a live runtime with a long
            # interpreter path from prune decisions.
            ["ps", "-A", "-ww", "-o", "pid=,ppid=,command="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"available": False, "processes": []}
    if result.returncode != 0:
        return {"available": False, "processes": []}
    processes = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        processes.append({"pid": pid, "ppid": ppid, "command": parts[2]})
    return {"available": True, "processes": processes}


def find_live_brain_runtime_processes(
    runtime_pythons: Iterable[str | Path],
    *,
    scan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return live processes currently executing one of the supplied runtime paths."""
    tracked = _tracked_runtime_processes(runtime_pythons)
    if not tracked:
        return {"available": True, "processes": {}}

    scan = scan_processes() if scan is None else scan
    if not scan["available"]:
        return {"available": False, "processes": _tracked_process_map(tracked)}

    @lru_cache(maxsize=None)
    def real_runtime_parent(path: str) -> str:
        return os.path.realpath(path)

    for process in scan["processes"]:
        tracked_key = _match_runtime_process(process["command"], tracked, parent_resolver=real_runtime_parent)
        if tracked_key is None:
            continue
        tracked[tracked_key]["processes"].append({"pid": process["pid"], "command": process["command"]})

    return {
        "available": True,
        "processes": _tracked_process_map(tracked),
    }


def _brain_role(command: str) -> str | None:
    """Return the Brain role of a role-named interpreter command line, if any."""
    from _common._venv import ROLE_INTERPRETER_NAMES

    executable = command.split(" ", 1)[0]
    name = os.path.basename(executable)
    for role, interpreter in ROLE_INTERPRETER_NAMES.items():
        if name == interpreter:
            return role
    return None


def find_orphaned_brain_processes(*, scan: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return Brain-role interpreters that have lost their parent (DD-082, D19).

    An orphan is a role-named interpreter whose parent is the init process
    or is absent from the scan: a server whose proxy died, or a job
    descendant whose supervisor is gone. Judgement, never automatic: the
    machine cannot see whether the work it was doing has finished.
    """
    scan = scan_processes() if scan is None else scan
    if not scan["available"]:
        return {"available": False, "processes": []}
    live = {process["pid"] for process in scan["processes"]}
    orphans = []
    for process in scan["processes"]:
        role = _brain_role(process["command"])
        if role is None:
            continue
        if process["ppid"] <= 1 or process["ppid"] not in live:
            orphans.append({**process, "role": role})
    return {"available": True, "processes": orphans}
