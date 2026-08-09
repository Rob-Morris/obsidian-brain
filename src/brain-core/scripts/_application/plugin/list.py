"""Typed ``plugin.list`` command and internal executor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._named_documents import (
    catalogue_entry as _catalogue_entry,
    command_error,
    decode_query,
    list_portable,
    resolver_entry as _resolver_entry,
)
from ..context import InvocationContext
from ..results import ErrorCode, Ok


@dataclass(frozen=True, slots=True)
class PluginListItem:
    name: str


@dataclass(frozen=True, slots=True)
class PluginListPayload:
    items: tuple[PluginListItem, ...]
    total: int


@dataclass(frozen=True, slots=True)
class PluginListRequest:
    COMMAND_ID: ClassVar[str] = "plugin.list"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = PluginListPayload

    query: str | None = None

    def __post_init__(self) -> None:
        if self.query is not None and (
            not isinstance(self.query, str) or not self.query.strip()
        ):
            raise ValueError("plugin.list query must be a non-empty string")


def execute(context: InvocationContext, request: PluginListRequest):
    try:
        resources = list_portable(
            context.selected_brain.vault_root,
            "plugin",
            request.query,
        )
    except FileNotFoundError as exc:
        return command_error(PluginListRequest, ErrorCode.CONFLICT, str(exc), None)
    items = tuple(
        PluginListItem(item["name"])
        for item in sorted(resources, key=lambda item: item["name"].casefold())
    )
    return Ok(
        PluginListRequest.COMMAND_ID,
        PluginListRequest.COMMAND_VERSION,
        PluginListPayload(items, len(items)),
    )


def decode(payload: Mapping[str, object]) -> PluginListRequest:
    return decode_query(payload, PluginListRequest)


def catalogue_entry():
    return _catalogue_entry(PluginListRequest, execute)


def resolver_entry():
    return _resolver_entry(PluginListRequest, decode)
