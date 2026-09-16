"""Shared typed mechanics for destructive artefact transitions."""

from __future__ import annotations

from ._decoding import reject_unexpected

from dataclasses import dataclass, replace
from .workspace_context import WorkspaceMutationPayload
from .workspace_context import WorkspaceMutationPartial
from typing import Callable, Mapping

from ._mutation_support import mutation_entry, no_effect_error
from .types import Authority
from .context import InvocationContext
from .receipts import CommittedEffect
from .results import (
    CommandWarning,
    ErrorCode,
    Ok,
    WarningCode,
)


@dataclass(frozen=True, slots=True)
class PathChange:
    old_path: str
    new_path: str


@dataclass(frozen=True, slots=True)
class UninspectedArchiveCandidate:
    path: str
    reason: str


@dataclass(frozen=True, slots=True)
class ArtefactRenamePayload(WorkspaceMutationPayload):
    old_path: str
    new_path: str
    links_updated: int


@dataclass(frozen=True, slots=True)
class ArtefactConvertPayload(WorkspaceMutationPayload):
    old_path: str
    new_path: str
    type: str
    links_updated: int
    attachment_scope_moved: PathChange | None
    orphaned_attachment_scopes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ArtefactArchivePayload(WorkspaceMutationPayload):
    old_path: str
    new_path: str
    links_updated: int
    archived: tuple[PathChange, ...]


@dataclass(frozen=True, slots=True)
class ArtefactUnarchivePayload(WorkspaceMutationPayload):
    old_path: str
    new_path: str
    links_updated: int
    restored: tuple[PathChange, ...]
    uninspected: tuple[UninspectedArchiveCandidate, ...]


@dataclass(frozen=True, slots=True)
class ArtefactDeletePayload(WorkspaceMutationPayload):
    path: str
    deleted: tuple[str, ...]
    links_replaced: int
    orphaned_attachment_scopes: tuple[str, ...]


def validate_string(command_id: str, field: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{command_id} {field} must be a non-empty string")


def validate_recursive(command_id: str, recursive: bool) -> None:
    if not isinstance(recursive, bool):
        raise ValueError(f"{command_id} recursive must be a boolean")


def decode_required_strings(
    payload: Mapping[str, object],
    request_type,
    fields: tuple[str, ...],
):
    reject_unexpected(payload, set(fields))
    for field in fields:
        if field not in payload:
            raise ValueError(f"{field} is required")
        if not isinstance(payload[field], str):
            raise ValueError(f"{field} must be a string")
    return request_type(*(payload[field] for field in fields))


def decode_path_recursive(payload: Mapping[str, object], request_type):
    reject_unexpected(payload, {"path", "recursive"})
    if "path" not in payload:
        raise ValueError("path is required")
    path = payload["path"]
    recursive = payload.get("recursive", False)
    if not isinstance(path, str):
        raise ValueError("path must be a string")
    if not isinstance(recursive, bool):
        raise ValueError("recursive must be a boolean")
    return request_type(path, recursive)


def execute_transition(
    context: InvocationContext,
    request,
    *,
    payload_builder: Callable[[dict], object],
    effect_subject: Callable[[object], str | None],
    planner,
    apply_plan,
    effect_subjects=None,
):
    from ._transition_indexes import combine_transition_errors, transition_error
    from _common import (
        MutationLockError,
        ParentChainError,
        PartialApplyError,
        parent_chain_error_message,
        public_mutation_error_message,
        vault_mutation_lock,
    )
    from _lifecycle.derived_cache_state import require_fresh_compiled_router

    if context.dry_run:
        return no_effect_error(
            type(request),
            ErrorCode.INVALID_REQUEST,
            f"{request.COMMAND_ID} does not support dry-run",
        )
    vault_root = str(context.selected_brain.vault_root)
    from .workspace_transitions import (prepare_workspace_transition,
        transition_effect_snapshot, committed_transition_effects)

    try:
        with vault_mutation_lock(vault_root):
            router = require_fresh_compiled_router(vault_root)
            partial_error = None
            try:
                from .preparation import admit_owner
                from .preparation_transition import transition_binding

                frozen = context.admission.frozen_inputs if context.admission else None
                plan, _frozen = planner(context, request, router, frozen_inputs=frozen)
                plan, effective = prepare_workspace_transition(context, request, router, plan)
                admit_owner(context, request, transition_binding, plan=plan, router=router, effective=effective)
                effects_before = transition_effect_snapshot(context, plan)
                raw_result = apply_plan(vault_root, plan)
            except PartialApplyError as exc:
                partial_error = exc
            from ._transition_indexes import reconcile_transition_indexes

            try:
                if partial_error is not None or effect_subject(payload_builder(raw_result)) is not None:
                    reconcile_transition_indexes(context)
            except PartialApplyError as exc:
                effects = committed_transition_effects(context, request, plan, effects_before)
                if partial_error is not None:
                    raise combine_transition_errors(partial_error, exc) from exc
                raise
            if partial_error is not None:
                effects = committed_transition_effects(context, request, plan, effects_before)
                raise partial_error
    except MutationLockError as exc:
        return no_effect_error(
            type(request),
            ErrorCode.CONFLICT,
            public_mutation_error_message(exc),
            retryable=True,
        )
    except ParentChainError as exc:
        message = parent_chain_error_message(exc)
        return no_effect_error(type(request), ErrorCode.INVALID_REQUEST, message)
    except PartialApplyError as exc:
        if not effects:
            raise RuntimeError("Transition reported partial effects without an observable committed subject") from exc
        return WorkspaceMutationPartial(
            request.COMMAND_ID,
            request.COMMAND_VERSION,
            transition_error(exc),
            effects, mutation_context=effective,
        )
    except FileNotFoundError as exc:
        return no_effect_error(type(request), ErrorCode.NOT_FOUND, str(exc))
    except FileExistsError as exc:
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))
    except ValueError as exc:
        return no_effect_error(type(request), ErrorCode.INVALID_REQUEST, str(exc))

    payload = replace(payload_builder(raw_result), mutation_context=effective)
    warnings = _warnings(payload)
    subject = effect_subject(payload)
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        payload,
        committed_effects=(tuple(CommittedEffect(request.COMMAND_ID, item) for item in effect_subjects(payload))
            if effect_subjects is not None else (
            ()
            if subject is None
            else (CommittedEffect(request.COMMAND_ID, subject),)
        )),
        warnings=warnings,
    )


def path_changes(values) -> tuple[PathChange, ...]:
    return tuple(PathChange(item["old_path"], item["new_path"]) for item in values)


def catalogue_entry(
    request_type,
    executor,
    *,
    authority: Authority = Authority.CONTRIBUTOR,
):
    return mutation_entry(request_type, executor, authority)


def _warnings(payload) -> tuple[CommandWarning, ...]:
    messages = []
    orphaned = getattr(payload, "orphaned_attachment_scopes", ())
    if orphaned:
        messages.append(
            "Preserved orphaned attachment scope(s): " + ", ".join(orphaned)
        )
    uninspected = getattr(payload, "uninspected", ())
    if uninspected:
        messages.append(
            "Recursive restore could not inspect archived candidate(s): "
            + "; ".join(f"{item.path}: {item.reason}" for item in uninspected)
        )
    return tuple(
        CommandWarning(WarningCode.FOLLOW_UP_REQUIRED, message)
        for message in messages
    )
