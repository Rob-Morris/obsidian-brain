"""Child responsiveness is independent of operation duration and output traffic."""
import os
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from brain_mcp._child_health import ChildHealth
from brain_mcp import _proxy_handoff as framing
from brain_mcp import proxy
from proxy_test_support import _FakeChild


def reply(request_id, **changes):
    return {"jsonrpc": "2.0", "id": request_id, "result": {}, **changes}


def test_health_lease_renews_only_for_matching_child_probe():
    child, replacement = object(), object()
    health = ChildHealth(1)
    assert health.poll(child, True, 0) is None
    first = health.poll(child, True, 1)
    assert first.kind == "probe"
    assert not health.consume(replacement, reply(first.request_id), 1.1)
    assert not health.consume(child, reply("unrelated"), 1.2)
    assert health.consume(child, reply(first.request_id), 1.3)
    assert health.poll(child, True, 2.2) is None
    second = health.poll(child, True, 2.3)
    assert second.kind == "probe"
    assert health.consume(child, reply(first.request_id), 2.4)
    assert health.poll(child, True, 4.3).kind == "stop"
    assert health.poll(replacement, True, 5) is None
    assert not health.consume(child, reply(second.request_id), 5.1)


@pytest.mark.parametrize("change", [
    {"jsonrpc": None}, {"result": {"unexpected": True}}, {"error": {}}, {"method": "ping"},
])
def test_malformed_reply_does_not_renew(change):
    child = object()
    health = ChildHealth(1)
    health.poll(child, True, 0)
    probe = health.poll(child, True, 1)
    assert health.consume(child, reply(probe.request_id, **change), 2)
    assert health.poll(child, True, 3).kind == "stop"


def test_oversized_owned_id_and_late_reply_cannot_renew():
    child = object()
    health = ChildHealth(1)
    health.poll(child, True, 0)
    probe = health.poll(child, True, 1)
    assert health.consume(child, reply(probe.request_id[:-1] + "9" * 5000), 2)
    assert health.consume(child, reply(probe.request_id), 3)
    assert health.poll(child, True, 3).kind == "stop"


def test_idle_does_not_impose_a_deadline_and_healthy_work_has_no_total_cap():
    child = object()
    health = ChildHealth(1)
    assert health.poll(child, False, 0) is None
    assert health.poll(child, False, 10000) is None
    for now in range(10001, 10101):
        probe = health.poll(child, True, now)
        assert probe.kind == "probe"
        assert health.consume(child, reply(probe.request_id), now)


def test_modern_empty_result_may_carry_protocol_server_metadata():
    child = object()
    health = ChildHealth(1)
    health.poll(child, True, 0)
    probe = health.poll(child, True, 1)
    assert health.consume(child, reply(probe.request_id, result={"_meta": {
        "io.modelcontextprotocol/serverInfo": {"name": "brain", "version": "test"}}}), 1.5)
    assert health.poll(child, True, 2) is None


def test_modern_probe_uses_supported_discovery_and_rejects_empty_ping_result():
    child = object()
    health = ChildHealth(1)
    health.poll(child, True, 0, modern=True)
    probe = health.poll(child, True, 1, modern=True)
    assert probe.method == "server/discover"
    assert health.consume(child, reply(probe.request_id), 2)
    assert health.poll(child, True, 3, modern=True).kind == "stop"


@pytest.mark.parametrize("write_error", [None, OSError])
def test_health_failure_claims_recovery_without_waiting_for_reader_eof(tmp_path, write_error):
    relay = proxy.Proxy(sys.executable, "unused", str(tmp_path))
    relay._client_protocol = "modern"
    relay._health = ChildHealth(.01)
    child = _FakeChild(send_exception=write_error)
    child.reap = lambda timeout: None
    relay._child = child
    relay._inflight_requests[1] = ({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, time.monotonic())
    thread = threading.Thread(target=relay._health_loop)
    thread.start()
    try:
        assert relay._recovery_trigger.wait(5)
        assert child.killed
        assert relay._get_child() is None
        assert relay._restart_in_progress
        assert not relay._inflight_requests
        assert relay._outbound.get(timeout=1)["id"] == 1
    finally:
        relay._health_stop.set()
        thread.join(5)
    assert not thread.is_alive()


@pytest.mark.skipif(os.name != "posix", reason="Native retained POSIX pipe ownership")
def test_health_retirement_reaps_child_while_descendant_retains_pipes(tmp_path):
    child = proxy.ChildProcess(sys.executable, "unused")
    script = "import subprocess,sys,threading; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); print(p.pid,flush=True); threading.Event().wait()"
    child._proc = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    descendant = int(child._proc.stdout.readline())
    stderr = threading.Thread(target=child._relay_stderr, daemon=True)
    stderr.start()
    relay = proxy.Proxy(sys.executable, "unused", str(tmp_path))
    relay._child = child
    try:
        relay._retire_unresponsive_child(child)
        assert relay._recovery_trigger.is_set()
        assert stderr.is_alive(), "descendant should still own the stderr pipe"
        with pytest.raises(ChildProcessError):
            os.waitpid(child.pid, os.WNOHANG)
    finally:
        os.kill(descendant, signal.SIGKILL)
        child.kill()
        child.reap(5)
        stderr.join(5)
        child._proc.stdout.close()
        child._proc.stderr.close()


def test_failed_reap_stops_proxy_instead_of_starting_replacement(tmp_path):
    relay = proxy.Proxy(sys.executable, "unused", str(tmp_path))
    child = _FakeChild()
    def unreaped(timeout):
        raise subprocess.TimeoutExpired("child", timeout)
    child.reap = unreaped
    relay._child = child
    relay._retire_unresponsive_child(child)
    assert child.killed and relay._shutdown and relay._health_stop.is_set()
    assert not relay._restart_in_progress


@pytest.mark.parametrize("windows", [False, True])
def test_timed_frame_preserves_partial_bytes_and_observes_eof(monkeypatch, windows):
    if not windows and os.name == "nt":
        pytest.skip("POSIX select branch requires a native pipe-select host")
    read_fd, write_fd = os.pipe()
    try:
        if windows:
            os.set_blocking(read_fd, False)
            monkeypatch.setattr(framing, "sys", SimpleNamespace(platform="win32"))
        reader = framing.RawLineReader(read_fd)
        os.write(write_fd, b'{"partial":')
        with pytest.raises(TimeoutError):
            reader.readline_timed(.01)
        assert reader.remainder == b'{"partial":'
        os.write(write_fd, b'true}\nnext\n')
        assert reader.readline_timed(1) == b'{"partial":true}\n'
        assert reader.readline_timed(1) == b'next\n'
        os.close(write_fd)
        write_fd = None
        assert reader.readline_timed(1) == b''
    finally:
        os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)
