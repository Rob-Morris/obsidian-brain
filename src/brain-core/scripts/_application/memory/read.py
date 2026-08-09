"""Typed exact-name ``memory.read`` command and internal executor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._read_support import (
    catalogue_entry as _catalogue_entry,
    command_error,
    decode_reference,
    resolver_entry as _resolver_entry,
)
from ..context import InvocationContext
from ..results import ErrorCode, Ok


@dataclass(frozen=True, slots=True)
class MemoryReadPayload:
    name: str
    triggers: tuple[str, ...]
    content: str


@dataclass(frozen=True, slots=True)
class MemoryReadRequest:
    COMMAND_ID: ClassVar[str] = "memory.read"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = MemoryReadPayload

    reference: str

    def __post_init__(self) -> None:
        if not isinstance(self.reference, str) or not self.reference.strip():
            raise ValueError("memory.read reference must be a non-empty string")


def execute(context: InvocationContext, request: MemoryReadRequest):
    from _portable.router_collections import read_memory_exact_from_vault

    try:
        result = read_memory_exact_from_vault(
            context.selected_brain.vault_root,
            request.reference,
        )
    except FileNotFoundError as exc:
        return command_error(MemoryReadRequest, ErrorCode.CONFLICT, str(exc), None)
    if isinstance(result, dict):
        return command_error(
            MemoryReadRequest,
            ErrorCode.NOT_FOUND,
            str(result["error"]),
            "reference",
        )
    metadata, content = result
    if not isinstance(content, str):
        return command_error(
            MemoryReadRequest,
            ErrorCode.NOT_FOUND,
            content.message,
            "reference",
        )
    return Ok(
        MemoryReadRequest.COMMAND_ID,
        MemoryReadRequest.COMMAND_VERSION,
        MemoryReadPayload(
            metadata["name"],
            tuple(metadata.get("triggers") or ()),
            content,
        ),
    )


def decode(payload: Mapping[str, object]) -> MemoryReadRequest:
    return decode_reference(payload, MemoryReadRequest)


def catalogue_entry():
    return _catalogue_entry(MemoryReadRequest, execute)


def resolver_entry():
    return _resolver_entry(MemoryReadRequest, decode)
