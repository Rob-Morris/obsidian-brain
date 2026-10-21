"""Dependency-previewed removal of a vault-local semantic definition."""

from dataclasses import dataclass, field
from typing import ClassVar
from _relationships.model import identifier
from .._decoding import reject_unexpected
from ._types import DefinitionKind, Disposition, GraphResult
from .batch import decode_disposition
from ._support import entry


@dataclass(frozen=True, slots=True)
class RelationshipUndefineRequest:
    COMMAND_ID: ClassVar[str] = "relationship.undefine"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = GraphResult
    definition_kind: DefinitionKind = field(metadata={"description": "Predicate or custom event-type catalogue."})
    identity: str = field(metadata={"description": "Registered local identity whose references must be explicitly dispositioned."})
    expected_revision: str = field(metadata={"description": "Observed exact definition document revision."})
    dispositions: tuple[Disposition, ...] = field(default=(), metadata={"description": "Exact revision-bound dependency closure to withdraw before removing this definition."})

    def __post_init__(self):
        identifier(self.identity)


def decode(payload):
    reject_unexpected(payload, {"definition_kind", "identity", "expected_revision", "dispositions"})
    return RelationshipUndefineRequest(DefinitionKind(payload["definition_kind"]), payload["identity"], payload["expected_revision"],
                                       tuple(decode_disposition(item) for item in payload.get("dispositions", ())))


def catalogue_entry():
    return entry(RelationshipUndefineRequest, "undefine", maintainer=True, summary="Remove a local definition only after explicit revision-bound dependency disposition, retaining pinned historic contracts.")
