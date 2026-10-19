"""Bounded semantic mutation planning and interpreted reads."""

from dataclasses import asdict, dataclass, field
import uuid

from .definitions import capture_policy, definition, pinned_contract
from .endpoints import Endpoints
from .model import (Conflict, MAX_WORK, assertion_revision, canonical, correction_state,
                    dependency_closure, digest, event_revision, node_key, slot_key,
                    validate_assertion, validate_definition, validate_event_payload)
from .storage import Store


@dataclass
class Plan:
    operation: str
    records: list = field(default_factory=list)
    events: list = field(default_factory=list)
    removals: list = field(default_factory=list)
    observations: dict = field(default_factory=dict)
    result: dict = field(default_factory=dict)


class Service:
    def __init__(self, root, router, operation, recorded_at):
        self.store = Store(root)
        _, stale = self.store.generation()
        if stale:
            raise Conflict("relationship projection is stale or incomplete; finish relationship.refresh before mutation")
        self.endpoints = Endpoints(self.store, router)
        self.plan = Plan(operation)
        self.recorded_at = recorded_at
        self.policy = capture_policy(self.store)
        self._fresh_inventory = False
        self.plan.observations["capture-policy"] = self.policy["revision"]

    def expose(self, request):
        from .native import expose
        return expose(self, request)

    def configure(self, request):
        from .definitions import DEFAULT_CAPTURE
        if self.policy["revision"] != request.expected_revision:
            raise Conflict("capture policy revision changed")
        if len(set(request.selected)) != len(request.selected) or set(request.selected) - set(DEFAULT_CAPTURE):
            raise ValueError("unknown or repeated automatic event type")
        record = {"kind": "policy", "id": "capture", "selected": list(request.selected)}
        self.plan.records.append(record)
        self.plan.result = {"policy": record, "coverage": "supported Brain commands only", "automatic_pruning": False}
        return self._finish()

    def undefine(self, request):
        from .definitions import BUILTINS
        kind = request.definition_kind.value
        if kind == "event-type" and request.identity in BUILTINS:
            raise ValueError("built-in event contracts cannot be removed; configure capture selection explicitly")
        record = definition(self.store, kind, request.identity)
        if self.store.revision(kind, request.identity) != request.expected_revision:
            raise Conflict("definition revision changed")
        self.plan.observations[kind + ":" + request.identity] = request.expected_revision
        if kind == "predicate":
            self._dispositions("predicate", request.identity, request.dispositions)
        else:
            rows = self.store.rows(kind="event", predicate=request.identity, retired=0)
            closure = {"event:" + row["id"] for row in rows}
            for row in rows:
                closure.update(self._closure("event", row["id"]))
            self._apply_dispositions(closure, request.dispositions)
        self.plan.removals.append((kind, request.identity))
        self.plan.result = {"removed_definition": request.identity, "retained_contract": record["contract_revision"]}
        return self._finish()

    def _event(self, event_type, subject, payload, *, provenance="observed", identity=None, **extra):
        contract = definition(self.store, "event-type", event_type)
        payload = {name: self.endpoints.resolve(value) if isinstance(value, dict) else value for name, value in payload.items()}
        validate_event_payload(payload, contract)
        self.plan.observations["event-type:" + event_type] = contract["contract_revision"]
        self.plan.records.append(pinned_contract(contract))
        event = {"kind": "event", "id": identity or str(uuid.uuid5(uuid.NAMESPACE_URL, self.plan.operation + ":" + str(len(self.plan.events)))),
                 "type": event_type, "contract_revision": contract["contract_revision"],
                 "subject": subject, "payload": payload, "provenance": provenance,
                 "operation": self.plan.operation, "recorded_at": self.recorded_at, **extra}
        event["workspace"] = self.endpoints.workspace(subject)
        event["revision"] = event_revision(event)
        self.plan.events.append(event)
        return event

    def _selected(self, event_type, subject, payload):
        if event_type in self.policy["selected"]:
            return self._event(event_type, subject, payload)

    def _finish(self):
        self.plan.records.extend(self.endpoints.bindings.values())
        self.plan.observations.update(self.endpoints.observations)
        # Contracts and endpoint bindings can be shared across a batch.
        unique = {(item["kind"], item["id"]): item for item in self.plan.records}
        self.plan.records = list(unique.values())
        # Forward metadata references inherit their subject's attribution in the
        # same batch. Cycles without an artefact anchor require explicit scope.
        def workspace(record, seen):
            key = (record["kind"], record["id"])
            if key in seen:
                record["workspace_resolution"] = "unanchored-cycle-global-default"
                return "global"
            subject = record["subject"]
            target_kind = {"relationship": "assertion", "occurrence": "occurrence", "event": "event"}.get(subject["kind"])
            target = unique.get((target_kind, subject["reference"])) if target_kind else None
            if target and target.get("subject"):
                return workspace(target, seen | {key})
            return self.endpoints.workspace(subject)
        for record in self.plan.records:
            if record["kind"] in ("assertion", "occurrence") and not record.get("retired"):
                record["workspace"] = workspace(record, set())
        retired = {("relationship" if item["kind"] == "assertion" else item["kind"]) + ":" + item["id"]
                   for item in self.plan.records if item.get("retired")}
        for record in self.plan.records:
            if record["kind"] == "assertion" and not record.get("retired"):
                if any(node_key(record[key]) in retired for key in ("subject", "object")):
                    raise ValueError("batch would create a reference to a retired entity")
        self.plan.records = [record for record in self.plan.records
                             if self.store.read(record["kind"], record["id"]) != {**record, "schema": "brain.relationship-record/1"}]
        return self.plan

    def define(self, request):
        from .definitions import BUILTINS
        kind = request.definition_kind.value
        previous = self.store.read(kind, request.identity)
        expected = self.store.revision(kind, request.identity)
        if expected != request.expected_revision:
            raise Conflict("definition revision changed")
        self.plan.observations[kind + ":" + request.identity] = expected
        record = {"kind": kind, "id": request.identity, "name": request.name, "meaning": request.meaning,
                  "description": request.description, "cardinality": request.cardinality.value,
                  "kinds": [item.value for item in request.kinds], "values": list(request.values),
                  "fields": [asdict(item) for item in request.payload_fields]}
        record["contract_revision"] = validate_definition(record, previous)
        if kind == "event-type" and request.identity in BUILTINS:
            builtin = BUILTINS[request.identity]
            if record["contract_revision"] != builtin["contract_revision"]:
                raise Conflict("built-in event contract collision; preserve the local definition and register the custom meaning under a new identity")
            record["core_contract_revision"] = builtin["contract_revision"]
        if previous and record == {key: value for key, value in previous.items() if key != "schema"}:
            self.plan.result = {"changed": False, "definition": previous}
            return self._finish()
        self.plan.records.extend([record, pinned_contract(record)])
        if previous and previous["contract_revision"] != record["contract_revision"]:
            self._selected("definition-change", {"kind": "predicate", "reference": record["id"], "value": None},
                           {"definition": record["id"], "before": previous["contract_revision"], "after": record["contract_revision"]})
        violations = []
        if kind == "predicate":
            rows = self.store.rows(kind="assertion", predicate=record["id"], retired=0)
            if len(rows) > MAX_WORK:
                raise ValueError("definition impact exceeds work budget; narrow data before changing constraints")
            slots = {}
            for row in rows:
                current = self.store.checked(row)
                self.plan.observations["assertion:" + row["id"]] = row["revision"]
                try:
                    validate_assertion(current, record)
                except ValueError as exc:
                    violations.append({"assertion": row["id"], "reason": str(exc)})
                slots.setdefault(slot_key(current), []).append(row["id"])
            if record.get("cardinality") == "one":
                violations.extend({"slot": slot, "assertions": ids, "reason": "single-value constraint"}
                                  for slot, ids in slots.items() if len(ids) > 1)
        self.plan.result = {"changed": True, "definition": record, "violations": violations}
        return self._finish()

    def _closure(self, kind, identity):
        self._require_inventory()
        def adjacent(key):
            rows = self.store.dependants(key)
            if len(rows) > MAX_WORK:
                raise ValueError("dependency work budget exhausted")
            result = []
            for row in rows:
                if row["retired"] or row["kind"] not in ("assertion", "event"):
                    continue
                if row["kind"] == "event" and not key.startswith("event:"):
                    # A historic account is not a current semantic obligation.
                    # Its subject may be retired; the retained account remains
                    # visibly historical and cannot confer current standing.
                    continue
                self.store.checked(row)
                self.plan.observations[row["kind"] + ":" + row["id"]] = row["revision"]
                prefix = "relationship" if row["kind"] == "assertion" else row["kind"]
                result.append(prefix + ":" + row["id"])
            self.plan.observations["dependants:" + key] = digest(sorted(result))
            return result
        prefix = "relationship" if kind == "assertion" else kind
        return dependency_closure([prefix + ":" + identity], adjacent)

    def _require_inventory(self):
        if not self._fresh_inventory:
            generation, stale = self.store.generation()
            if stale:
                raise Conflict("dependency projection is stale or incomplete; finish relationship.refresh")
            self.plan.observations["projection-generation"] = generation
            self._fresh_inventory = True

    def _dispositions(self, kind, identity, dispositions):
        closure = self._closure(kind, identity)
        return self._apply_dispositions(closure, dispositions)

    def _apply_dispositions(self, closure, dispositions):
        supplied = {item.identity: item for item in dispositions}
        if set(supplied) != closure:
            raise Conflict("explicit disposition required for exact dependency closure: " + canonical(sorted(closure)))
        for key, item in supplied.items():
            prefix, target = key.split(":", 1)
            target_kind = "assertion" if prefix == "relationship" else prefix
            current = self.store.read(target_kind, target)
            if item.expected_revision != current.get("revision"):
                raise Conflict("dependant revision changed: " + key)
            if item.explanation:
                explanation = self.endpoints.resolve(asdict(item.explanation))
            else:
                explanation = None
            self.plan.records.append({"kind": target_kind, "id": target, "retired": True,
                                      "revision": digest([current["revision"], "retired"]),
                                      "operation": self.plan.operation, "explanation": explanation})
        return sorted(closure)

    def batch(self, request):
        outcomes, staged_slots = [], {}
        identities = [item.identity or str(uuid.uuid5(uuid.NAMESPACE_URL, self.plan.operation + ":assertion:" + str(index)))
                      for index, item in enumerate(request.items)]
        if len(set(identities)) != len(identities):
            raise ValueError("one batch cannot mutate the same identity twice")
        self.endpoints.pending.update("relationship:" + identity for identity, item in zip(identities, request.items)
                                      if item.action.value == "create")
        for index, item in enumerate(request.items):
            identity = identities[index]
            current = self.store.read("assertion", identity)
            self.plan.observations["assertion:" + identity] = self.store.revision("assertion", identity)
            if item.action.value == "create" and current is not None:
                raise Conflict("assertion identity already exists")
            if item.action.value != "create" and (not current or current.get("retired") or current["revision"] != item.expected_revision):
                raise Conflict("assertion revision changed or unavailable")
            if item.action.value == "remove":
                closure = self._dispositions("assertion", identity, item.dispositions)
                self.plan.records.append({"kind": "assertion", "id": identity, "retired": True,
                                          "revision": digest([current["revision"], "retired"]), "operation": self.plan.operation})
                if closure:
                    self._selected("relationship-change", {"kind": "relationship", "reference": identity, "value": None},
                                   {"affected": canonical([identity]), "dispositions": canonical(closure)})
                outcomes.append({"id": identity, "state": "removed"})
                continue
            subject = self.endpoints.resolve(asdict(item.subject))
            obj = self.endpoints.resolve(asdict(item.object))
            predicate = definition(self.store, "predicate", item.predicate)
            self.plan.observations["predicate:" + item.predicate] = predicate["contract_revision"]
            record = {"kind": "assertion", "id": identity, "subject": subject, "predicate": item.predicate,
                      "object": obj, "scope": item.scope, "workspace": self.endpoints.workspace(subject),
                      "operation": self.plan.operation, "retired": False}
            validate_assertion(record, predicate)
            record["revision"] = assertion_revision(record)
            slot = slot_key(record)
            if predicate.get("cardinality") == "one":
                self._require_inventory()
                rows = self.store.rows(kind="assertion", slot=slot, retired=0)
                values = [(row["id"], self.store.checked(row)["revision"]) for row in rows]
                slot_revision = digest(sorted(values)) if values else "empty"
                if slot_revision != item.expected_slot_revision or slot in staged_slots:
                    raise Conflict("single-value slot revision changed")
                if values and (item.action.value != "replace" or values[0][0] != identity):
                    raise Conflict("single-value slot is occupied; replace its assertion explicitly")
                self.plan.observations["slot:" + slot] = slot_revision
                staged_slots[slot] = identity
            if current and current["revision"] == record["revision"]:
                outcomes.append({"id": identity, "state": "unchanged", "revision": current["revision"]})
                continue
            if current:
                closure = self._dispositions("assertion", identity, item.dispositions)
                new_id = str(uuid.uuid5(uuid.NAMESPACE_URL, self.plan.operation + ":replacement:" + identity))
                record["id"] = new_id
                self.plan.records.append({"kind": "assertion", "id": identity, "retired": True,
                                          "revision": digest([current["revision"], "retired"]), "operation": self.plan.operation})
                if closure:
                    self._selected("relationship-change", {"kind": "relationship", "reference": identity, "value": None},
                                   {"affected": canonical([identity, new_id]), "dispositions": canonical(closure)})
            self.plan.records.append(record)
            outcomes.append({"id": record["id"], "state": "created" if not current else "replaced", "revision": record["revision"], "slot": slot})
        self.plan.result = {"items": outcomes}
        return self._finish()

    def write_event(self, request):
        subject = self.endpoints.resolve(asdict(request.subject))
        from .model import Endpoint
        payload = {item.name: asdict(item.value) if isinstance(item.value, Endpoint) else item.value for item in request.payload}
        event = self._event(request.event_type, subject, payload, provenance="caller",
                            asserted_at=request.asserted_at, attribution=request.attribution)
        self.plan.result = {"event": event}
        return self._finish()

    def correct(self, request):
        original = self.store.read("event", request.identity)
        if not original or original.get("retired"):
            raise ValueError("event is unavailable")
        state = interpreted_event(self.store, request.identity)
        if state["interpretation"]["state"] not in ("current", "corrected"):
            raise Conflict("correction interpretation is unresolved")
        head = state["interpretation"]["head"]
        current = self.store.read("event", head)
        if current["revision"] != request.expected_head_revision:
            raise Conflict("correction head revision changed")
        self.plan.observations["correction-head:" + request.identity] = digest([head, current["revision"]])
        event = self._event("correction", original["subject"], {"reason": request.reason, "replacement": request.replacement},
                            provenance="caller", corrects=head)
        self.plan.result = {"event": event, "supersedes": head}
        return self._finish()

    def cleanup(self, request):
        targets = set(request.identities)
        for identity in sorted(targets):
            record = self.store.read("event", identity)
            if not record:
                raise ValueError("unknown history event: " + identity)
            self.plan.observations["event:" + identity] = self.store.revision("event", identity)
            publications = self.store.dependants("event:" + identity)
            if len(publications) > MAX_WORK:
                raise Conflict("cleanup dependency budget exhausted; inspect pending publications first")
            for row in publications:
                if row["kind"] == "operation":
                    journal = self.store.checked(row)
                    if journal["state"] not in ("complete", "abandoned"):
                        raise Conflict("event belongs to unresolved publication; complete history.repair operation " + journal["id"] + " before cleanup")
            closure = self._closure("event", identity)
            outside = closure - {"event:" + key for key in targets}
            if outside:
                raise Conflict("history cleanup requires dependency disposition: " + canonical(sorted(outside)))
            # A tombstone deliberately leaves a removed successor unresolved.
            self.plan.records.append({"kind": "event", "id": identity, "retired": True,
                                      "corrects": record.get("corrects"), "revision": digest([identity, "deleted"]),
                                      "operation": self.plan.operation})
        self.plan.result = {"cleanup": sorted(targets), "interpretation": "removed corrections remain unresolved"}
        return self._finish()


