"""Bounded, deduplicated provider refresh for capability snapshots."""

from __future__ import annotations

from concurrent.futures import Future, wait
from dataclasses import dataclass, field
from datetime import datetime
from time import monotonic
from threading import BoundedSemaphore, Lock, Thread
from typing import Callable, Protocol

from .context import Capability, CapabilitySnapshot
from .types import Availability, SnapshotFreshness


_MAX_BACKGROUND_PROBES = 8
_probe_slots = BoundedSemaphore(_MAX_BACKGROUND_PROBES)


class CapabilityProbe(Protocol):
    @property
    def provider_id(self) -> str: ...

    @property
    def timeout_seconds(self) -> float: ...

    def probe(self) -> Availability: ...


class SnapshotClock(Protocol):
    def now(self) -> datetime: ...


class SnapshotTokenFactory(Protocol):
    def next_token(self, previous_token: str | None) -> str: ...


@dataclass(frozen=True, slots=True)
class CapabilityRefresher:
    probes: tuple[CapabilityProbe, ...]
    clock: SnapshotClock
    token_factory: SnapshotTokenFactory
    aggregate_timeout_seconds: float = 2.0
    monotonic_clock: Callable[[], float] = monotonic
    retained_snapshots: int = 8
    _snapshots: dict[str, CapabilitySnapshot] = field(
        default_factory=dict,
        init=False,
        repr=False,
        compare=False,
    )
    _snapshot_lock: Lock = field(
        default_factory=Lock,
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if not 0 < self.aggregate_timeout_seconds <= 2.0:
            raise ValueError("capability refresh aggregate timeout must be within two seconds")
        names = [probe.provider_id for probe in self.probes]
        if any(not name.strip() for name in names):
            raise ValueError("capability probes require non-empty provider_id values")
        if len(names) != len(set(names)):
            raise ValueError("capability probes must be unique by provider_id")
        for probe in self.probes:
            if not 0 < probe.timeout_seconds <= self.aggregate_timeout_seconds:
                raise ValueError("provider timeout must be positive and within aggregate timeout")
        if not 1 <= self.retained_snapshots <= 32:
            raise ValueError("capability snapshot retention must be between 1 and 32")

    def refresh(
        self,
        provider_ids: tuple[str, ...],
        *,
        previous_token: str | None = None,
    ) -> CapabilitySnapshot:
        requested = tuple(sorted(set(provider_ids)))
        if any(not provider_id.strip() for provider_id in requested):
            raise ValueError("provider refresh identifiers must be non-empty")
        by_name = {probe.provider_id: probe for probe in self.probes}
        availability = {
            provider_id: Availability.UNKNOWN
            for provider_id in requested
        }
        selected = [by_name[name] for name in requested if name in by_name]
        if selected:
            futures = {}
            for probe in selected:
                future = _submit_background_probe(
                    lambda probe=probe: self._timed_probe(probe)
                )
                if future is not None:
                    futures[future] = probe
            done, pending = wait(
                futures,
                timeout=min(
                    self.aggregate_timeout_seconds,
                    max(probe.timeout_seconds for probe in selected),
                ),
            )
            for future in done:
                probe = futures[future]
                try:
                    result, elapsed = future.result()
                except Exception:
                    result, elapsed = Availability.UNKNOWN, 0.0
                availability[probe.provider_id] = (
                    result
                    if elapsed <= probe.timeout_seconds
                    else Availability.UNKNOWN
                )
            for future in pending:
                future.cancel()

        observed_at = self.clock.now()
        if observed_at.tzinfo is None:
            raise ValueError("capability refresh clock must be timezone-aware")
        token = self.token_factory.next_token(previous_token)
        snapshot = CapabilitySnapshot(
            token=token,
            freshness=SnapshotFreshness.FRESH,
            observed_at=observed_at,
            capabilities=tuple(
                Capability(provider_id, availability[provider_id])
                for provider_id in requested
            ),
        )
        with self._snapshot_lock:
            self._snapshots[token] = snapshot
            while len(self._snapshots) > self.retained_snapshots:
                oldest = next(iter(self._snapshots))
                del self._snapshots[oldest]
        return snapshot

    def read(self, token: str) -> CapabilitySnapshot | None:
        if not isinstance(token, str) or not token.strip():
            raise ValueError("capability snapshot token must be non-empty")
        with self._snapshot_lock:
            return self._snapshots.get(token)

    def _timed_probe(self, probe: CapabilityProbe) -> tuple[Availability, float]:
        started = self.monotonic_clock()
        result = probe.probe()
        elapsed = self.monotonic_clock() - started
        if not isinstance(result, Availability):
            return Availability.UNKNOWN, elapsed
        return result, elapsed


def _submit_background_probe(call: Callable[[], object]) -> Future | None:
    """Run a probe behind a process-wide bounded daemon boundary."""

    if not _probe_slots.acquire(blocking=False):
        return None
    future = Future()

    def run() -> None:
        try:
            if not future.set_running_or_notify_cancel():
                return
            try:
                future.set_result(call())
            except BaseException as exc:
                future.set_exception(exc)
        finally:
            _probe_slots.release()

    thread = Thread(
        target=run,
        name="brain-capability-probe",
        daemon=True,
    )
    try:
        thread.start()
    except RuntimeError:
        _probe_slots.release()
        return None
    return future
