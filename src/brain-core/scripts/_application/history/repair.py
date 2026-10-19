"""Recover captured publication effects without replaying a newer mutation."""

from dataclasses import dataclass, field
from typing import ClassVar
from _relationships.model import identifier, nonempty
from .._decoding import reject_unexpected
from ..relationship._types import GraphResult
from ..relationship._maintenance import entry


@dataclass(frozen=True, slots=True)
class HistoryRepairRequest:
    COMMAND_ID: ClassVar[str] = "history.repair"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = GraphResult
    operation: str = field(metadata={"description": "Exact interrupted operation ID returned with committed effects; repair validates captured state before writing missing events."})
    abandon: bool = field(default=False, metadata={"description": "Explicitly abandon only if captured native and semantic before-state revisions prove no state effects committed; never replay state."})
    reason: str | None = field(default=None, metadata={"description": "Required retained explanation when abandoning an operation."})

    def __post_init__(self):
        identifier(self.operation)
        if type(self.abandon) is not bool:
            raise ValueError("abandon must be boolean")
        if self.abandon:
            nonempty(self.reason, "abandon reason")


def decode(payload):
    reject_unexpected(payload, {"operation", "abandon", "reason"})
    return HistoryRepairRequest(**payload)


def catalogue_entry():
    return entry(HistoryRepairRequest, "Repair an interrupted semantic publication from its captured operation without replaying current-state changes.")
