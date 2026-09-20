"""Failure boundaries of the proxy's private same-process handoff."""

import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from brain_mcp import proxy
from brain_mcp._proxy_handoff import (HANDOFF_VERSION, MAX_STATE_BYTES, READ_CHUNK,
                                      RawLineReader, public_session, read_state, state_descriptor)
from _bootstrap.consent_owner import ConsentOwner, OwnerConnectionError
from _bootstrap.file_lock import exclusive_file_lock, MutationLockError
from test_mcp_proxy import _FakeChild, _write_vault
from test_mcp_proxy_refresh import lifecycle_request


@pytest.fixture(autouse=True)
def canonical_unit_runtime(monkeypatch):
    # These unit vaults have no dependency installation; subprocess tests use
    # real managed interpreters and exercise the canonical resolver.
    monkeypatch.setattr(proxy, "find_existing_central_venv", lambda vault: Path(sys.executable))


def state():
    return {"version": HANDOFF_VERSION, "pid": os.getpid(), "vault": "/tmp/brain",
            "workspace": None, "python": sys.executable, "server": "brain_mcp.server",
            "protocol": "modern", "initialise_request": None, "initialise_response": None,
            "public_session": {"supportedVersions": ["2026-07-28"], "capabilities": {}},
            "tools": {}, "generation": 1, "request_id": "restart", "remainder": "", "subscriptions": [], "resolution": None}


def test_private_state_validates_process_and_bounds():
    value = state()
    with state_descriptor(value) as fd:
        assert read_state(fd, expected_pid=os.getpid()) == value
        with pytest.raises(ValueError, match="identity"):
            read_state(fd, expected_pid=os.getpid() + 1)
    with pytest.raises(OSError):
        os.fstat(fd)
    with pytest.raises(ValueError, match="1 MiB"):
        with state_descriptor({**value, "remainder": "x" * MAX_STATE_BYTES}):
            pytest.fail("oversized descriptor admitted")
    with state_descriptor({**value, "remainder": base64.b64encode(b"x" * (READ_CHUNK + 1)).decode()}) as fd:
        with pytest.raises(ValueError, match="read-ahead"):
            read_state(fd, expected_pid=os.getpid())


def test_named_descriptor_is_not_a_handoff(tmp_path):
    target = tmp_path / "state.json"
    target.write_text(json.dumps(state()))
    target.chmod(0o600)
    with target.open("rb") as stream:
        with pytest.raises(ValueError, match="unlinked"):
            read_state(stream.fileno(), expected_pid=os.getpid())


def test_previous_descriptor_version_has_no_subscriptions():
    value = state()
    value["version"] = 1
    del value["subscriptions"]
    del value["resolution"]
    with state_descriptor(value) as fd:
        restored = read_state(fd, expected_pid=os.getpid())
    assert restored == value
    relay = proxy.Proxy(sys.executable, "brain_mcp.server", value["vault"])
    relay._restore_transport(restored)
    assert relay._subscriptions.snapshot() == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX pipe wakeup")
def test_interruptible_reader_preserves_partial_input_and_prioritises_eof():
    incoming, sender = os.pipe()
    wake, notifier = os.pipe()
    try:
        reader = RawLineReader(incoming)
        os.write(sender, b'{"par')
        os.write(notifier, b"1")
        assert reader.readline_interruptible(wake) is None
        assert reader.remainder == b'{"par'
        os.write(sender, b'tial":1}\n')
        assert reader.readline_interruptible(wake) == b'{"partial":1}\n'
        os.write(notifier, b"1")
        os.close(sender)
        sender = None
        assert reader.readline_interruptible(wake) == b""
        assert reader.eof
    finally:
        for fd in (incoming, sender, wake, notifier):
            if fd is not None:
                os.close(fd)


