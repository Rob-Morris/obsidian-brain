"""Typed ``plugin.create`` owner."""

from __future__ import annotations

from .._decoding import reject_unexpected

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._definition_mutation import (
    DefinitionMutationPayload,
    catalogue_entry as definition_catalogue_entry,
    execute_definition,
    validate_content,
    validate_nonempty,
)
from .._mutation_support import MutationContent, decode_mutation_content
from ..context import InvocationContext


@dataclass(frozen=True, slots=True)
class PluginCreateRequest:
    COMMAND_ID: ClassVar[str] = "plugin.create"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = DefinitionMutationPayload

    name: str
    content: MutationContent

    def __post_init__(self) -> None:
        validate_nonempty(self.COMMAND_ID, "name", self.name)
        validate_content(self.COMMAND_ID, self.content)


def execute(context: InvocationContext, request: PluginCreateRequest):
    import define

    return execute_definition(
        context,
        request,
        content=request.content,
        operation=plan_operation(request),
    )


def decode(payload: Mapping[str, object]) -> PluginCreateRequest:
    reject_unexpected(payload, {"name", "content"})
    if "name" not in payload or "content" not in payload:
        raise ValueError("name and content are required")
    if not isinstance(payload["name"], str):
        raise ValueError("name must be a string")
    return PluginCreateRequest(
        payload["name"], decode_mutation_content(payload["content"])
    )


def catalogue_entry():
    from dataclasses import replace
    from ..preparation import OperationPreparation

    return replace(definition_catalogue_entry(PluginCreateRequest, execute), preparation=OperationPreparation(prepare))


def plan_operation(request):
    import define

    return lambda root, body: define.plan_write_definition(
            root,
            kind="plugin",
            operation="create",
            name=request.name,
            definition=body,
        )


def prepare(context, request, *, frozen_inputs=None):
    from .._definition_mutation import prepare_definition_command

    return prepare_definition_command(context, request, operation=plan_operation(request),
                                       contents=(request.content,), frozen_inputs=frozen_inputs)
