"""Typed ``retrieval.repair-lexical`` owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._lexical_maintenance import (
    LexicalMaintenancePayload,
    catalogue_entry as lexical_catalogue_entry,
    decode_empty,
    execute_lexical_maintenance,
)
from ..context import InvocationContext


@dataclass(frozen=True, slots=True)
class RetrievalRepairLexicalRequest:
    COMMAND_ID: ClassVar[str] = "retrieval.repair-lexical"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = LexicalMaintenancePayload


def execute(context: InvocationContext, request: RetrievalRepairLexicalRequest):
    return execute_lexical_maintenance(context, request, force=False)


def decode(payload: Mapping[str, object]) -> RetrievalRepairLexicalRequest:
    return decode_empty(payload, RetrievalRepairLexicalRequest)


def catalogue_entry():
    return lexical_catalogue_entry(RetrievalRepairLexicalRequest, execute)


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(RetrievalRepairLexicalRequest, decode)
