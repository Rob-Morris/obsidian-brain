"""Persist referenceable occurrences of existing authoritative native facts."""

from dataclasses import dataclass, field
from typing import ClassVar

from _relationships.model import Endpoint, NodeKind
from .._decoding import reject_unexpected
from ._types import GraphResult, Disposition
from ._support import entry
from .batch import decode_endpoint, decode_disposition


@dataclass(frozen=True, slots=True)
class RelationshipExposeRequest:
    COMMAND_ID: ClassVar[str] = "relationship.expose"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = GraphResult
    MINIMAL_EXAMPLE: ClassVar[dict] = {"subject": {"kind": "artefact", "reference": "design/example"}}

    subject: Endpoint = field(metadata={"description": "Native artefact whose parent, membership, relevance and status facts become referenceable occurrences."})
    dispositions: tuple[Disposition, ...] = field(default=(), metadata={"description": "Explicit dependency withdrawal for previously exposed facts changed outside supported commands."})

    def __post_init__(self):
        if not isinstance(self.subject, Endpoint) or self.subject.kind is not NodeKind.ARTEFACT:
            raise ValueError("expose requires an artefact subject")


def decode(payload):
    reject_unexpected(payload, {"subject", "dispositions"})
    return RelationshipExposeRequest(decode_endpoint(payload["subject"]), tuple(decode_disposition(item) for item in payload.get("dispositions", ())))


def catalogue_entry():
    return entry(RelationshipExposeRequest, "expose", summary="Persist annotatable occurrence identities for native-owned parent, membership, relevance and status facts.")
