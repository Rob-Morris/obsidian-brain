"""Explicit harness-gated consent for one prepared operation or one command."""
from dataclasses import dataclass, field
import json
from typing import ClassVar, Literal, Mapping

from ..access_contracts import AccessRequestDecision
from ..consent import ConsentScope
from ..receipts import CommittedEffect
from ..results import Ok
from ..types import validate_command_id
from ._support import control_entry, nonempty, object_fields

MAX_CONSENT_REQUEST_BYTES = 8000


@dataclass(frozen=True, slots=True)
class OperationConsent:
    operation_id: str
    digest: str
    review: str
    scope: Literal["operation"] = field(default="operation", init=False)

    def __post_init__(self):
        nonempty(self.operation_id, "operation_id")
        nonempty(self.digest, "digest")
        nonempty(self.review, "review")


@dataclass(frozen=True, slots=True)
class CommandConsent:
    command_id: str
    review: str
    scope: Literal["command"] = field(default="command", init=False)

    def __post_init__(self):
        nonempty(self.command_id, "command_id")
        validate_command_id(self.command_id)
        nonempty(self.review, "review")


def validate_consent_request_size(consent):
    from dataclasses import asdict
    value = {"command_id": "access.request", "command_version": 2,
             "arguments": {"consent": asdict(consent)}}
    if len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) >= MAX_CONSENT_REQUEST_BYTES:
        raise ValueError("Consent review exceeds the 8000-byte request budget; prepare a smaller operation.")


@dataclass(frozen=True, slots=True)
class AccessRequestRequest:
    COMMAND_ID: ClassVar[str] = "access.request"
    COMMAND_VERSION: ClassVar[int] = 2
    RESULT_TYPE: ClassVar[type] = AccessRequestDecision
    FIELD_DESCRIPTIONS: ClassVar[dict[str, str]] = {
        "consent": "Echo the exact canonical review from access.prepare or access.status. Command consent covers that command throughout this Brain until this context ends or is revoked."}
    MINIMAL_EXAMPLE: ClassVar[dict[str, object]] = {"consent": {"scope": "command", "command_id": "artefact.delete", "review": "<exact command_review from access.status>"}}
    consent: OperationConsent | CommandConsent

    def __post_init__(self):
        if not isinstance(self.consent, (OperationConsent, CommandConsent)):
            raise ValueError("consent must select operation or command scope")
        validate_consent_request_size(self.consent)


def execute(context, request):
    consent = request.consent
    decision = context.access.request(scope=ConsentScope(consent.scope), review=consent.review,
        command_id=consent.command_id if isinstance(consent, CommandConsent) else None,
        operation_id=consent.operation_id if isinstance(consent, OperationConsent) else None,
        digest=consent.digest if isinstance(consent, OperationConsent) else None)
    effects = (CommittedEffect("access.authorised", decision.grant_id or decision.command_id),) if decision.changed else ()
    return Ok(request.COMMAND_ID, request.COMMAND_VERSION, decision, committed_effects=effects)


def decode(payload: Mapping[str, object]):
    object_fields(payload, {"consent"}, {"consent"})
    value = payload["consent"]
    if not isinstance(value, Mapping):
        raise ValueError("consent must be an object")
    if value.get("scope") == "operation":
        object_fields(value, {"scope", "operation_id", "digest", "review"}, {"scope", "operation_id", "digest", "review"})
        consent = OperationConsent(value["operation_id"], value["digest"], value["review"])
    elif value.get("scope") == "command":
        object_fields(value, {"scope", "command_id", "review"}, {"scope", "command_id", "review"})
        consent = CommandConsent(value["command_id"], value["review"])
    else:
        raise ValueError("consent scope must be operation or command")
    return AccessRequestRequest(consent)


def catalogue_entry():
    return control_entry(AccessRequestRequest, execute, mutation=True,
        summary="Explicitly authorise one prepared operation or one command in this Brain context.")
