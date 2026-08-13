"""Typed ``type.create`` owner."""

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
class TypeCreateRequest:
    COMMAND_ID: ClassVar[str] = "type.create"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = TypeDefinitionMutationPayload

    name: str
    classification: ArtefactTypeClassification
    definition: MutationContent
    template: MutationContent

    def __post_init__(self) -> None:
        validate_nonempty(self.COMMAND_ID, "name", self.name)
        if not isinstance(self.classification, ArtefactTypeClassification):
            raise ValueError(
                "type.create classification must use ArtefactTypeClassification"
            )
        validate_type_contents(self.COMMAND_ID, self.definition, self.template)


def execute(context: InvocationContext, request: TypeCreateRequest):
    import define

    return execute_type_definition(
        context,
        request,
        definition=request.definition,
        template=request.template,
        operation=lambda root, definition, template: define.write_definition(
            root,
            kind="type",
            operation="create",
            name=request.name,
            classification=request.classification.value,
            definition=definition,
            template=template,
        ),
    )


def decode(payload: Mapping[str, object]) -> TypeCreateRequest:
    fields = {"name", "classification", "definition", "template"}
    reject_unexpected(payload, fields)
    for field in fields:
        if field not in payload:
            raise ValueError(f"{field} is required")
    if not isinstance(payload["name"], str) or not isinstance(
        payload["classification"], str
    ):
        raise ValueError("name and classification must be strings")
    try:
        classification = ArtefactTypeClassification(payload["classification"])
    except ValueError as exc:
        raise ValueError("classification must be 'living' or 'temporal'") from exc
    return TypeCreateRequest(
        payload["name"],
        classification,
        decode_mutation_content(payload["definition"]),
        decode_mutation_content(payload["template"]),
    )


def catalogue_entry():
    return definition_catalogue_entry(TypeCreateRequest, execute)
