"""Discover separate vault-local semantic definition catalogues."""

from dataclasses import dataclass, field
from typing import ClassVar

from .._decoding import reject_unexpected
from ._types import DefinitionKind, GraphResult, GraphCursor, bounds
from . import _reads


@dataclass(frozen=True, slots=True)
class RelationshipDefinitionsRequest:
    COMMAND_ID: ClassVar[str] = "relationship.definitions"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = GraphResult

    definition_kind: DefinitionKind = field(default=DefinitionKind.PREDICATE, metadata={"description": "Predicate or event-type catalogue to inspect before registering new vocabulary."})
    work_budget: int = field(default=1000, metadata={"description": "Maximum definitions inspected, from 1 to 5000; exhaustion is explicit."})
    limit: int = field(default=50, metadata={"description": "Maximum definitions returned per page, from 1 to 100."})
    cursor: GraphCursor | None = field(default=None, metadata={"description": "Continuation bound to catalogue projection generation and selectors."})

    def __post_init__(self):
        bounds(self.limit, self.work_budget)


def decode(payload):
    reject_unexpected(payload, {"definition_kind", "work_budget", "limit", "cursor"})
    return RelationshipDefinitionsRequest(DefinitionKind(payload.get("definition_kind", "predicate")), payload.get("work_budget", 1000),
                                          payload.get("limit", 50), GraphCursor(**payload["cursor"]) if payload.get("cursor") else None)


def catalogue_entry():
    return _reads.entry(RelationshipDefinitionsRequest, _reads.definitions, "Discover registered vault-local predicates or event contracts before authoring metadata.")
