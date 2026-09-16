"""Typed selected-Brain repair result and execution mechanics."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, ClassVar

from ._mutation_support import contributor_mutation_entry, no_effect_error
from .context import InvocationContext
from .receipts import CommittedEffect
from .results import CommandError, ErrorCode, Ok, Partial, RequestErrorDetails


class RepairStatus(str, Enum):
    NOOP = "noop"
    PLANNED = "planned"
    CHANGED = "ok"


@dataclass(frozen=True, slots=True)
class RepairStep:
    name: str
    status: str
    message: str


@dataclass(frozen=True, slots=True)
class ArtefactRepairPayload:
    scope: str
    status: RepairStatus
    dry_run: bool
    steps: tuple[RepairStep, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ArtefactRepairRequest:
    """Inherited no-field contract for one explicit artefact repair scope."""

    COMMAND_ID: ClassVar[str] = ""
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = ArtefactRepairPayload


def execute_repair(
    context: InvocationContext,
    request,
    *,
    operation: Callable[[object, bool], dict],
):
    from _common import (
        MutationLockError,
        public_mutation_error_message,
        vault_mutation_lock,
    )

    root = context.selected_brain.vault_root
    try:
        with vault_mutation_lock(root):
            from .preparation import admit_owner

            frozen = context.admission.frozen_inputs if context.admission else None
            plan, _frozen = plan_repair_request(context, request, frozen_inputs=frozen)
            if not plan.error:
                admit_owner(context, request, repair_binding, plan=plan)
            result = operation(root, context.dry_run, prepared_plan=plan)
            if not context.dry_run and result.get("status") in {"ok", "partial"} and plan.scope != "empty_folders":
                from ._transition_indexes import reconcile_transition_indexes, TransitionIndexesIncomplete
                try:
                    reconcile_transition_indexes(context)
                except TransitionIndexesIncomplete as exc:
                    from dataclasses import replace
                    error = exc.error
                    if result.get("status") == "partial":
                        error = replace(error, message=_error_message(result) + " " + error.message)
                    return Partial(request.COMMAND_ID, request.COMMAND_VERSION, error,
                                   (CommittedEffect(request.COMMAND_ID, "vault"),))
    except MutationLockError as exc:
        return no_effect_error(
            type(request),
            ErrorCode.CONFLICT,
            public_mutation_error_message(exc),
            retryable=True,
        )

    status = result.get("status")
    if status == "error":
        message = _error_message(result)
        return no_effect_error(type(request), ErrorCode.CONFLICT, message)
    if status == "partial":
        message = _error_message(result)
        return Partial(
            request.COMMAND_ID,
            request.COMMAND_VERSION,
            CommandError(
                ErrorCode.CONFLICT,
                message,
                RequestErrorDetails(None, message),
            ),
            (CommittedEffect(request.COMMAND_ID, "vault"),),
        )
    if status not in {"ok", "noop", "planned"}:
        raise ValueError("repair owner returned an unsupported status")

    payload = ArtefactRepairPayload(
        scope=result["scope"],
        status=RepairStatus(status),
        dry_run=bool(result["dry_run"]),
        steps=tuple(
            RepairStep(item["name"], item["status"], item["message"])
            for item in result.get("steps") or ()
        ),
        notes=tuple(result.get("notes") or ()),
    )
    effects = (
        (CommittedEffect(request.COMMAND_ID, "vault"),)
        if status == "ok" and not payload.dry_run
        else ()
    )
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        payload,
        committed_effects=effects,
    )


def catalogue_entry(request_type, executor):
    from dataclasses import replace
    from .preparation import OperationPreparation

    return replace(contributor_mutation_entry(request_type, executor),
                   preparation=OperationPreparation(prepare_repair))


def plan_repair_request(context, request, *, frozen_inputs=None):
    import _repair_runtime
    from .preparation_transition import transition_time

    effective_at, frozen = transition_time(context, frozen_inputs)
    return _repair_runtime.plan_artefact_repair(context.selected_brain.vault_root,
                                               request.scope.value, effective_at=effective_at), frozen


def repair_binding(context, request, *, plan, frozen_inputs=None):
    from .preparation import ObservedResource, bind_operation, canonical_json, content_digest
    from .preparation_transition import transition_binding
    from _common import serialize_frontmatter

    if plan.error or plan.uninspected:
        raise ValueError(plan.error or "Cannot prepare a complete repair; candidates are unreadable")
    if plan.scope == "ownership":
        return transition_binding(context, request, plan=plan.movement, router=plan.router,
                                  frozen_inputs=frozen_inputs)
    root = context.selected_brain.vault_root
    observations, effects = [], []
    for item in plan.findings:
        if plan.scope == "frontmatter":
            path = item["file"]
            observations.append(ObservedResource("source", path, content_digest((root / path).read_bytes())))
            effects.append({"path": path, "replacement": content_digest(
                serialize_frontmatter(item["merged_fields"], body=item["body"]))})
        else:
            effects.append(item)
            for path in item["directories"]:
                stat = (root / path).lstat()
                observations.append(ObservedResource("directory", path, f"{stat.st_dev}:{stat.st_ino}"))
            for path in item["junk_files"]:
                observations.append(ObservedResource("junk-file", path, content_digest((root / path).read_bytes())))
    observations.append(ObservedResource("repair-workset", plan.scope,
                                        content_digest(canonical_json(effects))))
    return bind_operation(request, observations=observations, frozen_inputs=frozen_inputs,
                          review={"scope": plan.scope, "findings": len(plan.findings)})


def prepare_repair(context, request, *, frozen_inputs=None):
    from _common import vault_mutation_lock

    with vault_mutation_lock(context.selected_brain.vault_root):
        plan, frozen = plan_repair_request(context, request, frozen_inputs=frozen_inputs)
        return repair_binding(context, request, plan=plan, frozen_inputs=frozen)


def _error_message(result: dict) -> str:
    errors = [
        item.get("message", "")
        for item in result.get("steps") or ()
        if item.get("status") == "error"
    ]
    return "; ".join(item for item in errors if item) or "Repair did not complete."
