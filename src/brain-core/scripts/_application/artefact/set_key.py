"""Typed ``artefact.set-key`` owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._lifecycle_mutation import (
    ArtefactLifecyclePayload,
    catalogue_entry as lifecycle_catalogue_entry,
    decode_lifecycle_request,
    execute_lifecycle_mutation,
    validate_path,
    validate_required_value,
)
from ..context import InvocationContext


@dataclass(frozen=True, slots=True)
class ArtefactSetKeyRequest:
    COMMAND_ID: ClassVar[str] = "artefact.set-key"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = ArtefactLifecyclePayload

    path: str
    key: str

    def __post_init__(self) -> None:
        validate_path(self.COMMAND_ID, self.path)
        validate_required_value(self.COMMAND_ID, "key", self.key)


def execute(context: InvocationContext, request: ArtefactSetKeyRequest):
    return execute_lifecycle_mutation(context, request, field="key", value=request.key)


def decode(payload: Mapping[str, object]) -> ArtefactSetKeyRequest:
    return decode_lifecycle_request(
        payload, ArtefactSetKeyRequest, value_field="key", nullable=False
    )


def catalogue_entry():
    from dataclasses import replace
    from ..preparation_transition import TransitionPreparation

    return replace(lifecycle_catalogue_entry(ArtefactSetKeyRequest, execute),
                   preparation=TransitionPreparation(plan_operation))


def plan_operation(context, request, router, *, frozen_inputs=None):
    from .._lifecycle_mutation import plan_lifecycle_request

    return plan_lifecycle_request(context, request, router, field='key',
                                  value=request.key, frozen_inputs=frozen_inputs)
