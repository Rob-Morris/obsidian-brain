"""Typed ``machine-registry.sync`` owner: add-and-refresh the derived machine registry (DD-082, D19)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar

from .context import LauncherContext
from .contracts import CommittedEffect, ErrorCode, Ok, no_effect_error


class MachineRegistrySyncStatus(str, Enum):
    NOOP = "noop"
    PLANNED = "planned"
    CHANGED = "changed"


@dataclass(frozen=True, slots=True)
class MachineRegistrySyncPayload:
    status: MachineRegistrySyncStatus
    path: str
    brains_count: int
    added: tuple[str, ...]
    refreshed: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.status, MachineRegistrySyncStatus):
            raise ValueError("machine registry sync status must be closed and typed")
        if self.brains_count < 0:
            raise ValueError("machine registry brain count cannot be negative")


@dataclass(frozen=True, slots=True)
class MachineRegistrySyncRequest:
    COMMAND_ID: ClassVar[str] = "machine-registry.sync"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = MachineRegistrySyncPayload


def execute_sync(context: LauncherContext, request: MachineRegistrySyncRequest):
    import vault_registry
    from _bootstrap.file_lock import MutationLockError
    from _machine.discovery import add_and_refresh_machine_registry, discover_brains

    try:
        discovery = discover_brains(
            current_vault=str(context.current_vault) if context.current_vault is not None else None,
        )
        result = add_and_refresh_machine_registry(discovery["brains"], dry_run=context.dry_run)
    except MutationLockError as exc:
        return no_effect_error(type(request), ErrorCode.CONFLICT,
                               f"The derived machine registry is busy; retry. {exc}", retryable=True)
    except (vault_registry.RegistryReadError, OSError, ValueError) as exc:
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))
    if result["blocked"]:
        return no_effect_error(
            type(request), ErrorCode.CONFLICT,
            f"The derived machine registry is {result['blocked_reason']}; inspect {result['path']} before syncing.",
        )
    if not result["changed"]:
        status = MachineRegistrySyncStatus.NOOP
    elif context.dry_run:
        status = MachineRegistrySyncStatus.PLANNED
    else:
        status = MachineRegistrySyncStatus.CHANGED
    effects = (CommittedEffect(request.COMMAND_ID, f"file:{result['path']}"),) if status is MachineRegistrySyncStatus.CHANGED else ()
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        MachineRegistrySyncPayload(status, result["path"], result["brains_count"],
                                   tuple(result["added"]), tuple(result["refreshed"])),
        effects,
    )


def sync_owner():
    from .owners import LauncherOwner

    return LauncherOwner(MachineRegistrySyncRequest, MachineRegistrySyncPayload,
                         "_launcher.machine_registry:sync", execute_sync)
