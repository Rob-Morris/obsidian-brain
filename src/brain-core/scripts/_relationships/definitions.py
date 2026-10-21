"""Small vault-owned semantic catalogue; built-ins use the same contracts."""

from .model import Conflict, digest, validate_definition


def _event(identity, fields):
    definition = {"kind": "event-type", "id": identity, "name": identity.replace("-", " ").title(),
                  "meaning": "Brain observed " + identity, "fields": [
                      {"name": name, "kind": "string", "required": required} for name, required in fields]}
    definition["contract_revision"] = validate_definition(definition)
    return definition


BUILTINS = {
    name: _event(name, fields) for name, fields in {
        "native-transition": [("command", True), ("before", True), ("after", True), ("reason", False)],
        "relationship-change": [("affected", True), ("dispositions", True), ("reason", False)],
        "definition-change": [("definition", True), ("before", True), ("after", True)],
        "correction": [("reason", True), ("replacement", True)],
    }.items()
}
DEFAULT_CAPTURE = ("native-transition", "relationship-change", "definition-change", "correction")


def definition(store, kind, identity):
    record = store.read(kind, identity)
    if record is None and kind == "event-type":
        record = BUILTINS.get(identity)
    if record is None:
        raise ValueError("unregistered " + kind + ": " + identity)
    expected = validate_definition(record)
    if record.get("contract_revision") != expected:
        raise ValueError("definition contract revision is invalid: " + identity)
    if kind == "event-type" and identity in BUILTINS and record["contract_revision"] != BUILTINS[identity]["contract_revision"]:
        raise Conflict("local/core event contract conflict: " + identity + "; retain the local data and migrate explicitly to a new identity")
    return record


def pinned_contract(record):
    return {"kind": "contract", "id": record["contract_revision"].removeprefix("sha256:"), "definition": record}


def capture_policy(store):
    record = store.read("policy", "capture")
    if record is None:
        return {"selected": list(DEFAULT_CAPTURE), "revision": digest(list(DEFAULT_CAPTURE)), "source": "built-in"}
    value = {"selected": record.get("selected")}
    if not isinstance(value["selected"], list):
        raise ValueError("capture policy requires a selected event-type list")
    if any(item not in DEFAULT_CAPTURE for item in value["selected"]) or len(set(value["selected"])) != len(value["selected"]):
        raise ValueError("capture selection contains unknown or repeated automatic event types")
    return {**value, "revision": digest(value), "source": "vault"}
