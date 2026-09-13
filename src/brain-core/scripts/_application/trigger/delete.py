"""Typed ``trigger.delete`` owner."""

from __future__ import annotations

from .._decoding import reject_unexpected

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._definition_mutation import (
    DefinitionMutationPayload,
    catalogue_entry as definition_catalogue_entry,
    execute_definition,
    validate_nonempty,
)
from ..context import InvocationContext


@dataclass(frozen=True, slots=True)
class TriggerDeleteRequest:
    COMMAND_ID: ClassVar[str] = "trigger.delete"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = DefinitionMutationPayload

    condition: str
    target: str | None = None

    def __post_init__(self) -> None:
        validate_nonempty(self.COMMAND_ID, "condition", self.condition)
        if self.target is not None:
            validate_nonempty(self.COMMAND_ID, "target", self.target)


def execute(context: InvocationContext, request: TriggerDeleteRequest):
    import define

    return execute_definition(
        context,
        request,
        operation=plan_operation(request),
    )


def decode(payload: Mapping[str, object]) -> TriggerDeleteRequest:
    reject_unexpected(payload, {"condition", "target"})
    if "condition" not in payload:
        raise ValueError("condition is required")
    condition = payload["condition"]
    target = payload.get("target")
    if not isinstance(condition, str) or (
        target is not None and not isinstance(target, str)
    ):
        raise ValueError("condition and target must be strings")
    return TriggerDeleteRequest(condition, target)


def catalogue_entry():
    from dataclasses import replace
    from ..preparation import OperationPreparation

    return replace(definition_catalogue_entry(TriggerDeleteRequest, execute), preparation=OperationPreparation(prepare))


def plan_operation(request):
    import define

    return lambda root, _body: define.plan_update_trigger(
            root,
            operation="delete",
            condition=request.condition,
            target=request.target,
        )


def prepare(context, request, *, frozen_inputs=None):
    from .._definition_mutation import prepare_definition_command

    return prepare_definition_command(context, request, operation=plan_operation(request),
                                       contents=(), frozen_inputs=frozen_inputs)
