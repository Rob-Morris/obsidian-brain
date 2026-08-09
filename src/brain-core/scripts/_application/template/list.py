"""Typed ``template.list`` command and internal executor."""

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
class TemplateListItem:
    type_key: str
    artefact_type: str
    path: str


@dataclass(frozen=True, slots=True)
class TemplateListPayload:
    items: tuple[TemplateListItem, ...]
    total: int


@dataclass(frozen=True, slots=True)
class TemplateListRequest:
    COMMAND_ID: ClassVar[str] = "template.list"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = TemplateListPayload

    query: str | None = None

    def __post_init__(self) -> None:
        if self.query is not None and (
            not isinstance(self.query, str) or not self.query.strip()
        ):
            raise ValueError("template.list query must be a non-empty string")


def execute(context: InvocationContext, request: TemplateListRequest):
    from _portable.type_definitions import list_templates_from_vault

    try:
        resources = list_templates_from_vault(
            context.selected_brain.vault_root,
            request.query,
        )
    except FileNotFoundError as exc:
        return command_error(TemplateListRequest, ErrorCode.CONFLICT, str(exc), None)
    items = tuple(
        TemplateListItem(
            type_key=item["name"],
            artefact_type=item["type"],
            path=item["template_file"],
        )
        for item in sorted(resources, key=lambda item: item["name"].casefold())
    )
    return Ok(
        TemplateListRequest.COMMAND_ID,
        TemplateListRequest.COMMAND_VERSION,
        TemplateListPayload(items, len(items)),
    )


def decode(payload: Mapping[str, object]) -> TemplateListRequest:
    return decode_query(payload, TemplateListRequest)


def catalogue_entry():
    return _catalogue_entry(TemplateListRequest, execute)


def resolver_entry():
    return _resolver_entry(TemplateListRequest, decode)
