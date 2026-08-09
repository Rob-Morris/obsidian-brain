"""Typed ``retrieval.rebuild-lexical`` owner."""

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
class RetrievalRebuildLexicalRequest:
    COMMAND_ID: ClassVar[str] = "retrieval.rebuild-lexical"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = LexicalMaintenancePayload


def execute(context: InvocationContext, request: RetrievalRebuildLexicalRequest):
    return execute_lexical_maintenance(context, request, force=True)


def decode(payload: Mapping[str, object]) -> RetrievalRebuildLexicalRequest:
    return decode_empty(payload, RetrievalRebuildLexicalRequest)


def catalogue_entry():
    return lexical_catalogue_entry(RetrievalRebuildLexicalRequest, execute)


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(RetrievalRebuildLexicalRequest, decode)
