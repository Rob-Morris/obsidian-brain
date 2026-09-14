"""Shared caller-local workspace command mechanics."""

from __future__ import annotations

from .types import InitialAuthorisationClass

from ._decoding import optional_bool, reject_unexpected

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping

from ._mutation_support import no_effect_error
from .context import InvocationContext
from .receipts import CommittedEffect
from .results import CommandError, Error, ErrorCode, Ok, Partial, RequestErrorDetails
from .types import (
    Authority,
    DependencyTier,
    EffectClass,
    Locality,
    Projection,
    ProjectionEligibility,
    RetryClass,
    validate_slug,
)


MCP_UNSUPPORTED_REASON = (
    "Caller-filesystem workspace mutations are available through local CLI, "
    "direct script and Python adapters only."
)


class CallerWorkspaceStatus(str, Enum):
    NOOP = "noop"
    PLANNED = "planned"
    CHANGED = "changed"


@dataclass(frozen=True, slots=True)
class CallerWorkspaceStep:
    name: str
    status: str
    message: str


@dataclass(frozen=True, slots=True)
class CallerWorkspacePayload:
    operation: str
    status: CallerWorkspaceStatus
    dry_run: bool
    workspace_name: str | None
    steps: tuple[CallerWorkspaceStep, ...]
    notes: tuple[str, ...]


def workspace_dir(context: InvocationContext, request_type) -> Path | Error:
    if context.workspace_dir is None:
        return no_effect_error(
            request_type,
            ErrorCode.CAPABILITY_UNAVAILABLE,
            "The caller filesystem provider did not resolve a workspace directory.",
        )
    if not context.workspace_dir.is_dir():
        return no_effect_error(
            request_type,
            ErrorCode.NOT_FOUND,
            "The resolved caller workspace directory does not exist.",
        )
    return context.workspace_dir


def execute_workspace_lifecycle(
    context: InvocationContext,
    request,
    *,
    operation: str,
    invoke,
    effect_subjects,
    lock_root: Path,
):
    from _common import (
        MutationLockError,
        public_mutation_error_message,
        vault_mutation_lock,
    )

    try:
        with vault_mutation_lock(lock_root):
            before_write = workspace_admission(context, request)
            if context.dry_run:
                before_write()
                return Ok(
                    request.COMMAND_ID,
                    request.COMMAND_VERSION,
                    CallerWorkspacePayload(
                        operation,
                        CallerWorkspaceStatus.PLANNED,
                        True,
                        context.workspace_dir.name if context.workspace_dir else None,
                        (),
                        (),
                    ),
                )
            result = invoke(before_write)
            if isinstance(result, Mapping) and result.get("status") in {"ok", "noop"}:
                before_write()
    except MutationLockError as exc:
        return no_effect_error(
            type(request),
            ErrorCode.CONFLICT,
            public_mutation_error_message(exc),
            retryable=True,
        )
    except (OSError, ValueError) as exc:
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))

    if not isinstance(result, Mapping):
        raise TypeError("workspace lifecycle owner returned a non-object result")
    status = result.get("status")
    if status == "error":
        return no_effect_error(
            type(request),
            ErrorCode.CONFLICT,
            _error_message(result),
        )
    effects = tuple(
        CommittedEffect(request.COMMAND_ID, subject)
        for subject in effect_subjects(result)
    )
    if status == "partial":
        message = _error_message(result)
        if not effects:
            raise RuntimeError("partial caller-workspace result omitted committed effects")
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
    if status not in {"ok", "noop"}:
        raise ValueError("workspace lifecycle owner returned an unsupported status")
    payload = CallerWorkspacePayload(
        operation,
        CallerWorkspaceStatus.CHANGED if status == "ok" else CallerWorkspaceStatus.NOOP,
        False,
        context.workspace_dir.name if context.workspace_dir else None,
        tuple(
            CallerWorkspaceStep(item["name"], item["status"], item["message"])
            for item in result.get("steps") or ()
        ),
        tuple(str(note) for note in result.get("notes") or ()),
    )
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        payload,
        committed_effects=effects,
    )


def lifecycle_effects(default_subject: str, result: Mapping[str, object]):
    if any(
        step.get("status") == "changed"
        for step in result.get("steps") or ()
        if isinstance(step, Mapping)
    ):
        return (default_subject,)
    return ()


def _error_message(result: Mapping[str, object]) -> str:
    messages = [
        str(step.get("message"))
        for step in result.get("steps") or ()
        if isinstance(step, Mapping) and step.get("status") == "error"
    ]
    return "; ".join(messages) or "Workspace operation did not complete."


def require_string(value: object, field: str, *, optional: bool = False):
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def validate_workspace_binding_request(request) -> None:
    require_string(request.brain_id, "brain_id", optional=True)
    require_string(request.slug, "slug", optional=True)
    if request.slug is not None:
        validate_slug(request.slug)
    if not isinstance(request.force, bool):
        raise ValueError("force must be a boolean")


def decode_workspace_binding(payload: Mapping[str, object], request_type):
    reject_unexpected(payload, {"brain_id", "slug", "force"})
    return request_type(
        require_string(payload.get("brain_id"), "brain_id", optional=True),
        require_string(payload.get("slug"), "slug", optional=True),
        optional_bool(payload.get("force"), "force"),
    )


def caller_workspace_entry(request_type, executor):
    from .catalogue import ApplicationEntry
    from .preparation import OperationPreparation
    from .workspace._preparation import prepare_workspace

    return ApplicationEntry(
        initial_class=InitialAuthorisationClass.EXCEPTIONAL,
        request_type=request_type,
        executor=executor,
        dependency_tier=DependencyTier.BOOTSTRAP,
        locality=Locality.CALLER_LOCAL,
        required_providers=("caller_filesystem",),
        optional_providers=(),
        authority=Authority.OPERATOR,
        effect_class=EffectClass.CALLER_LOCAL_MUTATION,
        retry_class=RetryClass.RECEIPT_REQUIRED,
        preparation=OperationPreparation(prepare_workspace),
        projections=(
            ProjectionEligibility(Projection.MCP, False, MCP_UNSUPPORTED_REASON),
            ProjectionEligibility(Projection.CLI, True),
            ProjectionEligibility(Projection.SCRIPT, True),
            ProjectionEligibility(Projection.PYTHON, True),
        ),
    )


def workspace_admission(context, request):
    """Share one admission across every write in a compound workspace action."""
    from .preparation import admit_owner
    from .workspace._preparation import prepare_workspace

    admitted = False
    def before_write():
        nonlocal admitted
        if not admitted:
            admit_owner(context, request, prepare_workspace)
            admitted = True
    return before_write
