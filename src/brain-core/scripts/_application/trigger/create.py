"""Typed ``trigger.create`` owner."""

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
class TriggerCreateRequest:
    COMMAND_ID: ClassVar[str] = "trigger.create"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = DefinitionMutationPayload

    condition: str
    target: str

    def __post_init__(self) -> None:
        validate_nonempty(self.COMMAND_ID, "condition", self.condition)
        validate_nonempty(self.COMMAND_ID, "target", self.target)


def execute(context: InvocationContext, request: TriggerCreateRequest):
    import define

    return execute_definition(
        context,
        request,
        operation=lambda root, _body: define.update_trigger(
            root,
            operation="create",
            condition=request.condition,
            target=request.target,
        ),
    )


def decode(payload: Mapping[str, object]) -> TriggerCreateRequest:
    return _decode_strings(payload, TriggerCreateRequest, ("condition", "target"))


def _decode_strings(payload, request_type, fields):
    unexpected = sorted(set(payload) - set(fields))
    if unexpected:
        raise ValueError(f"unexpected fields: {', '.join(unexpected)}")
    if any(field not in payload for field in fields):
        raise ValueError("condition and target are required")
    if any(not isinstance(payload[field], str) for field in fields):
        raise ValueError("condition and target must be strings")
    return request_type(*(payload[field] for field in fields))


def catalogue_entry():
    return definition_catalogue_entry(TriggerCreateRequest, execute)


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(TriggerCreateRequest, decode)
