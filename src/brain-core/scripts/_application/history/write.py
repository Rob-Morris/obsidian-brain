"""Explicit custom events retain caller provenance."""

from dataclasses import dataclass, field
from typing import ClassVar

from _relationships.model import Endpoint, identifier
from .._decoding import reject_unexpected
from ..relationship._types import GraphResult, PayloadValue
from ..relationship._support import entry
from ..relationship.batch import decode_endpoint


@dataclass(frozen=True, slots=True)
class HistoryWriteRequest:
    COMMAND_ID: ClassVar[str] = "history.write"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = GraphResult
    MINIMAL_EXAMPLE: ClassVar[dict] = {"event_type": "milestone", "subject": {"kind": "artefact", "reference": "design/example"}}

    event_type: str = field(metadata={"description": "Registered vault-local event type; the event pins its current contract."})
    subject: Endpoint = field(metadata={"description": "Typed subject of the caller-authored event."})
    payload: tuple[PayloadValue, ...] = field(default=(), metadata={"description": "Fields validated against the registered required/optional contract."})
    asserted_at: str | None = field(default=None, metadata={"description": "Optional caller-asserted event time, distinct from recorded time."})
    attribution: str | None = field(default=None, metadata={"description": "Optional claimed attribution; never authenticated by this command."})

    def __post_init__(self):
        identifier(self.event_type)
        if not isinstance(self.payload, tuple) or len(self.payload) > 100 or any(not isinstance(item, PayloadValue) for item in self.payload):
            raise ValueError("event payload requires at most 100 typed values")
        if not isinstance(self.subject, Endpoint) or len({item.name for item in self.payload}) != len(self.payload):
            raise ValueError("event requires a typed subject and unique payload fields")


def decode(payload):
    reject_unexpected(payload, {"event_type", "subject", "payload", "asserted_at", "attribution"})
    for item in payload.get("payload", ()):
        reject_unexpected(item, {"name", "value"})
    return HistoryWriteRequest(payload["event_type"], decode_endpoint(payload["subject"]),
        tuple(PayloadValue(item["name"], decode_endpoint(item["value"]) if isinstance(item.get("value"), dict) else item["value"])
              for item in payload.get("payload", ())), payload.get("asserted_at"), payload.get("attribution"))


def catalogue_entry():
    return entry(HistoryWriteRequest, "write_event", summary="Write a registered custom event with explicit caller provenance without changing current state.")
