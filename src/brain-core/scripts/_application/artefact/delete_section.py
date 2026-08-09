"""Typed ``artefact.delete-section`` owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._document_edit import (
    DocumentEditPayload,
    StructuralSelector,
    decode_delete_request,
    execute_document_edit,
    validate_delete_request,
)
from .._mutation_support import FrontmatterField, contributor_mutation_entry
from ..context import InvocationContext


@dataclass(frozen=True, slots=True)
class ArtefactDeleteSectionRequest:
    COMMAND_ID: ClassVar[str] = "artefact.delete-section"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = DocumentEditPayload

    path: str
    target: str
    selector: StructuralSelector | None = None
    frontmatter: tuple[FrontmatterField, ...] = ()
    fix_links: bool = False

    def __post_init__(self) -> None:
        validate_delete_request(self, subject_field="path")


def execute(context: InvocationContext, request: ArtefactDeleteSectionRequest):
    return execute_document_edit(
        context,
        request,
        resource="artefact",
        operation="delete_section",
        subject_field="path",
    )


def decode(payload: Mapping[str, object]) -> ArtefactDeleteSectionRequest:
    return decode_delete_request(
        payload,
        ArtefactDeleteSectionRequest,
        subject_field="path",
        allow_fix_links=True,
    )


def catalogue_entry():
    return contributor_mutation_entry(ArtefactDeleteSectionRequest, execute)


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(ArtefactDeleteSectionRequest, decode)
