"""Shared typed runtime-readiness value used by bootstrap-facing commands."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal


class RuntimeState(str, Enum):
    COLD = "cold"
    WARMING = "warming"
    READY = "ready"
    FAILED = "failed"


class RuntimeComponentState(str, Enum):
    NOT_STARTED = "not_started"
    WARMING = "warming"
    READY = "ready"
    FAILED = "failed"


class SemanticComponentState(str, Enum):
    DISABLED = "disabled"
    NOT_STARTED = "not_started"
    WARMING = "warming"
    READY = "ready"
    DEFERRED = "deferred"
    FAILED = "failed"


class RuntimeErrorComponent(str, Enum):
    ROUTER = "router"
    LEXICAL = "lexical"
    SEMANTIC = "semantic"
    RUNTIME = "runtime"


@dataclass(frozen=True, slots=True)
class RuntimeComponents:
    router: RuntimeComponentState
    lexical: RuntimeComponentState
    semantic: SemanticComponentState


@dataclass(frozen=True, slots=True)
class RuntimeStatusError:
    code: str
    component: RuntimeErrorComponent | None
    message: str
    retryable: bool

    def __post_init__(self) -> None:
        if not self.code.strip() or not self.message.strip():
            raise ValueError("runtime status errors require a code and message")


@dataclass(frozen=True, slots=True)
class RuntimeStatusSnapshot:
    state: RuntimeState
    phase: str | None
    components: RuntimeComponents
    retry_after_ms: int | None
    started_at: str | None
    last_error: RuntimeStatusError | None
    schema: Literal["brain.runtime-status/1"] = "brain.runtime-status/1"

    def __post_init__(self) -> None:
        if self.phase is not None and not self.phase.strip():
            raise ValueError("runtime status phase must be non-empty when present")
        if self.retry_after_ms is not None and self.retry_after_ms < 0:
            raise ValueError("runtime status retry_after_ms cannot be negative")
        if self.state is RuntimeState.WARMING and self.retry_after_ms is None:
            raise ValueError("warming runtime status requires retry guidance")
        if self.state is not RuntimeState.WARMING and self.retry_after_ms is not None:
            raise ValueError("only warming runtime status carries retry guidance")
        if self.started_at is not None:
            parsed = datetime.fromisoformat(self.started_at)
            if parsed.tzinfo is None:
                raise ValueError("runtime status started_at must include a timezone")


@dataclass(frozen=True, slots=True)
class RuntimeProgressDetails:
    runtime_status: RuntimeStatusSnapshot
