"""Typed managed ``session.start`` bootstrap owner."""

from __future__ import annotations

from ..types import InitialAuthorisationClass

from .._decoding import reject_unexpected
from dataclasses import dataclass, asdict
import json

from typing import ClassVar, Mapping

from ..context import InvocationContext
from ..results import CommandError, Error, ErrorCode, InstructionNextAction, Ok
from ..runtime._snapshot import typed_snapshot
from ..runtime_status import RuntimeProgressDetails, RuntimeState
from ..types import (
    Authority,
    Availability,
    DependencyTier,
    EffectClass,
    Locality,
    RetryClass,
)
from .._read_support import command_error


@dataclass(frozen=True, slots=True)
class SessionStartPayload:
    brain_core_version: str
    content: str
    revision: str


@dataclass(frozen=True, slots=True)
class SessionStartRequest:
    COMMAND_ID: ClassVar[str] = "session.start"
    COMMAND_VERSION: ClassVar[int] = 9
    RESULT_TYPE: ClassVar[type] = SessionStartPayload


def execute(context: InvocationContext, request: SessionStartRequest):
    from _bootstrap.readiness import ensure_runtime_warmup, read_runtime_status

    status = typed_snapshot(read_runtime_status(context.selected_brain.vault_root))
    if status.state is not RuntimeState.READY:
        outcome, value = ensure_runtime_warmup(
            context.selected_brain.vault_root,
            retry_failed=False,
        )
        status = typed_snapshot(value)
        if status.state is RuntimeState.FAILED or outcome == "failed":
            message = "Brain runtime warm-up failed; request an explicit retry."
            guidance = (
                "Call runtime.warmup to retry warm-up, then poll runtime.status "
                "and retry session.start when state is ready."
            )
            retryable = False
        else:
            message = "Brain runtime warm-up is still in progress."
            guidance = (
                "Poll runtime.status, then retry session.start when state is ready."
            )
            retryable = True
        return Error(
            SessionStartRequest.COMMAND_ID,
            SessionStartRequest.COMMAND_VERSION,
            CommandError(
                ErrorCode.CONFLICT,
                message,
                RuntimeProgressDetails(status),
                next_action=InstructionNextAction(guidance),
            ),
            retryable=retryable,
        )

    import config
    import session
    if context.derived_snapshots is not None:
        router = context.derived_snapshots.load_router()
    else:
        from _common import load_compiled_router

        router = load_compiled_router(context.selected_brain.vault_root)
    if "error" in router:
        return command_error(
            SessionStartRequest,
            ErrorCode.CONFLICT,
            router["error"],
            None,
        )
    try:
        merged_config = config.load_config(context.selected_brain.vault_root)
        model = session.build_session_model(
            router,
            context.selected_brain.vault_root,
            obsidian_cli_available=(
                context.capabilities.availability_of("obsidian_cli")
                is Availability.AVAILABLE
            ),
            workspace_dir=(
                str(context.workspace_dir) if context.workspace_dir is not None else None
            ),
            config=merged_config,
            access_summary=asdict(context.access.summary()),
            load_config_if_missing=False,
            include_command_catalogue=True,
        )
        if context.session_mirror is None:
            raise RuntimeError("session mirror publisher is not available")
        context.session_mirror.publish(model)
        if len(json.dumps(model["access"], ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 768:
            raise ValueError("Authorisation bootstrap exceeds its 768-byte budget")
    except (OSError, RuntimeError, ValueError) as exc:
        return command_error(SessionStartRequest, ErrorCode.CONFLICT, str(exc), None)
    from _common import document_revision

    content = session.render_session_markdown(model)
    return Ok(request.COMMAND_ID, request.COMMAND_VERSION, SessionStartPayload(
        model["brain_core_version"], content, document_revision(content),
    ))


def decode(payload: Mapping[str, object]) -> SessionStartRequest:
    reject_unexpected(payload, set())
    return SessionStartRequest()


def catalogue_entry():
    from ..catalogue import ALL_APPLICATION_PROJECTIONS, ApplicationEntry

    return ApplicationEntry(
        initial_class=InitialAuthorisationClass.CONTROL,
        request_type=SessionStartRequest,
        executor=execute,
        dependency_tier=DependencyTier.MANAGED,
        locality=Locality.SELECTED_BRAIN_LOCAL,
        required_providers=(),
        optional_providers=("obsidian_cli",),
        authority=Authority.READER,
        effect_class=EffectClass.DERIVED_CACHE_WRITE,
        retry_class=RetryClass.SAFE,
        projections=ALL_APPLICATION_PROJECTIONS,
    )
