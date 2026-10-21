"""Explicit bounded projection rebuild and identity-bound publication repair."""

from dataclasses import replace

from _relationships.storage import Store
from _relationships.model import Conflict, digest
from .._mutation_support import maintainer_mutation_entry, no_effect_error
from ..preparation import OperationPreparation, ObservedResource, bind_operation
from ..receipts import CommittedEffect
from ..results import ErrorCode, Ok, Partial, CommandError, RequestErrorDetails
from ._types import GraphResult


def prepare(context, request, *, frozen_inputs=None):
    store = Store(context.selected_brain.vault_root)
    observations = [ObservedResource("semantic-source-inventory", "shared", store.directory_stamp())]
    if hasattr(request, "operation"):
        journal = store.read("operation", request.operation)
        if journal is None:
            raise ValueError("unknown semantic recovery operation")
        observations.append(ObservedResource("semantic-operation", request.operation, store.revision("operation", request.operation)))
    return bind_operation(request, observations=tuple(observations), frozen_inputs=frozen_inputs,
                          review={"operation": getattr(request, "operation", "bounded-projection-refresh")})


def execute(context, request):
    from _common import vault_mutation_lock, MutationLockError
    store = Store(context.selected_brain.vault_root)
    effects = ()
    before = {}
    try:
        with vault_mutation_lock(store.root):
            context.admission.admit(prepare(context, request, frozen_inputs=context.admission.frozen_inputs))
            if hasattr(request, "operation"):
                journal = store.read("operation", request.operation)
                paths = [store.path(item["kind"], item["id"]) for item in (*journal.get("records", ()), *journal.get("events", ()), journal)]
                paths.append(store.index)
            else:
                paths = [store.index]
            before = {path: digest(path.read_bytes()) if path.exists() else None for path in paths}
            if context.dry_run:
                result = {"planned": True}
            elif hasattr(request, "operation"):
                result = (store.abandon(request.operation, request.reason) if request.abandon else
                          {"repaired_events": store.repair(request.operation), "operation": request.operation})
                effects = (CommittedEffect(request.COMMAND_ID, request.operation),)
            else:
                result = store.refresh(budget=request.work_budget, cursor=request.cursor)
                effects = (CommittedEffect(request.COMMAND_ID, "relationship-projection"),)
            generation, stale = store.generation()
    except (ValueError, OSError, MutationLockError) as exc:
        changed = [str(path.relative_to(store.root)) for path, previous in before.items()
                   if (digest(path.read_bytes()) if path.exists() else None) != previous]
        if changed:
            message = str(exc) + "; recovery/projection effects committed; inspect and retry the same maintenance operation"
            return Partial(request.COMMAND_ID, request.COMMAND_VERSION,
                           CommandError(ErrorCode.CONFLICT, message, RequestErrorDetails(None, message)),
                           tuple(CommittedEffect(request.COMMAND_ID, path) for path in changed))
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))
    return Ok(request.COMMAND_ID, request.COMMAND_VERSION,
              GraphResult((result,), complete=result.get("complete", True) and not stale, generation=generation), committed_effects=effects)


def entry(request_type, summary):
    return replace(maintainer_mutation_entry(request_type, execute), preparation=OperationPreparation(prepare), summary=summary)
