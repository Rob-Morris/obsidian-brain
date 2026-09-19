"""Typed launcher ownership for selected Brain managed-runtime repair."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import ClassVar

from .context import LauncherContext
from .contracts import (
    CommandError,
    CommittedEffect,
    ErrorCode,
    Ok,
    Partial,
    RequestErrorDetails,
    no_effect_error,
)


class RuntimeRepairStatus(str, Enum):
    NOOP = "noop"
    PLANNED = "planned"
    REPAIRED = "repaired"


class RuntimeRepairStepKind(str, Enum):
    RUNTIME = "runtime"
    DEPENDENCIES = "dependencies"


class RuntimeRepairStepStatus(str, Enum):
    NOOP = "noop"
    PLANNED = "planned"
    CHANGED = "changed"


@dataclass(frozen=True, slots=True)
class RuntimeRepairStep:
    kind: RuntimeRepairStepKind
    status: RuntimeRepairStepStatus
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, RuntimeRepairStepKind):
            raise ValueError("runtime repair step kind must be closed and typed")
        if not isinstance(self.status, RuntimeRepairStepStatus):
            raise ValueError("runtime repair step status must be closed and typed")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("runtime repair step message must be non-empty")


@dataclass(frozen=True, slots=True)
class RuntimeRepairPayload:
    status: RuntimeRepairStatus
    vault_root: str
    runtime_dir: str
    managed_python: str
    steps: tuple[RuntimeRepairStep, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.status, RuntimeRepairStatus):
            raise ValueError("runtime repair status must be closed and typed")
        for value, field in (
            (self.vault_root, "vault_root"),
            (self.runtime_dir, "runtime_dir"),
            (self.managed_python, "managed_python"),
        ):
            if not isinstance(value, str) or not Path(value).is_absolute():
                raise ValueError(f"runtime repair {field} must be absolute")
        if not self.steps or any(
            not isinstance(step, RuntimeRepairStep) for step in self.steps
        ):
            raise ValueError("runtime repair payload requires typed steps")


@dataclass(frozen=True, slots=True)
class RuntimeRepairRequest:
    COMMAND_ID: ClassVar[str] = "runtime.repair"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = RuntimeRepairPayload


def _typed_steps(raw_steps: list[dict]) -> tuple[RuntimeRepairStep, ...]:
    kinds = {
        "managed_runtime": RuntimeRepairStepKind.RUNTIME,
        "managed_dependencies": RuntimeRepairStepKind.DEPENDENCIES,
    }
    statuses = {
        "noop": RuntimeRepairStepStatus.NOOP,
        "planned": RuntimeRepairStepStatus.PLANNED,
        "changed": RuntimeRepairStepStatus.CHANGED,
    }
    return tuple(
        RuntimeRepairStep(kinds[step["name"]], statuses[step["status"]], step["message"])
        for step in raw_steps
    )


def _runtime_effect(request: RuntimeRepairRequest, runtime_dir: str) -> CommittedEffect:
    return CommittedEffect(request.COMMAND_ID, f"managed-runtime:{runtime_dir}")


def execute_repair(context: LauncherContext, request: RuntimeRepairRequest):
    from _bootstrap import runtime as bootstrap_runtime

    vault_root = context.current_vault
    if vault_root is None:
        return no_effect_error(
            type(request),
            ErrorCode.NOT_FOUND,
            "No current Brain is selected for runtime repair.",
            "current_vault",
        )
    if not (vault_root / ".brain-core" / "VERSION").is_file():
        return no_effect_error(
            type(request),
            ErrorCode.NOT_FOUND,
            f"The selected path is not an installed Brain: {vault_root}",
            "current_vault",
        )
    if not (vault_root / ".brain-core" / "brain_mcp" / "requirements.txt").is_file():
        return no_effect_error(
            type(request),
            ErrorCode.CONFLICT,
            "The selected Brain has no managed-runtime requirements file.",
            "current_vault",
        )

    try:
        contract = bootstrap_runtime.target_runtime_contract(vault_root)
    except (OSError, RuntimeError, ValueError, ImportError) as exc:
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))

    summary = bootstrap_runtime.bootstrap_managed_runtime(
        vault_root,
        required_modules=bootstrap_runtime.required_modules_for_scope("runtime"),
        dependency_owner="runtime repair",
        full_conformance=True,
        launcher_python=(
            str(context.launcher_python)
            if context.launcher_python is not None
            else None
        ),
        dry_run=context.dry_run,
        runtime_contract=contract,
    )
    effect_outcome = summary["effect_outcome"]
    runtime_dir = summary.get("runtime_dir")

    if summary["status"] == "error":
        message = summary.get("message") or summary["steps"][-1]["message"]
        if effect_outcome == "partial" and runtime_dir:
            return Partial(
                request.COMMAND_ID,
                request.COMMAND_VERSION,
                CommandError(
                    ErrorCode.CONFLICT,
                    message,
                    RequestErrorDetails(None, message),
                ),
                (_runtime_effect(request, runtime_dir),),
            )
        if effect_outcome == "none":
            return no_effect_error(type(request), ErrorCode.CONFLICT, message)
        raise RuntimeError("managed-runtime repair outcome cannot be proven complete")

    if not runtime_dir or not summary["managed_python"]:
        raise RuntimeError("managed-runtime repair returned incomplete identity")
    if effect_outcome not in {"none", "committed"}:
        raise RuntimeError("managed-runtime repair returned invalid effect state")

    steps = _typed_steps(summary["steps"])
    if summary["status"] == "planned":
        status = RuntimeRepairStatus.PLANNED
    elif effect_outcome == "committed":
        status = RuntimeRepairStatus.REPAIRED
    else:
        status = RuntimeRepairStatus.NOOP
    effects = (
        (_runtime_effect(request, runtime_dir),)
        if effect_outcome == "committed"
        else ()
    )
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        RuntimeRepairPayload(
            status,
            str(vault_root),
            runtime_dir,
            summary["managed_python"],
            steps,
        ),
        effects,
    )


def repair_owner():
    from .owners import LauncherOwner

    return LauncherOwner(
        RuntimeRepairRequest,
        RuntimeRepairPayload,
        "_launcher.runtime:repair",
        execute_repair,
    )
