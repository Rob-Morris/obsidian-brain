"""Mapping boundary for the shared bootstrap runtime-status document."""

from __future__ import annotations

from typing import Mapping

from ..runtime_status import (
    RuntimeComponentState,
    RuntimeComponents,
    RuntimeErrorComponent,
    RuntimeState,
    RuntimeStatusError,
    RuntimeStatusSnapshot,
    SemanticComponentState,
)


def typed_snapshot(value: Mapping[str, object]) -> RuntimeStatusSnapshot:
    components = value.get("components")
    if not isinstance(components, Mapping):
        raise ValueError("runtime status components must be an object")
    raw_error = value.get("last_error")
    error = None
    if raw_error is not None:
        if not isinstance(raw_error, Mapping):
            raise ValueError("runtime status last_error must be an object")
        component = raw_error.get("component")
        error = RuntimeStatusError(
            str(raw_error.get("code", "")),
            None if component is None else RuntimeErrorComponent(component),
            str(raw_error.get("message", "")),
            bool(raw_error.get("retryable")),
        )
    phase = value.get("phase")
    retry_after_ms = value.get("retry_after_ms")
    started_at = value.get("started_at")
    if phase is not None and not isinstance(phase, str):
        raise ValueError("runtime status phase must be a string")
    if retry_after_ms is not None and not isinstance(retry_after_ms, int):
        raise ValueError("runtime status retry_after_ms must be an integer")
    if started_at is not None and not isinstance(started_at, str):
        raise ValueError("runtime status started_at must be a string")
    return RuntimeStatusSnapshot(
        RuntimeState(value.get("state")),
        phase,
        RuntimeComponents(
            RuntimeComponentState(components.get("router")),
            RuntimeComponentState(components.get("lexical")),
            SemanticComponentState(components.get("semantic")),
        ),
        retry_after_ms,
        started_at,
        error,
    )
