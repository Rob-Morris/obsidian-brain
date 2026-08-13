"""Typed ``retrieval.refresh-lexical`` owner."""

from __future__ import annotations

from .._decoding import reject_unexpected

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._lexical_maintenance import (
    LexicalMaintenancePayload,
    catalogue_entry as lexical_catalogue_entry,
    execute_lexical_maintenance,
)
from ..context import InvocationContext


@dataclass(frozen=True, slots=True)
class RetrievalRefreshLexicalRequest:
    COMMAND_ID: ClassVar[str] = "retrieval.refresh-lexical"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = LexicalMaintenancePayload

    force: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.force, bool):
            raise ValueError("retrieval.refresh-lexical force must be a boolean")


def execute(context: InvocationContext, request: RetrievalRefreshLexicalRequest):
    return execute_lexical_maintenance(context, request, force=request.force)


def decode(payload: Mapping[str, object]) -> RetrievalRefreshLexicalRequest:
    reject_unexpected(payload, {"force"})
    force = payload.get("force", False)
    if not isinstance(force, bool):
        raise ValueError("force must be a boolean")
    return RetrievalRefreshLexicalRequest(force)


def catalogue_entry():
    return lexical_catalogue_entry(RetrievalRefreshLexicalRequest, execute)
