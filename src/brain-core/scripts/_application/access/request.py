"""Typed ``access.request`` owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from ..access_contracts import AccessRequestDecision
from ..context import InvocationContext
from ..receipts import CommittedEffect
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
class AccessRequestRequest:
    COMMAND_ID: ClassVar[str] = "access.request"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = AccessRequestDecision
    MINIMAL_EXAMPLE: ClassVar[dict[str, object]] = {
        "commands": ["invocation.read"],
    }
    FIELD_DESCRIPTIONS: ClassVar[dict[str, str]] = {
        "commands": "One exact command or a small coherent command set within the authenticated ceiling.",
        "duration_seconds": "Optional requested absolute lease duration; server policy applies its maximum.",
        "use_count": "Optional positive maximum number of command invocations across the lease.",
    }

    commands: tuple[str, ...]
    duration_seconds: int | None = None
    use_count: int | None = None

    def __post_init__(self) -> None:
        if not self.commands or self.commands != tuple(sorted(set(self.commands))):
            raise ValueError("access.request commands must be non-empty, sorted and unique")
        if len(self.commands) > 8:
            raise ValueError("access.request accepts at most eight coherent commands")
        if any(not isinstance(item, str) or not item.strip() for item in self.commands):
            raise ValueError("access.request commands must be non-empty strings")
        if self.duration_seconds is not None and (
            not isinstance(self.duration_seconds, int)
            or isinstance(self.duration_seconds, bool)
            or self.duration_seconds < 1
        ):
            raise ValueError("duration_seconds must be a positive integer")
        if self.use_count is not None and (
            not isinstance(self.use_count, int)
            or isinstance(self.use_count, bool)
            or self.use_count < 1
        ):
            raise ValueError("use_count must be a positive integer")


def execute(context: InvocationContext, request: AccessRequestRequest):
    if context.access is None:
        raise RuntimeError("access controller is unavailable")
    decision = context.access.request(
        request.commands,
        duration_seconds=request.duration_seconds,
        use_count=request.use_count,
    )
    effects = (
        (CommittedEffect("access.changed", decision.snapshot.principal),)
        if decision.changed
        else ()
    )
    return Ok(
        AccessRequestRequest.COMMAND_ID,
        AccessRequestRequest.COMMAND_VERSION,
        decision,
        committed_effects=effects,
    )


def decode(payload: Mapping[str, object]) -> AccessRequestRequest:
    unexpected = sorted(set(payload) - {"commands", "duration_seconds", "use_count"})
    if unexpected:
        raise ValueError(f"unexpected fields: {', '.join(unexpected)}")
    commands = payload.get("commands")
    if not isinstance(commands, list) or any(not isinstance(item, str) for item in commands):
        raise ValueError("commands must be an array of strings")
    duration = payload.get("duration_seconds")
    use_count = payload.get("use_count")
    return AccessRequestRequest(tuple(commands), duration, use_count)


def catalogue_entry():
    from ..catalogue import ApplicationEntry

    return ApplicationEntry(
        request_type=AccessRequestRequest,
        executor=execute,
        dependency_tier=DependencyTier.BOOTSTRAP,
        locality=Locality.SELECTED_BRAIN_LOCAL,
        required_providers=(),
        optional_providers=(),
        authority=Authority.READER,
        effect_class=EffectClass.SELECTED_BRAIN_MUTATION,
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
        summary="Request an exact, bounded elevation lease within the ceiling.",
    )


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(AccessRequestRequest, decode)
