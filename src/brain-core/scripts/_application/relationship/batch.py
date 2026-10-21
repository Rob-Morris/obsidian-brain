"""One bounded assertion workflow with explicit revisions and dispositions."""

from dataclasses import dataclass, field
from typing import ClassVar

from _relationships.model import Endpoint, NodeKind, MAX_BATCH
from .._decoding import reject_unexpected
from ._types import Action, BatchItem, Disposition, GraphResult
from ._support import entry


def decode_endpoint(value):
    if not isinstance(value, dict):
        raise ValueError("endpoint must be an object")
    reject_unexpected(value, {"kind", "reference", "value"})
    return Endpoint(NodeKind(value["kind"]), value.get("reference", ""), value.get("value"))


def decode_disposition(value):
    reject_unexpected(value, {"identity", "expected_revision", "explanation"})
    return Disposition(value["identity"], value["expected_revision"],
                       decode_endpoint(value["explanation"]) if value.get("explanation") else None)


@dataclass(frozen=True, slots=True)
class RelationshipBatchRequest:
    COMMAND_ID: ClassVar[str] = "relationship.batch"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = GraphResult
    MINIMAL_EXAMPLE: ClassVar[dict] = {"items": [{"action": "create", "subject": {"kind": "artefact", "reference": "design/example"},
                                               "predicate": "confidence", "object": {"kind": "literal", "value": "high"}}]}

    items: tuple[BatchItem, ...] = field(metadata={"description": "One to 100 explicit assertion changes; all inputs validate before the first write and projection reconciles once."})

    def __post_init__(self):
        if not isinstance(self.items, tuple) or not 1 <= len(self.items) <= MAX_BATCH or any(not isinstance(item, BatchItem) for item in self.items):
            raise ValueError("batch requires 1–100 typed assertion changes")


def decode(payload):
    reject_unexpected(payload, {"items"})
    items = []
    for raw in payload["items"]:
        reject_unexpected(raw, {"action", "identity", "subject", "predicate", "object", "scope", "expected_revision", "expected_slot_revision", "dispositions"})
        items.append(BatchItem(Action(raw["action"]), raw.get("identity"),
            decode_endpoint(raw["subject"]) if raw.get("subject") else None, raw.get("predicate"),
            decode_endpoint(raw["object"]) if raw.get("object") else None, raw.get("scope", "global"),
            raw.get("expected_revision"), raw.get("expected_slot_revision", "empty"),
            tuple(decode_disposition(item) for item in raw.get("dispositions", ()))))
    return RelationshipBatchRequest(tuple(items))


def catalogue_entry():
    return entry(RelationshipBatchRequest, "batch", summary="Apply a bounded batch of relationship assertions with slot admission and explicit dependency dispositions.")
