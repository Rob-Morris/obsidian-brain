"""Typed ``access.reduce`` owner."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Literal, Mapping

from ..access_contracts import AccessReductionResult
from ..context import InvocationContext
from ..receipts import CommittedEffect
from ..results import Ok
from ..types import (
    Authority,
    DependencyTier,
    EffectClass,
    Locality,
    RetryClass,
)


@dataclass(frozen=True, slots=True)
class ResetReduction:
    kind: Literal["initial"] = field(default="initial", init=False)


@dataclass(frozen=True, slots=True)
class LeaseReduction:
    lease_ids: tuple[str, ...]
    kind: Literal["leases"] = field(default="leases", init=False)


@dataclass(frozen=True, slots=True)
class CommandReduction:
    commands: tuple[str, ...]
    kind: Literal["commands"] = field(default="commands", init=False)


Reduction = ResetReduction | LeaseReduction | CommandReduction


@dataclass(frozen=True, slots=True)
class AccessReduceRequest:
    COMMAND_ID: ClassVar[str] = "access.reduce"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = AccessReductionResult
    FIELD_DESCRIPTIONS: ClassVar[dict[str, str]] = {
        "reduction": "Reset to the initial grant, revoke exact leases, or remove exact commands from leases.",
    }

    reduction: Reduction

    def __post_init__(self) -> None:
        if not isinstance(
            self.reduction,
            (ResetReduction, LeaseReduction, CommandReduction),
        ):
            raise ValueError("access.reduce reduction has an invalid variant")
        values = getattr(
            self.reduction,
            "lease_ids" if isinstance(self.reduction, LeaseReduction) else "commands",
            (),
        )
        if not isinstance(self.reduction, ResetReduction) and (
            not values or values != tuple(sorted(set(values)))
        ):
            raise ValueError("access.reduce identifiers must be non-empty, sorted and unique")


def execute(context: InvocationContext, request: AccessReduceRequest):
    if context.access is None:
        raise RuntimeError("access controller is unavailable")
    reduction = request.reduction
    result = context.access.reduce(
        reset=isinstance(reduction, ResetReduction),
        lease_ids=reduction.lease_ids if isinstance(reduction, LeaseReduction) else (),
        commands=reduction.commands if isinstance(reduction, CommandReduction) else (),
    )
    effects = (
        (CommittedEffect("access.reduced", result.snapshot.principal),)
        if result.changed
        else ()
    )
    return Ok(
        AccessReduceRequest.COMMAND_ID,
        AccessReduceRequest.COMMAND_VERSION,
        result,
        committed_effects=effects,
    )


def decode(payload: Mapping[str, object]) -> AccessReduceRequest:
    if set(payload) != {"reduction"}:
        raise ValueError("access.reduce requires only reduction")
    reduction = payload.get("reduction")
    if not isinstance(reduction, Mapping):
        raise ValueError("reduction must be an object")
    kind = reduction.get("kind")
    if kind == "initial":
        if set(reduction) != {"kind"}:
            raise ValueError("initial reduction accepts only kind")
        typed: Reduction = ResetReduction()
    elif kind in {"leases", "commands"}:
        field = "lease_ids" if kind == "leases" else "commands"
        if set(reduction) != {"kind", field}:
            raise ValueError(f"{kind} reduction requires only kind and {field}")
        values = reduction.get(field)
        if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
            raise ValueError(f"{field} must be an array of strings")
        typed = (
            LeaseReduction(tuple(values))
            if kind == "leases"
            else CommandReduction(tuple(values))
        )
    else:
        raise ValueError("reduction kind must be initial, leases or commands")
    return AccessReduceRequest(typed)


def catalogue_entry():
    from ..catalogue import ALL_APPLICATION_PROJECTIONS, ApplicationEntry

    return ApplicationEntry(
        request_type=AccessReduceRequest,
        executor=execute,
        dependency_tier=DependencyTier.BOOTSTRAP,
        locality=Locality.SELECTED_BRAIN_LOCAL,
        required_providers=(),
        optional_providers=(),
        authority=Authority.READER,
        effect_class=EffectClass.SELECTED_BRAIN_MUTATION,
        retry_class=RetryClass.SAFE,
        projections=ALL_APPLICATION_PROJECTIONS,
        summary="Reduce exact elevation leases or return to the initial grant.",
    )
