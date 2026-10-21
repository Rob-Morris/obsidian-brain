"""Head-guarded corrections preserve the original event account."""

from dataclasses import dataclass, field
from typing import ClassVar

from _relationships.model import identifier, nonempty
from .._decoding import reject_unexpected
from ..relationship._types import GraphResult
from ..relationship._support import entry


@dataclass(frozen=True, slots=True)
class HistoryCorrectRequest:
    COMMAND_ID: ClassVar[str] = "history.correct"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = GraphResult

    identity: str = field(metadata={"description": "Original event or correction identity whose effective chain is being corrected."})
    expected_head_revision: str = field(metadata={"description": "Observed revision of the current effective correction head; competing corrections conflict."})
    reason: str = field(metadata={"description": "Explicit caller explanation for the correction."})
    replacement: str = field(metadata={"description": "Corrected interpretation retained in a linked event; the original payload is preserved."})

    def __post_init__(self):
        identifier(self.identity)
        for name in ("expected_head_revision", "reason", "replacement"):
            nonempty(getattr(self, name), name)


def decode(payload):
    reject_unexpected(payload, {"identity", "expected_head_revision", "reason", "replacement"})
    return HistoryCorrectRequest(**payload)


def catalogue_entry():
    return entry(HistoryCorrectRequest, "correct", summary="Add a linked correction guarded by the effective head revision and visibly supersede the retained original.")
