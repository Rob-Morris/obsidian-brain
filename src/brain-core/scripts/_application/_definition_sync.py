"""Typed mechanics shared by artefact-library install and sync owners."""

from __future__ import annotations

from .types import InitialAuthorisationClass

from dataclasses import dataclass

from ._mutation_support import maintainer_mutation_entry, no_effect_error
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
            from .preparation import admit_owner

            frozen = context.admission.frozen_inputs if context.admission else None
            plan, _frozen = plan_sync_request(context, request, frozen_inputs=frozen)
            customised = [item for item in plan.result["skipped"] if item["reason"] == "user_customised"]
            if customised and not force:
                return no_effect_error(type(request), ErrorCode.CONFLICT, "; ".join(
                    f"{item['role']} is locally customised; retry type.sync with force" for item in customised))
            admit_owner(context, request, sync_binding, plan=plan)
            result = sync_definitions.apply_definition_sync(root, plan)
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
    from dataclasses import replace
    from .preparation import OperationPreparation

    return replace(maintainer_mutation_entry(request_type, executor),
                   initial_class=InitialAuthorisationClass.EXCEPTIONAL, preparation=OperationPreparation(prepare_sync))


def plan_sync_request(context, request, *, frozen_inputs=None):
    """Freeze the installation timestamp and use the single sync classifier."""
    import sync_definitions
    from .preparation_transition import transition_time

    effective_at, frozen = transition_time(context, frozen_inputs)
    plan = sync_definitions.plan_sync_definitions(
        str(context.selected_brain.vault_root), dry_run=context.dry_run,
        force=request.force, types=[request.type_key], preference="ask", effective_at=effective_at)
    return plan, frozen


def sync_binding(context, request, *, plan, frozen_inputs=None):
    """Bind selected managed files and tracking entries, preserving other type updates."""
    import sync_definitions
    from .preparation import ObservedResource, bind_operation, canonical_json, content_digest

    root = context.selected_brain.vault_root
    observed = []
    for kind, paths in (("upstream", plan.sources), ("target", plan.targets)):
        for path in paths:
            target = root / path
            observed.append(ObservedResource(kind, path,
                                             content_digest(target.read_bytes()) if target.exists() else None))
            observed.append(ObservedResource(kind + "-identity", path,
                                             target.resolve().relative_to(root.resolve()).as_posix()))
    for path in plan.folders:
        observed.append(ObservedResource("folder", path, "directory" if (root / path).is_dir() else None))
    tracking = sync_definitions.load_tracking(str(root))
    for key in plan.type_keys:
        observed.append(ObservedResource("tracking", key,
                        content_digest(canonical_json(tracking["installed"].get(key)))))
    exclusion = sync_definitions.load_exclude_set(sync_definitions.load_preferences(str(root)))
    relevant_exclusions = sorted(item for item in exclusion
                                 if any(item.startswith(key + "/") for key in plan.type_keys))
    rendered = {"copies": {item.target: content_digest(item.content) for item in plan.copies},
                "tracking": {key: plan.tracking["installed"].get(key) for key in plan.type_keys}
                            if plan.tracking else None,
                "result": plan.result, "excluded": relevant_exclusions}
    observed.append(ObservedResource("sync-plan", request.type_key,
                                     content_digest(canonical_json(rendered))))
    return bind_operation(request, observations=observed, frozen_inputs=frozen_inputs,
                          review={"type": request.type_key, "writes": sorted(rendered["copies"]),
                                  "folders": list(plan.folders), "tracking": list(plan.type_keys)})


def prepare_sync(context, request, *, frozen_inputs=None):
    from _common import vault_mutation_lock
    import sync_definitions

    root = str(context.selected_brain.vault_root)
    with vault_mutation_lock(root):
        status = sync_definitions.status_definitions(root, types=[request.type_key])
        state, reason = _resolve_state(status, request.type_key)
        if state is None or state is TypeDefinitionState.NOT_INSTALLABLE:
            raise ValueError(reason or f"Unknown artefact-library type: {request.type_key}")
        plan, frozen = plan_sync_request(context, request, frozen_inputs=frozen_inputs)
        return sync_binding(context, request, plan=plan, frozen_inputs=frozen)
