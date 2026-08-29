"""Typed ``retrieval.rebuild-semantic`` owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._decoding import decode_empty
from .._semantic_maintenance import (
    SemanticMaintenancePayload,
    catalogue_entry as semantic_catalogue_entry,
    execute_rebuild,
)
from ..context import InvocationContext
from ..types import Authority


@dataclass(frozen=True, slots=True)
class RetrievalRebuildSemanticRequest:
    COMMAND_ID: ClassVar[str] = "retrieval.rebuild-semantic"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = SemanticMaintenancePayload


def execute(context: InvocationContext, request: RetrievalRebuildSemanticRequest):
    return execute_rebuild(context, request)


def decode(payload: Mapping[str, object]) -> RetrievalRebuildSemanticRequest:
    return decode_empty(payload, RetrievalRebuildSemanticRequest)


def catalogue_entry():
    return semantic_catalogue_entry(
        RetrievalRebuildSemanticRequest,
        execute,
        authority=Authority.MAINTAINER,
    )
