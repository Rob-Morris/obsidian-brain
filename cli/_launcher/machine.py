"""Typed machine-global runtime-maintenance launcher owners."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import re
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


_BRAIN_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class LegacyMigrationStatus(str, Enum):
    NOOP = "noop"
    PLANNED = "planned"
    CHANGED = "changed"


class LegacyMigrationOperation(str, Enum):
    RUNTIME = "runtime"
    MCP = "mcp"
    REGISTRY = "registry"
    LEGACY_RUNTIME = "legacy-runtime"
    VERIFY = "verify"


class LegacyMigrationStepStatus(str, Enum):
    NOOP = "noop"
    PLANNED = "planned"
    CHANGED = "changed"


@dataclass(frozen=True, slots=True)
class LegacyBrainIdTarget:
    brain_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.brain_id, str) or not _BRAIN_ID.fullmatch(self.brain_id):
            raise ValueError("legacy migration brain_id must be a canonical slug")


@dataclass(frozen=True, slots=True)
class LegacyBrainPathTarget:
    vault_root: Path

    def __post_init__(self) -> None:
        if not isinstance(self.vault_root, Path) or not self.vault_root.is_absolute():
            raise ValueError("legacy migration vault_root must be an absolute Path")


LegacyMigrationSelector = LegacyBrainIdTarget | LegacyBrainPathTarget


@dataclass(frozen=True, slots=True)
class LegacyMigrationStep:
    operation: LegacyMigrationOperation
    status: LegacyMigrationStepStatus
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.operation, LegacyMigrationOperation):
            raise ValueError("legacy migration operation must be closed and typed")
        if not isinstance(self.status, LegacyMigrationStepStatus):
            raise ValueError("legacy migration step status must be closed and typed")
        if not self.message.strip():
            raise ValueError("legacy migration step message must be non-empty")


@dataclass(frozen=True, slots=True)
class LegacyMigrationTarget:
    brain_id: str | None
    vault_root: str
    status: LegacyMigrationStatus
    steps: tuple[LegacyMigrationStep, ...]

    def __post_init__(self) -> None:
        if self.brain_id is not None and (
            not isinstance(self.brain_id, str)
            or not _BRAIN_ID.fullmatch(self.brain_id)
        ):
            raise ValueError("legacy migration result brain_id must be a canonical slug")
        if not isinstance(self.vault_root, str) or not Path(self.vault_root).is_absolute():
            raise ValueError("legacy migration result vault_root must be absolute")
        if not isinstance(self.status, LegacyMigrationStatus):
            raise ValueError("legacy migration target status must be closed and typed")
        if not self.steps or any(
            not isinstance(step, LegacyMigrationStep) for step in self.steps
        ):
            raise ValueError("legacy migration targets require typed steps")


@dataclass(frozen=True, slots=True)
class BrainMigrateLegacyInstallationsPayload:
    status: LegacyMigrationStatus
    targets: tuple[LegacyMigrationTarget, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.status, LegacyMigrationStatus):
            raise ValueError("legacy migration status must be closed and typed")
        if any(not isinstance(target, LegacyMigrationTarget) for target in self.targets):
            raise ValueError("legacy migration payload requires typed targets")
        paths = tuple(target.vault_root for target in self.targets)
        if paths != tuple(sorted(set(paths))):
            raise ValueError("legacy migration targets must be sorted and unique")
        if not self.targets and self.status is not LegacyMigrationStatus.NOOP:
            raise ValueError("an empty legacy migration result must be a no-op")


@dataclass(frozen=True, slots=True)
class BrainMigrateLegacyInstallationsRequest:
    COMMAND_ID: ClassVar[str] = "brain.migrate-legacy-installations"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = BrainMigrateLegacyInstallationsPayload

    target: LegacyMigrationSelector | None = None

    def __post_init__(self) -> None:
        if self.target is not None and not isinstance(
            self.target,
            (LegacyBrainIdTarget, LegacyBrainPathTarget),
        ):
            raise ValueError("legacy migration target must be typed")


class RuntimeRemovalStatus(str, Enum):
    NOOP = "noop"
    PLANNED = "planned"
    REMOVED = "removed"


@dataclass(frozen=True, slots=True)
class OrphanRuntimeTarget:
    name: str
    directory: str
    python: str
    status: RuntimeRemovalStatus

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("orphan runtime targets require a name")
        for value, field in (
            (self.directory, "directory"),
            (self.python, "python"),
        ):
            if not isinstance(value, str) or not Path(value).is_absolute():
                raise ValueError(f"orphan runtime target {field} must be absolute")
        if not isinstance(self.status, RuntimeRemovalStatus):
            raise ValueError("runtime removal status must be closed and typed")


@dataclass(frozen=True, slots=True)
class RuntimeRemoveOrphansPayload:
    status: RuntimeRemovalStatus
    targets: tuple[OrphanRuntimeTarget, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.status, RuntimeRemovalStatus):
            raise ValueError("runtime removal status must be closed and typed")
        if any(not isinstance(target, OrphanRuntimeTarget) for target in self.targets):
            raise ValueError("runtime removal payload requires typed targets")
        directories = tuple(target.directory for target in self.targets)
        if directories != tuple(sorted(set(directories))):
            raise ValueError("orphan runtime targets must be sorted and unique")
        if not self.targets and self.status is not RuntimeRemovalStatus.NOOP:
            raise ValueError("an empty runtime removal result must be a no-op")


@dataclass(frozen=True, slots=True)
class RuntimeRemoveOrphansRequest:
    COMMAND_ID: ClassVar[str] = "runtime.remove-orphans"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = RuntimeRemoveOrphansPayload


def _selector_value(target: LegacyMigrationSelector | None) -> str | None:
    if isinstance(target, LegacyBrainIdTarget):
        return target.brain_id
    if isinstance(target, LegacyBrainPathTarget):
        return str(target.vault_root)
    return None


def _legacy_step_operation(name: str) -> LegacyMigrationOperation:
    return {
        "runtime": LegacyMigrationOperation.RUNTIME,
        "mcp": LegacyMigrationOperation.MCP,
        "registry": LegacyMigrationOperation.REGISTRY,
        "legacy_venv": LegacyMigrationOperation.LEGACY_RUNTIME,
        "verify": LegacyMigrationOperation.VERIFY,
    }[name]


def _legacy_step_status(status: str) -> LegacyMigrationStepStatus:
    return {
        "noop": LegacyMigrationStepStatus.NOOP,
        "planned": LegacyMigrationStepStatus.PLANNED,
        "changed": LegacyMigrationStepStatus.CHANGED,
    }[status]


def _legacy_target_status(status: str) -> LegacyMigrationStatus:
    return {
        "noop": LegacyMigrationStatus.NOOP,
        "planned": LegacyMigrationStatus.PLANNED,
        "ok": LegacyMigrationStatus.CHANGED,
    }[status]


def _typed_legacy_targets(raw_targets: list[dict]) -> tuple[LegacyMigrationTarget, ...]:
    targets = []
    for raw in raw_targets:
        brain = raw["brain"]
        brain_id = brain.get("alias")
        if not isinstance(brain_id, str) or not _BRAIN_ID.fullmatch(brain_id):
            brain_id = None
        steps = tuple(
            LegacyMigrationStep(
                _legacy_step_operation(step["name"]),
                _legacy_step_status(step["status"]),
                step["message"],
            )
            for step in raw["steps"]
        )
        targets.append(
            LegacyMigrationTarget(
                brain_id,
                brain["path"],
                _legacy_target_status(raw["status"]),
                steps,
            )
        )
    return tuple(sorted(targets, key=lambda target: target.vault_root))


def _migration_effects(
    request: BrainMigrateLegacyInstallationsRequest,
    raw_targets: list[dict],
):
    effects = []
    seen = set()
    for raw in raw_targets:
        brain_path = raw["brain"]["path"]
        for step in raw["steps"]:
            if step["status"] not in {"changed", "partial"}:
                continue
            if step["name"] == "legacy_venv":
                subject = f"legacy-runtime:{step['path']}"
            elif step["name"] in {"runtime", "mcp", "registry"}:
                subject = f"brain-{step['name']}:{brain_path}"
            else:
                continue
            if subject in seen:
                continue
            seen.add(subject)
            effects.append(CommittedEffect(request.COMMAND_ID, subject))
    return tuple(effects)


def _migration_failure_message(raw: dict) -> str:
    for target in raw.get("targets", []):
        for step in target["steps"]:
            if step["status"] in {"error", "partial"}:
                return step["message"]
    return raw["steps"][-1]["message"]


def _migration_has_unknown_outcome(raw_targets: list[dict]) -> bool:
    return any(
        step.get("outcome") == "unknown"
        for target in raw_targets
        for step in target["steps"]
    )


def execute_migrate_legacy_installations(
    context: LauncherContext,
    request: BrainMigrateLegacyInstallationsRequest,
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

    raw = maintenance.migrate_legacy_brains(
        summary,
        launcher_python=(
            str(context.launcher_python)
            if context.launcher_python is not None
            else None
        ),
        dry_run=context.dry_run,
        selector=_selector_value(request.target),
    )
    raw_targets = raw.get("targets", [])
    if _migration_has_unknown_outcome(raw_targets):
        raise RuntimeError("legacy migration outcome cannot be proven complete")

    effects = _migration_effects(request, raw_targets)
    if raw["status"] in {"error", "partial"}:
        message = _migration_failure_message(raw)
        if effects:
            return Partial(
                request.COMMAND_ID,
                request.COMMAND_VERSION,
                CommandError(
                    ErrorCode.CONFLICT,
                    message,
                    RequestErrorDetails(None, message),
                ),
                effects,
            )
        code = ErrorCode.NOT_FOUND if not raw_targets else ErrorCode.CONFLICT
        return no_effect_error(type(request), code, message, "target")

    status = {
        "noop": LegacyMigrationStatus.NOOP,
        "planned": LegacyMigrationStatus.PLANNED,
        "ok": LegacyMigrationStatus.CHANGED,
    }[raw["status"]]
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        BrainMigrateLegacyInstallationsPayload(
            status,
            _typed_legacy_targets(raw_targets),
        ),
        effects,
    )


def _target_status(raw: str) -> RuntimeRemovalStatus:
    return {
        "noop": RuntimeRemovalStatus.NOOP,
        "planned": RuntimeRemovalStatus.PLANNED,
        "changed": RuntimeRemovalStatus.REMOVED,
    }[raw]


def _payload_status(raw: str) -> RuntimeRemovalStatus:
    return {
        "noop": RuntimeRemovalStatus.NOOP,
        "planned": RuntimeRemovalStatus.PLANNED,
        "ok": RuntimeRemovalStatus.REMOVED,
    }[raw]


def _typed_targets(raw_targets: list[dict]) -> tuple[OrphanRuntimeTarget, ...]:
    targets = []
    for raw in raw_targets:
        runtime = raw["runtime"]
        step = raw["steps"][0]
        targets.append(
            OrphanRuntimeTarget(
                runtime["name"],
                runtime["dir"],
                runtime["python"],
                _target_status(step["status"]),
            )
        )
    return tuple(sorted(targets, key=lambda target: target.directory))


def execute_remove_orphans(
    context: LauncherContext,
    request: RuntimeRemoveOrphansRequest,
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
        raise RuntimeError("orphan runtime removal outcome cannot be proven complete")

    targets = _typed_targets(raw_targets)
    effects = tuple(
        CommittedEffect(request.COMMAND_ID, f"managed-runtime:{target.directory}")
        for target in targets
        if target.status is RuntimeRemovalStatus.REMOVED
    )
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        RuntimeRemoveOrphansPayload(_payload_status(raw["status"]), targets),
        effects,
    )


def remove_orphans_owner():
    from .owners import LauncherOwner

    return LauncherOwner(
        RuntimeRemoveOrphansRequest,
        RuntimeRemoveOrphansPayload,
        "_launcher.machine:remove_orphans",
        execute_remove_orphans,
    )


def migrate_legacy_installations_owner():
    from .owners import LauncherOwner

    return LauncherOwner(
        BrainMigrateLegacyInstallationsRequest,
        BrainMigrateLegacyInstallationsPayload,
        "_launcher.machine:migrate_legacy_installations",
        execute_migrate_legacy_installations,
    )
