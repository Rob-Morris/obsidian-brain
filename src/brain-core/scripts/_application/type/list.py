"""Typed ``type.list`` command and internal executor."""

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
from .read import ArtefactTypeClassification


@dataclass(frozen=True, slots=True)
class ArtefactTypeListItem:
    key: str
    classification: ArtefactTypeClassification
    frontmatter_type: str
    configured: bool
    has_template: bool


@dataclass(frozen=True, slots=True)
class ArtefactTypeListPayload:
    items: tuple[ArtefactTypeListItem, ...]
    total: int


@dataclass(frozen=True, slots=True)
class ArtefactTypeListRequest:
    COMMAND_ID: ClassVar[str] = "type.list"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = ArtefactTypeListPayload

    query: str | None = None

    def __post_init__(self) -> None:
        if self.query is not None and (
            not isinstance(self.query, str) or not self.query.strip()
        ):
            raise ValueError("type.list query must be a non-empty string")


def execute(context: InvocationContext, request: ArtefactTypeListRequest):
    from _portable.type_definitions import list_types_from_vault

    try:
        resources = list_types_from_vault(
            context.selected_brain.vault_root,
            request.query,
        )
    except FileNotFoundError as exc:
        return command_error(ArtefactTypeListRequest, ErrorCode.CONFLICT, str(exc), None)
    items = tuple(
        ArtefactTypeListItem(
            key=item["key"],
            classification=ArtefactTypeClassification(item["classification"]),
            frontmatter_type=item["frontmatter_type"],
            configured=bool(item["configured"]),
            has_template=bool(item.get("template_file")),
        )
        for item in sorted(resources, key=lambda item: item["key"].casefold())
    )
    return Ok(
        ArtefactTypeListRequest.COMMAND_ID,
        ArtefactTypeListRequest.COMMAND_VERSION,
        ArtefactTypeListPayload(items, len(items)),
    )


def decode(payload: Mapping[str, object]) -> ArtefactTypeListRequest:
    return decode_query(payload, ArtefactTypeListRequest)


def catalogue_entry():
    return _catalogue_entry(ArtefactTypeListRequest, execute)


def resolver_entry():
    return _resolver_entry(ArtefactTypeListRequest, decode)
