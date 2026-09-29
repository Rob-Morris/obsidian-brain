"""Failure context is bounded and works for both completed and timed-out tests."""

import subprocess

from brain_test_support import process_diagnostics


def test_completed_process_diagnostics_include_both_streams_but_not_arguments():
    result = subprocess.CompletedProcess(["secret-argument"], 3, "checker unavailable", "reason")
    message = process_diagnostics(result)
    assert "returncode=3" in message
    assert "checker unavailable" in message and "reason" in message
    assert "secret-argument" not in message


def test_timeout_diagnostics_decode_bytes_and_handle_empty_streams():
    failure = subprocess.TimeoutExpired(["test"], 20, output=b"partial\xff", stderr=None)
    message = process_diagnostics(failure)
    assert "timeout=20" in message and "partial\ufffd" in message
    assert "stderr:\n<empty>" in message


def test_diagnostics_bound_each_stream_and_mark_truncation():
    result = subprocess.CompletedProcess([], 1, "discard" + "o" * 5000, "e" * 5000)
    message = process_diagnostics(result)
    assert "discard" not in message
    assert message.count("<tail only>") == 2
    assert len(message) < 8400
