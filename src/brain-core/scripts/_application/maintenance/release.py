"""Typed ``maintenance.release`` owner: give a claimed finding back (D12)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from _bootstrap.maintenance_decisions import decide_release, validate_actor, validate_key

from .._decoding import reject_unexpected
from ..context import InvocationContext
from ._decisions import DecisionPayload, apply_decision, catalogue_entry as _decision_entry


@dataclass(frozen=True, slots=True)
class MaintenanceReleaseRequest:
    COMMAND_ID: ClassVar[str] = "maintenance.release"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = DecisionPayload

    key: str
    actor: str

    def __post_init__(self) -> None:
        validate_key(self.key)
        validate_actor(self.actor, "actor")


def execute(context: InvocationContext, request: MaintenanceReleaseRequest):
    def decide(group, decisions, now):
        return decide_release(group, decisions, now, actor=request.actor)

    return apply_decision(context, request, kind="release", decide=decide)


def decode(payload: Mapping[str, object]) -> MaintenanceReleaseRequest:
    reject_unexpected(payload, {"key", "actor"})
    return MaintenanceReleaseRequest(payload.get("key"), payload.get("actor"))


def catalogue_entry():
    return _decision_entry(MaintenanceReleaseRequest, execute,
                           "Release a claimed maintenance finding so someone else can take it.")
