"""Typed ``artefact.delete`` owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._artefact_transition import (
    ArtefactDeletePayload,
    catalogue_entry as transition_catalogue_entry,
    decode_path_recursive,
    execute_transition,
    validate_recursive,
    validate_string,
)
from ..context import InvocationContext
from ..types import Authority


@dataclass(frozen=True, slots=True)
class ArtefactDeleteRequest:
    COMMAND_ID: ClassVar[str] = "artefact.delete"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = ArtefactDeletePayload

    path: str
    recursive: bool = False

    def __post_init__(self) -> None:
        validate_string(self.COMMAND_ID, "path", self.path)
        validate_recursive(self.COMMAND_ID, self.recursive)


def execute(context: InvocationContext, request: ArtefactDeleteRequest):
    return execute_transition(
        context,
        request,
        operation=lambda root, router: _delete(root, router, request),
        payload_builder=lambda result: ArtefactDeletePayload(
            request.path,
            tuple(result["deleted"]),
            result["links_replaced"],
            tuple(result["orphaned_attachment_scopes"]),
        ),
        effect_subject=lambda payload: payload.path,
    )


def _delete(root: str, router: dict, request: ArtefactDeleteRequest) -> dict:
    import rename

    return rename.delete_and_clean_links(
        root,
        request.path,
        router=router,
        recursive=request.recursive,
        return_details=True,
    )


def decode(payload: Mapping[str, object]) -> ArtefactDeleteRequest:
    return decode_path_recursive(payload, ArtefactDeleteRequest)


def catalogue_entry():
    return transition_catalogue_entry(
        ArtefactDeleteRequest,
        execute,
        authority=Authority.ADMINISTRATOR,
    )


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(ArtefactDeleteRequest, decode)