class HistoryView:
    """One bounded inventory and read cache shared by a history page."""

    def __init__(self, store, budget):
        from .model import Work
        self.store, self.work, self.cache = store, Work(budget), {}
        _, stale = store.generation()
        self.fresh = not stale
        self.reason = "source inventory changed; refresh required" if stale else ""

    def record(self, identity):
        if identity not in self.cache:
            self.work.take()
            rows = self.store.rows(kind="event", id=identity, budget=1)
            self.cache[identity] = self.store.checked(rows[0]) if rows else self.store.read("event", identity)
        return self.cache[identity]

    def read(self, identity):
        from .model import BudgetExceeded
        record = self.record(identity)
        if record is None or record.get("retired"):
            raise ValueError("history event is unavailable")
        def successors(key):
            self.work.take()
            current = self.record(key)
            if current is None or current.get("retired"):
                return None
            rows = self.store.rows(kind="event", corrects=key, budget=self.work.limit - self.work.visited)
            self.work.take(len(rows))
            return [self.record(row["id"])["id"] for row in rows]
        try:
            interpretation = correction_state(identity, successors, budget=self.work.limit)
        except BudgetExceeded:
            interpretation = {"state": "incomplete", "reason": "history work budget exhausted", "chain": []}
        if not self.fresh:
            interpretation = {"state": "unresolved", "reason": self.reason, "chain": []}
        contract = self.store.read("contract", record["contract_revision"].removeprefix("sha256:"))
        historical, present = "valid", "valid"
        try:
            if not contract:
                raise ValueError("pinned contract is unavailable")
            validate_event_payload(record["payload"], contract["definition"])
        except ValueError as exc:
            historical = str(exc)
        try:
            validate_event_payload(record["payload"], definition(self.store, "event-type", record["type"]))
        except ValueError as exc:
            present = str(exc)
        return {**record, "interpretation": interpretation, "historical_validation": historical,
                "present_conformance": present, "attribution_is_authenticated": False,
                "correction_freshness": "observed-projection-and-visited-source-revisions"}


def interpreted_event(store, identity, *, budget=100):
    return HistoryView(store, budget).read(identity)
