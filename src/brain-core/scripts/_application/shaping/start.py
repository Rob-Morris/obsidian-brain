"""Typed ``shaping.start`` owner."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import ClassVar, Mapping

from .._decoding import reject_unexpected
from .._mutation_support import contributor_mutation_entry, no_effect_error
from ..context import InvocationContext
from ..preparation import admit_owner
from ._start_preparation import SHAPING_SESSION, session_plan, session_binding
from ..workspace_context import (WorkspaceAwareRequest, WorkspaceMutationPayload, WorkspaceMutationPartial,
                                 workspace_request_decoder, validate_workspace_request)
from ..results import (
    CommandError,
    ErrorCode,
    Ok,
    RequestErrorDetails,
)


class ShapingMode(str, Enum):
    BRAINSTORM = "brainstorm"
    REFINE = "refine"
    DISCOVER = "discover"


class TranscriptOperation(str, Enum):
    CREATED = "created"
    APPENDED = "appended"


class StatusBehaviour(str, Enum):
    TRANSITION = "transition"
    PRESERVE = "preserve"


@dataclass(frozen=True, slots=True)
class ShapingStartPayload(WorkspaceMutationPayload):
    resolved_target_path: str
    target_path: str
    target_path_changed: bool
    transcript_path: str
    transcript_type: str
    mode: ShapingMode
    status_behaviour: StatusBehaviour
    status_changed: bool
    transcript_operation: TranscriptOperation
    changed_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ShapingStartRequest(WorkspaceAwareRequest):
    COMMAND_ID: ClassVar[str] = "shaping.start"
    COMMAND_VERSION: ClassVar[int] = 2
    RESULT_TYPE: ClassVar[type] = ShapingStartPayload

    target: str
    mode: ShapingMode

    def __post_init__(self) -> None:
        validate_workspace_request(self)
        if not isinstance(self.target, str) or not self.target.strip():
            raise ValueError("target must be a non-empty string")
        if not isinstance(self.mode, ShapingMode):
            raise ValueError("mode must be a ShapingMode")


def execute(context: InvocationContext, request: ShapingStartRequest):
    from _common import (
        MutationLockError,
        PartialApplyError,
        public_mutation_error_message,
        vault_mutation_lock,
    )
    from _lifecycle.derived_cache_state import require_fresh_compiled_router
    from .._transition_indexes import index_refresh_error
    from _portable.transition_indexes import IndexRefreshIncomplete
    from start_shaping_session import start_shaping_session

    if context.dry_run:
        return no_effect_error(
            ShapingStartRequest,
            ErrorCode.INVALID_REQUEST,
            "shaping.start does not support dry-run",
        )
    root = str(context.selected_brain.vault_root)

    try:
        with vault_mutation_lock(root):
            router = require_fresh_compiled_router(root)
            options = {}
            plan, frozen = session_plan(context, request, router, frozen_inputs=context.admission.frozen_inputs)
            def binding(context, request, *, frozen_inputs=None):
                return session_binding(context, request, plan=plan, router=router, frozen_inputs=frozen)
            admit_owner(context, request, binding)
            from ._start_preparation import session_effect_snapshot, committed_session_effects
            before = session_effect_snapshot(context, plan)
            options["_plan"] = plan
            try:
                result = start_shaping_session(
                    root,
                    router,
                    request.target.strip(),
                    mode=request.mode.value,
                    **options,
                )
            finally:
                effects = committed_session_effects(context, request, plan, before)
                if context.derived_snapshots is not None:
                    context.derived_snapshots.invalidate()
    except MutationLockError as exc:
        return no_effect_error(
            ShapingStartRequest,
            ErrorCode.CONFLICT,
            public_mutation_error_message(exc),
            retryable=True,
        )
    except IndexRefreshIncomplete as exc:
        return WorkspaceMutationPartial(request.COMMAND_ID, request.COMMAND_VERSION, index_refresh_error(exc),
            effects, mutation_context=plan["mutation_context"])
    except PartialApplyError as exc:
        message = public_mutation_error_message(exc)
        if not effects:
            return no_effect_error(ShapingStartRequest, ErrorCode.CONFLICT, message)
        return WorkspaceMutationPartial(
            request.COMMAND_ID,
            request.COMMAND_VERSION,
            CommandError(
                ErrorCode.CONFLICT,
                message,
                RequestErrorDetails(None, message),
            ),
            effects, mutation_context=plan["mutation_context"],
        )
    except FileNotFoundError as exc:
        return no_effect_error(
            ShapingStartRequest,
            ErrorCode.NOT_FOUND,
            str(exc),
            "target",
        )
    except ValueError as exc:
        return no_effect_error(
            ShapingStartRequest,
            ErrorCode.INVALID_REQUEST,
            str(exc),
        )
    except OSError as exc:
        return no_effect_error(
            ShapingStartRequest,
            ErrorCode.CONFLICT,
            str(exc),
        )

    payload = ShapingStartPayload(
        result["resolved_target_path"],
        result["target_path"],
        result["target_path_changed"],
        result["transcript_path"],
        result["type"],
        ShapingMode(result["mode"]),
        StatusBehaviour(result["status_behaviour"]),
        result["status_changed"],
        TranscriptOperation(result["transcript_operation"]),
        tuple(effect.subject for effect in effects),
        mutation_context=plan["mutation_context"],
    )
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        payload,
        committed_effects=effects,
    )


@workspace_request_decoder
def decode(payload: Mapping[str, object]) -> ShapingStartRequest:
    reject_unexpected(payload, {"target", "mode"})
    target = payload.get("target")
    mode = payload.get("mode")
    if not isinstance(target, str):
        raise ValueError("target must be a string")
    try:
        resolved_mode = ShapingMode(mode)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "mode must be 'brainstorm', 'refine', or 'discover'"
        ) from exc
    return ShapingStartRequest(target, resolved_mode)


def catalogue_entry():
    return replace(contributor_mutation_entry(ShapingStartRequest, execute), preparation=SHAPING_SESSION)
