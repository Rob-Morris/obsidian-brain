"""Every supported direct event read includes correction interpretation."""

from dataclasses import dataclass, field
from functools import partial
from typing import ClassVar

from _relationships.model import identifier
from .._decoding import reject_unexpected
from ..relationship._types import GraphResult, bounds
from ..relationship import _reads


@dataclass(frozen=True, slots=True)
class HistoryReadRequest:
    COMMAND_ID: ClassVar[str] = "history.read"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = GraphResult

    identity: str = field(metadata={"description": "Event identity to inspect, including original superseded records."})
    work_budget: int = field(default=100, metadata={"description": "Maximum correction-chain traversal work, from 1 to 5000."})

    def __post_init__(self):
        identifier(self.identity)
        bounds(100, self.work_budget)


def decode(payload):
    reject_unexpected(payload, {"identity", "work_budget"})
    return HistoryReadRequest(**payload)


def catalogue_entry():
    return _reads.entry(HistoryReadRequest, partial(_reads.read, kind="event"),
                        "Read an event with visible correction links, effective interpretation and pinned-contract validation.")
