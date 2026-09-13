"""Typed ``artefact.set-naming-field`` owner."""

from __future__ import annotations

from .._decoding import reject_unexpected

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._lifecycle_mutation import (
    ArtefactLifecyclePayload,
    catalogue_entry as lifecycle_catalogue_entry,
    execute_lifecycle_mutation,
    validate_path,
    validate_required_value,
)
from ..context import InvocationContext


@dataclass(frozen=True, slots=True)
class ArtefactSetNamingFieldRequest:
    COMMAND_ID: ClassVar[str] = "artefact.set-naming-field"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = ArtefactLifecyclePayload

    path: str
    field: str
    value: str

    def __post_init__(self) -> None:
        validate_path(self.COMMAND_ID, self.path)
        validate_required_value(self.COMMAND_ID, "field", self.field)
        validate_required_value(self.COMMAND_ID, "value", self.value)


def execute(context: InvocationContext, request: ArtefactSetNamingFieldRequest):
    return execute_lifecycle_mutation(
        context, request, field=request.field, value=request.value
    )


def decode(payload: Mapping[str, object]) -> ArtefactSetNamingFieldRequest:
    reject_unexpected(payload, {"path", "field", "value"})
    missing = [name for name in ("path", "field", "value") if name not in payload]
    if missing:
        raise ValueError(f"{missing[0]} is required")
    values = tuple(payload[name] for name in ("path", "field", "value"))
    if any(not isinstance(value, str) for value in values):
        raise ValueError("path, field, and value must be strings")
    return ArtefactSetNamingFieldRequest(*values)


def catalogue_entry():
    from dataclasses import replace
    from ..preparation_transition import TransitionPreparation

    return replace(lifecycle_catalogue_entry(ArtefactSetNamingFieldRequest, execute),
                   preparation=TransitionPreparation(plan_operation))


def plan_operation(context, request, router, *, frozen_inputs=None):
    from .._lifecycle_mutation import plan_lifecycle_request

    return plan_lifecycle_request(context, request, router, field=request.field,
                                  value=request.value, frozen_inputs=frozen_inputs)
