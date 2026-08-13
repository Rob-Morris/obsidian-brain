"""Typed ``artefact.reparent`` owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._lifecycle_mutation import (
    ArtefactLifecyclePayload,
    catalogue_entry as lifecycle_catalogue_entry,
    decode_lifecycle_request,
    execute_lifecycle_mutation,
    validate_nullable_value,
    validate_path,
)
from ..context import InvocationContext


@dataclass(frozen=True, slots=True)
class ArtefactReparentRequest:
    COMMAND_ID: ClassVar[str] = "artefact.reparent"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = ArtefactLifecyclePayload

    path: str
    parent: str | None

    def __post_init__(self) -> None:
        validate_path(self.COMMAND_ID, self.path)
        validate_nullable_value(self.COMMAND_ID, "parent", self.parent)


def execute(context: InvocationContext, request: ArtefactReparentRequest):
    return execute_lifecycle_mutation(
        context, request, field="parent", value=request.parent
    )


def decode(payload: Mapping[str, object]) -> ArtefactReparentRequest:
    return decode_lifecycle_request(
        payload, ArtefactReparentRequest, value_field="parent", nullable=True
    )


def catalogue_entry():
    return lifecycle_catalogue_entry(ArtefactReparentRequest, execute)
