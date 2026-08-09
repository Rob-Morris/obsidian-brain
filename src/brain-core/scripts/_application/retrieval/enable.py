"""Typed ``retrieval.enable`` owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._semantic_maintenance import (
    SemanticMaintenancePayload,
    catalogue_entry as semantic_catalogue_entry,
    decode_empty,
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
    return semantic_catalogue_entry(RetrievalEnableRequest, execute)


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(RetrievalEnableRequest, decode)
