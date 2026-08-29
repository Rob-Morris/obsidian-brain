"""Typed application mechanics for managed semantic retrieval mutations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ._mutation_support import no_effect_error
from .context import InvocationContext
from .receipts import CommittedEffect
from .results import CommandError, ErrorCode, Ok, Partial, RequestErrorDetails
from .types import (
    Authority,
    DependencyTier,
    EffectClass,
    Locality,
    RetryClass,
)


class SemanticMaintenanceStatus(str, Enum):
    NOOP = "noop"
    PLANNED = "planned"
    CHANGED = "changed"


@dataclass(frozen=True, slots=True)
class SemanticMaintenanceStep:
    name: str
    status: str
    message: str


@dataclass(frozen=True, slots=True)
class SemanticMaintenancePayload:
    operation: str
    status: SemanticMaintenanceStatus
    dry_run: bool
    steps: tuple[SemanticMaintenanceStep, ...]
    notes: tuple[str, ...]


def execute_enable(context: InvocationContext, request):
    from _lifecycle.semantic_enable import enable_semantic

    return _execute_lifecycle(
        context,
        request,
        operation="enable",
        invoke=lambda root: enable_semantic(
            root,
            provision=True,
            dry_run=context.dry_run,
        ),
    )


def execute_repair(context: InvocationContext, request):
    from _lifecycle.semantic_repairs import repair_semantic

    return _execute_lifecycle(
        context,
        request,
        operation="repair",
        invoke=lambda root: repair_semantic(root, dry_run=context.dry_run),
    )


def execute_rebuild(context: InvocationContext, request):
    from _bootstrap.runtime import step
    from _lifecycle.retrieval_assets import refresh_retrieval_assets

    def rebuild(root):
        if context.dry_run:
            return {
                "status": "planned",
                "dry_run": True,
                "steps": [
                    step(
                        "semantic_assets",
                        "planned",
                        "Would rebuild the compiled router, retrieval index, and "
                        "semantic embeddings sidecars.",
                    )
                ],
            }
        notes = refresh_retrieval_assets(root, force_embeddings=True)
        return {
            "status": "ok",
            "dry_run": False,
            "steps": [
                step(
                    "semantic_assets",
                    "changed",
                    "Rebuilt the compiled router, retrieval index, and semantic "
                    "embeddings sidecars.",
                )
            ],
            "notes": notes,
        }

    return _execute_lifecycle(
        context,
        request,
        operation="rebuild",
        invoke=rebuild,
    )


def _execute_lifecycle(context, request, *, operation: str, invoke):
    from _common import (
        MutationLockError,
        public_mutation_error_message,
        vault_mutation_lock,
    )

    root = context.selected_brain.vault_root
    try:
        with vault_mutation_lock(root):
            result = invoke(root)
    except MutationLockError as exc:
        return no_effect_error(
            type(request),
            ErrorCode.CONFLICT,
            public_mutation_error_message(exc),
            retryable=True,
        )

    raw_status = result.get("status")
    if raw_status == "error":
        message = _error_message(result)
        return no_effect_error(type(request), ErrorCode.CONFLICT, message)
    if raw_status == "partial":
        message = _error_message(result)
        return Partial(
            request.COMMAND_ID,
            request.COMMAND_VERSION,
            CommandError(
                ErrorCode.CONFLICT,
                message,
                RequestErrorDetails(None, message),
            ),
            _committed_effects(request.COMMAND_ID, result, fallback=True),
        )
    if raw_status not in {"ok", "noop", "planned"}:
        raise ValueError("semantic lifecycle owner returned an unsupported status")

    payload = SemanticMaintenancePayload(
        operation,
        {
            "ok": SemanticMaintenanceStatus.CHANGED,
            "noop": SemanticMaintenanceStatus.NOOP,
            "planned": SemanticMaintenanceStatus.PLANNED,
        }[raw_status],
        bool(result.get("dry_run", context.dry_run)),
        tuple(
            SemanticMaintenanceStep(item["name"], item["status"], item["message"])
            for item in result.get("steps") or ()
        ),
        tuple(result.get("notes") or ()),
    )
    effects = (
        _committed_effects(request.COMMAND_ID, result)
        if raw_status == "ok" and not payload.dry_run
        else ()
    )
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        payload,
        committed_effects=effects,
    )


def _committed_effects(command_id: str, result: dict, *, fallback: bool = False):
    subjects: list[str] = []
    changed = {
        item.get("name")
        for item in result.get("steps") or ()
        if item.get("status") == "changed"
    }
    if {"semantic_config", "semantic_runtime_marker"} & changed:
        subjects.append(".brain/local/config.yaml")
    if {"semantic_runtime", "semantic_model"} & changed:
        subjects.append("semantic-runtime")
    if "semantic_assets" in changed:
        subjects.extend(
            (
                ".brain/local/compiled-router.json",
                ".brain/local/retrieval-index.json",
                ".brain/local/type-embeddings.npy",
                ".brain/local/doc-embeddings.npy",
                ".brain/local/embeddings-meta.json",
            )
        )
    if fallback and not subjects:
        subjects.append("semantic-runtime")
    return tuple(
        CommittedEffect(command_id, subject)
        for subject in dict.fromkeys(subjects)
    )


def _error_message(result: dict) -> str:
    messages = [
        item.get("message", "")
        for item in result.get("steps") or ()
        if item.get("status") == "error"
    ]
    return (
        "; ".join(message for message in messages if message)
        or "Semantic maintenance did not complete."
    )


def catalogue_entry(
    request_type,
    executor,
    *,
    authority: Authority = Authority.OPERATOR,
):
    from .catalogue import ALL_APPLICATION_PROJECTIONS, ApplicationEntry

    return ApplicationEntry(
        request_type=request_type,
        executor=executor,
        dependency_tier=DependencyTier.MANAGED,
        locality=Locality.SELECTED_BRAIN_LOCAL,
        required_providers=("semantic_runtime",),
        optional_providers=(),
        authority=authority,
        effect_class=EffectClass.SELECTED_BRAIN_MUTATION,
        retry_class=RetryClass.RECEIPT_REQUIRED,
        projections=ALL_APPLICATION_PROJECTIONS,
    )
