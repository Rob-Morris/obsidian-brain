"""Typed bootstrap-safe ``runtime.status`` owner."""

from __future__ import annotations

from .._decoding import decode_empty
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class RuntimeStatusPayload:
    runtime_status: RuntimeStatusSnapshot


@dataclass(frozen=True, slots=True)
class RuntimeStatusRequest:
    COMMAND_ID: ClassVar[str] = "runtime.status"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = RuntimeStatusPayload


def execute(context: InvocationContext, _request: RuntimeStatusRequest):
    from _bootstrap.readiness import read_runtime_status

    snapshot = typed_snapshot(read_runtime_status(context.selected_brain.vault_root))
    return Ok(
        RuntimeStatusRequest.COMMAND_ID,
        RuntimeStatusRequest.COMMAND_VERSION,
        RuntimeStatusPayload(snapshot),
    )


def decode(payload: Mapping[str, object]) -> RuntimeStatusRequest:
    return decode_empty(payload, RuntimeStatusRequest)


def catalogue_entry():
    from ..catalogue import ALL_APPLICATION_PROJECTIONS, ApplicationEntry
    from ..preparation import LIVE_QUERY

    return ApplicationEntry(
        request_type=RuntimeStatusRequest,
        executor=execute,
        dependency_tier=DependencyTier.BOOTSTRAP,
        locality=Locality.SELECTED_BRAIN_LOCAL,
        required_providers=(),
        optional_providers=(),
        authority=Authority.READER,
        effect_class=EffectClass.NONE,
        retry_class=RetryClass.SAFE,
        projections=ALL_APPLICATION_PROJECTIONS,
        preparation=LIVE_QUERY,
        summary="Read the selected Brain's recorded runtime warm-up status.",
    )
