"""Typed ``type.install`` owner."""

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
class TypeInstallRequest:
    COMMAND_ID: ClassVar[str] = "type.install"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = TypeDefinitionSyncPayload

    type_key: str

    def __post_init__(self) -> None:
        validate_type_key(self.COMMAND_ID, self.type_key)


def execute(context: InvocationContext, request: TypeInstallRequest):
    return execute_definition_sync(
        context,
        request,
        expected_state=TypeDefinitionState.UNINSTALLED,
        force=False,
    )


def decode(payload: Mapping[str, object]) -> TypeInstallRequest:
    unexpected = sorted(set(payload) - {"type_key"})
    if unexpected:
        raise ValueError(f"unexpected fields: {', '.join(unexpected)}")
    type_key = payload.get("type_key")
    if not isinstance(type_key, str):
        raise ValueError("type_key is required and must be a string")
    return TypeInstallRequest(type_key)


def catalogue_entry():
    return sync_catalogue_entry(TypeInstallRequest, execute)


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(TypeInstallRequest, decode)
