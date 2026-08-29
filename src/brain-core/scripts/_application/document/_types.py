"""Shared public value types for editable Brain documents."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Mapping

from .._decoding import reject_unexpected


class DocumentResource(str, Enum):
    ARTEFACT = "artefact"
    MEMORY = "memory"
    SKILL = "skill"
    STYLE = "style"
    TEMPLATE = "template"


@dataclass(frozen=True, slots=True)
class DocumentLocator:
    FIELD_DESCRIPTIONS: ClassVar[dict[str, str]] = {
        "resource": "Kind of editable Brain document.",
        "reference": "Artefact key/path or named memory, skill, style or template.",
    }

    resource: DocumentResource
    reference: str

    def __post_init__(self) -> None:
        if not isinstance(self.resource, DocumentResource):
            raise ValueError("document resource is invalid")
        if not isinstance(self.reference, str) or not self.reference.strip():
            raise ValueError("document reference must be non-empty")


def decode_document(value: object) -> DocumentLocator:
    if not isinstance(value, Mapping):
        raise ValueError("document must be an object")
    reject_unexpected(value, {"resource", "reference"}, label="document fields")
    resource = value.get("resource")
    reference = value.get("reference")
    if not isinstance(resource, str) or not isinstance(reference, str):
        raise ValueError("document requires string resource and reference fields")
    try:
        return DocumentLocator(DocumentResource(resource), reference)
    except ValueError as exc:
        if resource not in {item.value for item in DocumentResource}:
            raise ValueError(
                "document resource must be artefact, memory, skill, style, or template"
            ) from exc
        raise


def validate_document_request(request) -> None:
    from _common import validate_document_revision

    if not isinstance(request.document, DocumentLocator):
        raise ValueError(f"{request.COMMAND_ID} document must be a DocumentLocator")
    validate_document_revision(request.expected_revision, label="expected_revision")
    if not isinstance(getattr(request, "fix_links", False), bool):
        raise ValueError(f"{request.COMMAND_ID} fix_links must be a boolean")
    if (
        getattr(request, "fix_links", False)
        and request.document.resource is not DocumentResource.ARTEFACT
    ):
        raise ValueError(
            f"{request.COMMAND_ID} fix_links is available only for artefacts"
        )
