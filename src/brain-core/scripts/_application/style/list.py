"""Typed ``style.list`` command and internal executor."""

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
class StyleListItem:
    name: str


@dataclass(frozen=True, slots=True)
class StyleListPayload:
    items: tuple[StyleListItem, ...]
    total: int


@dataclass(frozen=True, slots=True)
class StyleListRequest:
    COMMAND_ID: ClassVar[str] = "style.list"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = StyleListPayload

    query: str | None = None

    def __post_init__(self) -> None:
        if self.query is not None and (
            not isinstance(self.query, str) or not self.query.strip()
        ):
            raise ValueError("style.list query must be a non-empty string")


def execute(context: InvocationContext, request: StyleListRequest):
    try:
        resources = list_portable(
            context.selected_brain.vault_root,
            "style",
            request.query,
        )
    except FileNotFoundError as exc:
        return command_error(StyleListRequest, ErrorCode.CONFLICT, str(exc), None)
    items = tuple(
        StyleListItem(item["name"])
        for item in sorted(resources, key=lambda item: item["name"].casefold())
    )
    return Ok(
        StyleListRequest.COMMAND_ID,
        StyleListRequest.COMMAND_VERSION,
        StyleListPayload(items, len(items)),
    )


def decode(payload: Mapping[str, object]) -> StyleListRequest:
    return decode_query(payload, StyleListRequest)


def catalogue_entry():
    return _catalogue_entry(StyleListRequest, execute)


def resolver_entry():
    return _resolver_entry(StyleListRequest, decode)
