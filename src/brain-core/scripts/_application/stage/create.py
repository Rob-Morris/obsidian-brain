"""Typed retry-safe ``stage.create`` owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._mutation_support import contributor_mutation_entry, no_effect_error
from ..context import InvocationContext
from ..receipts import CommittedEffect
from ..results import ErrorCode, Ok


@dataclass(frozen=True, slots=True)
class StageCreatePayload:
    handle: str
    bytes: int
    expires_in_seconds: int


@dataclass(frozen=True, slots=True)
class StageCreateRequest:
    COMMAND_ID: ClassVar[str] = "stage.create"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = StageCreatePayload

    content: str

    def __post_init__(self) -> None:
        if not isinstance(self.content, str):
            raise ValueError("stage.create content must be a string")


def execute(context: InvocationContext, request: StageCreateRequest):
    from _common import (
        MutationLockError,
        public_mutation_error_message,
        vault_mutation_lock,
    )
    from _staging import stage_body

    if context.dry_run:
        return no_effect_error(
            StageCreateRequest,
            ErrorCode.INVALID_REQUEST,
            "stage.create cannot dry-run because a usable handle requires a write",
        )
    vault_root = str(context.selected_brain.vault_root)
    try:
        with vault_mutation_lock(vault_root):
            result = stage_body(vault_root, request.content)
    except MutationLockError as exc:
        return no_effect_error(
            StageCreateRequest,
            ErrorCode.CONFLICT,
            public_mutation_error_message(exc),
            retryable=True,
        )
    except ValueError as exc:
        return no_effect_error(
            StageCreateRequest,
            ErrorCode.INVALID_REQUEST,
            str(exc),
            "content",
        )
    payload = StageCreatePayload(
        handle=result["handle"],
        bytes=result["bytes"],
        expires_in_seconds=result["expires_in_seconds"],
    )
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        payload,
        committed_effects=(CommittedEffect("stage.created", payload.handle),),
    )


def decode(payload: Mapping[str, object]) -> StageCreateRequest:
    unexpected = sorted(set(payload) - {"content"})
    if unexpected:
        raise ValueError(f"unexpected fields: {', '.join(unexpected)}")
    content = payload.get("content")
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    return StageCreateRequest(content)


def catalogue_entry():
    return contributor_mutation_entry(StageCreateRequest, execute)


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(StageCreateRequest, decode)
