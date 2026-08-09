"""Typed ``trigger.delete`` owner."""

from __future__ import annotations

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
        operation=lambda root, _body: define.update_trigger(
            root,
            operation="delete",
            condition=request.condition,
            target=request.target,
        ),
    )


def decode(payload: Mapping[str, object]) -> TriggerDeleteRequest:
    unexpected = sorted(set(payload) - {"condition", "target"})
    if unexpected:
        raise ValueError(f"unexpected fields: {', '.join(unexpected)}")
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
    return definition_catalogue_entry(TriggerDeleteRequest, execute)


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(TriggerDeleteRequest, decode)
