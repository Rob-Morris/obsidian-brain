"""Typed selected-Brain repair result and execution mechanics."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, ClassVar

from ._mutation_support import contributor_mutation_entry, no_effect_error
from .context import InvocationContext
from .receipts import CommittedEffect
from .results import CommandError, ErrorCode, Ok, Partial, RequestErrorDetails


class RepairStatus(str, Enum):
    NOOP = "noop"
    PLANNED = "planned"
    CHANGED = "ok"


@dataclass(frozen=True, slots=True)
class RepairStep:
    name: str
    status: str
    message: str


@dataclass(frozen=True, slots=True)
class ArtefactRepairPayload:
    scope: str
    status: RepairStatus
    dry_run: bool
    steps: tuple[RepairStep, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ArtefactRepairRequest:
    """Inherited no-field contract for one explicit artefact repair scope."""

    COMMAND_ID: ClassVar[str] = ""
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = ArtefactRepairPayload


def execute_repair(
    context: InvocationContext,
    request,
    *,
    operation: Callable[[object, bool], dict],
):
    from _common import (
        MutationLockError,
        public_mutation_error_message,
        vault_mutation_lock,
    )

    root = context.selected_brain.vault_root
    try:
        with vault_mutation_lock(root):
            result = operation(root, context.dry_run)
    except MutationLockError as exc:
        return no_effect_error(
            type(request),
            ErrorCode.CONFLICT,
            public_mutation_error_message(exc),
            retryable=True,
        )

    status = result.get("status")
    if status == "error":
        message = _error_message(result)
        return no_effect_error(type(request), ErrorCode.CONFLICT, message)
    if status == "partial":
        message = _error_message(result)
        return Partial(
            request.COMMAND_ID,
            request.COMMAND_VERSION,
            CommandError(
                ErrorCode.CONFLICT,
                message,
                RequestErrorDetails(None, message),
            ),
            (CommittedEffect(request.COMMAND_ID, "vault"),),
        )
    if status not in {"ok", "noop", "planned"}:
        raise ValueError("repair owner returned an unsupported status")

    payload = ArtefactRepairPayload(
        scope=result["scope"],
        status=RepairStatus(status),
        dry_run=bool(result["dry_run"]),
        steps=tuple(
            RepairStep(item["name"], item["status"], item["message"])
            for item in result.get("steps") or ()
        ),
        notes=tuple(result.get("notes") or ()),
    )
    effects = (
        (CommittedEffect(request.COMMAND_ID, "vault"),)
        if status == "ok" and not payload.dry_run
        else ()
    )
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        payload,
        committed_effects=effects,
    )


def catalogue_entry(request_type, executor):
    return contributor_mutation_entry(request_type, executor)


def _error_message(result: dict) -> str:
    errors = [
        item.get("message", "")
        for item in result.get("steps") or ()
        if item.get("status") == "error"
    ]
    return "; ".join(item for item in errors if item) or "Repair did not complete."
