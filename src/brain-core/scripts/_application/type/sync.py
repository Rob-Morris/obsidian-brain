"""Typed ``type.sync`` owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._definition_sync import (
    TypeDefinitionSyncPayload,
    catalogue_entry as sync_catalogue_entry,
    execute_definition_sync,
    validate_type_key,
)
from ..context import InvocationContext
from .status import TypeDefinitionState


@dataclass(frozen=True, slots=True)
class TypeSyncRequest:
    COMMAND_ID: ClassVar[str] = "type.sync"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = TypeDefinitionSyncPayload

    type_key: str
    force: bool = False

    def __post_init__(self) -> None:
        validate_type_key(self.COMMAND_ID, self.type_key)
        if not isinstance(self.force, bool):
            raise ValueError("type.sync force must be a boolean")


def execute(context: InvocationContext, request: TypeSyncRequest):
    return execute_definition_sync(
        context,
        request,
        expected_state=TypeDefinitionState.IN_SYNC,
        force=request.force,
    )


def decode(payload: Mapping[str, object]) -> TypeSyncRequest:
    unexpected = sorted(set(payload) - {"type_key", "force"})
    if unexpected:
        raise ValueError(f"unexpected fields: {', '.join(unexpected)}")
    type_key = payload.get("type_key")
    force = payload.get("force", False)
    if not isinstance(type_key, str):
        raise ValueError("type_key is required and must be a string")
    if not isinstance(force, bool):
        raise ValueError("force must be a boolean")
    return TypeSyncRequest(type_key, force)


def catalogue_entry():
    return sync_catalogue_entry(TypeSyncRequest, execute)


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(TypeSyncRequest, decode)