def test_raw_reader_preserves_read_ahead_without_limiting_ordinary_frames(tmp_path):
    large = b"x" * (MAX_STATE_BYTES + 1) + b"\n"
    target = tmp_path / "frames"
    target.write_bytes(large + b'{"id":2}\n{"par')
    with target.open("rb", buffering=0) as stream:
        reader = RawLineReader(stream.fileno())
        assert reader.readline() == large
        transferred = RawLineReader(stream.fileno(), reader.remainder)
        assert transferred.readline() == b'{"id":2}\n'
        assert transferred.readline() == b'{"par'
        assert transferred.readline() == b""


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX pipe wakeup")
def test_interruptible_reader_drains_pipelined_frames_before_more_reads(tmp_path):
    target = tmp_path / "many-frames"
    target.write_bytes(b"{}\n" * READ_CHUNK)
    wake, notifier = os.pipe()
    try:
        with target.open("rb", buffering=0) as stream:
            reader = RawLineReader(stream.fileno())
            for _ in range(READ_CHUNK):
                assert reader.readline_interruptible(wake) == b"{}\n"
                assert len(reader.remainder) <= READ_CHUNK
            assert reader.readline_interruptible(wake) == b""
    finally:
        os.close(wake)
        os.close(notifier)


def test_public_contract_excludes_catalogue_but_keeps_modern_versions():
    response = {"result": {"supportedVersions": ["2026-07-28"], "capabilities": {
        "tools": {"listChanged": True}, "experimental": {"brainCommandInterface": {"fingerprint": "old"}}}}}
    contract = public_session(response)
    response["result"]["capabilities"]["experimental"]["brainCommandInterface"] = {"fingerprint": "new"}
    assert public_session(response) == contract
    response["result"]["supportedVersions"] = ["2099-01-01"]
    assert public_session(response) != contract


def test_host_replies_belong_to_child_instance_even_when_ids_recur(tmp_path):
    _write_vault(tmp_path)
    relay = proxy.Proxy(sys.executable, "brain_mcp.server", str(tmp_path))
    first, second = _FakeChild(), _FakeChild()
    relay._child = first
    request = {"jsonrpc": "2.0", "id": 1, "method": "roots/list"}
    old = relay._correlate_child_request(first, request)
    assert relay._signal_recovery(1, child=first)
    assert not relay._server_requests
    relay._child = second
    new = relay._correlate_child_request(second, request)
    assert old["id"] != new["id"]
    relay._forward_host_response({"id": old["id"], "result": {"roots": []}})
    assert second.sent == []
    relay._forward_host_response({"id": new["id"], "result": {"roots": []}})
    assert second.sent == [{"id": 1, "result": {"roots": []}}]
    assert not relay._server_requests and not relay._inflight_requests


def test_owner_close_timeout_still_destroys_consent_and_channels(tmp_path):
    owner = ConsentOwner(tmp_path)
    client = owner.connect()
    with exclusive_file_lock(owner.coordination_path):
        began = time.monotonic()
        with pytest.raises(MutationLockError):
            owner.close(lock_timeout=.02)
        assert time.monotonic() - began < .5
        with pytest.raises(OwnerConnectionError):
            client.snapshot(("grant",))
        with pytest.raises(MutationLockError):
            ConsentOwner(tmp_path, lock_timeout=.02)
    # Cleanup is deliberately left pending when the shared lock cannot be taken.
    assert owner.private_directory.exists()
    import shutil
    shutil.rmtree(owner.private_directory)


def test_handoff_busy_never_touches_child_or_consent(tmp_path, monkeypatch):
    _write_vault(tmp_path)
    relay = proxy.Proxy(sys.executable, "brain_mcp.server", str(tmp_path))
    relay._child = _FakeChild()
    relay._public_session = {}
    monkeypatch.setattr(relay, "_check_proxy_drift", lambda: None)
    relay._proxy_drift = True
    relay._inflight_requests[1] = ({"id": 1}, 0)
    assert relay._admit_lifecycle(lifecycle_request("brain_proxy_restart", 2), RawLineReader(0)) == "server_busy"
    assert not relay._child.killed
    relay._inflight_requests.clear()
    relay._correlate_child_request(relay._child, {"id": 1})
    assert relay._admit_lifecycle(lifecycle_request("brain_proxy_restart", 2), RawLineReader(0)) == "server_busy"
    assert not relay._child.killed


