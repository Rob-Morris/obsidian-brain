"""Typed mechanics shared by artefact-library install and sync owners."""

from __future__ import annotations

from dataclasses import dataclass

from ._mutation_support import no_effect_error, operator_mutation_entry
from .context import InvocationContext
from .receipts import CommittedEffect
from .results import (
    CommandError,
    CommandWarning,
    ErrorCode,
    Ok,
    Partial,
    RequestErrorDetails,
    WarningCode,
)
from .type.status import TypeDefinitionState


@dataclass(frozen=True, slots=True)
class TypeDefinitionChange:
    role: str
    target: str
    action: str


@dataclass(frozen=True, slots=True)
class TypeDefinitionSkip:
    role: str
    target: str
    reason: str


@dataclass(frozen=True, slots=True)
class TypeDefinitionIssue:
    role: str
    target: str | None
    action: str | None
    message: str
    upstream_sha256: str | None = None
    local_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class TypeDefinitionSyncPayload:
    type_key: str
    brain_core_version: str
    dry_run: bool
    forced: bool
    updated: tuple[TypeDefinitionChange, ...]
    skipped: tuple[TypeDefinitionSkip, ...]
    warnings: tuple[TypeDefinitionIssue, ...]
    errors: tuple[TypeDefinitionIssue, ...]


def validate_type_key(command_id: str, type_key: str) -> None:
    if not isinstance(type_key, str) or not type_key.strip():
        raise ValueError(f"{command_id} type_key must be a non-empty string")


def execute_definition_sync(
    context: InvocationContext,
    request,
    *,
    expected_state: TypeDefinitionState,
    force: bool,
):
    from _common import (
        MutationLockError,
        public_mutation_error_message,
        vault_mutation_lock,
    )
    import sync_definitions

    root = str(context.selected_brain.vault_root)
    try:
        with vault_mutation_lock(root):
            try:
                status = sync_definitions.status_definitions(
                    root, types=[request.type_key]
                )
                state, reason = _resolve_state(status, request.type_key)
            except (OSError, ValueError) as exc:
                return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))
            if state is None:
                return no_effect_error(
                    type(request),
                    ErrorCode.NOT_FOUND,
                    f"Unknown artefact-library type: {request.type_key}",
                    "type_key",
                )
            if state is TypeDefinitionState.NOT_INSTALLABLE:
                return no_effect_error(type(request), ErrorCode.CONFLICT, reason)
            if expected_state is TypeDefinitionState.UNINSTALLED:
                if state is not TypeDefinitionState.UNINSTALLED:
                    return no_effect_error(
                        type(request),
                        ErrorCode.CONFLICT,
                        f"{request.type_key} is {state.value}; use type.sync",
                        "type_key",
                    )
            elif state is TypeDefinitionState.UNINSTALLED:
                return no_effect_error(
                    type(request),
                    ErrorCode.CONFLICT,
                    f"{request.type_key} is uninstalled; use type.install",
                    "type_key",
                )
            result = sync_definitions.sync_definitions(
                root,
                dry_run=context.dry_run,
                force=force,
                types=[request.type_key],
                preference="ask",
            )
    except MutationLockError as exc:
        return no_effect_error(
            type(request),
            ErrorCode.CONFLICT,
            public_mutation_error_message(exc),
            retryable=True,
        )

    payload = _payload(request.type_key, force, result)
    changed = bool(payload.updated) or any(
        item.reason == "baseline_established" for item in payload.skipped
    )
    effects = (
        (CommittedEffect(request.COMMAND_ID, request.type_key),)
        if changed and not context.dry_run
        else ()
    )
    customised = tuple(
        item for item in payload.skipped if item.reason == "user_customised"
    )
    if customised and not force:
        message = "; ".join(
            f"{item.role} is locally customised; retry type.sync with force"
            for item in customised
        )
        return no_effect_error(type(request), ErrorCode.CONFLICT, message)
    issues = (*payload.errors, *payload.warnings)
    if issues:
        message = "; ".join(item.message for item in issues)
        warnings = tuple(
            CommandWarning(WarningCode.FOLLOW_UP_REQUIRED, item.message)
            for item in payload.warnings
        )
        if effects:
            return Partial(
                request.COMMAND_ID,
                request.COMMAND_VERSION,
                CommandError(
                    ErrorCode.CONFLICT,
                    message,
                    RequestErrorDetails(None, message),
                ),
                effects,
                warnings,
            )
        return no_effect_error(type(request), ErrorCode.CONFLICT, message)
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        payload,
        committed_effects=effects,
    )


def _resolve_state(status: dict, type_key: str):
    for state_name, entries in status["types"].items():
        if any(entry["type"] == type_key for entry in entries):
            return TypeDefinitionState(state_name), None
    for entry in status["not_installable"]:
        if entry["type"] == type_key:
            return TypeDefinitionState.NOT_INSTALLABLE, entry["reason"]
    return None, None


def _payload(type_key: str, force: bool, result: dict) -> TypeDefinitionSyncPayload:
    return TypeDefinitionSyncPayload(
        type_key=type_key,
        brain_core_version=result["brain_core_version"],
        dry_run=bool(result["dry_run"]),
        forced=force,
        updated=tuple(
            TypeDefinitionChange(item["role"], item["target"], item["action"])
            for item in result["updated"]
        ),
        skipped=tuple(
            TypeDefinitionSkip(item["role"], item["target"], item["reason"])
            for item in result["skipped"]
        ),
        warnings=tuple(
            TypeDefinitionIssue(
                item["role"],
                item.get("target"),
                item.get("action"),
                f"{item['role']} requires explicit force ({item['action']})",
                item.get("upstream_hash"),
                item.get("local_hash"),
            )
            for item in result["warnings"]
        ),
        errors=tuple(
            TypeDefinitionIssue(
                item["role"],
                item.get("target"),
                None,
                item["error"],
            )
            for item in result["errors"]
        ),
    )


def catalogue_entry(request_type, executor):
    return operator_mutation_entry(request_type, executor)
