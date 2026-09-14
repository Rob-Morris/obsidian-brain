"""Typed application projection for portable router maintenance."""

from __future__ import annotations

from .types import InitialAuthorisationClass

from dataclasses import dataclass, replace
from enum import Enum

from ._mutation_support import maintainer_mutation_entry, no_effect_error
from .context import InvocationContext
from ._managed_preparation import MAINTENANCE, maintenance_binding
from .preparation import admit_owner
from .receipts import CommittedEffect
from .results import CommandError, ErrorCode, Ok, Partial, RequestErrorDetails


class RouterMaintenanceStatus(str, Enum):
    NOOP = "noop"
    PLANNED = "planned"
    CHANGED = "changed"


@dataclass(frozen=True, slots=True)
class RouterMaintenancePayload:
    status: RouterMaintenanceStatus
    reason: str
    dry_run: bool
    forced: bool
    sidecars_removed: tuple[str, ...]
    session_refreshed: bool


def execute_router_maintenance(
    context: InvocationContext,
    request,
    *,
    force: bool,
):
    from _common import (
        MutationLockError,
        public_mutation_error_message,
        vault_mutation_lock,
    )
    from _portable.router_maintenance import maintain_router

    root = context.selected_brain.vault_root
    try:
        with vault_mutation_lock(root):
            admit_owner(context, request, maintenance_binding)
            result = maintain_router(root, dry_run=context.dry_run, force=force)
    except MutationLockError as exc:
        return no_effect_error(
            type(request),
            ErrorCode.CONFLICT,
            public_mutation_error_message(exc),
            retryable=True,
        )

    if result.status == "partial":
        message = (
            "Router rebuilt but session markdown refresh failed: "
            f"{result.session_error}"
        )
        return Partial(
            request.COMMAND_ID,
            request.COMMAND_VERSION,
            CommandError(
                ErrorCode.CONFLICT,
                message,
                RequestErrorDetails(None, message),
            ),
            (CommittedEffect(request.COMMAND_ID, ".brain/local/compiled-router.json"),),
        )
    status = RouterMaintenanceStatus(result.status)
    payload = RouterMaintenancePayload(
        status,
        result.reason,
        result.dry_run,
        result.forced,
        result.sidecars_removed,
        result.session_refreshed,
    )
    effects = (
        (CommittedEffect(request.COMMAND_ID, ".brain/local/compiled-router.json"),)
        if status is RouterMaintenanceStatus.CHANGED
        else ()
    )
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        payload,
        committed_effects=effects,
    )


def catalogue_entry(request_type, executor):
    return replace(maintainer_mutation_entry(request_type, executor), initial_class=InitialAuthorisationClass.OBSERVATION, preparation=MAINTENANCE)