@pytest.mark.parametrize("size", [READ_CHUNK, READ_CHUNK + 1])
def test_final_handoff_revalidates_input_grown_after_preflight(tmp_path, monkeypatch, size):
    _write_vault(tmp_path)
    owner = ConsentOwner(tmp_path)
    relay = proxy.Proxy(sys.executable, "brain_mcp.server", str(tmp_path), owner=owner)
    relay._client_protocol = "modern"
    relay._public_session = {}
    relay._child = child = _FakeChild()
    reader = RawLineReader(0, b'{"partial":"')
    monkeypatch.setattr(relay, "_check_proxy_drift", lambda: None)
    relay._proxy_drift = True
    replaced = []
    monkeypatch.setattr(relay, "_replace_idle_image", lambda *args, **kwargs: replaced.append(args[1]))
    monkeypatch.setattr(relay, "_preflight_handoff", lambda fd, python: bool(read_state(fd, expected_pid=os.getpid())))
    incoming, outgoing = os.pipe()
    relay._wake_write = outgoing
    try:
        assert relay._admit_lifecycle(lifecycle_request("brain_proxy_restart"), reader) is None
        relay._prepare_lifecycle()
        reader.remainder += b"x" * (size - len(reader.remainder))
        retained = reader.remainder
        relay._finish_prepared_handoff(reader)
        response = relay._outbound.get_nowait()["result"]
        assert response["isError"] is (size > READ_CHUNK)
        assert len(replaced) == (0 if size > READ_CHUNK else 1)
        if replaced:
            assert base64.b64decode(replaced[0]["remainder"]) == retained
        else:
            assert response["structuredContent"]["error"]["effects"] == "none"
        assert reader.remainder == retained
        assert not owner._closed and not child.killed and not relay._shutdown
        assert relay._pending_lifecycle is None and not relay._restart_in_progress
        assert relay._serve_transport_request({"method": "ping", "id": 2})
        assert relay._outbound.get_nowait()["result"] == {}
    finally:
        relay._wake_write = None
        os.close(incoming)
        os.close(outgoing)
        owner.close()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX child quiescence")
@pytest.mark.parametrize("pending_output", [True, False])
def test_quiescence_refuses_pending_output_and_exec_failure_reaps_child(tmp_path, monkeypatch, pending_output):
    _write_vault(tmp_path)
    owner = ConsentOwner(tmp_path)
    relay = proxy.Proxy(sys.executable, "brain_mcp.server", str(tmp_path), owner=owner)
    child = proxy.ChildProcess(sys.executable, "unused")
    child._proc = subprocess.Popen([sys.executable, "-c", "import sys,time; print('pending',flush=True); time.sleep(30)"], stdout=subprocess.PIPE)
    child._stdout_reader = RawLineReader(child.stdout_fd)
    relay._child = child
    # Ensure the frame exists before testing the quiescence boundary.
    import select
    assert select.select([child.stdout_fd], [], [], 5)[0]
    if not pending_output:
        assert child.readline() == b"pending\n"
    monkeypatch.setattr(proxy.os, "execve", lambda *args: (_ for _ in ()).throw(OSError("injected exec failure")))
    def writer():
        while True:
            item = relay._outbound.get(timeout=5)
            if item is None:
                return
            item.set()
    relay._writer_thread_handle = threading.Thread(target=writer)
    relay._writer_thread_handle.start()
    try:
        result = relay._replace_idle_image(123, state())
        if pending_output:
            assert result == "server_busy"
            assert child.poll() is None
            assert not owner._closed
            # SIGCONT actually resumed the child; stdin/stdout ownership remains.
            assert child.readline() == b"pending\n"
        else:
            assert result == "proxy_exec_failed"
            assert owner._closed
            assert relay._handoff_state == state()
            with pytest.raises(ChildProcessError):
                os.waitpid(child.pid, os.WNOHANG)
    finally:
        child.kill()
        child.reap(5)
        owner.close()
        relay._outbound.put(None)
        relay._writer_thread_handle.join(5)


def test_child_cancellation_uses_host_id_and_releases_busy_state(tmp_path):
    relay = proxy.Proxy(sys.executable, "brain_mcp.server", str(tmp_path))
    child = _FakeChild()
    request = relay._correlate_child_request(child, {"id": 7, "method": "roots/list"})
    cancelled = relay._translate_child_cancellation(child, {"method": "notifications/cancelled", "params": {"requestId": 7}})
    assert cancelled["params"]["requestId"] == request["id"]
    assert not relay._server_requests
    assert relay._translate_child_cancellation(child, {"method": "notifications/cancelled", "params": {"requestId": 7}}) is None


