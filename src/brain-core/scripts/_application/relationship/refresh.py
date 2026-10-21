"""Explicit bounded rebuild after external edits or missing projection state."""

from dataclasses import dataclass, field
from typing import ClassVar
from .._decoding import reject_unexpected
from ._types import GraphResult, bounds
from ._maintenance import entry


@dataclass(frozen=True, slots=True)
class RelationshipRefreshRequest:
    COMMAND_ID: ClassVar[str] = "relationship.refresh"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = GraphResult
    work_budget: int = field(default=5000, metadata={"description": "Maximum source records read per rebuild page, from 1 to 5000. Explicit filesystem filename enumeration is additional rebuild work."})
    cursor: str | None = field(default=None, metadata={"description": "Opaque continuation returned by the preceding refresh page; omission restarts the rebuild. Semantic authoring and native document/lifecycle plans refuse until the rebuild finishes; maintenance remains available."})

    def __post_init__(self):
        bounds(100, self.work_budget)


def decode(payload):
    reject_unexpected(payload, {"work_budget", "cursor"})
    return RelationshipRefreshRequest(**payload)


def catalogue_entry():
    return entry(RelationshipRefreshRequest, "Rebuild the disposable relationship projection within an explicit source work budget.")
