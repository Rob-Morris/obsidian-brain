"""Closed mutation shapes and bounded query controls for semantic commands."""

from dataclasses import dataclass, field
from enum import Enum

from _relationships.model import Endpoint, Scalar, identifier, MAX_BATCH, MAX_WORK, nonempty


class DefinitionKind(str, Enum):
    PREDICATE = "predicate"
    EVENT = "event-type"


class Cardinality(str, Enum):
    MANY = "many"
    ONE = "one"


class Action(str, Enum):
    CREATE = "create"
    REPLACE = "replace"
    REMOVE = "remove"


@dataclass(frozen=True, slots=True)
class PayloadField:
    name: str = field(metadata={"description": "Unique field name within this event contract."})
    kind: str = field(metadata={"description": "Payload scalar kind: string, number, boolean or reference."})
    required: bool = field(default=False, metadata={"description": "Whether every event using this contract must supply the field."})

    def __post_init__(self):
        identifier(self.name)
        if self.kind not in ("string", "number", "boolean", "reference") or type(self.required) is not bool:
            raise ValueError("invalid payload field declaration")


@dataclass(frozen=True, slots=True)
class PayloadValue:
    name: str = field(metadata={"description": "Registered payload field name."})
    value: Scalar | Endpoint = field(metadata={"description": "Explicit scalar or typed reference; validated against the pinned event contract."})

    def __post_init__(self):
        identifier(self.name)
        if not (isinstance(self.value, Endpoint) or self.value is None or type(self.value) in (str, int, float, bool)):
            raise ValueError("payload value must be a scalar or typed endpoint")


@dataclass(frozen=True, slots=True)
class Disposition:
    identity: str = field(metadata={"description": "Dependent relationship:<id> or event:<id> explicitly withdrawn by this operation."})
    expected_revision: str = field(metadata={"description": "Observed logical revision of the dependent record."})
    explanation: Endpoint | None = field(default=None, metadata={"description": "Optional report/decision reference preserving the supplied explanation before withdrawal."})

    def __post_init__(self):
        nonempty(self.identity, "disposition identity")
        kind, _, identity = self.identity.partition(":")
        if kind not in ("relationship", "event"):
            raise ValueError("disposition identity must be relationship:id or event:id")
        identifier(identity)
        nonempty(self.expected_revision, "disposition expected_revision")
        if self.explanation is not None and not isinstance(self.explanation, Endpoint):
            raise ValueError("disposition explanation must be a typed endpoint")


@dataclass(frozen=True, slots=True)
class BatchItem:
    action: Action = field(metadata={"description": "Create an independent assertion, replace its meaning with a new ID, or remove it."})
    identity: str | None = field(default=None, metadata={"description": "Existing assertion ID for replace/remove; optional new stable ID for create."})
    subject: Endpoint | None = field(default=None, metadata={"description": "Subject reference for create/replace; direct artefact metadata needs no intermediate edge."})
    predicate: str | None = field(default=None, metadata={"description": "Registered vault-local predicate identity for create/replace."})
    object: Endpoint | None = field(default=None, metadata={"description": "Object reference or explicitly tagged scalar literal."})
    scope: str = field(default="global", metadata={"description": "Explicit slot scope identity; global is the documented default."})
    expected_revision: str | None = field(default=None, metadata={"description": "Observed logical assertion revision required for replace/remove."})
    expected_slot_revision: str = field(default="empty", metadata={"description": "Observed single-value slot revision; empty admits only an unoccupied slot."})
    dispositions: tuple[Disposition, ...] = field(default=(), metadata={"description": "Exact bounded dependency closure to withdraw; omitted dependants refuse the operation."})

    def __post_init__(self):
        if not isinstance(self.action, Action):
            raise ValueError("batch action must be an Action")
        if self.identity is not None:
            identifier(self.identity)
        if self.action is not Action.CREATE and (not self.identity or not self.expected_revision):
            raise ValueError("replace/remove requires identity and expected_revision")
        if self.action is not Action.REMOVE and (not isinstance(self.subject, Endpoint) or not isinstance(self.object, Endpoint) or not self.predicate):
            raise ValueError("create/replace requires subject, registered predicate and object")
        nonempty(self.scope, "scope")
        if len(self.dispositions) > MAX_BATCH:
            raise ValueError("too many dispositions")


@dataclass(frozen=True, slots=True)
class GraphCursor:
    generation: str = field(metadata={"description": "Projection generation returned by the preceding page."})
    offset: int = field(metadata={"description": "Bounded offset within this exact generation and selector set."})
    selectors: str = field(metadata={"description": "Digest binding the original query selectors and budgets."})
    traversal: str | None = field(default=None, metadata={"description": "Opaque bounded traversal stack for depth queries; preserve exactly when continuing. Null for flat pages."})

    def __post_init__(self):
        if type(self.offset) is not int or self.offset < 0:
            raise ValueError("cursor offset must be a non-negative integer")
        nonempty(self.generation, "cursor generation")
        nonempty(self.selectors, "cursor selectors")
        if self.traversal is not None and (not isinstance(self.traversal, str) or len(self.traversal) > 8192):
            raise ValueError("traversal cursor must be a bounded opaque string")


@dataclass(frozen=True, slots=True)
class GraphResult:
    records: tuple[dict[str, object], ...] = ()
    findings: tuple[dict[str, object], ...] = ()
    operation: str | None = None
    generation: str | None = None
    complete: bool = True
    source_freshness: str = "admitted-inputs"
    visited: int = 0
    next_cursor: GraphCursor | None = None
    continuation: str | None = None


def bounds(limit, budget, depth=1):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be 1–100")
    if type(budget) is not int or not 1 <= budget <= MAX_WORK:
        raise ValueError("work_budget must be 1–5000")
    if type(depth) is not int or not 1 <= depth <= 10:
        raise ValueError("depth must be 1–10")
