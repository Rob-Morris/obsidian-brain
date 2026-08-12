"""Typed bootstrap-safe ``runtime.warmup`` owner."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Mapping

from ..context import InvocationContext
from ..results import Ok
from ..runtime_status import RuntimeStatusSnapshot
from ..types import (
    Authority,
    DependencyTier,
    EffectClass,
    Locality,
    Projection,
    ProjectionEligibility,
    RetryClass,
)
from ._snapshot import typed_snapshot


class WarmupRequestOutcome(str, Enum):
    STARTED = "started"
    ALREADY_RUNNING = "already_running"
    ALREADY_READY = "already_ready"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class RuntimeWarmupPayload:
    request_outcome: WarmupRequestOutcome
    runtime_status: RuntimeStatusSnapshot


@dataclass(frozen=True, slots=True)
class RuntimeWarmupRequest:
    COMMAND_ID: ClassVar[str] = "runtime.warmup"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = RuntimeWarmupPayload


def execute(context: InvocationContext, _request: RuntimeWarmupRequest):
    from _bootstrap.readiness import ensure_runtime_warmup

    outcome, value = ensure_runtime_warmup(
        context.selected_brain.vault_root,
        retry_failed=True,
    )
    return Ok(
        RuntimeWarmupRequest.COMMAND_ID,
        RuntimeWarmupRequest.COMMAND_VERSION,
        RuntimeWarmupPayload(
            WarmupRequestOutcome(outcome),
            typed_snapshot(value),
        ),
    )


def decode(payload: Mapping[str, object]) -> RuntimeWarmupRequest:
    if payload:
        raise ValueError(f"unexpected fields: {', '.join(sorted(payload))}")
    return RuntimeWarmupRequest()


def catalogue_entry():
    from ..catalogue import ApplicationEntry

    return ApplicationEntry(
        request_type=RuntimeWarmupRequest,
        executor=execute,
        dependency_tier=DependencyTier.BOOTSTRAP,
        locality=Locality.SELECTED_BRAIN_LOCAL,
        required_providers=(),
        optional_providers=(),
        authority=Authority.READER,
        effect_class=EffectClass.DERIVED_CACHE_WRITE,
        retry_class=RetryClass.SAFE,
        projections=tuple(
            ProjectionEligibility(projection, True)
            for projection in (
                Projection.MCP,
                Projection.CLI,
                Projection.SCRIPT,
                Projection.PYTHON,
            )
        ),
        summary="Start, join or retry selected-Brain runtime warm-up.",
    )


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(RuntimeWarmupRequest, decode)
