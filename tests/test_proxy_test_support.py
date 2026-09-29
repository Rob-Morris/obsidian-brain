"""Behavioural checks for the shared proxy test harness."""

import os
import logging
import threading
import pytest
from brain_mcp import proxy as proxy_mod

from proxy_test_support import (
    _make_response,
    _read_all_responses,
    _read_responses,
    _make_inprocess_proxy,
    _make_inprocess_proxy_with_real_threads,
    _NoOpThread,
)

class _PipeProc:
    def __init__(self, payload: bytes):
        read_fd, write_fd = os.pipe()
        os.write(write_fd, payload)
        os.close(write_fd)
        self.stdout = os.fdopen(read_fd, "rb", buffering=0)

    def close(self) -> None:
        self.stdout.close()


@pytest.mark.slow
@pytest.mark.slow
class TestReadJsonMessages:
    def test_preserves_unread_lines_between_helper_calls(self):
        proc = _PipeProc(
            _make_response(1, {"value": "one"}).encode("utf-8")
            + _make_response(2, {"value": "two"}).encode("utf-8")
        )
        try:
            first = _read_responses(proc, timeout=1.0, count=1)
            assert [msg["id"] for msg in first] == [1]

            second = _read_all_responses(proc, timeout=1.0, idle=0.1, max_count=5)
            assert [msg["id"] for msg in second] == [2]
        finally:
            proc.close()


@pytest.mark.parametrize("factory", [_make_inprocess_proxy, _make_inprocess_proxy_with_real_threads])
def test_inprocess_logging_is_local_and_restored(tmp_path, monkeypatch, caplog, factory):
    registered = logging.getLogger("brain-proxy")
    monkeypatch.setattr(registered, "handlers", [])
    monkeypatch.setattr(registered, "level", logging.ERROR)
    previous = proxy_mod._logger
    with monkeypatch.context() as scope:
        factory(tmp_path, scope, [])
        assert proxy_mod._logger is not registered
        assert registered.handlers == []
        assert registered.level == logging.ERROR
        with caplog.at_level(logging.WARNING):
            proxy_mod._logger.warning("isolated proxy diagnostic")
        assert caplog.messages == ["isolated proxy diagnostic"]
    assert proxy_mod._logger is previous


def test_fake_threads_do_not_replace_stdlib_threads(tmp_path, monkeypatch):
    original = threading.Thread
    with monkeypatch.context() as scope:
        _make_inprocess_proxy(tmp_path, scope, [])
        assert threading.Thread is original
        assert proxy_mod.threading.Thread is _NoOpThread
        assert proxy_mod.threading.Event is threading.Event
        assert proxy_mod.threading.Lock is threading.Lock
    assert proxy_mod.threading is threading
