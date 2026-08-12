"""Typed ``access.status`` owner."""

from __future__ import annotations

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
    Projection,
    ProjectionEligibility,
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
    if payload:
        raise ValueError(f"unexpected fields: {', '.join(sorted(payload))}")
    return AccessStatusRequest()


def catalogue_entry():
    from ..catalogue import ApplicationEntry

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
        projections=tuple(
            ProjectionEligibility(projection, True)
            for projection in (
                Projection.MCP,
                Projection.CLI,
                Projection.SCRIPT,
                Projection.PYTHON,
            )
        ),
        summary="Read the active grant, ceiling and expiring access leases.",
    )


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(AccessStatusRequest, decode)
