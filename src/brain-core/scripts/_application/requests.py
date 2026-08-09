"""Sealed typed requests for foundational application commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from .receipts import OutcomeReference
from .types import (
    Authority,
    DependencyTier,
    EffectClass,
    Locality,
    Projection,
    validate_command_id,
)


@dataclass(frozen=True, slots=True)
class CommandListPayload:
    command_ids: tuple[str, ...]
    next_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class CommandDescriptionPayload:
    command_id: str
    command_version: int


@dataclass(frozen=True, slots=True)
class InvocationReadPayload:
    reference: OutcomeReference
    state: str


@dataclass(frozen=True, slots=True)
class CommandListRequest:
    COMMAND_ID: ClassVar[str] = "command.list"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = CommandListPayload

    query: str | None = None
    domain: str | None = None
    authority: Authority | None = None
    dependency_tier: DependencyTier | None = None
    locality: Locality | None = None
    effect_class: EffectClass | None = None
    projection: Projection | None = None
    cursor: str | None = None
    page_size: int = 100

    def __post_init__(self) -> None:
        if not 1 <= self.page_size <= 500:
            raise ValueError("command list page_size must be between 1 and 500")


@dataclass(frozen=True, slots=True)
class CommandDescribeRequest:
    COMMAND_ID: ClassVar[str] = "command.describe"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = CommandDescriptionPayload

    target_command_id: str

    def __post_init__(self) -> None:
        validate_command_id(self.target_command_id)


@dataclass(frozen=True, slots=True)
class InvocationReadRequest:
    COMMAND_ID: ClassVar[str] = "invocation.read"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = InvocationReadPayload

    reference: OutcomeReference


CommandRequest = CommandListRequest | CommandDescribeRequest | InvocationReadRequest


def command_identity(request: CommandRequest) -> tuple[str, int, type]:
    """Return identity owned by the concrete request type, never caller input."""
    request_type = type(request)
    if request_type not in {
        CommandListRequest,
        CommandDescribeRequest,
        InvocationReadRequest,
    }:
        raise TypeError(f"unregistered command request type: {request_type.__name__}")
    return request_type.COMMAND_ID, request_type.COMMAND_VERSION, request_type.RESULT_TYPE
