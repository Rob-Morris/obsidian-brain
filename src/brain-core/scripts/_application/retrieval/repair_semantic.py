"""Typed ``retrieval.repair-semantic`` owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._decoding import decode_empty
from .._semantic_maintenance import (
    SemanticMaintenancePayload,
    catalogue_entry as semantic_catalogue_entry,
    execute_repair,
)
from ..context import InvocationContext


@dataclass(frozen=True, slots=True)
class RetrievalRepairSemanticRequest:
    COMMAND_ID: ClassVar[str] = "retrieval.repair-semantic"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = SemanticMaintenancePayload


def execute(context: InvocationContext, request: RetrievalRepairSemanticRequest):
    return execute_repair(context, request)


def decode(payload: Mapping[str, object]) -> RetrievalRepairSemanticRequest:
    return decode_empty(payload, RetrievalRepairSemanticRequest)


def catalogue_entry():
    return semantic_catalogue_entry(RetrievalRepairSemanticRequest, execute)
