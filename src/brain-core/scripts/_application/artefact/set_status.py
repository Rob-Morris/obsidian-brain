"""Typed ``artefact.set-status`` owner."""

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
class ArtefactSetStatusRequest:
    COMMAND_ID: ClassVar[str] = "artefact.set-status"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = ArtefactLifecyclePayload

    path: str
    status: str

    def __post_init__(self) -> None:
        validate_path(self.COMMAND_ID, self.path)
        validate_required_value(self.COMMAND_ID, "status", self.status)


def execute(context: InvocationContext, request: ArtefactSetStatusRequest):
    return execute_lifecycle_mutation(
        context, request, field="status", value=request.status
    )


def decode(payload: Mapping[str, object]) -> ArtefactSetStatusRequest:
    return decode_lifecycle_request(
        payload, ArtefactSetStatusRequest, value_field="status", nullable=False
    )


def catalogue_entry():
    return lifecycle_catalogue_entry(ArtefactSetStatusRequest, execute)
