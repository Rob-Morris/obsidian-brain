"""Typed read-only ``type.status`` command owner."""

from __future__ import annotations

from .._decoding import reject_unexpected

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Mapping

from .._read_support import (
    catalogue_entry as _catalogue_entry,
    command_error,
)
from ..context import InvocationContext
from ..results import ErrorCode, Ok


class TypeDefinitionState(str, Enum):
    UNINSTALLED = "uninstalled"
    IN_SYNC = "in_sync"
    SYNC_READY = "sync_ready"
    LOCALLY_CUSTOMISED = "locally_customised"
    CONFLICT = "conflict"
    NOT_INSTALLABLE = "not_installable"


@dataclass(frozen=True, slots=True)
class TypeDefinitionFileState:
    role: str
    state: TypeDefinitionState


@dataclass(frozen=True, slots=True)
class TypeStatusItem:
    type_key: str
    artefact_type: str | None
    state: TypeDefinitionState
    files: tuple[TypeDefinitionFileState, ...]
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class TypeStatusPayload:
    items: tuple[TypeStatusItem, ...]
    total: int


@dataclass(frozen=True, slots=True)
class TypeStatusRequest:
    COMMAND_ID: ClassVar[str] = "type.status"
    COMMAND_VERSION: ClassVar[int] = 2
    RESULT_TYPE: ClassVar[type] = TypeStatusPayload

    type_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.type_keys, tuple):
            raise ValueError("type.status type_keys must be a tuple")
        if any(not isinstance(item, str) or not item.strip() for item in self.type_keys):
            raise ValueError("type.status type_keys must contain non-empty strings")
        if len(set(self.type_keys)) != len(self.type_keys):
            raise ValueError("type.status type_keys must be unique")


def execute(context: InvocationContext, request: TypeStatusRequest):
    import sync_definitions

    try:
        result = sync_definitions.status_definitions(
            context.selected_brain.vault_root,
            types=list(request.type_keys) if request.type_keys else None,
        )
    except (OSError, ValueError) as exc:
        return command_error(TypeStatusRequest, ErrorCode.CONFLICT, str(exc), None)

    items = []
    for state_name, entries in result["types"].items():
        state = TypeDefinitionState(state_name)
        for entry in entries:
            files = tuple(
                TypeDefinitionFileState(role, TypeDefinitionState(file_state))
                for role, file_state in sorted(entry.get("files", {}).items())
            )
            items.append(
                TypeStatusItem(entry["type"], entry["artefact_type"], state, files)
            )
    for entry in result["not_installable"]:
        items.append(
            TypeStatusItem(
                entry["type"],
                entry["artefact_type"],
                TypeDefinitionState.NOT_INSTALLABLE,
                (),
                entry["reason"],
            )
        )
    found = {item.type_key for item in items}
    missing = sorted(set(request.type_keys) - found)
    if missing:
        return command_error(
            TypeStatusRequest,
            ErrorCode.NOT_FOUND,
            f"Unknown artefact-library type(s): {', '.join(missing)}",
            "type_keys",
        )
    ordered = tuple(sorted(items, key=lambda item: item.type_key.casefold()))
    return Ok(
        TypeStatusRequest.COMMAND_ID,
        TypeStatusRequest.COMMAND_VERSION,
        TypeStatusPayload(ordered, len(ordered)),
    )


def decode(payload: Mapping[str, object]) -> TypeStatusRequest:
    reject_unexpected(payload, {"type_keys"})
    raw = payload.get("type_keys", [])
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise ValueError("type_keys must be an array of strings")
    return TypeStatusRequest(tuple(raw))


def catalogue_entry():
    return _catalogue_entry(TypeStatusRequest, execute)
