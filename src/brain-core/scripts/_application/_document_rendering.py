"""Shared application mechanics for provider-backed document rendering."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

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


def execute_render(
    context: InvocationContext,
    request,
    *,
    operation: str,
    invoke,
    build_payload,
):
    from _common import (
        MutationLockError,
        public_mutation_error_message,
        resolve_and_check_bounds,
        vault_mutation_lock,
    )

    root = context.selected_brain.vault_root
    try:
        source = resolve_and_check_bounds(root / request.source, root)
    except ValueError as exc:
        return no_effect_error(type(request), ErrorCode.INVALID_REQUEST, str(exc))
    if not Path(source).is_file():
        return no_effect_error(
            type(request),
            ErrorCode.NOT_FOUND,
            f"Source file not found: {request.source}",
        )

    if context.dry_run:
        return Ok(
            request.COMMAND_ID,
            request.COMMAND_VERSION,
            build_payload(
                {
                    "status": "planned",
                    "created": False,
                    "rendered": False,
                },
                dry_run=True,
            ),
        )

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
    except (FileNotFoundError, ValueError) as exc:
        return no_effect_error(type(request), ErrorCode.INVALID_REQUEST, str(exc))

    if not isinstance(result, Mapping):
        raise TypeError(f"{operation} owner returned a non-object result")
    if "error" in result:
        return no_effect_error(
            type(request),
            ErrorCode.INVALID_REQUEST,
            str(result["error"]),
        )

    status = result.get("status")
    if status not in {"ok", "partial"}:
        raise ValueError(f"{operation} owner returned an unsupported status")
    effects = _render_effects(request.COMMAND_ID, result)
    if status == "partial":
        message = str(result.get("warning") or f"{operation} did not complete")
        if not effects:
            return no_effect_error(type(request), ErrorCode.CONFLICT, message)
        return Partial(
            request.COMMAND_ID,
            request.COMMAND_VERSION,
            CommandError(
                ErrorCode.CONFLICT,
                message,
                RequestErrorDetails(None, message),
            ),
            effects,
        )

    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        build_payload(result, dry_run=False),
        committed_effects=effects,
    )


def _render_effects(command_id: str, result: Mapping[str, object]):
    subjects: list[str] = []
    path = result.get("path")
    if result.get("created") and isinstance(path, str):
        subjects.append(path)
    pdf_path = result.get("pdf_path")
    if result.get("rendered") and isinstance(pdf_path, str):
        subjects.append(pdf_path)
    preview_pid = result.get("preview_pid")
    if isinstance(preview_pid, int):
        subjects.append(f"preview-process:{preview_pid}")
    return tuple(CommittedEffect(command_id, subject) for subject in subjects)


def require_non_empty(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def optional_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    return require_non_empty(value, field)


def catalogue_entry(request_type, executor):
    from .catalogue import ALL_APPLICATION_PROJECTIONS, ApplicationEntry

    return ApplicationEntry(
        request_type=request_type,
        executor=executor,
        dependency_tier=DependencyTier.MANAGED,
        locality=Locality.SELECTED_BRAIN_LOCAL,
        required_providers=("document_renderer",),
        optional_providers=(),
        authority=Authority.CONTRIBUTOR,
        effect_class=EffectClass.SELECTED_BRAIN_MUTATION,
        retry_class=RetryClass.RECEIPT_REQUIRED,
        projections=ALL_APPLICATION_PROJECTIONS,
    )
