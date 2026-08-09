"""Typed ``skill.list`` command and internal executor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._named_documents import list_portable
from .._read_support import (
    catalogue_entry as _catalogue_entry,
    command_error,
    decode_query,
    resolver_entry as _resolver_entry,
)
from ..context import InvocationContext
from ..results import ErrorCode, Ok
from .read import SkillSource


@dataclass(frozen=True, slots=True)
class SkillListItem:
    name: str
    source: SkillSource


@dataclass(frozen=True, slots=True)
class SkillListPayload:
    items: tuple[SkillListItem, ...]
    total: int


@dataclass(frozen=True, slots=True)
class SkillListRequest:
    COMMAND_ID: ClassVar[str] = "skill.list"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = SkillListPayload

    query: str | None = None

    def __post_init__(self) -> None:
        if self.query is not None and (
            not isinstance(self.query, str) or not self.query.strip()
        ):
            raise ValueError("skill.list query must be a non-empty string")


def execute(context: InvocationContext, request: SkillListRequest):
    try:
        resources = list_portable(
            context.selected_brain.vault_root,
            "skill",
            request.query,
        )
    except FileNotFoundError as exc:
        return command_error(SkillListRequest, ErrorCode.CONFLICT, str(exc), None)
    items = tuple(
        SkillListItem(item["name"], SkillSource(item["source"]))
        for item in sorted(resources, key=lambda item: item["name"].casefold())
    )
    return Ok(
        SkillListRequest.COMMAND_ID,
        SkillListRequest.COMMAND_VERSION,
        SkillListPayload(items, len(items)),
    )


def decode(payload: Mapping[str, object]) -> SkillListRequest:
    return decode_query(payload, SkillListRequest)


def catalogue_entry():
    return _catalogue_entry(SkillListRequest, execute)


def resolver_entry():
    return _resolver_entry(SkillListRequest, decode)
