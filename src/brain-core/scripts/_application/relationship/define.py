"""Register or refine one vault-local predicate or event contract."""

from dataclasses import dataclass, field
from typing import ClassVar

from _relationships.model import NodeKind, Scalar, identifier, nonempty
from .._decoding import reject_unexpected
from ._types import DefinitionKind, Cardinality, GraphResult, PayloadField
from ._support import entry


@dataclass(frozen=True, slots=True)
class RelationshipDefineRequest:
    COMMAND_ID: ClassVar[str] = "relationship.define"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = GraphResult
    MINIMAL_EXAMPLE: ClassVar[dict] = {"definition_kind": "predicate", "identity": "implements", "name": "Implements", "meaning": "Delivers the specified design"}

    definition_kind: DefinitionKind = field(metadata={"description": "Separate predicate or event-type catalogue."})
    identity: str = field(metadata={"description": "Stable vault-local identity; meaning changes require a new identity."})
    name: str = field(metadata={"description": "Human-readable definition name."})
    meaning: str = field(metadata={"description": "Clear semantic meaning; immutable after registration."})
    description: str = field(default="", metadata={"description": "Documentation-only clarification, excluded from semantic contract revision."})
    cardinality: Cardinality = field(default=Cardinality.MANY, metadata={"description": "Many independent assertions or one active assertion per subject/predicate/scope slot."})
    kinds: tuple[NodeKind, ...] = field(default=(), metadata={"description": "Optional allowed object node kinds for a predicate."})
    values: tuple[Scalar, ...] = field(default=(), metadata={"description": "Optional typed literal enumeration; booleans and numbers are distinct."})
    payload_fields: tuple[PayloadField, ...] = field(default=(), metadata={"description": "Required/optional scalar fields for an event type."})
    expected_revision: str | None = field(default=None, metadata={"description": "Exact stored definition revision, or null to require a new definition."})

    def __post_init__(self):
        identifier(self.identity)
        nonempty(self.name, "name")
        nonempty(self.meaning, "meaning")
        if not isinstance(self.definition_kind, DefinitionKind) or not isinstance(self.cardinality, Cardinality):
            raise ValueError("typed definition kind and cardinality are required")


def decode(payload):
    reject_unexpected(payload, {"definition_kind", "identity", "name", "meaning", "description", "cardinality", "kinds", "values", "payload_fields", "expected_revision"})
    return RelationshipDefineRequest(DefinitionKind(payload["definition_kind"]), payload["identity"], payload["name"], payload["meaning"],
        payload.get("description", ""), Cardinality(payload.get("cardinality", "many")),
        tuple(NodeKind(item) for item in payload.get("kinds", ())), tuple(payload.get("values", ())),
        tuple(PayloadField(**item) for item in payload.get("payload_fields", ())), payload.get("expected_revision"))


def catalogue_entry():
    return entry(RelationshipDefineRequest, "define", maintainer=True,
                 summary="Register or refine a vault-local predicate or event contract without rewriting existing assertions.")
