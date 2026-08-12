"""Transport-neutral active-grant and elevation contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol


class ElevationPolicy(str, Enum):
    AUTOMATIC = "automatic"
    EXTERNAL = "external"
    DENIED = "denied"


class AccessRequestState(str, Enum):
    GRANTED = "granted"
    ALREADY_ACTIVE = "already_active"
    APPROVAL_REQUIRED = "approval_required"
    DENIED = "denied"


@dataclass(frozen=True, slots=True)
class AccessLease:
    lease_id: str
    commands: tuple[str, ...]
    issued_at: str
    expires_at: str
    remaining_uses: int | None
    policy: ElevationPolicy

    def __post_init__(self) -> None:
        if not self.lease_id.strip():
            raise ValueError("access lease requires a lease_id")
        _identifiers(self.commands, "access lease commands")
        _timestamp(self.issued_at, "access lease issued_at")
        _timestamp(self.expires_at, "access lease expires_at")
        if self.remaining_uses is not None and (
            not isinstance(self.remaining_uses, int)
            or isinstance(self.remaining_uses, bool)
            or self.remaining_uses < 0
        ):
            raise ValueError("access lease remaining_uses is invalid")
        if not isinstance(self.policy, ElevationPolicy):
            raise ValueError("access lease policy is invalid")


@dataclass(frozen=True, slots=True)
class PendingAccessRequest:
    request_id: str
    commands: tuple[str, ...]
    requested_at: str
    expires_at: str

    def __post_init__(self) -> None:
        if not self.request_id.strip():
            raise ValueError("pending access request requires a request_id")
        _identifiers(self.commands, "pending access request commands")
        _timestamp(self.requested_at, "pending access request requested_at")
        _timestamp(self.expires_at, "pending access request expires_at")


@dataclass(frozen=True, slots=True)
class AccessSnapshot:
    principal: str
    ceiling_profile: str
    initial_profile: str | None
    policy: ElevationPolicy
    active_commands: tuple[str, ...]
    inactive_commands: tuple[str, ...]
    leases: tuple[AccessLease, ...]
    pending_requests: tuple[PendingAccessRequest, ...]

    def __post_init__(self) -> None:
        if not self.principal.strip() or not self.ceiling_profile.strip():
            raise ValueError("access snapshot requires principal and ceiling profile")
        if self.initial_profile is not None and not self.initial_profile.strip():
            raise ValueError("access snapshot initial profile is invalid")
        if not isinstance(self.policy, ElevationPolicy):
            raise ValueError("access snapshot policy is invalid")
        _identifiers(self.active_commands, "active commands", allow_empty=True)
        _identifiers(self.inactive_commands, "inactive commands", allow_empty=True)
        if set(self.active_commands) & set(self.inactive_commands):
            raise ValueError("active and inactive access commands overlap")
        if any(not isinstance(item, AccessLease) for item in self.leases):
            raise ValueError("access snapshot leases are invalid")
        if any(
            not isinstance(item, PendingAccessRequest)
            for item in self.pending_requests
        ):
            raise ValueError("access snapshot pending requests are invalid")


@dataclass(frozen=True, slots=True)
class AccessRequestDecision:
    state: AccessRequestState
    snapshot: AccessSnapshot
    lease: AccessLease | None = None
    pending_request: PendingAccessRequest | None = None
    denied_commands: tuple[str, ...] = ()
    changed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.state, AccessRequestState):
            raise ValueError("access request state is invalid")
        _identifiers(self.denied_commands, "denied commands", allow_empty=True)
        if self.state is AccessRequestState.GRANTED and self.lease is None:
            raise ValueError("granted access requires a lease")
        if (
            self.state is AccessRequestState.APPROVAL_REQUIRED
            and self.pending_request is None
        ):
            raise ValueError("approval-required access requires a pending request")
        if self.state is AccessRequestState.DENIED and not self.denied_commands:
            raise ValueError("denied access must enumerate denied commands")
        if self.state is not AccessRequestState.GRANTED and self.lease is not None:
            raise ValueError("only granted access can carry a lease")
        if (
            self.state is not AccessRequestState.APPROVAL_REQUIRED
            and self.pending_request is not None
        ):
            raise ValueError("only approval-required access can carry a pending request")
        if not isinstance(self.changed, bool):
            raise ValueError("access request changed must be a boolean")


@dataclass(frozen=True, slots=True)
class AccessReductionResult:
    snapshot: AccessSnapshot
    changed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, AccessSnapshot) or not isinstance(
            self.changed,
            bool,
        ):
            raise ValueError("access reduction result is invalid")


def _identifiers(
    values: tuple[str, ...],
    field: str,
    *,
    allow_empty: bool = False,
) -> None:
    if (
        (not values and not allow_empty)
        or any(not isinstance(value, str) or not value.strip() for value in values)
        or values != tuple(sorted(set(values)))
    ):
        raise ValueError(f"{field} must be sorted, unique command identifiers")


def _timestamp(value: str, field: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a timezone-aware ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be a timezone-aware ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must be a timezone-aware ISO timestamp")


class AccessController(Protocol):
    def allows(self, command_id: str) -> bool: ...

    def consume(self, command_id: str) -> bool: ...

    def can_elevate(self, command_id: str) -> bool: ...

    def status(self) -> AccessSnapshot: ...

    def request(
        self,
        commands: tuple[str, ...],
        *,
        duration_seconds: int | None,
        use_count: int | None,
    ) -> AccessRequestDecision: ...

    def reduce(
        self,
        *,
        reset: bool,
        lease_ids: tuple[str, ...],
        commands: tuple[str, ...],
    ) -> AccessReductionResult: ...
