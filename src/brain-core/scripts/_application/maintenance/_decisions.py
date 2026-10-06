"""Shared mechanics of the decision commands (D11 to D13)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from _bootstrap.file_lock import MutationLockError
from _bootstrap.maintenance_decisions import (
    Decisions,
    Refusal,
    apply_decisions,
    locked_decisions,
    prune,
    write_decisions,
)
from _bootstrap.maintenance_findings import FindingGroup
from _bootstrap.maintenance_summary import brain_paths

from .._mutation_support import MAINTENANCE_MCP_EXCLUSION, maintainer_mutation_entry
from ..context import record_safely
from ..preparation import OperationPreparation, admit_owner, bind_operation
from ..receipts import CommittedEffect
from ..results import ErrorCode, Ok, request_error
from ..types import Projection
from ._items import DetectedState, MaintenanceItem, describe, last_outcome_for, load_state, never_held, read_decisions_or_error


DECISIONS_SUBJECT = ".brain/local/maintenance/decisions.json"


@dataclass(frozen=True, slots=True)
class DecisionPayload:
    kind: str
    item: MaintenanceItem


def decision_binding(context, request, *, frozen_inputs=None):
    """Bind the typed decision arguments; the executor re-checks detection under the lock."""
    del context
    return bind_operation(request, frozen_inputs=frozen_inputs, review={
        "operation": f"Record a maintenance {request.COMMAND_ID.split('.', 1)[1]} decision",
        "key": request.key,
    })


def catalogue_entry(request_type, executor, summary: str):
    from dataclasses import replace

    from ..catalogue import exclude_projection

    entry = replace(maintainer_mutation_entry(request_type, executor),
                    preparation=OperationPreparation(decision_binding), summary=summary)
    return exclude_projection(entry, Projection.MCP, MAINTENANCE_MCP_EXCLUSION)


def refused(request, refusal: Refusal):
    return request_error(type(request), ErrorCode(refusal.code), refusal.message, refusal.field)


def apply_decision(context, request, *, kind: str, decide):
    """Detect in process, then apply ``decide`` inside the locked, pruned write.

    ``decide(group, decisions, now) -> (Decisions, None) | (None, Refusal)``.
    The admission happens under the decisions lock, immediately before the
    write, so a prepared operation cannot enter after the file has changed.
    """
    paths = brain_paths(context.selected_brain.vault_root)
    state, error = load_state(type(request), context)
    if error is not None:
        return error
    group = state.group(request.key)
    if group is None:
        return request_error(type(request), ErrorCode.NOT_FOUND,
                             "No detected Brain-owned maintenance finding has that key; run maintenance list.",
                             "key")
    try:
        with locked_decisions(paths.decisions_lock):
            decisions, error = read_decisions_or_error(type(request), paths, context.correlation_id)
            if error is not None:
                return error
            now = context.clock.now()
            decisions = prune(decisions, now)
            updated, refusal = decide(group, decisions, now)
            if refusal is not None:
                return refused(request, refusal)
            admit_owner(context, request, decision_binding)
            if context.dry_run:
                return _ok(context, request, kind, group, state, decisions, now, committed=False)
            write_decisions(paths.decisions, updated)
    except MutationLockError as exc:
        return request_error(type(request), ErrorCode.CONFLICT, f"The decisions file is busy; retry. {exc}", retryable=True)
    record_safely(context, "maintenance.decision_recorded", family="maintenance", kind=kind, key=request.key)
    return _ok(context, request, kind, group, state, updated, now, committed=True)


def _ok(context, request, kind, group: FindingGroup, state: DetectedState, decisions: Decisions, now: datetime,
        *, committed: bool):
    decision = apply_decisions((group,), decisions, now, never_held=never_held())[group.key]
    item = describe(group, decision, vault_root=context.selected_brain.vault_root,
                    last_outcome=last_outcome_for(group, state.last_pass))
    effects = (CommittedEffect(request.COMMAND_ID, DECISIONS_SUBJECT),) if committed else ()
    return Ok(request.COMMAND_ID, request.COMMAND_VERSION, DecisionPayload(kind, item), effects, state.contract_warnings)
