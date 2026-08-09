"""Typed ``artefact.replace-text`` owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._document_edit import (
    DocumentEditPayload,
    EditScope,
    StructuralSelector,
    decode_replace_request,
    execute_document_edit,
    validate_replace_request,
)
from .._mutation_support import contributor_mutation_entry
from ..context import InvocationContext


@dataclass(frozen=True, slots=True)
class ArtefactReplaceTextRequest:
    COMMAND_ID: ClassVar[str] = "artefact.replace-text"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = DocumentEditPayload

    path: str
    old_text: str
    new_text: str
    target: str | None = None
    selector: StructuralSelector | None = None
    scope: EditScope | None = None
    match_occurrence: int | None = None
    replace_all: bool = False
    fix_links: bool = False

    def __post_init__(self) -> None:
        validate_replace_request(self, subject_field="path")


def execute(context: InvocationContext, request: ArtefactReplaceTextRequest):
    return execute_document_edit(
        context,
        request,
        resource="artefact",
        operation="replace_text",
        subject_field="path",
    )


def decode(payload: Mapping[str, object]) -> ArtefactReplaceTextRequest:
    return decode_replace_request(
        payload,
        ArtefactReplaceTextRequest,
        subject_field="path",
        allow_fix_links=True,
    )


def catalogue_entry():
    return contributor_mutation_entry(ArtefactReplaceTextRequest, execute)


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(ArtefactReplaceTextRequest, decode)
