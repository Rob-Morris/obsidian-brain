"""Typed ``trigger.list`` command and internal executor."""

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
from .read import TriggerCategory


@dataclass(frozen=True, slots=True)
class TriggerListItem:
    condition: str
    target: str
    category: TriggerCategory
    detail: str | None


@dataclass(frozen=True, slots=True)
class TriggerListPayload:
    items: tuple[TriggerListItem, ...]
    total: int


@dataclass(frozen=True, slots=True)
class TriggerListRequest:
    COMMAND_ID: ClassVar[str] = "trigger.list"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = TriggerListPayload

    query: str | None = None

    def __post_init__(self) -> None:
        if self.query is not None and (
            not isinstance(self.query, str) or not self.query.strip()
        ):
            raise ValueError("trigger.list query must be a non-empty string")


def execute(context: InvocationContext, request: TriggerListRequest):
    from _portable.router_collections import list_triggers_from_vault

    try:
        resources = list_triggers_from_vault(
            context.selected_brain.vault_root,
            request.query,
        )
    except FileNotFoundError as exc:
        return command_error(TriggerListRequest, ErrorCode.CONFLICT, str(exc), None)
    items = tuple(
        TriggerListItem(
            condition=item["condition"],
            target=item["target"],
            category=TriggerCategory(item["category"]),
            detail=item.get("detail"),
        )
        for item in sorted(
            resources,
            key=lambda item: (
                item["condition"].casefold(),
                item["target"].casefold(),
            ),
        )
    )
    return Ok(
        TriggerListRequest.COMMAND_ID,
        TriggerListRequest.COMMAND_VERSION,
        TriggerListPayload(items, len(items)),
    )


def decode(payload: Mapping[str, object]) -> TriggerListRequest:
    return decode_query(payload, TriggerListRequest)


def catalogue_entry():
    return _catalogue_entry(TriggerListRequest, execute)


def resolver_entry():
    return _resolver_entry(TriggerListRequest, decode)
