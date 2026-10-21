"""Meaning, cardinality and definition contracts independent of persistence."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
import json
import math
import re


Scalar = str | int | float | bool | None
MAX_BATCH = 100
MAX_WORK = 5000
DEFAULT_SCOPE = "global"


class Conflict(ValueError):
    """An observed revision or dependency set no longer admits this operation."""


class BudgetExceeded(ValueError):
    """A bounded operation has not established a complete result."""


class Work:
    """One shared counter across source, traversal and validation visits."""

    def __init__(self, limit):
        self.limit, self.visited = limit, 0

    def take(self, count=1):
        if self.visited + count > self.limit:
            raise BudgetExceeded("work budget exhausted")
        self.visited += count


class NodeKind(str, Enum):
    ARTEFACT = "artefact"
    RELATIONSHIP = "relationship"
    OCCURRENCE = "occurrence"
    PREDICATE = "predicate"
    EVENT = "event"
    LITERAL = "literal"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    raw = value if isinstance(value, bytes) else canonical(value).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", value):
        raise ValueError("identity must be 1–128 letters, digits, dots, hyphens or underscores")
    return value


def nonempty(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(name + " must be a non-empty string")
    return value


@dataclass(frozen=True, slots=True)
class Endpoint:
    kind: NodeKind = field(metadata={"description": "Explicit node kind; literals never resolve as references."})
    reference: str = field(default="", metadata={"description": "Canonical artefact key, maintained temporal path or graph identity; empty for literals."})
    value: Scalar = field(default=None, metadata={"description": "JSON scalar payload for a literal; null for reference nodes."})

    def __post_init__(self):
        if not isinstance(self.kind, NodeKind):
            raise ValueError("endpoint kind must be a NodeKind")
        if not isinstance(self.reference, str):
            raise ValueError("endpoint reference must be a string")
        if self.kind is NodeKind.LITERAL:
            if self.reference or not (self.value is None or type(self.value) in (str, int, float, bool)):
                raise ValueError("literal endpoint requires a scalar value and no reference")
            if isinstance(self.value, float) and not math.isfinite(self.value):
                raise ValueError("literal numbers must be finite")
        elif self.value is not None:
            raise ValueError("reference endpoint cannot contain a literal value")
        else:
            nonempty(self.reference, "endpoint reference")


def endpoint(value):
    if not isinstance(value, dict) or "kind" not in value or set(value) - {"kind", "reference", "value", "identity"}:
        raise ValueError("malformed endpoint")
    if "identity" in value:
        nonempty(value["identity"], "endpoint identity")
        if value["kind"] != "artefact":
            raise ValueError("only artefact endpoints may carry a persisted identity")
    result = Endpoint(NodeKind(value["kind"]), value.get("reference", ""), value.get("value"))
    return asdict(result)


def node_key(node):
    if node["kind"] == "literal":
        return "literal:" + canonical(node.get("value"))
    return node["kind"] + ":" + node.get("identity", node["reference"])


def meaning(record):
    return {"subject": node_key(record["subject"]), "predicate": record["predicate"],
            "object": node_key(record["object"]), "scope": record.get("scope", DEFAULT_SCOPE)}


def assertion_revision(record):
    return digest(meaning(record))


def event_revision(record):
    """Retained event meaning excludes maintained endpoint locator spelling."""
    return digest({key: node_key(value) if key == "subject" else
                   {name: node_key(item) if isinstance(item, dict) and "kind" in item else item for name, item in value.items()} if key == "payload" else value
                   for key, value in record.items() if key not in ("schema", "revision")})


def slot_key(record):
    return digest([node_key(record["subject"]), record["predicate"], record.get("scope", DEFAULT_SCOPE)])


def definition_contract(definition):
    fields = ("cardinality", "kinds", "values") if definition["kind"] == "predicate" else ("fields",)
    return {key: definition[key] for key in ("id", "kind", "meaning", *fields)
            if key in definition}


def validate_definition(definition, previous=None):
    if not isinstance(definition, dict):
        raise ValueError("definition must be an object")
    identifier(definition.get("id"))
    if definition["id"].startswith("native."):
        raise ValueError("native.* is reserved for derived facts; use artefact.reparent, artefact.set-workspace or artefact.set-status")
    for key in ("name", "meaning"):
        nonempty(definition.get(key), key)
    if definition.get("kind") not in ("predicate", "event-type"):
        raise ValueError("definition kind must be predicate or event")
    if previous and previous["meaning"] != definition["meaning"]:
        raise ValueError("meaning changes require a new identity and explicit migration")
    if definition.get("cardinality", "many") not in ("one", "many"):
        raise ValueError("cardinality must be one or many")
    for key in ("kinds", "values", "fields"):
        if key in definition and not isinstance(definition[key], list):
            raise ValueError("definition " + key + " must be an array")
    for value in definition.get("values", ()):
        Endpoint(NodeKind.LITERAL, value=value)
    for kind in definition.get("kinds", ()):
        NodeKind(kind)
    fields = definition.get("fields", ())
    for item in fields:
        if not isinstance(item, dict) or set(item) != {"name", "kind", "required"}:
            raise ValueError("event field must contain name, kind and required")
        identifier(item["name"])
        if item["kind"] not in ("string", "number", "boolean", "reference") or type(item["required"]) is not bool:
            raise ValueError("invalid event field contract")
    if len({item["name"] for item in fields}) != len(fields):
        raise ValueError("event field names must be unique")
    return digest(definition_contract(definition))


def validate_assertion(record, definition):
    if record["predicate"].startswith("native."):
        raise ValueError("native facts are authoritative; use the native artefact owner, not authored assertions")
    if definition["kind"] != "predicate":
        raise ValueError("an assertion requires a registered predicate")
    nonempty(record.get("scope"), "scope")
    subject, obj = record["subject"], record["object"]
    if subject["kind"] == "literal":
        raise ValueError("assertion subjects must be reference nodes")
    if definition.get("kinds") and obj["kind"] not in definition["kinds"]:
        raise ValueError("object kind violates predicate constraint")
    if definition.get("values") and (obj["kind"] != "literal" or canonical(obj.get("value")) not in {canonical(value) for value in definition["values"]}):
        raise ValueError("object value violates predicate constraint")


def validate_event_payload(payload, definition):
    if definition["kind"] != "event-type":
        raise ValueError("history requires a registered event contract")
    fields = {item["name"]: item for item in definition.get("fields", ())}
    if set(payload) - set(fields):
        raise ValueError("event payload contains undeclared fields")
    for name, contract in fields.items():
        if name not in payload:
            if contract["required"]:
                raise ValueError("event payload requires " + name)
            continue
        value, kind = payload[name], contract["kind"]
        if kind == "reference":
            endpoint(value)
            if value["kind"] == "literal":
                raise ValueError("reference payload field requires a reference node")
        valid = ((kind == "string" and isinstance(value, str) and bool(value.strip()))
                 or (kind == "reference" and isinstance(value, dict))
                 or (kind == "number" and type(value) in (int, float))
                 or (kind == "boolean" and type(value) is bool))
        if not valid:
            raise ValueError("event payload field violates contract: " + name)


def dependency_closure(seeds, dependants, *, budget=MAX_WORK):
    visited, pending = set(), list(seeds)
    while pending:
        identity = pending.pop()
        if identity in visited:
            continue
        if len(visited) >= budget:
            raise ValueError("dependency work budget exhausted; narrow the operation")
        visited.add(identity)
        pending.extend(dependants(identity))
    return visited - set(seeds)


def correction_state(identity, successors, *, budget=100):
    chain, seen, current = [], set(), identity
    while True:
        if current in seen:
            return {"state": "unresolved", "reason": "correction cycle", "chain": chain}
        if len(seen) >= budget:
            return {"state": "incomplete", "reason": "correction work budget", "chain": chain}
        seen.add(current)
        following = successors(current)
        if following is None:
            return {"state": "unresolved", "reason": "unavailable correction", "chain": chain}
        if len(following) > 1:
            return {"state": "unresolved", "reason": "correction fork", "chain": chain}
        if not following:
            return {"state": "corrected" if chain else "current", "head": current, "chain": chain}
        current = following[0]
        chain.append(current)
