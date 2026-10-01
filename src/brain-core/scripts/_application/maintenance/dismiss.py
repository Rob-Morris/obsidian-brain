"""Typed ``maintenance.dismiss`` owner: "this is fine as it is" (D13)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from _bootstrap.maintenance_decisions import decide_dismiss, validate_actor, validate_key, validate_reason

from .._decoding import reject_unexpected
from ..context import InvocationContext
from ._decisions import DecisionPayload, apply_decision, catalogue_entry as _decision_entry


@dataclass(frozen=True, slots=True)
class MaintenanceDismissRequest:
    COMMAND_ID: ClassVar[str] = "maintenance.dismiss"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = DecisionPayload

    key: str
    expected_fingerprint: str
    reason: str
    actor: str

    def __post_init__(self) -> None:
        validate_key(self.key)
        validate_key(self.expected_fingerprint, "expected_fingerprint")
        validate_reason(self.reason)
        validate_actor(self.actor, "actor")


def execute(context: InvocationContext, request: MaintenanceDismissRequest):
    def decide(group, decisions, now):
        return decide_dismiss(group, decisions, now, actor=request.actor, reason=request.reason,
                              expected_fingerprint=request.expected_fingerprint)

    return apply_decision(context, request, kind="dismiss", decide=decide)


def decode(payload: Mapping[str, object]) -> MaintenanceDismissRequest:
    reject_unexpected(payload, {"key", "expected_fingerprint", "reason", "actor"})
    return MaintenanceDismissRequest(
        payload.get("key"), payload.get("expected_fingerprint"), payload.get("reason"), payload.get("actor"),
    )


def catalogue_entry():
    return _decision_entry(MaintenanceDismissRequest, execute,
                           "Dismiss a judgement finding at its current evidence for the retention period.")
