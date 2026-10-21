"""Inspect one assertion, occurrence or local definition."""

from dataclasses import dataclass, field
from enum import Enum
from functools import partial
from typing import ClassVar

from _relationships.model import identifier
from .._decoding import reject_unexpected
from ._types import GraphResult
from . import _reads


class RecordKind(str, Enum):
    ASSERTION = "assertion"
    OCCURRENCE = "occurrence"
    PREDICATE = "predicate"
    EVENT_TYPE = "event-type"


@dataclass(frozen=True, slots=True)
class RelationshipReadRequest:
    COMMAND_ID: ClassVar[str] = "relationship.read"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = GraphResult

    identity: str = field(metadata={"description": "Stable record identity, independent of storage path or endpoint triple."})
    record_kind: RecordKind = field(default=RecordKind.ASSERTION, metadata={"description": "Assertion, structural occurrence or definition catalogue to inspect."})

    def __post_init__(self):
        identifier(self.identity)
        if not isinstance(self.record_kind, RecordKind):
            raise ValueError("record_kind must be a RecordKind")


def decode(payload):
    reject_unexpected(payload, {"identity", "record_kind"})
    return RelationshipReadRequest(payload["identity"], RecordKind(payload.get("record_kind", "assertion")))


def catalogue_entry():
    return _reads.entry(RelationshipReadRequest, partial(_reads.read, kind="assertion"),
                        "Inspect a relationship record with its logical, document and slot revisions.")
