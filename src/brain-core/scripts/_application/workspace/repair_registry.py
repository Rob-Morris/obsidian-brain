"""Typed ``workspace.repair-registry`` owner."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Mapping

from .._mutation_support import no_effect_error, operator_mutation_entry
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
class WorkspaceRepairRegistryPayload:
    status: RegistryRepairStatus
    reason: str
    dry_run: bool
    entry_count: int
    backup_path: str | None


@dataclass(frozen=True, slots=True)
class WorkspaceRepairRegistryRequest:
    COMMAND_ID: ClassVar[str] = "workspace.repair-registry"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = WorkspaceRepairRegistryPayload


def execute(context: InvocationContext, request: WorkspaceRepairRegistryRequest):
    from _common import (
        MutationLockError,
        public_mutation_error_message,
        vault_mutation_lock,
    )
    from _portable.registry_maintenance import (
        RegistryRepairPartialError,
        repair_registry,
    )

    root = context.selected_brain.vault_root
    try:
        with vault_mutation_lock(root):
            result = repair_registry(root, dry_run=context.dry_run)
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
    if payload:
        raise ValueError(f"unexpected fields: {', '.join(sorted(payload))}")
    return WorkspaceRepairRegistryRequest()


def catalogue_entry():
    from ..catalogue import exclude_projection
    from ..types import Projection

    return exclude_projection(
        operator_mutation_entry(WorkspaceRepairRegistryRequest, execute),
        Projection.MCP,
        "Local workspace-registry repair is reserved for deliberate CLI or "
        "direct-script administration.",
    )


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(WorkspaceRepairRegistryRequest, decode)
