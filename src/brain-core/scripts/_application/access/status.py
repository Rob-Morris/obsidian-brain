"""Typed ``access.status`` owner."""

from __future__ import annotations

from .._decoding import decode_empty
from dataclasses import dataclass
from typing import ClassVar, Mapping

from ..access_contracts import AccessSnapshot
from ..context import InvocationContext
from ..results import Ok
from ..types import (
    Authority,
    DependencyTier,
    EffectClass,
    Locality,
    RetryClass,
)


@dataclass(frozen=True, slots=True)
class AccessStatusRequest:
    COMMAND_ID: ClassVar[str] = "access.status"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = AccessSnapshot


def execute(context: InvocationContext, _request: AccessStatusRequest):
    if context.access is None:
        raise RuntimeError("access controller is unavailable")
    return Ok(
        AccessStatusRequest.COMMAND_ID,
        AccessStatusRequest.COMMAND_VERSION,
        context.access.status(),
    )


def decode(payload: Mapping[str, object]) -> AccessStatusRequest:
    return decode_empty(payload, AccessStatusRequest)


def catalogue_entry():
    from ..catalogue import ALL_APPLICATION_PROJECTIONS, ApplicationEntry

    return ApplicationEntry(
        request_type=AccessStatusRequest,
        executor=execute,
        dependency_tier=DependencyTier.BOOTSTRAP,
        locality=Locality.SELECTED_BRAIN_LOCAL,
        required_providers=(),
        optional_providers=(),
        authority=Authority.READER,
        effect_class=EffectClass.NONE,
        retry_class=RetryClass.SAFE,
        projections=ALL_APPLICATION_PROJECTIONS,
        summary="Read the active grant, ceiling and expiring access leases.",
    )
