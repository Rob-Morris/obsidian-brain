"""MCP session-mirror persistence stays bounded and coalescing."""

from __future__ import annotations

import threading
import time

from brain_mcp._session_mirror import SessionMirrorWorker
import session


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
    worker.close()
    assert calls == [1, 3]
