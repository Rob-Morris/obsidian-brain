"""Typed ``document.write`` owner for whole-body document mutation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Mapping

from .._decoding import decode_bool, reject_unexpected
from .._document_mutation import (
    DocumentWriteIntent,
    DocumentWritePayload,
    execute_document_mutation,
)
from .._mutation_support import (
    InlineContent,
    MutationContent,
    StagedContent,
    contributor_mutation_entry,
    decode_mutation_content,
)
from ..context import InvocationContext
from ._types import DocumentLocator, decode_document, validate_document_request


class DocumentWriteOperation(str, Enum):
    REPLACE = "replace"
    APPEND = "append"
    PREPEND = "prepend"


@dataclass(frozen=True, slots=True)
class DocumentWriteRequest:
    COMMAND_ID: ClassVar[str] = "document.write"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = DocumentWritePayload
    FIELD_DESCRIPTIONS: ClassVar[dict[str, str]] = {
        "document": "Existing editable Brain document.",
        "expected_revision": "Revision returned by the most recent document read.",
        "operation": "Replace, append or prepend the complete Markdown body.",
        "content": "Inline content or a staged-content handle.",
        "fix_links": "Resolve safe wikilink substitutions for an artefact document.",
    }
    MINIMAL_EXAMPLE: ClassVar[dict[str, object]] = {
        "document": {"resource": "artefact", "reference": "Designs/Example.md"},
        "expected_revision": "sha256:" + "0" * 64,
        "operation": "replace",
        "content": {"source": "inline", "content": "# Example\n"},
    }

    document: DocumentLocator
    expected_revision: str
    operation: DocumentWriteOperation
    content: MutationContent
    fix_links: bool = False

    def __post_init__(self) -> None:
        validate_document_request(self)
        if not isinstance(self.operation, DocumentWriteOperation):
            raise ValueError("document.write operation is invalid")
        if not isinstance(self.content, (InlineContent, StagedContent)):
            raise ValueError("document.write content has an invalid variant")
        if (
            self.operation is not DocumentWriteOperation.REPLACE
            and isinstance(self.content, InlineContent)
            and not self.content.content
        ):
            raise ValueError("document.write append and prepend content must be non-empty")


def execute(context: InvocationContext, request: DocumentWriteRequest):
    operation = {
        DocumentWriteOperation.REPLACE: "edit",
        DocumentWriteOperation.APPEND: "append",
        DocumentWriteOperation.PREPEND: "prepend",
    }[request.operation]
    return execute_document_mutation(
        context,
        request,
        DocumentWriteIntent(
            resource=request.document.resource.value,
            reference=request.document.reference,
            expected_revision=request.expected_revision,
            operation=operation,
            result_operation=request.operation.value,
            content=request.content,
            fix_links=request.fix_links,
        ),
    )


def decode(payload: Mapping[str, object]) -> DocumentWriteRequest:
    reject_unexpected(
        payload,
        {"document", "expected_revision", "operation", "content", "fix_links"},
    )
    expected_revision = payload.get("expected_revision")
    operation = payload.get("operation")
    if not isinstance(expected_revision, str) or not isinstance(operation, str):
        raise ValueError("expected_revision and operation must be strings")
    try:
        typed_operation = DocumentWriteOperation(operation)
    except ValueError as exc:
        raise ValueError("operation must be replace, append, or prepend") from exc
    return DocumentWriteRequest(
        decode_document(payload.get("document")),
        expected_revision,
        typed_operation,
        decode_mutation_content(payload.get("content")),
        decode_bool(payload, "fix_links"),
    )


def catalogue_entry():
    return contributor_mutation_entry(DocumentWriteRequest, execute)
