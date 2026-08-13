"""Typed ``type.replace`` owner."""

from __future__ import annotations

from .._decoding import reject_unexpected

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._definition_mutation import (
    TypeDefinitionMutationPayload,
    catalogue_entry as definition_catalogue_entry,
    execute_type_definition,
    validate_nonempty,
    validate_type_contents,
)
from .._mutation_support import MutationContent, decode_mutation_content
from ..context import InvocationContext
from ._classification import ArtefactTypeClassification


@dataclass(frozen=True, slots=True)
class TypeReplaceRequest:
    COMMAND_ID: ClassVar[str] = "type.replace"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = TypeDefinitionMutationPayload

    name: str
    classification: ArtefactTypeClassification
    definition: MutationContent
    template: MutationContent
    expected_sha256: str
    expected_template_sha256: str

    def __post_init__(self) -> None:
        validate_nonempty(self.COMMAND_ID, "name", self.name)
        if not isinstance(self.classification, ArtefactTypeClassification):
            raise ValueError(
                "type.replace classification must use ArtefactTypeClassification"
            )
        validate_type_contents(self.COMMAND_ID, self.definition, self.template)
        validate_nonempty(self.COMMAND_ID, "expected_sha256", self.expected_sha256)
        validate_nonempty(
            self.COMMAND_ID,
            "expected_template_sha256",
            self.expected_template_sha256,
        )


def execute(context: InvocationContext, request: TypeReplaceRequest):
    import define

    return execute_type_definition(
        context,
        request,
        definition=request.definition,
        template=request.template,
        operation=lambda root, definition, template: define.write_definition(
            root,
            kind="type",
            operation="replace",
            name=request.name,
            classification=request.classification.value,
            definition=definition,
            template=template,
            expected_sha256=request.expected_sha256,
            expected_template_sha256=request.expected_template_sha256,
        ),
    )


def decode(payload: Mapping[str, object]) -> TypeReplaceRequest:
    fields = {
        "name",
        "classification",
        "definition",
        "template",
        "expected_sha256",
        "expected_template_sha256",
    }
    reject_unexpected(payload, fields)
    for field in fields:
        if field not in payload:
            raise ValueError(f"{field} is required")
    string_fields = (
        "name",
        "classification",
        "expected_sha256",
        "expected_template_sha256",
    )
    if any(not isinstance(payload[field], str) for field in string_fields):
        raise ValueError(
            "name, classification and expected hashes must be strings"
        )
    try:
        classification = ArtefactTypeClassification(payload["classification"])
    except ValueError as exc:
        raise ValueError("classification must be 'living' or 'temporal'") from exc
    return TypeReplaceRequest(
        payload["name"],
        classification,
        decode_mutation_content(payload["definition"]),
        decode_mutation_content(payload["template"]),
        payload["expected_sha256"],
        payload["expected_template_sha256"],
    )


def catalogue_entry():
    return definition_catalogue_entry(TypeReplaceRequest, execute)
