"""Stdlib-only request, result and receipt contracts for launcher owners."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import re
from pathlib import Path
from typing import Generic, Literal, Protocol, TypeVar


RESULT_SCHEMA = "brain.command-result/1"
_COMMAND_ID = re.compile(
    r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*\.[a-z][a-z0-9]*(?:-[a-z0-9]+)*$"
)
T = TypeVar("T")


def validate_command_id(command_id: str) -> None:
    if not isinstance(command_id, str) or not _COMMAND_ID.fullmatch(command_id):
        raise ValueError("launcher command_id must be a canonical noun.verb identifier")


class ErrorCode(str, Enum):
    INVALID_REQUEST = "invalid_request"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    AUTHORITY_DENIED = "authority_denied"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    COMMAND_OUTCOME_UNKNOWN = "command_outcome_unknown"
    INTERNAL_ERROR = "internal_error"


class WarningCode(str, Enum):
    DEGRADED_CAPABILITY = "degraded_capability"
    STALE_SNAPSHOT = "stale_snapshot"
    FOLLOW_UP_REQUIRED = "follow_up_required"


class ReceiptState(str, Enum):
    NONE = "none"
    COMMITTED = "committed"
    KNOWN_PARTIAL = "known_partial"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class OutcomeReference:
    invocation_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.invocation_id, str) or not self.invocation_id.strip():
            raise ValueError("launcher outcome reference requires an invocation_id")


@dataclass(frozen=True, slots=True)
class CommittedEffect:
    kind: str
    subject: str

    def __post_init__(self) -> None:
        if not self.kind.strip() or not self.subject.strip():
            raise ValueError("launcher effects require non-empty kind and subject")


@dataclass(frozen=True, slots=True)
class RequestErrorDetails:
    field: str | None
    reason: str

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("launcher request error reason must be non-empty")


@dataclass(frozen=True, slots=True)
class AuthorityDeniedDetails:
    profile: str
    required: str

    def __post_init__(self) -> None:
        if not self.profile.strip() or not self.required.strip():
            raise ValueError("launcher authority details must be non-empty")


@dataclass(frozen=True, slots=True)
class CapabilityUnavailableDetails:
    locality: Literal["machine_local"]
    missing: tuple[str, ...]
    recoverable: bool

    def __post_init__(self) -> None:
        if not self.missing or any(not item.strip() for item in self.missing):
            raise ValueError("launcher capability details must name missing bindings")
        if len(self.missing) != len(set(self.missing)):
            raise ValueError("launcher missing bindings must be unique")


@dataclass(frozen=True, slots=True)
class InternalErrorDetails:
    correlation_id: str

    def __post_init__(self) -> None:
        if not self.correlation_id.strip():
            raise ValueError("launcher internal error requires a correlation_id")


@dataclass(frozen=True, slots=True)
class OutcomeUnknownDetails:
    reference: OutcomeReference
    recovery_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_recovery_paths(self.recovery_paths)


@dataclass(frozen=True, slots=True)
class RecoveryRequiredDetails:
    recovery_paths: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("launcher recovery details require a reason")
        if not self.recovery_paths:
            raise ValueError("launcher recovery details require at least one path")
        _validate_recovery_paths(self.recovery_paths)


ErrorDetails = (
    RequestErrorDetails
    | AuthorityDeniedDetails
    | CapabilityUnavailableDetails
    | InternalErrorDetails
    | OutcomeUnknownDetails
    | RecoveryRequiredDetails
)


@dataclass(frozen=True, slots=True)
class InstructionNextAction:
    instruction: str

    def __post_init__(self) -> None:
        if not self.instruction.strip():
            raise ValueError("launcher next action must be non-empty")


@dataclass(frozen=True, slots=True)
class CommandWarning:
    code: WarningCode
    message: str

    def __post_init__(self) -> None:
        if not self.message.strip():
            raise ValueError("launcher warning message must be non-empty")


@dataclass(frozen=True, slots=True)
class CommandError:
    code: ErrorCode
    message: str
    details: ErrorDetails | None = None
    next_action: InstructionNextAction | None = None

    def __post_init__(self) -> None:
        if not self.message.strip():
            raise ValueError("launcher command error message must be non-empty")


@dataclass(frozen=True, slots=True)
class Ok(Generic[T]):
    command_id: str
    command_version: int
    result: T
    committed_effects: tuple[CommittedEffect, ...] = ()
    warnings: tuple[CommandWarning, ...] = ()
    schema: Literal["brain.command-result/1"] = field(default=RESULT_SCHEMA, init=False)
    status: Literal["ok"] = field(default="ok", init=False)

    def __post_init__(self) -> None:
        _validate_identity(self.command_id, self.command_version)


@dataclass(frozen=True, slots=True)
class Partial:
    command_id: str
    command_version: int
    error: CommandError
    committed_effects: tuple[CommittedEffect, ...]
    warnings: tuple[CommandWarning, ...] = ()
    schema: Literal["brain.command-result/1"] = field(default=RESULT_SCHEMA, init=False)
    status: Literal["partial"] = field(default="partial", init=False)

    def __post_init__(self) -> None:
        _validate_identity(self.command_id, self.command_version)
        if not self.committed_effects:
            raise ValueError("partial launcher result must enumerate committed effects")


@dataclass(frozen=True, slots=True)
class Error:
    command_id: str
    command_version: int
    error: CommandError
    effects: Literal["none", "unknown"] = "none"
    outcome_reference: OutcomeReference | None = None
    retryable: bool = False
    warnings: tuple[CommandWarning, ...] = ()
    schema: Literal["brain.command-result/1"] = field(default=RESULT_SCHEMA, init=False)
    status: Literal["error"] = field(default="error", init=False)

    def __post_init__(self) -> None:
        _validate_identity(self.command_id, self.command_version)
        if self.effects == "unknown":
            if self.outcome_reference is None or self.retryable:
                raise ValueError("unknown launcher outcomes require a non-retryable reference")
            if self.error.code is not ErrorCode.COMMAND_OUTCOME_UNKNOWN:
                raise ValueError("unknown launcher outcomes require command_outcome_unknown")
            if not isinstance(self.error.details, OutcomeUnknownDetails):
                raise ValueError("unknown launcher outcomes require typed details")
            if self.error.details.reference != self.outcome_reference:
                raise ValueError("launcher outcome reference and details must agree")
        elif self.outcome_reference is not None:
            raise ValueError("known launcher errors cannot carry an outcome reference")
        elif self.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN:
            raise ValueError("command_outcome_unknown cannot claim known launcher effects")


CommandResult = Ok[T] | Partial | Error


@dataclass(frozen=True, slots=True)
class OutcomeReceipt:
    reference: OutcomeReference
    command_id: str
    command_version: int
    state: ReceiptState
    recorded_at: datetime
    committed_effects: tuple[CommittedEffect, ...] = ()
    recovery_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_identity(self.command_id, self.command_version)
        if self.recorded_at.tzinfo is None:
            raise ValueError("launcher receipt time must be timezone-aware")
        if self.state is ReceiptState.KNOWN_PARTIAL and not self.committed_effects:
            raise ValueError("known-partial launcher receipts require effects")
        if self.state in {ReceiptState.NONE, ReceiptState.UNKNOWN} and self.committed_effects:
            raise ValueError("none/unknown launcher receipts cannot claim effects")
        _validate_recovery_paths(self.recovery_paths)
        if self.recovery_paths and self.state not in {
            ReceiptState.KNOWN_PARTIAL,
            ReceiptState.UNKNOWN,
        }:
            raise ValueError("recovery paths require partial or unknown receipt state")


class ReceiptWriter(Protocol):
    def write(self, receipt: OutcomeReceipt) -> None: ...


def no_effect_error(
    request_type,
    code: ErrorCode,
    message: str,
    field: str | None = None,
) -> Error:
    return Error(
        request_type.COMMAND_ID,
        request_type.COMMAND_VERSION,
        CommandError(code, message, RequestErrorDetails(field, message)),
    )


def _validate_identity(command_id: str, command_version: int) -> None:
    validate_command_id(command_id)
    if not isinstance(command_version, int) or command_version < 1:
        raise ValueError("launcher command version must be positive")


def _validate_recovery_paths(paths: tuple[str, ...]) -> None:
    if paths != tuple(sorted(set(paths))):
        raise ValueError("launcher recovery paths must be ordered and unique")
    if any(not isinstance(path, str) or not Path(path).is_absolute() for path in paths):
        raise ValueError("launcher recovery paths must be absolute")
