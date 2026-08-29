"""Typed ``artefact.archive`` owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._artefact_transition import (
    ArtefactArchivePayload,
    catalogue_entry as transition_catalogue_entry,
    decode_path_recursive,
    execute_transition,
    path_changes,
    validate_recursive,
    validate_string,
)
from ..context import InvocationContext


@dataclass(frozen=True, slots=True)
class ArtefactArchiveRequest:
    COMMAND_ID: ClassVar[str] = "artefact.archive"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = ArtefactArchivePayload

    path: str
    recursive: bool = False

    def __post_init__(self) -> None:
        validate_string(self.COMMAND_ID, "path", self.path)
        validate_recursive(self.COMMAND_ID, self.recursive)


def execute(context: InvocationContext, request: ArtefactArchiveRequest):
    import edit

    return execute_transition(
        context,
        request,
        operation=lambda root, router: edit.archive_artefact(
            root, router, request.path, recursive=request.recursive
        ),
        payload_builder=lambda result: ArtefactArchivePayload(
            result["old_path"],
            result["new_path"],
            result["links_updated"],
            path_changes(result["archived"]),
        ),
        effect_subject=lambda payload: payload.new_path,
    )


def decode(payload: Mapping[str, object]) -> ArtefactArchiveRequest:
    return decode_path_recursive(payload, ArtefactArchiveRequest)


def catalogue_entry():
    return transition_catalogue_entry(ArtefactArchiveRequest, execute)
