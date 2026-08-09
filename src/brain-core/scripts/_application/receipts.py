"""Outcome receipt values and persistence ports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from threading import RLock
from typing import Protocol

from .types import validate_command_id


class ReceiptState(str, Enum):
    NONE = "none"
    COMMITTED = "committed"
    KNOWN_PARTIAL = "known_partial"
    UNKNOWN = "unknown"


class ReceiptLookupState(str, Enum):
    FOUND = "found"
    STILL_UNKNOWN = "still_unknown"


@dataclass(frozen=True, slots=True)
class OutcomeReference:
    invocation_id: str

    def __post_init__(self) -> None:
        if not self.invocation_id.strip():
            raise ValueError("outcome reference requires a non-empty invocation_id")


@dataclass(frozen=True, slots=True)
class CommittedEffect:
    kind: str
    subject: str

    def __post_init__(self) -> None:
        if not self.kind.strip() or not self.subject.strip():
            raise ValueError("committed effects require non-empty kind and subject")


@dataclass(frozen=True, slots=True)
class OutcomeReceipt:
    reference: OutcomeReference
    command_id: str
    command_version: int
    state: ReceiptState
    recorded_at: datetime
    committed_effects: tuple[CommittedEffect, ...] = ()

    def __post_init__(self) -> None:
        validate_command_id(self.command_id)
        if self.command_version < 1:
            raise ValueError("outcome receipt command_version must be positive")
        if self.recorded_at.tzinfo is None:
            raise ValueError("outcome receipt recorded_at must be timezone-aware")
        if self.state is ReceiptState.KNOWN_PARTIAL and not self.committed_effects:
            raise ValueError("known-partial receipts must enumerate committed effects")
        if self.state in {ReceiptState.NONE, ReceiptState.UNKNOWN} and self.committed_effects:
            raise ValueError(f"{self.state.value} receipts cannot claim committed effects")


class ReceiptWriter(Protocol):
    def write(self, receipt: OutcomeReceipt) -> None: ...


class ReceiptReader(Protocol):
    def read(self, reference: OutcomeReference) -> OutcomeReceipt | None: ...


class ReceiptClock(Protocol):
    def now(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class ReceiptLookup:
    """A lookup that never turns receipt absence into proof of no effects."""

    reference: OutcomeReference
    state: ReceiptLookupState
    receipt: OutcomeReceipt | None = None

    def __post_init__(self) -> None:
        if self.state is ReceiptLookupState.FOUND and self.receipt is None:
            raise ValueError("found receipt lookup requires a receipt")
        if self.state is ReceiptLookupState.STILL_UNKNOWN and self.receipt is not None:
            raise ValueError("still-unknown receipt lookup cannot carry a receipt")
        if self.receipt is not None and self.receipt.reference != self.reference:
            raise ValueError("receipt lookup reference must match its receipt")


@dataclass(frozen=True, slots=True)
class ReceiptPolicy:
    retention: timedelta = timedelta(days=7)
    max_records: int = 10_000

    def __post_init__(self) -> None:
        if self.retention <= timedelta(0):
            raise ValueError("receipt retention must be positive")
        if self.max_records < 1:
            raise ValueError("receipt max_records must be positive")


class MemoryReceiptStore:
    """Bounded process-local receipt seam for contracts and adapter composition.

    Receipts deliberately retain only command identity, outcome state, time and
    compact committed-effect references. Request bodies, result payloads,
    credentials and provider values are not accepted by the receipt model.
    Durable storage can implement the same reader/writer ports in Phase 5.
    """

    def __init__(self, clock: ReceiptClock, policy: ReceiptPolicy | None = None) -> None:
        self._clock = clock
        self._policy = policy or ReceiptPolicy()
        self._records: dict[str, OutcomeReceipt] = {}
        self._lock = RLock()

    def write(self, receipt: OutcomeReceipt) -> None:
        with self._lock:
            self._cleanup_locked(self._clock.now())
            key = receipt.reference.invocation_id
            existing = self._records.get(key)
            if existing is not None and existing != receipt:
                raise ValueError("invocation receipt is immutable once recorded")
            self._records[key] = receipt
            self._trim_locked()

    def read(self, reference: OutcomeReference) -> OutcomeReceipt | None:
        with self._lock:
            self._cleanup_locked(self._clock.now())
            return self._records.get(reference.invocation_id)

    def lookup(self, reference: OutcomeReference) -> ReceiptLookup:
        receipt = self.read(reference)
        if receipt is None:
            return ReceiptLookup(reference, ReceiptLookupState.STILL_UNKNOWN)
        return ReceiptLookup(reference, ReceiptLookupState.FOUND, receipt)

    def cleanup(self) -> int:
        with self._lock:
            return self._cleanup_locked(self._clock.now())

    def _cleanup_locked(self, now: datetime) -> int:
        if now.tzinfo is None:
            raise ValueError("receipt cleanup clock must be timezone-aware")
        cutoff = now - self._policy.retention
        expired = [
            key
            for key, receipt in self._records.items()
            if receipt.recorded_at < cutoff
        ]
        for key in expired:
            del self._records[key]
        return len(expired)

    def _trim_locked(self) -> None:
        overflow = len(self._records) - self._policy.max_records
        if overflow <= 0:
            return
        oldest = sorted(
            self._records,
            key=lambda key: (
                self._records[key].recorded_at,
                self._records[key].reference.invocation_id,
            ),
        )[:overflow]
        for key in oldest:
            del self._records[key]
