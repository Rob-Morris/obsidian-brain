"""Bounded provider refresh without executor imports or repeated probes."""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import subprocess
import sys
import threading
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
    assert refresher.read("snapshot-1") == snapshot


def test_refresh_retains_a_bounded_page_snapshot_window():
    refresher = CapabilityRefresher(
        (),
        _Clock(),
        _Tokens(),
        aggregate_timeout_seconds=0.1,
        retained_snapshots=2,
    )

    first = refresher.refresh(())
    second = refresher.refresh(())
    third = refresher.refresh(())

    assert refresher.read(first.token) is None
    assert refresher.read(second.token) == second
    assert refresher.read(third.token) == third


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


def test_timed_out_probes_are_daemonised_and_globally_bounded():
    slow = _Probe("renderer", Availability.AVAILABLE, timeout=0.01, delay=0.5)
    refresher = CapabilityRefresher(
        (slow,),
        _Clock(),
        _Tokens(),
        aggregate_timeout_seconds=0.1,
    )

    started = time.monotonic()
    snapshots = [refresher.refresh(("renderer",)) for _ in range(12)]
    elapsed = time.monotonic() - started
    workers = [
        thread
        for thread in threading.enumerate()
        if thread.name == "brain-capability-probe"
    ]

    assert elapsed < 0.25
    assert all(
        snapshot.availability_of("renderer") is Availability.UNKNOWN
        for snapshot in snapshots
    )
    assert len(workers) <= 8
    assert workers and all(worker.daemon for worker in workers)


def test_timed_out_probe_does_not_delay_process_exit():
    scripts = Path(__file__).resolve().parents[2] / "src" / "brain-core" / "scripts"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(scripts)
    program = """
from datetime import datetime
import time
from _application.availability import CapabilityRefresher
from _application.types import Availability

class Clock:
    def now(self):
        return datetime.fromisoformat("2026-08-11T12:00:00+10:00")

class Tokens:
    def next_token(self, previous_token):
        return "snapshot"

class Probe:
    provider_id = "renderer"
    timeout_seconds = 0.05
    def probe(self):
        time.sleep(2)
        return Availability.AVAILABLE

CapabilityRefresher(
    (Probe(),), Clock(), Tokens(), aggregate_timeout_seconds=0.1
).refresh(("renderer",))
"""

    completed = subprocess.run(
        [sys.executable, "-c", program],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        timeout=0.8,
    )

    assert completed.returncode == 0, completed.stderr
