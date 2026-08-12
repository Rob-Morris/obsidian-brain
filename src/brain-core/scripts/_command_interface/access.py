"""Principal-scoped active grants and bounded elevation leases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Mapping
import uuid

from _application.access_contracts import (
    AccessLease,
    AccessReductionResult,
    AccessRequestDecision,
    AccessRequestState,
    AccessSnapshot,
    ElevationPolicy,
    PendingAccessRequest,
)
from _application.types import validate_command_id


STATE_SCHEMA = "brain.access-state/1"
LOCAL_REL = Path(".brain/local")
STATE_REL = Path(".brain/local/access-state.json")
LOCK_REL = Path(".brain/local/access-state.lock")
DEFAULT_LEASE_SECONDS = 900
MAX_LEASE_SECONDS = 3600
DEFAULT_PENDING_SECONDS = 900
MAX_USE_COUNT = 100
MAX_AUDIT_EVENTS = 200
MAX_LEASES_PER_PRINCIPAL = 100
MAX_PENDING_PER_PRINCIPAL = 100


class AccessConfigError(ValueError):
    """Access configuration cannot establish a safe active grant."""


@dataclass(frozen=True, slots=True)
class AccessPolicy:
    initial_profile: str
    elevation_policy: ElevationPolicy
    default_lease_seconds: int
    max_lease_seconds: int
    pending_seconds: int
    max_use_count: int


@dataclass(frozen=True, slots=True)
class ExternalApprovalTarget:
    principal: str
    request_id: str
    commands: tuple[str, ...]


class FileAccessController:
    """Resolve and mutate leases under one selected-Brain local lock."""

    def __init__(
        self,
        *,
        vault_root: Path,
        principal: str,
        ceiling_profile: str,
        ceiling_commands: frozenset[str],
        initial_profile: str,
        initial_commands: frozenset[str],
        policy: AccessPolicy,
        clock,
    ) -> None:
        if not principal.strip() or not ceiling_profile.strip():
            raise AccessConfigError("access identity and ceiling profile are required")
        if not initial_commands <= ceiling_commands:
            raise AccessConfigError("initial access grant exceeds its authenticated ceiling")
        self._root = vault_root
        self._principal = principal
        self._ceiling_profile = ceiling_profile
        self._ceiling = ceiling_commands
        self._initial_profile = initial_profile
        self._initial = initial_commands
        self._policy = policy
        self._clock = clock

    @property
    def ceiling_commands(self) -> frozenset[str]:
        return self._ceiling

    def allows(self, command_id: str) -> bool:
        if command_id not in self._ceiling:
            return False
        if command_id in self._initial:
            return True
        now = self._now()
        return any(
            command_id in lease["commands"] and _lease_active(lease, now)
            for lease in self._principal_state(self._read_state())["leases"]
        )

    def consume(self, command_id: str) -> bool:
        if command_id not in self._ceiling:
            return False
        if command_id in self._initial:
            return True
        now = self._now()
        with self._locked_state() as transaction:
            state, principal = transaction.state, transaction.principal
            lease = next(
                (
                    item
                    for item in principal["leases"]
                    if command_id in item["commands"] and _lease_active(item, now)
                ),
                None,
            )
            if lease is None:
                return False
            remaining = lease.get("remaining_uses")
            if remaining is not None:
                lease["remaining_uses"] = remaining - 1
                transaction.changed = True
                self._audit(
                    state,
                    "lease_consumed",
                    command_id=command_id,
                    lease_id=lease["lease_id"],
                )
            return True

    def can_elevate(self, command_id: str) -> bool:
        return command_id in self._ceiling and command_id not in self._initial

    def status(self) -> AccessSnapshot:
        return self._snapshot(self._read_state())

    def request(
        self,
        commands: tuple[str, ...],
        *,
        duration_seconds: int | None,
        use_count: int | None,
    ) -> AccessRequestDecision:
        for command in commands:
            validate_command_id(command)
        denied = tuple(sorted(set(commands) - self._ceiling))
        if denied:
            return AccessRequestDecision(
                AccessRequestState.DENIED,
                self.status(),
                denied_commands=denied,
            )
        inactive = tuple(command for command in commands if not self.allows(command))
        if not inactive:
            return AccessRequestDecision(
                AccessRequestState.ALREADY_ACTIVE,
                self.status(),
            )
        if self._policy.elevation_policy is ElevationPolicy.DENIED:
            return AccessRequestDecision(
                AccessRequestState.DENIED,
                self.status(),
                denied_commands=inactive,
            )
        if duration_seconds is not None and duration_seconds > self._policy.max_lease_seconds:
            raise ValueError(
                f"duration_seconds exceeds the configured maximum of {self._policy.max_lease_seconds}"
            )
        if use_count is not None and use_count > self._policy.max_use_count:
            raise ValueError(
                f"use_count exceeds the configured maximum of {self._policy.max_use_count}"
            )
        now = self._now()
        if self._policy.elevation_policy is ElevationPolicy.EXTERNAL:
            with self._locked_state() as transaction:
                existing = next(
                    (
                        item
                        for item in transaction.principal["pending_requests"]
                        if tuple(item["commands"]) == inactive
                        and item.get("duration_seconds") == duration_seconds
                        and item.get("use_count") == use_count
                        and _timestamp(item["expires_at"]) > now
                    ),
                    None,
                )
                if existing is None:
                    if (
                        len(transaction.principal["pending_requests"])
                        >= MAX_PENDING_PER_PRINCIPAL
                    ):
                        raise ValueError("too many active access approval requests")
                    existing = {
                        "request_id": f"access-request-{uuid.uuid4()}",
                        "commands": list(inactive),
                        "requested_at": now.isoformat(),
                        "expires_at": (
                            now + timedelta(seconds=self._policy.pending_seconds)
                        ).isoformat(),
                        "duration_seconds": duration_seconds,
                        "use_count": use_count,
                    }
                    transaction.principal["pending_requests"].append(existing)
                    transaction.changed = True
                    self._audit(
                        transaction.state,
                        "approval_requested",
                        request_id=existing["request_id"],
                        commands=list(inactive),
                    )
                snapshot = self._snapshot(transaction.state)
                pending = _typed_pending(existing)
            return AccessRequestDecision(
                AccessRequestState.APPROVAL_REQUIRED,
                snapshot,
                pending_request=pending,
                changed=transaction.changed,
            )

        duration = duration_seconds or self._policy.default_lease_seconds
        with self._locked_state() as transaction:
            lease = self._issue_lease(
                transaction.state,
                transaction.principal,
                commands=inactive,
                now=now,
                duration_seconds=duration,
                use_count=use_count,
                policy=ElevationPolicy.AUTOMATIC,
                approver=None,
            )
            transaction.changed = True
            snapshot = self._snapshot(transaction.state)
        return AccessRequestDecision(
            AccessRequestState.GRANTED,
            snapshot,
            lease=_typed_lease(lease),
            changed=True,
        )

    def approve(self, request_id: str, *, approver: str) -> AccessLease:
        """Trusted external-approval seam; never exposed as an application request."""

        if not request_id.strip() or not approver.strip():
            raise ValueError("external approval requires request and approver identity")
        now = self._now()
        with self._locked_state() as transaction:
            pending = next(
                (
                    item
                    for item in transaction.principal["pending_requests"]
                    if item["request_id"] == request_id
                    and _timestamp(item["expires_at"]) > now
                ),
                None,
            )
            if pending is None:
                raise ValueError("access request is absent or expired")
            transaction.principal["pending_requests"].remove(pending)
            lease = self._issue_lease(
                transaction.state,
                transaction.principal,
                commands=tuple(pending["commands"]),
                now=now,
                duration_seconds=(
                    pending.get("duration_seconds")
                    or self._policy.default_lease_seconds
                ),
                use_count=pending.get("use_count"),
                policy=ElevationPolicy.EXTERNAL,
                approver=approver,
            )
            transaction.changed = True
        return _typed_lease(lease)

    def reduce(
        self,
        *,
        reset: bool,
        lease_ids: tuple[str, ...],
        commands: tuple[str, ...],
    ) -> AccessReductionResult:
        if sum(bool(item) for item in (reset, lease_ids, commands)) != 1:
            raise ValueError("access reduction requires exactly one scope")
        with self._locked_state() as transaction:
            before = json.dumps(transaction.principal, sort_keys=True)
            if reset:
                transaction.principal["leases"] = []
                transaction.principal["pending_requests"] = []
            elif lease_ids:
                selected = set(lease_ids)
                transaction.principal["leases"] = [
                    item
                    for item in transaction.principal["leases"]
                    if item["lease_id"] not in selected
                ]
            else:
                selected = set(commands)
                retained = []
                for item in transaction.principal["leases"]:
                    narrowed = sorted(set(item["commands"]) - selected)
                    if narrowed:
                        item["commands"] = narrowed
                        retained.append(item)
                transaction.principal["leases"] = retained
            transaction.changed = before != json.dumps(
                transaction.principal,
                sort_keys=True,
            )
            if transaction.changed:
                self._audit(
                    transaction.state,
                    "access_reduced",
                    reset=reset,
                    lease_ids=list(lease_ids),
                    commands=list(commands),
                )
            snapshot = self._snapshot(transaction.state)
        return AccessReductionResult(snapshot, transaction.changed)

    def _issue_lease(
        self,
        state: dict,
        principal: dict,
        *,
        commands: tuple[str, ...],
        now: datetime,
        duration_seconds: int,
        use_count: int | None,
        policy: ElevationPolicy,
        approver: str | None,
    ) -> dict:
        if len(principal["leases"]) >= MAX_LEASES_PER_PRINCIPAL:
            raise ValueError("too many active access leases")
        lease = {
            "lease_id": f"access-lease-{uuid.uuid4()}",
            "commands": list(commands),
            "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=duration_seconds)).isoformat(),
            "remaining_uses": use_count,
            "policy": policy.value,
        }
        principal["leases"].append(lease)
        self._audit(
            state,
            "lease_issued",
            lease_id=lease["lease_id"],
            commands=list(commands),
            policy=policy.value,
            approver=approver,
        )
        return lease

    def _snapshot(self, state: dict) -> AccessSnapshot:
        principal = self._principal_state(state)
        now = self._now()
        leases = tuple(
            _typed_lease(item)
            for item in principal["leases"]
            if _lease_active(item, now)
        )
        pending = tuple(
            _typed_pending(item)
            for item in principal["pending_requests"]
            if _timestamp(item["expires_at"]) > now
        )
        leased = {
            command for lease in leases for command in lease.commands
        }
        active = tuple(sorted(self._initial | leased))
        return AccessSnapshot(
            self._principal,
            self._ceiling_profile,
            self._initial_profile,
            self._policy.elevation_policy,
            active,
            tuple(sorted(self._ceiling - set(active))),
            leases,
            pending,
        )

    def _read_state(self) -> dict:
        return _read_state_file(self._root)

    def _principal_state(self, state: dict) -> dict:
        return state["principals"].get(
            self._principal,
            {"leases": [], "pending_requests": []},
        )

    def _now(self) -> datetime:
        value = self._clock.now()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("access clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)

    def _audit(self, state: dict, event: str, **details) -> None:
        state["audit"].append(
            {
                "event": event,
                "principal": self._principal,
                "at": self._now().isoformat(),
                "details": details,
            }
        )
        state["audit"] = state["audit"][-MAX_AUDIT_EVENTS:]

    def _locked_state(self):
        return _AccessTransaction(self)


class _AccessTransaction:
    def __init__(self, controller: FileAccessController) -> None:
        self.controller = controller
        self.state = None
        self.principal = None
        self.changed = False
        self._lock = None
        self._local = None

    def __enter__(self):
        from _common._file_lock import exclusive_file_lock

        self._local = _ensure_private_local_directory(self.controller._root)
        lock_path = self.controller._root / LOCK_REL
        if lock_path.is_symlink():
            raise OSError("access state lock cannot be a symlink")
        self._lock = exclusive_file_lock(lock_path, timeout=2.0)
        self._lock.__enter__()
        self.state = self.controller._read_state()
        self.principal = self.state["principals"].setdefault(
            self.controller._principal,
            {"leases": [], "pending_requests": []},
        )
        now = self.controller._now()
        leases = [
            item for item in self.principal["leases"] if _lease_active(item, now)
        ]
        pending = [
            item
            for item in self.principal["pending_requests"]
            if _timestamp(item["expires_at"]) > now
        ]
        if (
            len(leases) != len(self.principal["leases"])
            or len(pending) != len(self.principal["pending_requests"])
        ):
            self.principal["leases"] = leases
            self.principal["pending_requests"] = pending
            self.changed = True
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            if exc_type is None and self.changed:
                from _common import safe_write

                safe_write(
                    self.controller._root / STATE_REL,
                    json.dumps(self.state, ensure_ascii=False, sort_keys=True) + "\n",
                    bounds=self._local,
                    follow_symlinks=False,
                )
        finally:
            self._lock.__exit__(exc_type, exc, traceback)


def access_policy(config: Mapping[str, object]) -> AccessPolicy:
    vault = _mapping(config.get("vault"), "vault")
    defaults = _mapping(config.get("defaults"), "defaults")
    access = _mapping(vault.get("access"), "vault.access")
    local = _mapping(defaults.get("access"), "defaults.access")
    initial = local.get("initial_profile", "reader")
    policy_value = access.get("elevation_policy", "automatic")
    if not isinstance(initial, str) or not initial.strip():
        raise AccessConfigError("defaults.access.initial_profile must be non-empty")
    try:
        policy = ElevationPolicy(policy_value)
    except (TypeError, ValueError) as exc:
        raise AccessConfigError(
            "vault.access.elevation_policy must be automatic, external or denied"
        ) from exc
    default_seconds = _positive_int(
        access.get("default_lease_seconds", DEFAULT_LEASE_SECONDS),
        "vault.access.default_lease_seconds",
    )
    max_seconds = _positive_int(
        access.get("max_lease_seconds", MAX_LEASE_SECONDS),
        "vault.access.max_lease_seconds",
    )
    pending_seconds = _positive_int(
        access.get("pending_seconds", DEFAULT_PENDING_SECONDS),
        "vault.access.pending_seconds",
    )
    max_uses = _positive_int(
        access.get("max_use_count", MAX_USE_COUNT),
        "vault.access.max_use_count",
    )
    if default_seconds > max_seconds:
        raise AccessConfigError("default lease duration exceeds its maximum")
    return AccessPolicy(
        initial,
        policy,
        default_seconds,
        max_seconds,
        pending_seconds,
        max_uses,
    )


def compose_access_controller(
    *,
    vault_root: Path,
    config: Mapping[str, object],
    principal: str,
    ceiling_profile: str,
    ceiling_commands: frozenset[str],
    clock,
) -> FileAccessController:
    policy = access_policy(config)
    profiles = _mapping(_mapping(config.get("vault"), "vault").get("profiles"), "vault.profiles")
    initial_definition = profiles.get(policy.initial_profile)
    if initial_definition is None and policy.initial_profile != "reader":
        raise AccessConfigError(
            f"initial access profile '{policy.initial_profile}' does not exist"
        )
    if initial_definition is None:
        initial_commands = frozenset()
        initial_name = None
    else:
        definition = _mapping(
            initial_definition,
            f"vault.profiles.{policy.initial_profile}",
        )
        allowed = definition.get("allow")
        if not isinstance(allowed, list) or any(
            not isinstance(item, str) for item in allowed
        ):
            raise AccessConfigError("initial access profile has an invalid allow-list")
        initial_commands = frozenset(allowed) & ceiling_commands
        initial_name = policy.initial_profile
    return FileAccessController(
        vault_root=vault_root,
        principal=principal,
        ceiling_profile=ceiling_profile,
        ceiling_commands=ceiling_commands,
        initial_profile=initial_name or "ceiling-intersection",
        initial_commands=initial_commands,
        policy=policy,
        clock=clock,
    )


def resolve_external_approval(
    *,
    vault_root: Path,
    config: Mapping[str, object],
    request_id: str,
    approver_identity: str,
    approver_commands: frozenset[str],
    clock,
) -> ExternalApprovalTarget:
    """Validate one pending request for a separately authenticated approver."""

    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError("external approval requires a request_id")
    policy = access_policy(config)
    if policy.elevation_policy is not ElevationPolicy.EXTERNAL:
        raise ValueError("the selected Brain does not require external approval")
    now = clock.now()
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("access clock must return a timezone-aware datetime")
    now = now.astimezone(timezone.utc)
    state = _read_state_file(vault_root)
    matches = []
    for principal, principal_state in state["principals"].items():
        for pending in principal_state["pending_requests"]:
            if (
                pending["request_id"] == request_id
                and _timestamp(pending["expires_at"]) > now
            ):
                matches.append((principal, pending))
    if len(matches) != 1:
        raise ValueError("access request is absent or expired")
    principal, pending = matches[0]
    if principal == approver_identity:
        raise PermissionError("a principal cannot approve its own access request")
    commands = tuple(pending["commands"])
    denied = tuple(sorted(set(commands) - approver_commands))
    if denied:
        raise PermissionError(
            "approver profile does not cover requested commands: "
            + ", ".join(denied)
        )
    return ExternalApprovalTarget(principal, request_id, commands)


def approve_external_request(
    *,
    vault_root: Path,
    config: Mapping[str, object],
    target: ExternalApprovalTarget,
    approver_profile: str,
    approver_identity: str,
    approver_commands: frozenset[str],
    clock,
) -> AccessLease:
    """Issue a lease through the trusted, non-application approval boundary."""

    policy = access_policy(config)
    controller = FileAccessController(
        vault_root=vault_root,
        principal=target.principal,
        ceiling_profile=approver_profile,
        ceiling_commands=approver_commands,
        initial_profile="external-approval",
        initial_commands=frozenset(),
        policy=policy,
        clock=clock,
    )
    return controller.approve(target.request_id, approver=approver_identity)


def _empty_state() -> dict:
    return {"schema": STATE_SCHEMA, "principals": {}, "audit": []}


def _read_state_file(vault_root: Path) -> dict:
    if not _validate_existing_local_directory(vault_root):
        return _empty_state()
    path = vault_root / STATE_REL
    if not path.exists():
        return _empty_state()
    if path.is_symlink() or not path.is_file():
        raise OSError("access state is not a regular file")
    return _validate_state(json.loads(path.read_text(encoding="utf-8")))


def _ensure_private_local_directory(vault_root: Path) -> Path:
    current = vault_root
    for part in LOCAL_REL.parts:
        current = current / part
        if current.is_symlink():
            raise OSError(f"refusing symlinked access-state directory: {current}")
        if current.exists():
            if not current.is_dir():
                raise OSError(
                    f"access-state directory component is not a directory: {current}"
                )
        else:
            current.mkdir()
    if current.resolve() != current:
        raise OSError("access-state directory resolves outside its fixed location")
    return current


def _validate_existing_local_directory(vault_root: Path) -> bool:
    current = vault_root
    for part in LOCAL_REL.parts:
        current = current / part
        if current.is_symlink():
            raise OSError(f"refusing symlinked access-state directory: {current}")
        if not current.exists():
            return False
        if not current.is_dir():
            raise OSError(
                f"access-state directory component is not a directory: {current}"
            )
    if current.resolve() != current:
        raise OSError("access-state directory resolves outside its fixed location")
    return True


def _validate_state(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {"schema", "principals", "audit"}:
        raise ValueError("access state has an invalid shape")
    if value.get("schema") != STATE_SCHEMA:
        raise ValueError("access state has an invalid schema")
    if not isinstance(value["principals"], dict) or not isinstance(value["audit"], list):
        raise ValueError("access state collections are invalid")
    for principal, state in value["principals"].items():
        if (
            not isinstance(principal, str)
            or not principal.strip()
            or not isinstance(state, dict)
        ):
            raise ValueError("access principal state is invalid")
        if set(state) != {"leases", "pending_requests"}:
            raise ValueError("access principal state has unknown fields")
        if not isinstance(state["leases"], list) or not isinstance(
            state["pending_requests"], list
        ):
            raise ValueError("access principal state collections are invalid")
        if len(state["leases"]) > MAX_LEASES_PER_PRINCIPAL or len(
            state["pending_requests"]
        ) > MAX_PENDING_PER_PRINCIPAL:
            raise ValueError("access principal state exceeds its bounded capacity")
        for lease in state["leases"]:
            _typed_lease(lease)
        for pending in state["pending_requests"]:
            _typed_pending(pending)
    for event in value["audit"]:
        if not isinstance(event, dict) or set(event) != {
            "event",
            "principal",
            "at",
            "details",
        }:
            raise ValueError("access audit event has an invalid shape")
        if any(
            not isinstance(event[field], str) or not event[field].strip()
            for field in ("event", "principal")
        ) or not isinstance(event["details"], dict):
            raise ValueError("access audit event is invalid")
        _timestamp(event["at"])
    return value


def _typed_lease(value: Mapping[str, object]) -> AccessLease:
    required = {
        "lease_id",
        "commands",
        "issued_at",
        "expires_at",
        "remaining_uses",
        "policy",
    }
    if set(value) != required:
        raise ValueError("access lease has an invalid shape")
    lease_id = value["lease_id"]
    commands = value["commands"]
    issued_at = value["issued_at"]
    expires_at = value["expires_at"]
    remaining = value["remaining_uses"]
    if not isinstance(lease_id, str) or not lease_id.strip():
        raise ValueError("access lease id is invalid")
    if (
        not isinstance(commands, list)
        or not commands
        or any(not isinstance(item, str) for item in commands)
        or tuple(commands) != tuple(sorted(set(commands)))
    ):
        raise ValueError("access lease commands are invalid")
    if remaining is not None and (
        not isinstance(remaining, int) or isinstance(remaining, bool) or remaining < 0
    ):
        raise ValueError("access lease remaining uses are invalid")
    _timestamp(issued_at)
    _timestamp(expires_at)
    return AccessLease(
        lease_id,
        tuple(commands),
        issued_at,
        expires_at,
        remaining,
        ElevationPolicy(value["policy"]),
    )


def _typed_pending(value: Mapping[str, object]) -> PendingAccessRequest:
    required = {
        "request_id",
        "commands",
        "requested_at",
        "expires_at",
        "duration_seconds",
        "use_count",
    }
    if set(value) != required:
        raise ValueError("pending access request has an invalid shape")
    request_id = value["request_id"]
    commands = value["commands"]
    requested_at = value["requested_at"]
    expires_at = value["expires_at"]
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError("pending access request id is invalid")
    if (
        not isinstance(commands, list)
        or not commands
        or any(not isinstance(item, str) for item in commands)
        or tuple(commands) != tuple(sorted(set(commands)))
    ):
        raise ValueError("pending access request commands are invalid")
    duration = value["duration_seconds"]
    use_count = value["use_count"]
    for amount, name in ((duration, "duration"), (use_count, "use count")):
        if amount is not None and (
            not isinstance(amount, int) or isinstance(amount, bool) or amount < 1
        ):
            raise ValueError(f"pending access request {name} is invalid")
    _timestamp(requested_at)
    _timestamp(expires_at)
    return PendingAccessRequest(
        request_id,
        tuple(commands),
        requested_at,
        expires_at,
    )


def _lease_active(value: Mapping[str, object], now: datetime) -> bool:
    remaining = value.get("remaining_uses")
    return _timestamp(value["expires_at"]) > now and (
        remaining is None or remaining > 0
    )


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("access timestamp must be a string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("access timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise AccessConfigError(f"{field} must be a mapping")
    return value


def _positive_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise AccessConfigError(f"{field} must be a positive integer")
    return value
