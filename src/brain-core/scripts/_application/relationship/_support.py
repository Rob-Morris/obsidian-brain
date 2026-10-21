"""Admission and result mechanics shared by semantic mutation owners."""

from dataclasses import replace
import uuid

from _relationships.model import Conflict, digest
from _relationships.service import Service
from _relationships.storage import Store
from .._mutation_support import contributor_mutation_entry, maintainer_mutation_entry, no_effect_error
from ..preparation import OperationPreparation, ObservedResource, bind_operation
from ..receipts import CommittedEffect
from ..results import ErrorCode, Ok, Partial, CommandError, RequestErrorDetails
from ._types import GraphResult


def plan(context, request, method, frozen_inputs=None):
    from _lifecycle.derived_cache_state import require_fresh_compiled_router
    frozen = dict(frozen_inputs or {})
    frozen.setdefault("operation", str(uuid.uuid4()))
    frozen.setdefault("recorded_at", context.clock.now().isoformat())
    root = context.selected_brain.vault_root
    service = Service(root, require_fresh_compiled_router(root), frozen["operation"], frozen["recorded_at"])
    prepared = getattr(service, method)(request)
    binding = bind_operation(request, frozen_inputs=frozen,
        observations=tuple(ObservedResource("semantic-input", key, value) for key, value in sorted(prepared.observations.items())),
        review={"operation": prepared.operation, "records": [{"kind": item["kind"], "id": item["id"]} for item in prepared.records],
                "events": [item["id"] for item in prepared.events], "effects": digest([prepared.records, prepared.events, prepared.removals])})
    return prepared, binding


def prepare(context, request, method, *, frozen_inputs=None):
    from _common import vault_mutation_lock
    with vault_mutation_lock(context.selected_brain.vault_root):
        return plan(context, request, method, frozen_inputs)[1]


def execute(context, request, method):
    from _common import vault_mutation_lock, MutationLockError
    prepared = None
    before = {}
    store = Store(context.selected_brain.vault_root)
    try:
        with vault_mutation_lock(store.root):
            prepared, binding = plan(context, request, method, context.admission.frozen_inputs)
            context.admission.admit(binding)
            before = {(item["kind"], item["id"]): store.revision(item["kind"], item["id"])
                      for item in [*prepared.records, *prepared.events]}
            if not context.dry_run and (prepared.records or prepared.events or prepared.removals):
                store.publish(prepared.operation, prepared.records, prepared.events, prepared.removals)
            generation, stale = store.generation()
    except (ValueError, OSError, MutationLockError) as exc:
        effects = []
        if prepared:
            for item in [*prepared.records, *prepared.events]:
                if store.revision(item["kind"], item["id"]) != before.get((item["kind"], item["id"])):
                    effects.append(CommittedEffect(request.COMMAND_ID, item["kind"] + ":" + item["id"]))
            if store.read("operation", prepared.operation) is not None:
                effects.append(CommittedEffect("semantic.recovery", prepared.operation))
        code = ErrorCode.CONFLICT if isinstance(exc, (Conflict, MutationLockError)) else ErrorCode.INVALID_REQUEST
        if effects:
            message = str(exc) + "; inspect invocation receipt, then history.repair operation " + prepared.operation
            return Partial(request.COMMAND_ID, request.COMMAND_VERSION,
                           CommandError(code, message, RequestErrorDetails(None, message)), tuple(effects))
        return no_effect_error(type(request), code, str(exc))
    records = prepared.result.get("items", [prepared.result])
    return Ok(request.COMMAND_ID, request.COMMAND_VERSION,
              GraphResult(tuple(records), operation=prepared.operation, generation=generation,
                          complete=not stale, source_freshness="preview" if context.dry_run else "admitted-inputs"),
              committed_effects=() if context.dry_run else tuple(
                  CommittedEffect(request.COMMAND_ID, item["kind"] + ":" + item["id"])
                  for item in [*prepared.records, *prepared.events]))


def entry(request_type, method, *, maintainer=False, summary):
    from functools import partial
    factory = maintainer_mutation_entry if maintainer else contributor_mutation_entry
    return replace(factory(request_type, partial(execute, method=method)), summary=summary,
                   preparation=OperationPreparation(partial(prepare, method=method)))
