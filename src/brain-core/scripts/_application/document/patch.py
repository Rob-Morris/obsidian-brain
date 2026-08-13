"""Typed ``document.patch`` owner for guarded exact-text substitution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Literal, Mapping

from .._decoding import decode_bool, reject_unexpected
from .._document_mutation import (
    DocumentMutationIntent,
    DocumentPatchPayload,
    execute_document_mutation,
)
from .._mutation_support import contributor_mutation_entry
from ..context import InvocationContext
from ._types import DocumentLocator, decode_document, validate_document_request


@dataclass(frozen=True, slots=True)
class UniqueMatch:
    mode: Literal["unique"] = field(default="unique", init=False)


@dataclass(frozen=True, slots=True)
class OccurrenceMatch:
    occurrence: int
    mode: Literal["occurrence"] = field(default="occurrence", init=False)

    def __post_init__(self) -> None:
        if type(self.occurrence) is not int or self.occurrence < 1:
            raise ValueError("document.patch occurrence must be a positive integer")


@dataclass(frozen=True, slots=True)
class AllMatches:
    mode: Literal["all"] = field(default="all", init=False)


DocumentPatchMatch = UniqueMatch | OccurrenceMatch | AllMatches


@dataclass(frozen=True, slots=True)
class DocumentPatchRequest:
    COMMAND_ID: ClassVar[str] = "document.patch"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = DocumentPatchPayload
    FIELD_DESCRIPTIONS: ClassVar[dict[str, str]] = {
        "document": "Existing editable Brain document.",
        "expected_revision": "Revision returned by the most recent document read.",
        "old_text": "Exact non-empty body text to replace.",
        "new_text": "Replacement body text, which may be empty.",
        "match": "Require a unique match, one occurrence, or all matches.",
        "fix_links": "Resolve safe wikilink substitutions for an artefact document.",
    }
    MINIMAL_EXAMPLE: ClassVar[dict[str, object]] = {
        "document": {"resource": "artefact", "reference": "Designs/Example.md"},
        "expected_revision": "sha256:" + "0" * 64,
        "old_text": "Old wording.",
        "new_text": "New wording.",
        "match": {"mode": "unique"},
    }

    document: DocumentLocator
    expected_revision: str
    old_text: str
    new_text: str
    match: DocumentPatchMatch
    fix_links: bool = False

    def __post_init__(self) -> None:
        validate_document_request(self)
        if not isinstance(self.old_text, str) or not self.old_text:
            raise ValueError("document.patch old_text must be non-empty")
        if not isinstance(self.new_text, str):
            raise ValueError("document.patch new_text must be a string")
        if not isinstance(self.match, (UniqueMatch, OccurrenceMatch, AllMatches)):
            raise ValueError("document.patch match has an invalid variant")


def execute(context: InvocationContext, request: DocumentPatchRequest):
    occurrence = request.match.occurrence if isinstance(request.match, OccurrenceMatch) else None
    return execute_document_mutation(
        context,
        request,
        DocumentMutationIntent(
            resource=request.document.resource.value,
            reference=request.document.reference,
            expected_revision=request.expected_revision,
            operation="replace_text",
            result_operation="patch",
            old_text=request.old_text,
            new_text=request.new_text,
            match_occurrence=occurrence,
            replace_all=isinstance(request.match, AllMatches),
            fix_links=request.fix_links,
        ),
    )


def decode(payload: Mapping[str, object]) -> DocumentPatchRequest:
    reject_unexpected(
        payload,
        {"document", "expected_revision", "old_text", "new_text", "match", "fix_links"},
    )
    expected_revision = payload.get("expected_revision")
    old_text = payload.get("old_text")
    new_text = payload.get("new_text")
    if not all(isinstance(value, str) for value in (expected_revision, old_text, new_text)):
        raise ValueError("expected_revision, old_text and new_text must be strings")
    return DocumentPatchRequest(
        decode_document(payload.get("document")),
        expected_revision,
        old_text,
        new_text,
        _decode_match(payload.get("match")),
        decode_bool(payload, "fix_links"),
    )


def _decode_match(value: object) -> DocumentPatchMatch:
    if not isinstance(value, Mapping):
        raise ValueError("match must be an object")
    mode = value.get("mode")
    if mode == "unique":
        reject_unexpected(value, {"mode"}, label="unique match fields")
        return UniqueMatch()
    if mode == "all":
        reject_unexpected(value, {"mode"}, label="all match fields")
        return AllMatches()
    if mode == "occurrence":
        reject_unexpected(value, {"mode", "occurrence"}, label="occurrence match fields")
        occurrence = value.get("occurrence")
        if type(occurrence) is not int:
            raise ValueError("occurrence match requires an integer occurrence")
        return OccurrenceMatch(occurrence)
    raise ValueError("match mode must be unique, occurrence, or all")


def catalogue_entry():
    return contributor_mutation_entry(DocumentPatchRequest, execute)
