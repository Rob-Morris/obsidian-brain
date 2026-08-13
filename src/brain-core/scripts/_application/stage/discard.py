"""Typed idempotent ``stage.discard`` owner."""

from __future__ import annotations

from .._decoding import reject_unexpected

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._mutation_support import contributor_mutation_entry, no_effect_error
from ..context import InvocationContext
from ..receipts import CommittedEffect
from ..results import ErrorCode, Ok


@dataclass(frozen=True, slots=True)
class StageDiscardPayload:
    handle: str
    discarded: bool


@dataclass(frozen=True, slots=True)
class StageDiscardRequest:
    COMMAND_ID: ClassVar[str] = "stage.discard"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = StageDiscardPayload

    handle: str

    def __post_init__(self) -> None:
        if not isinstance(self.handle, str) or not self.handle.strip():
            raise ValueError("stage.discard handle must be a non-empty string")


def execute(context: InvocationContext, request: StageDiscardRequest):
    from _common import (
        MutationLockError,
        public_mutation_error_message,
        vault_mutation_lock,
    )
    from _staging import discard_staged_body, validate_staged_body_handle

    try:
        validate_staged_body_handle(request.handle)
    except ValueError as exc:
        return no_effect_error(
            StageDiscardRequest,
            ErrorCode.INVALID_REQUEST,
            str(exc),
            "handle",
        )

    if context.dry_run:
        return Ok(
            request.COMMAND_ID,
            request.COMMAND_VERSION,
            StageDiscardPayload(request.handle, False),
        )
    vault_root = str(context.selected_brain.vault_root)
    try:
        with vault_mutation_lock(vault_root):
            discarded = discard_staged_body(vault_root, request.handle)
    except MutationLockError as exc:
        return no_effect_error(
            StageDiscardRequest,
            ErrorCode.CONFLICT,
            public_mutation_error_message(exc),
            retryable=True,
        )
    except ValueError as exc:
        return no_effect_error(
            StageDiscardRequest,
            ErrorCode.INVALID_REQUEST,
            str(exc),
            "handle",
        )
    effects = (
        (CommittedEffect("stage.discarded", request.handle),)
        if discarded
        else ()
    )
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        StageDiscardPayload(request.handle, discarded),
        committed_effects=effects,
    )


def decode(payload: Mapping[str, object]) -> StageDiscardRequest:
    reject_unexpected(payload, {"handle"})
    handle = payload.get("handle")
    if not isinstance(handle, str):
        raise ValueError("handle must be a string")
    return StageDiscardRequest(handle)


def catalogue_entry():
    return contributor_mutation_entry(StageDiscardRequest, execute)
