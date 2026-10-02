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
)


MCP_UNSUPPORTED_REASON = (
    "Caller-filesystem workspace mutations are available through local CLI, "
    "direct script and Python adapters only."
)
LINK_MCP_UNSUPPORTED_REASON = (
    "Workspace link commands write a local folder outside the selected Brain and "
    "are available through local CLI, direct script and Python adapters only."
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
):
    from _common import (
        MutationLockError,
        public_mutation_error_message,
        vault_mutation_lock,
    )

    try:
        with vault_mutation_lock(context.workspace_dir):
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


def compound_workspace_entry(request_type, executor):
    """A link command: the selected Brain's registry, then one local workspace folder."""
    from dataclasses import replace

    entry = caller_workspace_entry(request_type, executor)
    return replace(
        entry,
        dependency_tier=DependencyTier.PORTABLE,
        locality=Locality.SELECTED_BRAIN_AND_CALLER_LOCAL,
        effect_class=EffectClass.SELECTED_BRAIN_AND_CALLER_LOCAL_MUTATION,
        projections=(ProjectionEligibility(Projection.MCP, False, LINK_MCP_UNSUPPORTED_REASON),
                     *entry.projections[1:]),
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
