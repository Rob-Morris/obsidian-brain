"""Typed ``trigger.replace`` owner."""

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
class TriggerReplaceRequest:
    COMMAND_ID: ClassVar[str] = "trigger.replace"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = DefinitionMutationPayload

    condition: str
    target: str
    new_condition: str | None = None
    new_target: str | None = None

    def __post_init__(self) -> None:
        validate_nonempty(self.COMMAND_ID, "condition", self.condition)
        validate_nonempty(self.COMMAND_ID, "target", self.target)
        for field in ("new_condition", "new_target"):
            value = getattr(self, field)
            if value is not None:
                validate_nonempty(self.COMMAND_ID, field, value)


def execute(context: InvocationContext, request: TriggerReplaceRequest):
    import define

    return execute_definition(
        context,
        request,
        operation=lambda root, _body: define.update_trigger(
            root,
            operation="replace",
            condition=request.condition,
            target=request.target,
            new_condition=request.new_condition,
            new_target=request.new_target,
        ),
    )


def decode(payload: Mapping[str, object]) -> TriggerReplaceRequest:
    allowed = {"condition", "target", "new_condition", "new_target"}
    reject_unexpected(payload, allowed)
    for field in ("condition", "target"):
        if field not in payload:
            raise ValueError(f"{field} is required")
    values = [payload.get(field) for field in allowed]
    if any(value is not None and not isinstance(value, str) for value in values):
        raise ValueError("trigger replacement fields must be strings")
    return TriggerReplaceRequest(
        payload["condition"],
        payload["target"],
        payload.get("new_condition"),
        payload.get("new_target"),
    )


def catalogue_entry():
    return definition_catalogue_entry(TriggerReplaceRequest, execute)
