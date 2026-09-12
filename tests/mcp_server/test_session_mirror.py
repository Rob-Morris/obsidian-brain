"""MCP session-mirror persistence stays bounded and coalescing."""

from __future__ import annotations

import threading
import time

import pytest

import session
from brain_mcp import server
from brain_mcp._session_mirror import SessionMirrorWorker


def _join_worker(worker: SessionMirrorWorker) -> None:
    thread = worker._thread
    assert thread is not None
    thread.join(timeout=1)
    assert not thread.is_alive()


def test_worker_returns_immediately_and_coalesces_to_latest_model(
    tmp_path,
    monkeypatch,
):
    entered = threading.Event()
    release = threading.Event()
    second_done = threading.Event()
    calls = []

    def persist(model, _root):
        calls.append(model["revision"])
        if len(calls) == 1:
            entered.set()
            assert release.wait(timeout=2)
        else:
            second_done.set()

    monkeypatch.setattr(session, "persist_session_markdown", persist)
    worker = SessionMirrorWorker(tmp_path.resolve(), drain_timeout=1)
    worker.publish({"revision": 1})
    assert entered.wait(timeout=1)

    started = time.monotonic()
    worker.publish({"revision": 2})
    worker.publish({"revision": 3})
    elapsed = time.monotonic() - started

    assert elapsed < 0.1
    release.set()
    assert second_done.wait(timeout=1)
    assert worker.close() is True
    assert calls == [1, 3]


def test_close_drains_the_newest_accepted_model_without_evicting_it(
    tmp_path,
    monkeypatch,
):
    entered = threading.Event()
    release = threading.Event()
    newest_done = threading.Event()
    calls = []

    def persist(model, _root):
        calls.append(model["revision"])
        if len(calls) == 1:
            entered.set()
            assert release.wait(timeout=2)
        else:
            newest_done.set()

    monkeypatch.setattr(session, "persist_session_markdown", persist)
    worker = SessionMirrorWorker(tmp_path.resolve(), drain_timeout=0.01)
    worker.publish({"revision": 1})
    assert entered.wait(timeout=1)
    worker.publish({"revision": 2})
    worker.publish({"revision": 3})

    assert worker.close() is False
    release.set()
    assert newest_done.wait(timeout=1)
    _join_worker(worker)
    assert worker.close() is True
    assert calls == [1, 3]


def test_close_deadline_is_bounded_and_observable_when_storage_stalls(
    tmp_path,
    monkeypatch,
):
    entered = threading.Event()
    release = threading.Event()

    def persist(_model, _root):
        entered.set()
        assert release.wait(timeout=2)

    monkeypatch.setattr(session, "persist_session_markdown", persist)
    worker = SessionMirrorWorker(tmp_path.resolve(), drain_timeout=0.01)
    worker.publish({"revision": 1})
    assert entered.wait(timeout=1)

    started = time.monotonic()
    assert worker.close() is False
    elapsed = time.monotonic() - started

    release.set()
    _join_worker(worker)
    assert worker.close() is True
    assert elapsed < 0.1


def test_close_reports_failed_latest_persistence_as_incomplete(
    tmp_path,
    monkeypatch,
):
    attempted = threading.Event()

    def persist(_model, _root):
        attempted.set()
        raise OSError("storage unavailable")

    monkeypatch.setattr(session, "persist_session_markdown", persist)
    worker = SessionMirrorWorker(tmp_path.resolve(), drain_timeout=0.1)
    worker.publish({"revision": 1})

    assert attempted.wait(timeout=1)
    assert worker.close() is False


def test_repeated_close_does_not_renew_the_shutdown_deadline(tmp_path):
    class Clock:
        value = 10.0

        def __call__(self):
            return self.value

    class Thread:
        def __init__(self, clock):
            self.clock = clock
            self.joins = []

        def join(self, timeout):
            self.joins.append(timeout)
            self.clock.value += timeout

        def is_alive(self):
            return True

    clock = Clock()
    thread = Thread(clock)
    worker = SessionMirrorWorker(
        tmp_path.resolve(),
        drain_timeout=0.03,
        monotonic_clock=clock,
    )
    worker._thread = thread
    worker._accepted_generation = 1

    assert worker.close() is False
    assert worker.close() is False
    assert thread.joins == pytest.approx([0.03])


def test_server_reports_an_incomplete_session_mirror_close(monkeypatch, caplog):
    class Mirror:
        def close(self):
            return False

    monkeypatch.setattr(server, "_SESSION_MIRROR", Mirror())

    with caplog.at_level("WARNING", logger="brain.session-mirror"):
        assert server._close_session_mirror() is False

    assert "newest session mirror was not persisted" in caplog.text
