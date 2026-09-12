"""Typed bootstrap-safe ``runtime.warmup`` owner."""

from __future__ import annotations

from .._decoding import decode_empty
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
    COMMAND_VERSION: ClassVar[int] = 2
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
    return decode_empty(payload, RuntimeWarmupRequest)


def catalogue_entry():
    from ..catalogue import ALL_APPLICATION_PROJECTIONS, ApplicationEntry

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
        projections=ALL_APPLICATION_PROJECTIONS,
        summary="Start, join or retry selected-Brain runtime warm-up.",
    )
