"""Typed ``memory.list`` command and internal executor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._read_support import (
    catalogue_entry as _catalogue_entry,
    command_error,
    decode_query,
    resolver_entry as _resolver_entry,
)
from ..context import InvocationContext
from ..results import ErrorCode, Ok


@dataclass(frozen=True, slots=True)
class MemoryListItem:
    name: str
    triggers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MemoryListPayload:
    items: tuple[MemoryListItem, ...]
    total: int


@dataclass(frozen=True, slots=True)
class MemoryListRequest:
    COMMAND_ID: ClassVar[str] = "memory.list"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = MemoryListPayload

    query: str | None = None

    def __post_init__(self) -> None:
        if self.query is not None and (
            not isinstance(self.query, str) or not self.query.strip()
        ):
            raise ValueError("memory.list query must be a non-empty string")


def execute(context: InvocationContext, request: MemoryListRequest):
    from _portable.router_collections import list_memories_from_vault

    try:
        resources = list_memories_from_vault(
            context.selected_brain.vault_root,
            request.query,
        )
    except FileNotFoundError as exc:
        return command_error(MemoryListRequest, ErrorCode.CONFLICT, str(exc), None)
    items = tuple(
        MemoryListItem(item["name"], tuple(item.get("triggers") or ()))
        for item in sorted(resources, key=lambda item: item["name"].casefold())
    )
    return Ok(
        MemoryListRequest.COMMAND_ID,
        MemoryListRequest.COMMAND_VERSION,
        MemoryListPayload(items, len(items)),
    )


def decode(payload: Mapping[str, object]) -> MemoryListRequest:
    return decode_query(payload, MemoryListRequest)


def catalogue_entry():
    return _catalogue_entry(MemoryListRequest, execute)


def resolver_entry():
    return _resolver_entry(MemoryListRequest, decode)
