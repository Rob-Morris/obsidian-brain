"""Typed ``plugin.replace`` owner."""

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
class PluginReplaceRequest:
    COMMAND_ID: ClassVar[str] = "plugin.replace"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = DefinitionMutationPayload

    name: str
    content: MutationContent
    expected_sha256: str

    def __post_init__(self) -> None:
        validate_nonempty(self.COMMAND_ID, "name", self.name)
        validate_content(self.COMMAND_ID, self.content)
        validate_nonempty(self.COMMAND_ID, "expected_sha256", self.expected_sha256)


def execute(context: InvocationContext, request: PluginReplaceRequest):
    import define

    return execute_definition(
        context,
        request,
        content=request.content,
        operation=plan_operation(request),
    )


def decode(payload: Mapping[str, object]) -> PluginReplaceRequest:
    reject_unexpected(payload, {"name", "content", "expected_sha256"})
    for field in ("name", "content", "expected_sha256"):
        if field not in payload:
            raise ValueError(f"{field} is required")
    if not isinstance(payload["name"], str) or not isinstance(
        payload["expected_sha256"], str
    ):
        raise ValueError("name and expected_sha256 must be strings")
    return PluginReplaceRequest(
        payload["name"],
        decode_mutation_content(payload["content"]),
        payload["expected_sha256"],
    )


def catalogue_entry():
    from dataclasses import replace
    from ..preparation import OperationPreparation

    return replace(definition_catalogue_entry(PluginReplaceRequest, execute), preparation=OperationPreparation(prepare))


def plan_operation(request):
    import define

    return lambda root, body: define.plan_write_definition(
            root,
            kind="plugin",
            operation="replace",
            name=request.name,
            definition=body,
            expected_sha256=request.expected_sha256,
        )


def prepare(context, request, *, frozen_inputs=None):
    from .._definition_mutation import prepare_definition_command

    return prepare_definition_command(context, request, operation=plan_operation(request),
                                       contents=(request.content,), frozen_inputs=frozen_inputs)
