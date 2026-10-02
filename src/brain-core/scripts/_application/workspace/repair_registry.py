"""Typed ``workspace.repair-registry`` owner."""

from __future__ import annotations

from .._decoding import optional_bool, reject_unexpected
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Mapping

from .._mutation_support import derived_cache_maintenance_entry, no_effect_error
from ..context import InvocationContext
from ..receipts import CommittedEffect
from ..results import (
    CommandError,
    ErrorCode,
    Ok,
    Partial,
    RequestErrorDetails,
)


class RegistryRepairStatus(str, Enum):
    NOOP = "noop"
    PLANNED = "planned"
    CHANGED = "changed"


@dataclass(frozen=True, slots=True)
class RegistryDroppedRow:
    key: str
    path: str


@dataclass(frozen=True, slots=True)
class WorkspaceRepairRegistryPayload:
    status: RegistryRepairStatus
    reason: str
    dry_run: bool
    entry_count: int
    backup_path: str | None
    dropped: tuple[RegistryDroppedRow, ...]


@dataclass(frozen=True, slots=True)
class WorkspaceRepairRegistryRequest:
    """``allow_row_loss`` is the person's explicit choice to rebuild a file whose rows cannot be read."""

    COMMAND_ID: ClassVar[str] = "workspace.repair-registry"
    COMMAND_VERSION: ClassVar[int] = 2
    RESULT_TYPE: ClassVar[type] = WorkspaceRepairRegistryPayload

    allow_row_loss: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.allow_row_loss, bool):
            raise ValueError("workspace.repair-registry allow_row_loss must be a boolean")


def execute(context: InvocationContext, request: WorkspaceRepairRegistryRequest):
    from _common import MutationLockError, public_mutation_error_message
    from _portable.registry_maintenance import (
        RegistryRepairPartialError,
        repair_registry,
    )
    from workspace_registry import RegistryChangedError
    from .._caller_workspace import workspace_admission

    root = context.selected_brain.vault_root
    before_write = workspace_admission(context, request)
    try:
        # The repair takes the vault lock itself, after classifying its rows outside it.
        result = repair_registry(root, dry_run=context.dry_run, allow_row_loss=request.allow_row_loss,
                                 before_write=before_write)
        before_write()
    except MutationLockError as exc:
        return no_effect_error(
            WorkspaceRepairRegistryRequest,
            ErrorCode.CONFLICT,
            public_mutation_error_message(exc),
            retryable=True,
        )
    except RegistryRepairPartialError as exc:
        message = str(exc)
        return Partial(
            request.COMMAND_ID,
            request.COMMAND_VERSION,
            CommandError(
                ErrorCode.CONFLICT,
                message,
                RequestErrorDetails(None, message),
            ),
            (CommittedEffect(request.COMMAND_ID, exc.backup_path),),
        )
    except RegistryChangedError as exc:
        return no_effect_error(WorkspaceRepairRegistryRequest, ErrorCode.CONFLICT, str(exc), retryable=True)
    except (OSError, ValueError) as exc:
        return no_effect_error(
            WorkspaceRepairRegistryRequest,
            ErrorCode.CONFLICT,
            str(exc),
        )

    status = RegistryRepairStatus(result.status)
    payload = WorkspaceRepairRegistryPayload(
        status,
        result.reason,
        result.dry_run,
        result.entry_count,
        result.backup_path,
        tuple(RegistryDroppedRow(row.key, row.path) for row in result.dropped),
    )
    effects = ()
    if status is RegistryRepairStatus.CHANGED:
        subjects = [".brain/local/workspaces.json"]
        if result.backup_path is not None:
            subjects.append(result.backup_path)
        effects = tuple(
            CommittedEffect(request.COMMAND_ID, subject)
            for subject in subjects
        )
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        payload,
        committed_effects=effects,
    )


def decode(payload: Mapping[str, object]) -> WorkspaceRepairRegistryRequest:
    reject_unexpected(payload, {"allow_row_loss"})
    return WorkspaceRepairRegistryRequest(optional_bool(payload.get("allow_row_loss"), "allow_row_loss"))


def catalogue_entry():
    from ..preparation import OperationPreparation
    from ._preparation import prepare_workspace

    return derived_cache_maintenance_entry(
        WorkspaceRepairRegistryRequest, execute, OperationPreparation(prepare_workspace))
