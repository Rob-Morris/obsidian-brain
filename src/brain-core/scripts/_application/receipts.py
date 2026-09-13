"""Outcome receipt values and persistence ports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
import re
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


class ReceiptOwnershipError(ValueError):
    """The requested receipt is not owned by the authenticated caller context."""


def _bounded_identity(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > 128:
        raise ValueError(f"receipt {name} must contain 1–128 UTF-8 bytes")


@dataclass(frozen=True, slots=True)
class ReceiptOwnership:
    brain_id: str
    principal: str
    kind: str
    context_id: str | None = None

    def __post_init__(self) -> None:
        _bounded_identity(self.brain_id, "Brain identity")
        _bounded_identity(self.principal, "principal")
        if self.kind not in {"mcp-instance", "cli-job", "standalone"}:
            raise ValueError("receipt ownership kind is invalid")
        if self.kind == "standalone":
            if self.context_id is not None:
                raise ValueError("standalone receipts cannot carry reusable context ownership")
        else:
            _bounded_identity(self.context_id, "context identity")


@dataclass(frozen=True, slots=True)
class AdmissionIntent:
    """Durable attribution before entry; it does not prove execution happened."""

    reference: OutcomeReference
    command_id: str
    command_version: int
    recorded_at: datetime
    basis: str
    generation: str
    source: str
    grant_id: str | None = None
    operation_id: str | None = None
    operation_digest: str | None = None
    request_id: str | None = None

    def __post_init__(self) -> None:
        _bounded_identity(self.reference.invocation_id, "invocation identity")
        validate_command_id(self.command_id)
        if type(self.command_version) is not int or self.command_version < 1:
            raise ValueError("admission command version must be a positive integer")
        if self.recorded_at.tzinfo is None:
            raise ValueError("admission timestamp must be timezone-aware")
        if self.basis not in {"initial", "command", "operation"}:
            raise ValueError("admission basis is invalid")
        if self.source not in {"host-request", "cli-request"}:
            raise ValueError("admission source is invalid")
        _bounded_identity(self.generation, "generation")
        for name in ("grant_id", "operation_id", "request_id"):
            value = getattr(self, name)
            if value is not None:
                _bounded_identity(value, name)
        if self.basis == "initial" and self.grant_id is not None:
            raise ValueError("initial admission cannot claim an exceptional grant")
        if self.basis != "initial" and self.grant_id is None:
            raise ValueError("exceptional admission requires grant identity")
        if self.basis == "operation" and (self.operation_id is None or self.operation_digest is None):
            raise ValueError("specific admission requires operation identity and digest")
        if self.operation_digest is not None and (
            not isinstance(self.operation_digest, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", self.operation_digest) is None
        ):
            raise ValueError("admission operation digest is invalid")


class ExecutionState(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class InvocationOutcome:
    """Execution completion is independent of whether domain effects occurred."""

    receipt: OutcomeReceipt
    execution: ExecutionState

    def __post_init__(self) -> None:
        if not isinstance(self.receipt, OutcomeReceipt) or not isinstance(self.execution, ExecutionState):
            raise ValueError("invocation outcome requires typed receipt and execution state")
        _bounded_identity(self.reference.invocation_id, "invocation identity")
        allowed = {
            ExecutionState.SUCCEEDED: {ReceiptState.NONE, ReceiptState.COMMITTED},
            ExecutionState.FAILED: {ReceiptState.NONE},
            ExecutionState.PARTIAL: {ReceiptState.KNOWN_PARTIAL},
            ExecutionState.UNKNOWN: {ReceiptState.NONE, ReceiptState.UNKNOWN},
        }
        if self.receipt.state not in allowed[self.execution]:
            raise ValueError("execution state contradicts its effect receipt")

    @property
    def reference(self) -> OutcomeReference:
        return self.receipt.reference

    @property
    def command_id(self) -> str:
        return self.receipt.command_id

    @property
    def command_version(self) -> int:
        return self.receipt.command_version

    @property
    def recorded_at(self) -> datetime:
        return self.receipt.recorded_at


@dataclass(frozen=True, slots=True)
class OwnedReceiptLookup:
    """FOUND means a final record exists; its execution can still be unknown."""

    reference: OutcomeReference
    intent: AdmissionIntent | None = None
    outcome: InvocationOutcome | None = None

    def __post_init__(self) -> None:
        if self.intent is not None and self.intent.reference != self.reference:
            raise ValueError("admission intent reference differs from lookup")
        if self.outcome is not None:
            if self.intent is None or (
                self.outcome.reference, self.outcome.command_id, self.outcome.command_version
            ) != (self.reference, self.intent.command_id, self.intent.command_version):
                raise ValueError("final outcome differs from its admission intent")
            if self.outcome.recorded_at < self.intent.recorded_at:
                raise ValueError("final outcome predates admission intent")

    @property
    def state(self) -> ReceiptLookupState:
        return ReceiptLookupState.FOUND if self.outcome is not None else ReceiptLookupState.STILL_UNKNOWN


class OwnedReceiptPort(Protocol):
    """A trusted ownership-bound port; IDs select receipts, never authorise reads."""

    def begin(self, intent: AdmissionIntent) -> None: ...

    def finalise(self, outcome: InvocationOutcome) -> None: ...

    def read(self, reference: OutcomeReference) -> OwnedReceiptLookup: ...


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
