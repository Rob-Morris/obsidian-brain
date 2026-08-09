"""Typed ``artefact.list-archived`` command and internal executor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._read_support import (
    catalogue_entry as _catalogue_entry,
    command_error,
    resolver_entry as _resolver_entry,
)
from ..context import InvocationContext
from ..results import ErrorCode, Ok


@dataclass(frozen=True, slots=True)
class ArchivedArtefactListItem:
    path: str
    title: str
    artefact_type: str
    status: str
    archived_date: str


@dataclass(frozen=True, slots=True)
class ArtefactListArchivedPayload:
    items: tuple[ArchivedArtefactListItem, ...]
    total: int


@dataclass(frozen=True, slots=True)
class ArtefactListArchivedRequest:
    COMMAND_ID: ClassVar[str] = "artefact.list-archived"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = ArtefactListArchivedPayload


def execute(context: InvocationContext, _request: ArtefactListArchivedRequest):
    from _portable.vault_files import list_archived_artefacts_from_vault

    try:
        resources = list_archived_artefacts_from_vault(
            context.selected_brain.vault_root
        )
    except FileNotFoundError as exc:
        return command_error(
            ArtefactListArchivedRequest,
            ErrorCode.CONFLICT,
            str(exc),
            None,
        )
    items = tuple(
        ArchivedArtefactListItem(
            path=item["path"],
            title=item["title"],
            artefact_type=item["type"],
            status=item["status"],
            archived_date=item["archiveddate"],
        )
        for item in resources
    )
    return Ok(
        ArtefactListArchivedRequest.COMMAND_ID,
        ArtefactListArchivedRequest.COMMAND_VERSION,
        ArtefactListArchivedPayload(items, len(items)),
    )


def decode(payload: Mapping[str, object]) -> ArtefactListArchivedRequest:
    if payload:
        raise ValueError(f"unexpected fields: {', '.join(sorted(payload))}")
    return ArtefactListArchivedRequest()


def catalogue_entry():
    return _catalogue_entry(ArtefactListArchivedRequest, execute)


def resolver_entry():
    return _resolver_entry(ArtefactListArchivedRequest, decode)
