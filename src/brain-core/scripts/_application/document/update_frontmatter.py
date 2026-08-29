"""Typed ``document.update-frontmatter`` owner for document metadata patches."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._decoding import reject_unexpected
from .._document_mutation import (
    DocumentFrontmatterUpdatePayload,
    DocumentFrontmatterIntent,
    execute_document_mutation,
)
from .._mutation_support import (
    FrontmatterField,
    contributor_mutation_entry,
    decode_frontmatter,
)
from ..context import InvocationContext
from ._types import DocumentLocator, decode_document, validate_document_request


@dataclass(frozen=True, slots=True)
class DocumentUpdateFrontmatterRequest:
    COMMAND_ID: ClassVar[str] = "document.update-frontmatter"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = DocumentFrontmatterUpdatePayload
    FIELD_DESCRIPTIONS: ClassVar[dict[str, str]] = {
        "document": "Existing editable Brain document.",
        "expected_revision": "Revision returned by the most recent document read.",
        "updates": "Non-empty field patch; null removes a field and omission preserves it.",
    }
    MINIMAL_EXAMPLE: ClassVar[dict[str, object]] = {
        "document": {"resource": "memory", "reference": "brain-core-reference"},
        "expected_revision": "sha256:" + "0" * 64,
        "updates": {"triggers": ["brain core"]},
    }

    document: DocumentLocator
    expected_revision: str
    updates: tuple[FrontmatterField, ...]

    def __post_init__(self) -> None:
        validate_document_request(self)
        if not isinstance(self.updates, tuple) or not self.updates:
            raise ValueError("document.update-frontmatter updates must be non-empty")
        if any(not isinstance(item, FrontmatterField) for item in self.updates):
            raise ValueError("document.update-frontmatter updates must be typed fields")
        names = tuple(item.name for item in self.updates)
        if len(names) != len(set(names)) or names != tuple(sorted(names)):
            raise ValueError(
                "document.update-frontmatter fields must be unique and ordered"
            )


def execute(context: InvocationContext, request: DocumentUpdateFrontmatterRequest):
    return execute_document_mutation(
        context,
        request,
        DocumentFrontmatterIntent(
            resource=request.document.resource.value,
            reference=request.document.reference,
            expected_revision=request.expected_revision,
            frontmatter=request.updates,
        ),
    )


def decode(payload: Mapping[str, object]) -> DocumentUpdateFrontmatterRequest:
    reject_unexpected(payload, {"document", "expected_revision", "updates"})
    expected_revision = payload.get("expected_revision")
    if not isinstance(expected_revision, str):
        raise ValueError("expected_revision must be a string")
    return DocumentUpdateFrontmatterRequest(
        decode_document(payload.get("document")),
        expected_revision,
        decode_frontmatter(payload.get("updates")),
    )


def catalogue_entry():
    return contributor_mutation_entry(DocumentUpdateFrontmatterRequest, execute)
