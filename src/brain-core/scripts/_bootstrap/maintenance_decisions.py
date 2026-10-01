"""Human maintenance decisions: claims and dismissals (DD-082, D11 to D13).

The only persistent maintenance state, and the one rule set for what a
claim or dismissal means. Readers (the pass, ``list``) treat expired, stale
or unmatched records as absent and never write; only the decision commands
prune, inside their own locked write. Both the Brain pass and the machine
pass use this module, so it stays launcher-safe.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
import json
from pathlib import Path
from types import MappingProxyType
from typing import Iterator, Mapping

from _bootstrap.file_lock import exclusive_file_lock
from _bootstrap.maintenance_findings import Disposition, FindingGroup


DECISIONS_SCHEMA = "brain.maintenance-decisions/1"
DECISIONS_NAME = "decisions.json"
DECISIONS_LOCK_NAME = "decisions.lock"
CLAIM_LEASE = timedelta(hours=1)
DISMISSAL_RETENTION = timedelta(days=30)
EXPIRED_CLAIM_RETENTION = timedelta(days=30)
LOCK_TIMEOUT = 2.0
MAX_KEY_CHARS = 64
MAX_ACTOR_BYTES = 128
MAX_REASON_CHARS = 512


class ItemState(str, Enum):
    """How one detected group stands against the recorded decisions."""

    OPEN = "open"
    HELD = "held"
    CLAIM_EXPIRED = "claim_expired"
    QUIET = "quiet"


class DecisionsUnreadable(ValueError):
    """The decisions file exists but cannot be trusted; an operator moves it aside."""

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"maintenance decisions file is unreadable ({reason}): {path}")
        self.path = path


@dataclass(frozen=True, slots=True)
class Claim:
    claimant: str
    expires_at: datetime

    def live(self, now: datetime) -> bool:
        return self.expires_at > now

    def retained(self, now: datetime) -> bool:
        return self.expires_at + EXPIRED_CLAIM_RETENTION > now


@dataclass(frozen=True, slots=True)
class Dismissal:
    fingerprint: str
    dismissed_by: str
    reason: str
    dismissed_at: datetime

    def retained(self, now: datetime) -> bool:
        return self.dismissed_at + DISMISSAL_RETENTION > now


@dataclass(frozen=True, slots=True)
class Decisions:
    claims: Mapping[str, Claim]
    dismissals: Mapping[str, Dismissal]

    def __post_init__(self) -> None:
        object.__setattr__(self, "claims", MappingProxyType(dict(self.claims)))
        object.__setattr__(self, "dismissals", MappingProxyType(dict(self.dismissals)))

    def with_claim(self, key: str, claim: Claim) -> "Decisions":
        return Decisions({**self.claims, key: claim}, self.dismissals)

    def without_claim(self, key: str) -> "Decisions":
        return Decisions({k: v for k, v in self.claims.items() if k != key}, self.dismissals)

    def with_dismissal(self, key: str, dismissal: Dismissal) -> "Decisions":
        return Decisions({k: v for k, v in self.claims.items() if k != key},
                         {**self.dismissals, key: dismissal})

    def held_by_other(self, key: str, actor: str, now: datetime) -> Claim | None:
        """The live claim that stops ``actor`` acting on ``key``, if any."""
        claim = self.claims.get(key)
        if claim is not None and claim.live(now) and claim.claimant != actor:
            return claim
        return None


EMPTY = Decisions({}, {})


@dataclass(frozen=True, slots=True)
class DecisionState:
    state: ItemState
    claimant: str | None = None
    expires_at: datetime | None = None
    dismissed_by: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class Refusal:
    """Why a decision was not recorded; each owner maps it to its own error type."""

    code: str
    field: str | None
    message: str

    def __post_init__(self) -> None:
        if self.code not in {"invalid_request", "conflict", "not_found"}:
            raise ValueError(f"unknown refusal code: {self.code}")


# ---------------------------------------------------------------------------
# Request field rules: one schema, both owners
# ---------------------------------------------------------------------------

def validate_key(value: object, field: str = "key") -> None:
    """Keys are opaque identifiers handed back by ``list``; an unknown one is simply not found."""
    if (not isinstance(value, str) or not value.strip() or len(value) > MAX_KEY_CHARS
            or any(char.isspace() or not char.isprintable() for char in value)):
        raise ValueError(f"{field} must be a non-empty identifier of at most {MAX_KEY_CHARS} characters")


def validate_actor(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > MAX_ACTOR_BYTES:
        raise ValueError(f"{field} must be a non-empty string of at most {MAX_ACTOR_BYTES} bytes")


def validate_reason(value: object) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_REASON_CHARS:
        raise ValueError(f"reason must be a non-empty string of at most {MAX_REASON_CHARS} characters")


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _parse_time(value: object, path: Path, field: str) -> datetime:
    if not isinstance(value, str):
        raise DecisionsUnreadable(path, f"{field} is not a timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise DecisionsUnreadable(path, f"{field} is not a timestamp") from exc
    if parsed.tzinfo is None:
        raise DecisionsUnreadable(path, f"{field} is not timezone-aware")
    return parsed


def _text(value: object, path: Path, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DecisionsUnreadable(path, f"{field} is not a non-empty string")
    return value


def read_decisions(path: str | Path) -> Decisions:
    """Return the recorded decisions; a missing file is empty."""
    target = Path(path)
    if target.is_symlink():
        raise DecisionsUnreadable(target, "symlink")
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return EMPTY
    except OSError as exc:
        raise DecisionsUnreadable(target, str(exc)) from exc
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise DecisionsUnreadable(target, "invalid JSON") from exc
    if not isinstance(raw, dict) or raw.get("schema") != DECISIONS_SCHEMA:
        raise DecisionsUnreadable(target, "unknown schema")
    claims_raw = raw.get("claims", {})
    dismissals_raw = raw.get("dismissals", {})
    if not isinstance(claims_raw, dict) or not isinstance(dismissals_raw, dict):
        raise DecisionsUnreadable(target, "claims and dismissals must be objects")
    claims = {}
    for key, item in claims_raw.items():
        if not isinstance(item, dict):
            raise DecisionsUnreadable(target, f"claim {key} is not an object")
        claims[_text(key, target, "claim key")] = Claim(
            _text(item.get("claimant"), target, "claimant"),
            _parse_time(item.get("expires_at"), target, "expires_at"),
        )
    dismissals = {}
    for key, item in dismissals_raw.items():
        if not isinstance(item, dict):
            raise DecisionsUnreadable(target, f"dismissal {key} is not an object")
        dismissals[_text(key, target, "dismissal key")] = Dismissal(
            _text(item.get("fingerprint"), target, "fingerprint"),
            _text(item.get("dismissed_by"), target, "dismissed_by"),
            _text(item.get("reason"), target, "reason"),
            _parse_time(item.get("dismissed_at"), target, "dismissed_at"),
        )
    return Decisions(claims, dismissals)


def write_decisions(path: str | Path, decisions: Decisions) -> None:
    """Write atomically; the caller holds the decisions lock."""
    from _common._filesystem import safe_write_json

    payload = {
        "schema": DECISIONS_SCHEMA,
        "claims": {
            key: {"claimant": claim.claimant, "expires_at": claim.expires_at.isoformat()}
            for key, claim in sorted(decisions.claims.items())
        },
        "dismissals": {
            key: {
                "fingerprint": item.fingerprint,
                "dismissed_by": item.dismissed_by,
                "reason": item.reason,
                "dismissed_at": item.dismissed_at.isoformat(),
            }
            for key, item in sorted(decisions.dismissals.items())
        },
    }
    safe_write_json(Path(path), payload, follow_symlinks=False)


@contextmanager
def locked_decisions(lock_path: str | Path, *, timeout: float = LOCK_TIMEOUT) -> Iterator[None]:
    """Serialise decision writes; raises ``MutationLockError`` when busy."""
    with exclusive_file_lock(Path(lock_path), timeout=timeout, follow_symlinks=False):
        yield


def prune(decisions: Decisions, now: datetime) -> Decisions:
    """Drop dismissals past retention and claims expired past retention."""
    claims = {key: claim for key, claim in decisions.claims.items() if claim.retained(now)}
    dismissals = {key: item for key, item in decisions.dismissals.items() if item.retained(now)}
    return Decisions(claims, dismissals)


# ---------------------------------------------------------------------------
# Reading decisions against detection
# ---------------------------------------------------------------------------

def apply_decisions(
    groups,
    decisions: Decisions,
    now: datetime,
    *,
    never_held: frozenset[str] = frozenset(),
) -> dict[str, DecisionState]:
    """Mark each detected group open, held, claim-expired or quiet.

    A dismissal only quiets a judgement group whose fingerprint still matches
    and whose retention has not lapsed. A claim never holds a never-held
    family, and a claim past its own retention is absent, as ``prune`` would
    make it. Records for keys that are not detected are ignored.
    """
    states: dict[str, DecisionState] = {}
    for group in groups:
        dismissal = decisions.dismissals.get(group.key)
        if (dismissal is not None and group.disposition is Disposition.JUDGEMENT
                and dismissal.fingerprint == group.fingerprint and dismissal.retained(now)):
            states[group.key] = DecisionState(ItemState.QUIET, dismissed_by=dismissal.dismissed_by,
                                              reason=dismissal.reason)
            continue
        claim = decisions.claims.get(group.key)
        if claim is None or group.scope in never_held or not claim.retained(now):
            states[group.key] = DecisionState(ItemState.OPEN)
        elif claim.live(now):
            states[group.key] = DecisionState(ItemState.HELD, claim.claimant, claim.expires_at)
        else:
            states[group.key] = DecisionState(ItemState.CLAIM_EXPIRED, claim.claimant, claim.expires_at)
    return states


# ---------------------------------------------------------------------------
# The decision rules (D12, D13)
# ---------------------------------------------------------------------------

def decide_claim(group: FindingGroup, decisions: Decisions, now: datetime, *, claimant: str,
                 never_held: frozenset[str] = frozenset()) -> tuple[Decisions | None, Refusal | None]:
    """Claim for one lease; re-claiming extends, another claimant waits for expiry."""
    if group.scope in never_held:
        return None, Refusal("invalid_request", "key",
                             f"The {group.scope} family is never held: detection depends on it, so the pass always repairs it.")
    holder = decisions.held_by_other(group.key, claimant, now)
    if holder is not None:
        return None, Refusal("conflict", "key", f"{holder.claimant!r} holds this finding until {holder.expires_at.isoformat()}.")
    return decisions.with_claim(group.key, Claim(claimant, now + CLAIM_LEASE)), None


def decide_dismiss(group: FindingGroup, decisions: Decisions, now: datetime, *, actor: str, reason: str,
                   expected_fingerprint: str) -> tuple[Decisions | None, Refusal | None]:
    """Dismiss a judgement finding at its current evidence; the claimant's own claim retires."""
    if group.disposition is Disposition.AUTOMATIC:
        return None, Refusal("invalid_request", "key",
                             "Automatic families are never dismissible: their fingerprint never changes, so a dismissal "
                             "would silence the family for the whole retention period. Claim it to hold it instead.")
    if group.fingerprint != expected_fingerprint:
        return None, Refusal("conflict", "expected_fingerprint",
                             f"The finding's evidence changed: current fingerprint is {group.fingerprint}; re-read it before dismissing.")
    holder = decisions.held_by_other(group.key, actor, now)
    if holder is not None:
        return None, Refusal("conflict", "actor",
                             f"{holder.claimant!r} holds this finding until {holder.expires_at.isoformat()}; only the claimant may dismiss it.")
    return decisions.with_dismissal(group.key, Dismissal(group.fingerprint, actor, reason, now)), None


def decide_release(group: FindingGroup, decisions: Decisions, now: datetime, *, actor: str) -> tuple[Decisions | None, Refusal | None]:
    """Release a claim: the claimant while it is live, anyone after expiry."""
    if group.key not in decisions.claims:
        return None, Refusal("not_found", "key", "This finding is not claimed.")
    holder = decisions.held_by_other(group.key, actor, now)
    if holder is not None:
        return None, Refusal("conflict", "actor",
                             f"{holder.claimant!r} holds this finding until {holder.expires_at.isoformat()}; only the claimant may release it.")
    return decisions.without_claim(group.key), None
