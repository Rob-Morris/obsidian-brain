"""Explicit dependency-safe history cleanup."""

from dataclasses import dataclass, field
from typing import ClassVar

from _relationships.model import MAX_BATCH, identifier
from .._decoding import reject_unexpected
from ..relationship._types import GraphResult
from ..relationship._support import entry


@dataclass(frozen=True, slots=True)
class HistoryCleanupRequest:
    COMMAND_ID: ClassVar[str] = "history.cleanup"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = GraphResult
    MINIMAL_EXAMPLE: ClassVar[dict] = {"identities": ["example-event"]}

    identities: tuple[str, ...] = field(metadata={"description": "One to 100 event identities to remove after dependency preview; retained tombstones prevent implicit correction revival."})

    def __post_init__(self):
        if not 1 <= len(self.identities) <= MAX_BATCH or len(set(self.identities)) != len(self.identities):
            raise ValueError("cleanup requires 1–100 distinct identities")
        for identity in self.identities:
            identifier(identity)


def decode(payload):
    reject_unexpected(payload, {"identities"})
    return HistoryCleanupRequest(tuple(payload["identities"]))


def catalogue_entry():
    return entry(HistoryCleanupRequest, "cleanup", maintainer=True, summary="Preview or remove selected history records with dependency checks and unresolved deleted corrections.")
