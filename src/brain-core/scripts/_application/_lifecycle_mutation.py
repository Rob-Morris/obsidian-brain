"""Shared execution mechanics for explicit artefact lifecycle commands."""

from __future__ import annotations

from ._decoding import reject_unexpected

from dataclasses import dataclass
from typing import Mapping

from ._mutation_support import contributor_mutation_entry, no_effect_error
from .context import InvocationContext
from .receipts import CommittedEffect
from .results import ErrorCode, Ok


@dataclass(frozen=True, slots=True)
class ArtefactLifecyclePayload:
    path: str
    resolved_path: str
    field: str
    old_value: str | None
    new_value: str | None
    old_body_line_count: int
    new_body_line_count: int


def validate_path(command_id: str, path: str) -> None:
    if not isinstance(path, str) or not path.strip():
        raise ValueError(f"{command_id} path must be a non-empty string")


def validate_nullable_value(command_id: str, field: str, value: str | None) -> None:
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ValueError(
            f"{command_id} {field} must be null or a non-empty string"
        )


def validate_required_value(command_id: str, field: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{command_id} {field} must be a non-empty string")


def decode_lifecycle_request(
    payload: Mapping[str, object],
    request_type,
    *,
    value_field: str,
    nullable: bool,
):
    reject_unexpected(payload, {"path", value_field})
    if "path" not in payload:
        raise ValueError("path is required")
    if value_field not in payload:
        raise ValueError(f"{value_field} is required")
    path = payload["path"]
    value = payload[value_field]
    if not isinstance(path, str):
        raise ValueError("path must be a string")
    if nullable:
        if value is not None and not isinstance(value, str):
            raise ValueError(f"{value_field} must be a string or null")
    elif not isinstance(value, str):
        raise ValueError(f"{value_field} must be a string")
    return request_type(path, value)


def execute_lifecycle_mutation(
    context: InvocationContext,
    request,
    *,
    field: str,
    value: str | None,
):
    from _common import (
        MutationLockError,
        public_mutation_error_message,
        vault_mutation_lock,
    )
    from _lifecycle.derived_cache_state import load_fresh_compiled_router
    import edit

    if context.dry_run:
        return no_effect_error(
            type(request),
            ErrorCode.INVALID_REQUEST,
            f"{request.COMMAND_ID} does not support dry-run",
        )

    vault_root = str(context.selected_brain.vault_root)
    router = load_fresh_compiled_router(vault_root)
    if "error" in router:
        return no_effect_error(type(request), ErrorCode.CONFLICT, router["error"])

    try:
        with vault_mutation_lock(vault_root):
            from .preparation import admit_owner
            from .preparation_transition import transition_binding

            router = load_fresh_compiled_router(vault_root)
            if "error" in router:
                raise ValueError(router["error"])
            frozen = context.admission.frozen_inputs if context.admission else None
            plan, _frozen = plan_lifecycle_request(context, request, router, field=field,
                                                   value=value, frozen_inputs=frozen)
            admit_owner(context, request, transition_binding, plan=plan, router=router)
            result = edit.apply_artefact_transition(vault_root, plan)
    except MutationLockError as exc:
        return no_effect_error(
            type(request),
            ErrorCode.CONFLICT,
            public_mutation_error_message(exc),
            retryable=True,
        )
    except FileNotFoundError as exc:
        return no_effect_error(
            type(request), ErrorCode.NOT_FOUND, str(exc), "path"
        )
    except ValueError as exc:
        return no_effect_error(type(request), ErrorCode.INVALID_REQUEST, str(exc))

    payload = ArtefactLifecyclePayload(
        path=result["path"],
        resolved_path=result["resolved_path"],
        field=field,
        old_value=result.get("old_value"),
        new_value=result.get("new_value"),
        old_body_line_count=result["old_body_line_count"],
        new_body_line_count=result["new_body_line_count"],
    )
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        payload,
        committed_effects=(CommittedEffect(request.COMMAND_ID, payload.path),),
    )


def catalogue_entry(request_type, executor):
    return contributor_mutation_entry(request_type, executor)


def plan_lifecycle_request(context, request, router, *, field, value, frozen_inputs=None):
    """Use the explicit lifecycle owner's single invariant plan."""
    import edit
    from .preparation_transition import transition_time

    effective_at, frozen = transition_time(context, frozen_inputs)
    return edit.plan_lifecycle_field(str(context.selected_brain.vault_root), router,
                                     request.path, field, value,
                                     effective_at=effective_at), frozen
