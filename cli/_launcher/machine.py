"""Typed machine-global runtime-maintenance launcher owners."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import ClassVar

from .context import LauncherContext
from .contracts import CommittedEffect, ErrorCode, Ok, no_effect_error


class RuntimePruneStatus(str, Enum):
    NOOP = "noop"
    PLANNED = "planned"
    REMOVED = "removed"


@dataclass(frozen=True, slots=True)
class RuntimePruneTarget:
    name: str
    directory: str
    python: str
    status: RuntimePruneStatus

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("runtime prune targets require a name")
        for value, field in (
            (self.directory, "directory"),
            (self.python, "python"),
        ):
            if not isinstance(value, str) or not Path(value).is_absolute():
                raise ValueError(f"runtime prune target {field} must be absolute")
        if not isinstance(self.status, RuntimePruneStatus):
            raise ValueError("runtime prune target status must be closed and typed")


@dataclass(frozen=True, slots=True)
class MachinePruneRuntimesPayload:
    status: RuntimePruneStatus
    targets: tuple[RuntimePruneTarget, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.status, RuntimePruneStatus):
            raise ValueError("machine prune status must be closed and typed")
        if any(not isinstance(target, RuntimePruneTarget) for target in self.targets):
            raise ValueError("machine prune payload requires typed targets")
        directories = tuple(target.directory for target in self.targets)
        if directories != tuple(sorted(set(directories))):
            raise ValueError("machine prune targets must be sorted and unique")
        if not self.targets and self.status is not RuntimePruneStatus.NOOP:
            raise ValueError("an empty machine prune result must be a no-op")


@dataclass(frozen=True, slots=True)
class MachinePruneRuntimesRequest:
    COMMAND_ID: ClassVar[str] = "machine.prune-runtimes"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = MachinePruneRuntimesPayload


def _target_status(raw: str) -> RuntimePruneStatus:
    return {
        "noop": RuntimePruneStatus.NOOP,
        "planned": RuntimePruneStatus.PLANNED,
        "changed": RuntimePruneStatus.REMOVED,
    }[raw]


def _payload_status(raw: str) -> RuntimePruneStatus:
    return {
        "noop": RuntimePruneStatus.NOOP,
        "planned": RuntimePruneStatus.PLANNED,
        "ok": RuntimePruneStatus.REMOVED,
    }[raw]


def _typed_targets(raw_targets: list[dict]) -> tuple[RuntimePruneTarget, ...]:
    targets = []
    for raw in raw_targets:
        runtime = raw["runtime"]
        step = raw["steps"][0]
        targets.append(
            RuntimePruneTarget(
                runtime["name"],
                runtime["dir"],
                runtime["python"],
                _target_status(step["status"]),
            )
        )
    return tuple(sorted(targets, key=lambda target: target.directory))


def execute_prune_runtimes(
    context: LauncherContext,
    request: MachinePruneRuntimesRequest,
):
    import vault_registry
    from _machine import maintenance

    try:
        summary = maintenance.collect_machine_summary(
            current_vault=(
                str(context.current_vault)
                if context.current_vault is not None
                else None
            ),
            launcher_python=(
                str(context.launcher_python)
                if context.launcher_python is not None
                else None
            ),
            synchronise_registry=False,
        )
    except (vault_registry.RegistryReadError, OSError, ValueError) as exc:
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))

    raw = maintenance.prune_orphaned_runtimes(
        summary,
        dry_run=context.dry_run,
    )
    raw_targets = raw.get("targets", [])
    if raw["status"] == "error" and not raw_targets:
        message = raw["steps"][0]["message"]
        return no_effect_error(type(request), ErrorCode.CONFLICT, message)
    if raw["status"] in {"error", "partial"} or any(
        target["status"] in {"error", "partial"} for target in raw_targets
    ):
        raise RuntimeError("runtime prune outcome cannot be proven complete")

    targets = _typed_targets(raw_targets)
    effects = tuple(
        CommittedEffect(request.COMMAND_ID, f"managed-runtime:{target.directory}")
        for target in targets
        if target.status is RuntimePruneStatus.REMOVED
    )
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        MachinePruneRuntimesPayload(_payload_status(raw["status"]), targets),
        effects,
    )


def prune_runtimes_owner():
    from .owners import LauncherOwner

    return LauncherOwner(
        MachinePruneRuntimesRequest,
        MachinePruneRuntimesPayload,
        "_launcher.machine:prune_runtimes",
        execute_prune_runtimes,
    )
