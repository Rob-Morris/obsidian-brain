"""Outcome receipt values and persistence ports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol


class ReceiptState(str, Enum):
    NONE = "none"
    COMMITTED = "committed"
    KNOWN_PARTIAL = "known_partial"
    UNKNOWN = "unknown"


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
        if not self.command_id.strip():
            raise ValueError("outcome receipt requires a command_id")
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
