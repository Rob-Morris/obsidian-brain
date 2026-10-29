"""Compose semantic effects with native transition admission and completion."""

import uuid

from _bootstrap import branch_guard
from _bootstrap.file_transaction import FileTransactionError

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
    with branch_guard.operation(context.selected_brain.vault_root) as guard:
        return _begin(context, request, native_plan, router, guard)


def _begin(context, request, native_plan, router, guard):
    plan, _, _ = prepare(context, request, native_plan, router,
                          context.admission.frozen_inputs if context.admission else None)
    if not plan.records and not plan.events:
        return None
    if context.admission is not None and context.admission.entered:
        frozen = dict(context.admission.frozen_inputs or {})
        if frozen.get("relationship_operation", plan.operation) != plan.operation:
            raise ValueError("native semantic operation differs from admitted context")
        frozen["relationship_operation"] = plan.operation
        context.admission.frozen_inputs = frozen
    store = Store(context.selected_brain.vault_root)
    store.initialise()
    originals = {item["kind"] + ":" + item["id"]: store.revision(item["kind"], item["id"]) for item in (*plan.records, *plan.events)}
    native_before = {locator: digest((store.root / locator).read_bytes()) if (store.root / locator).exists() else None
                     for locator in plan.result["native_guards"]}
    store.write([{"kind": "operation", "id": plan.operation, "state": "native-pending",
                  "records": plan.records, "events": plan.events, "removals": [],
                  "native_guards": plan.result["native_guards"], "native_before": native_before, "originals": originals,
                  "branch_guard": guard.evidence()}])
    return plan


def complete(context, plan):
    if plan is None:
        return
    from _common import PartialApplyError
    store = Store(context.selected_brain.vault_root)
    try:
        store.publish(plan.operation, plan.records, plan.events)
    except branch_guard.BranchGuardError as exc:
        from .._transition_indexes import BranchGuardPartial
        for path, revision in plan.result["native_guards"].items():
            exc.record_completed("delete" if revision is None else "write", path)
        exc.facts["journal_path"] = str(store.path("operation", plan.operation).relative_to(store.root))
        raise BranchGuardPartial(exc, "Native state committed; semantic publication incomplete. "
                                 "Inspect invocation, then history.repair operation " + plan.operation + ": " + str(exc)) from exc
    except (OSError, ValueError, FileTransactionError) as exc:
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


def retain_context_loss(context):
    """Retain loss for the admitted native journal without touching graph state.

    The application kernel calls this when BranchGuardError escapes an owner,
    before leaving that invocation's branch operation scope. Missing admission,
    missing operation identity or a non-pending journal requires no write.
    """
    admission = context.admission
    if admission is None or not admission.entered:
        return
    frozen = admission.frozen_inputs or {}
    operation = frozen.get("relationship_operation")
    if operation is None:
        return
    store = Store(context.selected_brain.vault_root)
    journal = store.read("operation", operation)
    if journal is None or journal["state"] != "native-pending":
        return
    with branch_guard.operation(store.root) as guard:
        store._retain_guard_loss(operation, guard.evidence())
