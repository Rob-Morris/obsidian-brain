"""Typed ``runtime.read-environment`` command and internal executor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

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


RuntimeValue = str | bool | int | float | None


@dataclass(frozen=True, slots=True)
class RuntimeFact:
    name: str
    value: RuntimeValue


@dataclass(frozen=True, slots=True)
class RuntimeEnvironmentPayload:
    facts: tuple[RuntimeFact, ...]


@dataclass(frozen=True, slots=True)
class RuntimeReadEnvironmentRequest:
    COMMAND_ID: ClassVar[str] = "runtime.read-environment"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = RuntimeEnvironmentPayload


def execute(context: InvocationContext, _request: RuntimeReadEnvironmentRequest):
    from _portable.router_views import environment_from_vault

    environment = environment_from_vault(context.selected_brain.vault_root)
    facts = tuple(
        RuntimeFact(name, value)
        for name, value in sorted(environment.items())
        if isinstance(value, (str, bool, int, float)) or value is None
    )
    if len(facts) != len(environment):
        raise TypeError("runtime environment contained a non-scalar value")
    return Ok(
        RuntimeReadEnvironmentRequest.COMMAND_ID,
        RuntimeReadEnvironmentRequest.COMMAND_VERSION,
        RuntimeEnvironmentPayload(facts),
    )


def decode(payload: Mapping[str, object]) -> RuntimeReadEnvironmentRequest:
    if payload:
        raise ValueError(f"unexpected fields: {', '.join(sorted(payload))}")
    return RuntimeReadEnvironmentRequest()


def catalogue_entry():
    from ..catalogue import ApplicationEntry

    return ApplicationEntry(
        request_type=RuntimeReadEnvironmentRequest,
        executor=execute,
        dependency_tier=DependencyTier.PORTABLE,
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
    )


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(RuntimeReadEnvironmentRequest, decode)
