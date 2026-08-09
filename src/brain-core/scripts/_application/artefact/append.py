"""Typed ``artefact.append`` owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._document_edit import (
    DocumentEditPayload,
    EditScope,
    StructuralSelector,
    decode_structural_request,
    execute_document_edit,
    validate_structural_request,
)
from .._mutation_support import FrontmatterField, MutationContent, contributor_mutation_entry
from ..context import InvocationContext


@dataclass(frozen=True, slots=True)
class ArtefactAppendRequest:
    COMMAND_ID: ClassVar[str] = "artefact.append"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = DocumentEditPayload

    path: str
    content: MutationContent | None = None
    frontmatter: tuple[FrontmatterField, ...] = ()
    target: str | None = None
    selector: StructuralSelector | None = None
    scope: EditScope | None = None
    fix_links: bool = False

    def __post_init__(self) -> None:
        validate_structural_request(self, subject_field="path")


def execute(context: InvocationContext, request: ArtefactAppendRequest):
    return execute_document_edit(
        context,
        request,
        resource="artefact",
        operation="append",
        subject_field="path",
    )


def decode(payload: Mapping[str, object]) -> ArtefactAppendRequest:
    return decode_structural_request(
        payload, ArtefactAppendRequest, subject_field="path", allow_fix_links=True
    )


def catalogue_entry():
    return contributor_mutation_entry(ArtefactAppendRequest, execute)


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(ArtefactAppendRequest, decode)
