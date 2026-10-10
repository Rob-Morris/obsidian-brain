"""Typed ``maintenance.claim`` owner: "someone is on this" (D12)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from _bootstrap.maintenance_decisions import decide_claim, validate_actor, validate_key

from .._decoding import reject_unexpected
from ..context import InvocationContext
from ._decisions import DecisionPayload, apply_decision, catalogue_entry as _decision_entry
from ._items import never_held


@dataclass(frozen=True, slots=True)
class MaintenanceClaimRequest:
    COMMAND_ID: ClassVar[str] = "maintenance.claim"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = DecisionPayload

    key: str
    claimant: str

    def __post_init__(self) -> None:
        validate_key(self.key)
        validate_actor(self.claimant, "claimant")


def execute(context: InvocationContext, request: MaintenanceClaimRequest):
    def decide(group, decisions, now):
        return decide_claim(group, decisions, now, claimant=request.claimant, never_held=never_held())

    return apply_decision(context, request, kind="claim", decide=decide)


def decode(payload: Mapping[str, object]) -> MaintenanceClaimRequest:
    reject_unexpected(payload, {"key", "claimant"})
    return MaintenanceClaimRequest(payload.get("key"), payload.get("claimant"))


def catalogue_entry():
    return _decision_entry(MaintenanceClaimRequest, execute,
                           "Claim a maintenance finding for one lease so the pass and other people leave it alone.")
