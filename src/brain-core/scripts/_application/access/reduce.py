"""Monotonic narrowing and disposal of instance authorisation."""
from dataclasses import dataclass, field
from typing import ClassVar, Literal

from ..access_contracts import AccessReductionResult
from ..receipts import CommittedEffect
from ..results import Ok
from ._support import control_entry, identifiers, object_fields


@dataclass(frozen=True, slots=True)
class RevokeGrants:
    grant_ids: tuple[str, ...]
    kind: Literal["grants"] = field(default="grants", init=False)
    def __post_init__(self):
        identifiers(self.grant_ids, "grant_ids")


@dataclass(frozen=True, slots=True)
class DiscardOperations:
    operation_ids: tuple[str, ...]
    kind: Literal["operations"] = field(default="operations", init=False)
    def __post_init__(self):
        identifiers(self.operation_ids, "operation_ids")


@dataclass(frozen=True, slots=True)
class NarrowInitial:
    commands: tuple[str, ...]
    kind: Literal["commands"] = field(default="commands", init=False)
    def __post_init__(self):
        identifiers(self.commands, "commands")


@dataclass(frozen=True, slots=True)
class ClearGrants:
    kind: Literal["all-grants"] = field(default="all-grants", init=False)


Reduction = RevokeGrants | DiscardOperations | NarrowInitial | ClearGrants


@dataclass(frozen=True, slots=True)
class AccessReduceRequest:
    COMMAND_ID: ClassVar[str] = "access.reduce"
    COMMAND_VERSION: ClassVar[int] = 2
    RESULT_TYPE: ClassVar[type] = AccessReductionResult
    FIELD_DESCRIPTIONS: ClassVar[dict[str, str]] = {"reduction": "Revoke grants, discard prepared operations, or narrow initial commands. Never restores authorisation."}
    MINIMAL_EXAMPLE: ClassVar[dict[str, object]] = {"reduction": {"kind": "all-grants"}}
    reduction: Reduction

    def __post_init__(self):
        if not isinstance(self.reduction, (RevokeGrants, DiscardOperations, NarrowInitial, ClearGrants)):
            raise ValueError("invalid reduction variant")


def execute(context, request):
    value = request.reduction
    result = context.access.reduce(
        grant_ids=value.grant_ids if isinstance(value, RevokeGrants) else (),
        operation_ids=value.operation_ids if isinstance(value, DiscardOperations) else (),
        commands=value.commands if isinstance(value, NarrowInitial) else (),
        clear_grants=isinstance(value, ClearGrants))
    effects = (CommittedEffect("access.reduced", value.kind),) if result.changed else ()
    return Ok(request.COMMAND_ID, request.COMMAND_VERSION, result, committed_effects=effects)


def decode(payload):
    object_fields(payload, {"reduction"}, {"reduction"})
    value = object_fields(payload["reduction"], {"kind", "grant_ids", "operation_ids", "commands"}, {"kind"})
    kind = value["kind"]
    if kind == "all-grants":
        object_fields(value, {"kind"})
        return AccessReduceRequest(ClearGrants())
    variants = {"grants": ("grant_ids", RevokeGrants), "operations": ("operation_ids", DiscardOperations), "commands": ("commands", NarrowInitial)}
    if kind not in variants:
        raise ValueError("reduction kind must be grants, operations, commands or all-grants")
    name, variant = variants[kind]
    object_fields(value, {"kind", name}, {"kind", name})
    if not isinstance(value[name], list):
        raise ValueError(f"{name} must be an array")
    return AccessReduceRequest(variant(tuple(value[name])))


def catalogue_entry():
    return control_entry(AccessReduceRequest, execute, mutation=True,
        summary="Revoke grants, discard preparations, or narrow initial authorisation in this context.")
