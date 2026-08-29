"""Typed ``retrieval.enable`` owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._decoding import decode_empty
from .._semantic_maintenance import (
    SemanticMaintenancePayload,
    catalogue_entry as semantic_catalogue_entry,
    execute_enable,
)
from ..context import InvocationContext


@dataclass(frozen=True, slots=True)
class RetrievalEnableRequest:
    COMMAND_ID: ClassVar[str] = "retrieval.enable"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = SemanticMaintenancePayload


def execute(context: InvocationContext, request: RetrievalEnableRequest):
    return execute_enable(context, request)


def decode(payload: Mapping[str, object]) -> RetrievalEnableRequest:
    return decode_empty(payload, RetrievalEnableRequest)


def catalogue_entry():
    from ..catalogue import exclude_projection
    from ..types import Projection

    return exclude_projection(
        semantic_catalogue_entry(RetrievalEnableRequest, execute),
        Projection.MCP,
        "Managed semantic provisioning is reserved for deliberate CLI or "
        "direct-script administration.",
    )
