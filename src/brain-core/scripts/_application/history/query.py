"""Separate bounded history inspection, never a current-state source."""

from dataclasses import dataclass, field
from functools import partial
from typing import ClassVar

from _relationships.model import Endpoint
from .._decoding import reject_unexpected
from ..relationship._types import GraphResult, GraphCursor, bounds
from ..relationship.batch import decode_endpoint
from ..relationship import _reads


@dataclass(frozen=True, slots=True)
class HistoryQueryRequest:
    COMMAND_ID: ClassVar[str] = "history.query"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = GraphResult

    subject: Endpoint | None = field(default=None, metadata={"description": "Optional typed event subject filter."})
    predicate: str | None = field(default=None, metadata={"description": "Optional registered event-type identity filter."})
    workspace: str | None = field(default=None, metadata={"description": "Optional explicit workspace/key or global filter."})
    date_from: str | None = field(default=None, metadata={"description": "Inclusive lower recorded-time ISO 8601 bound."})
    date_to: str | None = field(default=None, metadata={"description": "Inclusive upper recorded-time ISO 8601 bound."})
    limit: int = field(default=50, metadata={"description": "Maximum output events, from 1 to 100."})
    work_budget: int = field(default=1000, metadata={"description": "Maximum inspected events and correction work, from 1 to 5000."})
    cursor: GraphCursor | None = field(default=None, metadata={"description": "Continuation tied to this projection generation and unchanged selectors."})

    def __post_init__(self):
        bounds(self.limit, self.work_budget)
        from datetime import datetime
        for value in (self.date_from, self.date_to):
            if value is not None and datetime.fromisoformat(value).tzinfo is None:
                raise ValueError("history date bounds require an explicit timezone")
        if self.date_from and self.date_to and datetime.fromisoformat(self.date_from) > datetime.fromisoformat(self.date_to):
            raise ValueError("date_from must precede date_to")


def decode(payload):
    reject_unexpected(payload, {"subject", "predicate", "workspace", "date_from", "date_to", "limit", "work_budget", "cursor"})
    values = dict(payload)
    if values.get("subject") is not None:
        values["subject"] = decode_endpoint(values["subject"])
    if values.get("cursor") is not None:
        values["cursor"] = GraphCursor(**values["cursor"])
    return HistoryQueryRequest(**values)


def catalogue_entry():
    return _reads.entry(HistoryQueryRequest, partial(_reads.query, kind="event"),
                        "Inspect selected history by subject, event type and recorded date with bounded correction interpretation.")
