"""Bounded provider refresh without executor imports or repeated probes."""

from __future__ import annotations

from datetime import datetime
import time

import pytest

from _application.availability import CapabilityRefresher
from _application.types import Availability, SnapshotFreshness


NOW = datetime.fromisoformat("2026-08-09T12:00:00+10:00")


class _Clock:
    def now(self):
        return NOW


class _Tokens:
    def __init__(self):
        self.calls = []

    def next_token(self, previous_token):
        self.calls.append(previous_token)
        return f"snapshot-{len(self.calls)}"


class _Probe:
    def __init__(self, provider_id, result, *, timeout=0.05, delay=0.0):
        self.provider_id = provider_id
        self.result = result
        self.timeout_seconds = timeout
        self.delay = delay
        self.calls = 0

    def probe(self):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_refresh_deduplicates_providers_and_marks_missing_or_failed_unknown():
    available = _Probe("renderer", Availability.AVAILABLE)
    failed = _Probe("semantic", OSError("offline"))
    tokens = _Tokens()
    refresher = CapabilityRefresher(
        (available, failed),
        _Clock(),
        tokens,
        aggregate_timeout_seconds=0.1,
    )

    snapshot = refresher.refresh(
        ("semantic", "renderer", "renderer", "absent"),
        previous_token="old",
    )

    assert snapshot.freshness is SnapshotFreshness.FRESH
    assert snapshot.token == "snapshot-1"
    assert tokens.calls == ["old"]
    assert available.calls == 1
    assert failed.calls == 1
    assert snapshot.availability_of("renderer") is Availability.AVAILABLE
    assert snapshot.availability_of("semantic") is Availability.UNKNOWN
    assert snapshot.availability_of("absent") is Availability.UNKNOWN


def test_provider_specific_deadline_degrades_to_unknown():
    slow = _Probe("renderer", Availability.AVAILABLE, timeout=0.005, delay=0.02)
    refresher = CapabilityRefresher(
        (slow,),
        _Clock(),
        _Tokens(),
        aggregate_timeout_seconds=0.05,
    )

    snapshot = refresher.refresh(("renderer",))

    assert snapshot.availability_of("renderer") is Availability.UNKNOWN


def test_refresh_contract_rejects_duplicate_probes_and_overlong_deadlines():
    probe = _Probe("renderer", Availability.AVAILABLE)
    with pytest.raises(ValueError, match="unique"):
        CapabilityRefresher((probe, probe), _Clock(), _Tokens(), 0.1)
    with pytest.raises(ValueError, match="two seconds"):
        CapabilityRefresher((probe,), _Clock(), _Tokens(), 2.1)
