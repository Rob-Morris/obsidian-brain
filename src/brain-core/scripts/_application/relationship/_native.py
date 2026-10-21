"""Compose semantic effects with native transition admission and completion."""

import uuid

from _relationships.model import digest
from _relationships.native import plan_transition
from _relationships.service import Service
from _relationships.storage import Store
from ..preparation import ObservedResource


def prepare(context, request, native_plan, router, frozen_inputs=None):
    frozen = dict(frozen_inputs or {})
    frozen.setdefault("relationship_operation", str(uuid.uuid5(uuid.NAMESPACE_URL,
        context.invocation_id + ":" + request.COMMAND_ID + ":" + digest(getattr(native_plan, "result", {})))))
    frozen.setdefault("relationship_recorded_at", context.clock.now().isoformat())
    service = Service(context.selected_brain.vault_root, router, frozen["relationship_operation"], frozen["relationship_recorded_at"])
    plan = plan_transition(service, request, native_plan)
    observations = tuple(ObservedResource("semantic-input", key, value) for key, value in sorted(plan.observations.items()))
    observations += (ObservedResource("semantic-effects", request.COMMAND_ID, digest([plan.records, plan.events, plan.result])),)
    return plan, observations, frozen


def begin(context, request, native_plan, router):
    plan, _, _ = prepare(context, request, native_plan, router,
                          context.admission.frozen_inputs if context.admission else None)
    if not plan.records and not plan.events:
        return None
    store = Store(context.selected_brain.vault_root)
    store.initialise()
    originals = {item["kind"] + ":" + item["id"]: store.revision(item["kind"], item["id"]) for item in (*plan.records, *plan.events)}
    native_before = {locator: digest((store.root / locator).read_bytes()) if (store.root / locator).exists() else None
                     for locator in plan.result["native_guards"]}
    store.write([{"kind": "operation", "id": plan.operation, "state": "native-pending",
                  "records": plan.records, "events": plan.events, "removals": [],
                  "native_guards": plan.result["native_guards"], "native_before": native_before, "originals": originals}])
    return plan


def complete(context, plan):
    if plan is None:
        return
    from _common import PartialApplyError
    store = Store(context.selected_brain.vault_root)
    try:
        store.publish(plan.operation, plan.records, plan.events)
    except (OSError, ValueError) as exc:
        raise PartialApplyError("Native state committed; semantic publication incomplete. "
                                "Inspect invocation, then history.repair operation " + plan.operation + ": " + str(exc)) from exc


def committed_effects(context, plan):
    """Report durable recovery metadata separately from native content effects."""
    from ..receipts import CommittedEffect
    if plan is None:
        return ()
    store = Store(context.selected_brain.vault_root)
    journal = store.read("operation", plan.operation)
    if journal is None:
        return ()
    effects = [CommittedEffect("semantic.recovery", plan.operation)]
    for item in (*plan.records, *plan.events):
        key = item["kind"] + ":" + item["id"]
        actual = store.revision(item["kind"], item["id"])
        if actual is not None and actual != journal.get("originals", {}).get(key):
            effects.append(CommittedEffect("semantic.publication", key))
    return tuple(effects)
