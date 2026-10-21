"""Bounded revision-aware current graph query."""

from dataclasses import dataclass, field
from functools import partial
from typing import ClassVar

from _relationships.model import Endpoint
from .._decoding import reject_unexpected
from ._types import GraphResult, GraphCursor, bounds
from .batch import decode_endpoint
from . import _reads


@dataclass(frozen=True, slots=True)
class RelationshipQueryRequest:
    COMMAND_ID: ClassVar[str] = "relationship.query"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = GraphResult

    subject: Endpoint | None = field(default=None, metadata={"description": "Optional typed subject filter."})
    object: Endpoint | None = field(default=None, metadata={"description": "Optional typed object filter."})
    predicate: str | None = field(default=None, metadata={"description": "Optional registered predicate identity filter."})
    scope: str | None = field(default=None, metadata={"description": "Optional explicit assertion slot scope filter."})
    workspace: str | None = field(default=None, metadata={"description": "Explicit workspace/key or global scope; omission includes visible workspaces."})
    limit: int = field(default=50, metadata={"description": "Maximum output records, from 1 to 100."})
    work_budget: int = field(default=1000, metadata={"description": "Maximum visited records across traversal, from 1 to 5000; independent of output limit."})
    depth: int = field(default=1, metadata={"description": "Maximum dependency traversal depth, from 1 to 10."})
    cursor: GraphCursor | None = field(default=None, metadata={"description": "Continuation tied to projection generation and unchanged selectors."})
    include_native: bool = field(default=True, metadata={"description": "Include native owner-backed facts. An artefact subject evaluates current native facts without writes; unmaterialised facts have no occurrence ID. Broad queries include materialised occurrences only and report unevaluated native scope. False excludes native facts."})

    def __post_init__(self):
        bounds(self.limit, self.work_budget, self.depth)
        if type(self.include_native) is not bool:
            raise ValueError("include_native must be boolean")


def decode(payload):
    reject_unexpected(payload, {"subject", "object", "predicate", "scope", "workspace", "limit", "work_budget", "depth", "cursor", "include_native"})
    values = dict(payload)
    for name in ("subject", "object"):
        if values.get(name) is not None:
            values[name] = decode_endpoint(values[name])
    if values.get("cursor") is not None:
        values["cursor"] = GraphCursor(**values["cursor"])
    return RelationshipQueryRequest(**values)


def catalogue_entry():
    return _reads.entry(RelationshipQueryRequest, partial(_reads.query, kind="assertion"),
                        "Query the current graph with finite traversal budgets and explicit projection freshness.")
