"""Typed machine managed-runtime path read owners."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from .context import LauncherContext
from .contracts import ErrorCode, Ok, no_effect_error


@dataclass(frozen=True, slots=True)
class RuntimeResolvePayload:
    vault_root: str
    python: str
    exists: bool

    def __post_init__(self) -> None:
        _validate_payload_paths(self.vault_root, self.python)
        if not isinstance(self.exists, bool):
            raise ValueError("runtime.resolve exists must be a boolean")


@dataclass(frozen=True, slots=True)
class RuntimeResolveRunnablePayload:
    vault_root: str
    python: str

    def __post_init__(self) -> None:
        _validate_payload_paths(self.vault_root, self.python)


@dataclass(frozen=True, slots=True)
class RuntimeResolveRequest:
    COMMAND_ID: ClassVar[str] = "runtime.resolve"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = RuntimeResolvePayload

    vault_root: Path

    def __post_init__(self) -> None:
        _validate_absolute_directory(self.vault_root, "vault_root")


@dataclass(frozen=True, slots=True)
class RuntimeResolveRunnableRequest:
    COMMAND_ID: ClassVar[str] = "runtime.resolve-runnable"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = RuntimeResolveRunnablePayload

    vault_root: Path

    def __post_init__(self) -> None:
        _validate_absolute_directory(self.vault_root, "vault_root")


def _validate_absolute_directory(value: object, field: str) -> None:
    if not isinstance(value, Path) or not value.is_absolute():
        raise ValueError(f"{field} must be an absolute Path")


def _validate_payload_paths(vault_root: object, python: object) -> None:
    for value, field in ((vault_root, "vault_root"), (python, "python")):
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError(f"runtime result {field} must be absolute")


def execute_resolve(context: LauncherContext, request: RuntimeResolveRequest):
    from _common._venv import resolve_vault_venv_python

    try:
        python = resolve_vault_venv_python(
            request.vault_root,
            launcher=context.launcher_python,
        )
    except (OSError, ValueError) as exc:
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        RuntimeResolvePayload(
            str(request.vault_root),
            str(python),
            python.is_file(),
        ),
    )


def execute_resolve_runnable(
    context: LauncherContext,
    request: RuntimeResolveRunnableRequest,
):
    from _common._venv import find_runnable_python

    try:
        python = find_runnable_python(
            request.vault_root,
            launcher=context.launcher_python,
        )
    except (OSError, ValueError) as exc:
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))
    if python is None:
        return no_effect_error(
            type(request),
            ErrorCode.NOT_FOUND,
            "No runnable managed, legacy, or launcher Python is available.",
        )
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        RuntimeResolveRunnablePayload(str(request.vault_root), str(python)),
    )


def resolve_owner():
    from .owners import LauncherOwner

    return LauncherOwner(
        RuntimeResolveRequest,
        RuntimeResolvePayload,
        "_launcher.managed_runtime:resolve",
        execute_resolve,
    )


def resolve_runnable_owner():
    from .owners import LauncherOwner

    return LauncherOwner(
        RuntimeResolveRunnableRequest,
        RuntimeResolveRunnablePayload,
        "_launcher.managed_runtime:resolve_runnable",
        execute_resolve_runnable,
    )
