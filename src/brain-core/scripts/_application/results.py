"""Structural ``brain.command-result/1`` values."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Generic, Literal, TypeVar

from .receipts import CommittedEffect, OutcomeReference
from .runtime_status import RuntimeProgressDetails
from .types import DependencyTier, Locality, SnapshotFreshness, validate_command_id


RESULT_SCHEMA = "brain.command-result/1"
T = TypeVar("T")


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


@dataclass(frozen=True, slots=True)
class CommandArgument:
    name: str
    value: str | int | float | bool | None | tuple[str | int | float | bool | None, ...]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("command argument name must be non-empty")


@dataclass(frozen=True, slots=True)
class CommandNextAction:
    command_id: str
    arguments: tuple[CommandArgument, ...] = ()

    def __post_init__(self) -> None:
        validate_command_id(self.command_id)
        names = [argument.name for argument in self.arguments]
        if len(names) != len(set(names)):
            raise ValueError("command next action cannot repeat argument names")


@dataclass(frozen=True, slots=True)
class InstructionNextAction:
    instruction: str

    def __post_init__(self) -> None:
        if not self.instruction.strip():
            raise ValueError("instruction next action must be non-empty")


NextAction = CommandNextAction | InstructionNextAction


@dataclass(frozen=True, slots=True)
class CommandWarning:
    code: WarningCode
    message: str

    def __post_init__(self) -> None:
        if not self.message.strip():
            raise ValueError("command warning message must be non-empty")


@dataclass(frozen=True, slots=True)
class RequestErrorDetails:
    field: str | None
    reason: str

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("request error reason must be non-empty")


@dataclass(frozen=True, slots=True)
class CapabilityUnavailableDetails:
    required_tier: DependencyTier
    current_tier: DependencyTier
    locality: Locality
    missing: tuple[str, ...]
    snapshot_freshness: SnapshotFreshness
    recoverable: bool

    def __post_init__(self) -> None:
        if not self.missing:
            raise ValueError("capability-unavailable details must name missing capability or provider")
        if any(not item.strip() for item in self.missing):
            raise ValueError("missing capability or provider names must be non-empty")
        if len(self.missing) != len(set(self.missing)):
            raise ValueError("missing capability or provider names must be unique")


@dataclass(frozen=True, slots=True)
class AuthorityDeniedDetails:
    profile: str
    required: str

    def __post_init__(self) -> None:
        if not self.profile.strip() or not self.required.strip():
            raise ValueError("authority-denied details require profile and required authority")


@dataclass(frozen=True, slots=True)
class InternalErrorDetails:
    correlation_id: str

    def __post_init__(self) -> None:
        if not self.correlation_id.strip():
            raise ValueError("internal-error details require a correlation_id")


@dataclass(frozen=True, slots=True)
class OutcomeUnknownDetails:
    reference: OutcomeReference


ErrorDetails = (
    RequestErrorDetails
    | CapabilityUnavailableDetails
    | AuthorityDeniedDetails
    | InternalErrorDetails
    | OutcomeUnknownDetails
    | RuntimeProgressDetails
)


@dataclass(frozen=True, slots=True)
class CommandError:
    code: ErrorCode
    message: str
    details: ErrorDetails | None = None
    next_action: NextAction | None = None

    def __post_init__(self) -> None:
        if not self.message.strip():
            raise ValueError("command error message must be non-empty")


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
            raise ValueError("partial result must enumerate committed effects")


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
            if self.outcome_reference is None:
                raise ValueError("unknown-effect error requires an outcome reference")
            if self.retryable:
                raise ValueError("unknown-effect error cannot be retryable")
            if self.error.code is not ErrorCode.COMMAND_OUTCOME_UNKNOWN:
                raise ValueError("unknown effects require command_outcome_unknown")
            if not isinstance(self.error.details, OutcomeUnknownDetails):
                raise ValueError("unknown effects require typed outcome-unknown details")
            if self.error.details.reference != self.outcome_reference:
                raise ValueError("unknown-effect details and outcome reference must agree")
        elif self.outcome_reference is not None:
            raise ValueError("no-effect error cannot carry an outcome reference")
        elif self.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN:
            raise ValueError("command_outcome_unknown cannot claim effects none")


CommandResult = Ok[T] | Partial | Error


def _validate_identity(command_id: str, command_version: int) -> None:
    validate_command_id(command_id)
    if command_version < 1:
        raise ValueError("command result version must be positive")
