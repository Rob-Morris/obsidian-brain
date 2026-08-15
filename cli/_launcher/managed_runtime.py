"""Typed managed-runtime inspection launcher owner."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import ClassVar

from .context import LauncherContext
from .contracts import ErrorCode, Ok, no_effect_error


class RuntimePythonSource(str, Enum):
    MANAGED = "managed"
    LEGACY = "legacy"
    LAUNCHER = "launcher"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ManagedRuntimeInspection:
    python: str
    exists: bool

    def __post_init__(self) -> None:
        _validate_absolute_string(self.python, "managed_runtime.python")
        if not isinstance(self.exists, bool):
            raise ValueError("managed_runtime.exists must be a boolean")


@dataclass(frozen=True, slots=True)
class SelectedPythonInspection:
    path: str | None
    source: RuntimePythonSource

    def __post_init__(self) -> None:
        if not isinstance(self.source, RuntimePythonSource):
            raise ValueError("selected_python.source must be closed and typed")
        if self.source is RuntimePythonSource.UNAVAILABLE:
            if self.path is not None:
                raise ValueError("unavailable selected Python must not have a path")
        elif self.path is None:
            raise ValueError("available selected Python must have a path")
        else:
            _validate_absolute_string(self.path, "selected_python.path")


@dataclass(frozen=True, slots=True)
class RuntimeInspectPayload:
    vault_root: str
    managed_runtime: ManagedRuntimeInspection
    selected_python: SelectedPythonInspection

    def __post_init__(self) -> None:
        _validate_absolute_string(self.vault_root, "vault_root")
        if not isinstance(self.managed_runtime, ManagedRuntimeInspection):
            raise ValueError("managed_runtime must be typed")
        if not isinstance(self.selected_python, SelectedPythonInspection):
            raise ValueError("selected_python must be typed")


@dataclass(frozen=True, slots=True)
class RuntimeInspectRequest:
    COMMAND_ID: ClassVar[str] = "runtime.inspect"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = RuntimeInspectPayload


def _validate_absolute_string(value: object, field: str) -> None:
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError(f"runtime inspection {field} must be absolute")


def _selected_source(
    selected: Path | None,
    *,
    managed: Path | None,
    legacy: Path,
    launcher: Path | None,
) -> RuntimePythonSource:
    from _common._venv import same_executable_path

    if selected is None:
        return RuntimePythonSource.UNAVAILABLE
    if managed is not None and same_executable_path(selected, managed):
        return RuntimePythonSource.MANAGED
    if same_executable_path(selected, legacy):
        return RuntimePythonSource.LEGACY
    if launcher is not None and same_executable_path(selected, launcher):
        return RuntimePythonSource.LAUNCHER
    raise RuntimeError("runnable Python does not match a documented source")


def execute_inspect(context: LauncherContext, request: RuntimeInspectRequest):
    from _common._venv import (
        find_existing_central_venv,
        find_runnable_python,
        legacy_vault_venv_python,
        resolve_vault_venv_python,
    )

    vault_root = context.current_vault
    if vault_root is None:
        return no_effect_error(
            type(request),
            ErrorCode.NOT_FOUND,
            "No current Brain is selected for runtime inspection.",
            "current_vault",
        )

    try:
        expected = resolve_vault_venv_python(
            vault_root,
            launcher=context.launcher_python,
        )
        managed = find_existing_central_venv(
            vault_root,
            launcher=context.launcher_python,
        )
        legacy = legacy_vault_venv_python(vault_root)
        selected = find_runnable_python(
            vault_root,
            launcher=context.launcher_python,
        )
        source = _selected_source(
            selected,
            managed=managed,
            legacy=legacy,
            launcher=context.launcher_python,
        )
    except (OSError, ValueError) as exc:
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))

    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        RuntimeInspectPayload(
            str(vault_root),
            ManagedRuntimeInspection(str(expected), expected.is_file()),
            SelectedPythonInspection(
                str(selected) if selected is not None else None,
                source,
            ),
        ),
    )


def inspect_owner():
    from .owners import LauncherOwner

    return LauncherOwner(
        RuntimeInspectRequest,
        RuntimeInspectPayload,
        "_launcher.managed_runtime:inspect",
        execute_inspect,
    )