def test_private_negotiation_does_not_publish_untracked_host_request(tmp_path, monkeypatch):
    relay = proxy.Proxy(sys.executable, "brain_mcp.server", str(tmp_path))
    monkeypatch.setattr(relay, "_read_with_timeout", lambda *args: {"id": 1, "method": "roots/list"})
    with pytest.raises(ValueError, match="host input"):
        relay._read_internal_response(_FakeChild(), "private", 1)
    assert relay._outbound.empty()


def test_modern_discovery_rejects_changed_supported_versions(tmp_path, monkeypatch):
    relay = proxy.Proxy(sys.executable, "brain_mcp.server", str(tmp_path))
    child = _FakeChild()
    relay._public_session = public_session({"result": {"supportedVersions": ["2026-07-28"], "capabilities": {}}})
    monkeypatch.setattr(relay, "_read_internal_response", lambda *args: {"result": {"supportedVersions": ["2099-01-01"], "capabilities": {}}})
    monkeypatch.setattr(relay, "_capture_interface_header", lambda response: True)
    assert not relay._discover_child(child)
    assert child.killed
    assert "public MCP" in relay._interface_header_error


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX child output probe")
@pytest.mark.parametrize("payload", [b"", b"partial", b"{}\n"])
def test_dead_child_pending_output_distinguishes_eof_without_discarding_bytes(payload):
    child = proxy.ChildProcess(sys.executable, "unused")
    child._proc = subprocess.Popen([sys.executable, "-c", f"import os; os.write(1, {payload!r})"], stdout=subprocess.PIPE)
    child._stdout_reader = RawLineReader(child.stdout_fd)
    try:
        child.reap(5)
        assert child.output_pending() is bool(payload)
        assert child._stdout_reader.remainder == payload
        assert child.readline() == (payload or None)
    finally:
        child.kill()
        child.reap(5)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX quiescence")
def test_blocked_writer_refuses_handoff_without_losing_queued_output(tmp_path, monkeypatch):
    owner = ConsentOwner(tmp_path)
    relay = proxy.Proxy(sys.executable, "brain_mcp.server", str(tmp_path), owner=owner)
    child = proxy.ChildProcess(sys.executable, "unused")
    child._proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], stdout=subprocess.PIPE)
    child._stdout_reader = RawLineReader(child.stdout_fd)
    relay._child = child
    response = {"id": 1, "result": {"completed": True}}
    relay._send_to_client(response)
    monkeypatch.setattr(proxy, "HANDOFF_TIMEOUT", .1)
    try:
        assert relay._replace_idle_image(123, state()) == "proxy_output_busy"
        assert relay._outbound.get_nowait() == response
        assert child.poll() is None
        assert not owner._closed and not relay._shutdown
    finally:
        child.kill()
        child.reap(5)
        owner.close()


def test_reader_discards_old_child_frame_selected_before_swap(tmp_path, monkeypatch):
    relay = proxy.Proxy(sys.executable, "brain_mcp.server", str(tmp_path))
    child, replacement = _FakeChild(), _FakeChild()
    child.stdout_fd = 123
    child.line_ready = True
    child.readline = lambda: pytest.fail("retired child's selected output was read")
    relay._initial_protocol_selected.set()
    selected = False
    def current_child():
        nonlocal selected
        if not selected:
            selected = True
            return child
        relay._shutdown = True
        return replacement
    monkeypatch.setattr(relay, "_get_child", current_child)
    relay._reader_thread()
    assert relay._outbound.empty() and not relay._server_requests


def test_windows_idle_blocking_read_does_not_own_publication_gate(tmp_path, monkeypatch):
    relay = proxy.Proxy(sys.executable, "brain_mcp.server", str(tmp_path))
    child = _FakeChild()
    relay._child = child
    relay._initial_protocol_selected.set()
    entered, release = threading.Event(), threading.Event()
    def read():
        entered.set()
        assert release.wait(3)
        return b'{"jsonrpc":"2.0","method":"notifications/tools/list_changed"}\n'
    child.readline = read
    monkeypatch.setattr(proxy.sys, "platform", "win32")
    reader = threading.Thread(target=relay._reader_thread)
    reader.start()
    try:
        assert entered.wait(3)
        assert relay._publication_gate.acquire(timeout=.2), "idle Windows read blocked server replacement"
        try:
            relay._child = _FakeChild()
            relay._shutdown = True
        finally:
            relay._publication_gate.release()
    finally:
        relay._shutdown = True
        release.set()
        reader.join(3)
    assert not reader.is_alive()
    assert relay._outbound.empty()
