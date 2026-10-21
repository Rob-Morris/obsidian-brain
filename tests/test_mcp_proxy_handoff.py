"""Failure boundaries of the proxy's private same-process handoff."""

import base64
from contextlib import contextmanager
import tempfile
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from brain_mcp import proxy
from brain_mcp._proxy_handoff import (HANDOFF_TIMEOUT, HANDOFF_VERSION, MAX_STATE_BYTES, READ_CHUNK,
                                      RawLineReader, consume_remainder, public_session, read_state, state_descriptor)
from _bootstrap.consent_owner import ConsentOwner, OwnerConnectionError
from _bootstrap.file_lock import exclusive_file_lock, MutationLockError
from proxy_test_support import _FakeChild, _write_vault
from proxy_test_support import lifecycle_request


@contextmanager
def legacy_descriptor(value):
    """Model an installed v1/v2 writer; production only emits v3."""
    with tempfile.TemporaryFile() as stream:
        stream.write(json.dumps(value).encode())
        stream.flush()
        yield stream.fileno()


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
            "tools": {}, "generation": 1, "request_id": "restart", "remainder": b"", "subscriptions": [], "resolution": None}


@pytest.mark.skipif(os.name != "posix", reason="POSIX preflight child ownership")
@pytest.mark.parametrize("accepted", [True, False])
def test_preflight_entry_reaps_accepted_and_unpublished_children(tmp_path, monkeypatch, accepted):
    for name in ("BRAIN_VAULT_ROOT", "PYTHONPATH", "BRAIN_WORKSPACE_DIR"):
        monkeypatch.setenv(name, os.environ.get(name, ""))
    child = proxy.ChildProcess(sys.executable, "unused")
    child._proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    class Candidate:
        _preparing_child = None if accepted else child
        def _restore_transport(self, value):
            pass
        def _start_child(self):
            return accepted
        def _get_child(self):
            return child if accepted else None
    monkeypatch.setattr(proxy, "Proxy", lambda *args: Candidate())
    monkeypatch.setattr(proxy, "__file__", str(tmp_path / ".brain-core/brain_mcp/proxy.py"))
    monkeypatch.setattr(proxy, "read_state", lambda *args, **kwargs: {**state(), "vault": str(tmp_path)})
    try:
        if accepted:
            proxy._handoff_entry("--check-handoff", 123)
        else:
            with pytest.raises(ValueError, match="replacement cannot preserve"):
                proxy._handoff_entry("--check-handoff", 123)
        assert child._proc.returncode is not None
        with pytest.raises(ChildProcessError):
            os.waitpid(child.pid, os.WNOHANG)
    finally:
        child.kill()
        child.reap(5)


def test_preflight_entry_refuses_unreaped_child(tmp_path, monkeypatch):
    for name in ("BRAIN_VAULT_ROOT", "PYTHONPATH", "BRAIN_WORKSPACE_DIR"):
        monkeypatch.setenv(name, os.environ.get(name, ""))
    events = []
    class Child:
        def kill(self):
            events.append("kill")
        def reap(self, timeout):
            events.append(("reap", timeout))
            raise subprocess.TimeoutExpired("probe child", timeout)
    child = Child()
    class Candidate:
        def _restore_transport(self, value):
            pass
        def _start_child(self):
            return True
        def _get_child(self):
            return child
    monkeypatch.setattr(proxy, "Proxy", lambda *args: Candidate())
    monkeypatch.setattr(proxy, "__file__", str(tmp_path / ".brain-core/brain_mcp/proxy.py"))
    monkeypatch.setattr(proxy, "read_state", lambda *args, **kwargs: {**state(), "vault": str(tmp_path)})
    with pytest.raises(subprocess.TimeoutExpired):
        proxy._handoff_entry("--check-handoff", 123)
    assert events == ["kill", ("reap", HANDOFF_TIMEOUT)]


@pytest.mark.skipif(os.name != "posix", reason="POSIX process group cleanup")
@pytest.mark.parametrize("timed_out", [False, True])
@pytest.mark.parametrize("denied", [False, True])
def test_preflight_group_cleanup_retains_timeout_and_permission_failures(tmp_path, monkeypatch, timed_out, denied):
    events = []
    class Process:
        pid = 123
        def wait(self, timeout):
            events.append(("wait", timeout))
            if timed_out and len(events) == 1:
                raise subprocess.TimeoutExpired("probe", timeout)
            return 0
    monkeypatch.setattr(proxy.subprocess, "Popen", lambda *args, **kwargs: Process())
    def kill_group(pid, sig):
        events.append(("killpg", pid, sig))
        if denied:
            raise PermissionError(1, "private detail")
    monkeypatch.setattr(proxy.os, "killpg", kill_group)
    relay = proxy.Proxy(sys.executable, "brain_mcp.server", str(tmp_path))
    if denied:
        with pytest.raises(PermissionError):
            relay._preflight_handoff(123, sys.executable)
    else:
        assert relay._preflight_handoff(123, sys.executable) is (not timed_out)
        assert events[-1] == ("wait", HANDOFF_TIMEOUT)
    assert events[:2] == [("wait", HANDOFF_TIMEOUT), ("killpg", 123, proxy.signal.SIGKILL)]


@pytest.mark.parametrize("outcome", [0, 1, -9, "timeout"])
def test_preflight_diagnostic_is_bounded_and_preserves_cleanup(tmp_path, monkeypatch, caplog, outcome):
    relay = proxy.Proxy(sys.executable, "brain_mcp.server", str(tmp_path))
    events = []
    launches = []
    secret = "private-candidate-output"

    class Process:
        pid = 123

        def wait(self, timeout):
            events.append(("wait", timeout))
            if len(events) == 1:
                if outcome == "timeout":
                    raise subprocess.TimeoutExpired(secret, timeout, output=secret, stderr=secret)
                return outcome
            return -9

    def managed(argv, *, role, env):
        assert argv == [sys.executable, "-m", "brain_mcp.proxy", "--check-handoff", "123"]
        assert role == proxy.ROLE_MCP

        def launch(**kwargs):
            launches.append({**kwargs, "env": env})
            return Process()

        return SimpleNamespace(popen=launch)

    monkeypatch.setenv("BRAIN_OPERATOR_KEY", secret)
    monkeypatch.setattr(proxy, "managed_command", managed)
    monkeypatch.setattr(proxy, "os", SimpleNamespace(
        getpid=os.getpid, killpg=lambda pid, sig: events.append(("killpg", pid, sig))))
    ticks = iter([10.0, 15.0 if outcome == "timeout" else 10.125])
    monkeypatch.setattr(proxy, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    with caplog.at_level(logging.WARNING, logger="brain-proxy"):
        assert relay._preflight_handoff(123, sys.executable, input_fd=456) is (outcome == 0)

    assert HANDOFF_TIMEOUT == 5.0
    assert events == [("wait", 5.0), ("killpg", 123, proxy.signal.SIGKILL), ("wait", 5.0)]
    assert len(launches) == 1
    launch_options = launches[0]
    assert launch_options["pass_fds"] == (123, 456)
    assert launch_options["start_new_session"] is True
    assert all(launch_options[name] == subprocess.DEVNULL for name in ("stdin", "stdout", "stderr"))
    assert "BRAIN_OPERATOR_KEY" not in launch_options["env"]
    messages = [record.getMessage() for record in caplog.records if record.name == "brain-proxy"]
    if outcome == 0:
        assert messages == []
    elif outcome == "timeout":
        assert messages == ["handoff preflight refused: reason=timeout elapsed_ms=5000 returncode=unknown"]
    else:
        assert messages == [f"handoff preflight refused: reason=nonzero_exit elapsed_ms=125 returncode={outcome}"]
    assert secret not in caplog.text
    assert str(tmp_path) not in caplog.text


def test_preparation_diagnostic_keeps_location_and_errno_without_private_text(tmp_path, monkeypatch, caplog):
    relay = proxy.Proxy(sys.executable, "brain_mcp.server", str(tmp_path))
    relay._pending_lifecycle = {"request": lifecycle_request("brain_proxy_restart")}
    monkeypatch.setattr(relay, "_assess_startup", lambda: None)
    def denied():
        raise PermissionError(13, "private secret", "/private/user-data")
    monkeypatch.setattr(relay, "_check_proxy_drift", denied)
    outcomes = []
    monkeypatch.setattr(relay, "_complete_lifecycle", lambda pending, code, **kwargs: outcomes.append(code))
    with caplog.at_level(logging.WARNING, logger="brain-proxy"):
        relay._prepare_lifecycle()
    assert outcomes == ["server_refresh_blocked"]
    assert "errno=13" in caplog.text and "proxy_line=" in caplog.text
    assert "private secret" not in caplog.text and "/private/user-data" not in caplog.text


def test_private_state_validates_process_and_bounds():
    value = state()
    with state_descriptor(value) as fd:
        restored = read_state(fd, expected_pid=os.getpid())
        assert {k: v for k, v in restored.items() if k != "remainder"} == {k: v for k, v in value.items() if k != "remainder"}
        assert restored["remainder"]["bytes"] == 0
        with pytest.raises(ValueError, match="identity"):
            read_state(fd, expected_pid=os.getpid() + 1)
    with pytest.raises(OSError):
        os.fstat(fd)
    with pytest.raises(ValueError, match="1 MiB"):
        with state_descriptor({**value, "tools": {"oversize": "x" * MAX_STATE_BYTES}}):
            pytest.fail("oversized descriptor admitted")
    with pytest.raises(ValueError, match="read-ahead"):
        with state_descriptor({**value, "remainder": b"x" * (READ_CHUNK + 1)}):
            pytest.fail("oversized read-ahead admitted")


def test_named_descriptor_is_not_a_handoff(tmp_path):
    target = tmp_path / "state.json"
    target.write_text(json.dumps({**state(), "version": 2, "remainder": ""}))
    target.chmod(0o600)
    with target.open("rb") as stream:
        with pytest.raises(ValueError, match="unlinked"):
            read_state(stream.fileno(), expected_pid=os.getpid())


def test_previous_descriptor_version_has_no_subscriptions():
    value = state()
    value["version"] = 1
    value["remainder"] = ""
    del value["subscriptions"]
    del value["resolution"]
    with legacy_descriptor(value) as fd:
        restored = read_state(fd, expected_pid=os.getpid())
    assert restored == value
    relay = proxy.Proxy(sys.executable, "brain_mcp.server", value["vault"])
    relay._restore_transport(restored)
    assert relay._subscriptions.snapshot() == []


def test_pre_notice_descriptor_v2_restores_catalogue_without_new_fields(tmp_path, monkeypatch):
    from dataclasses import asdict
    from brain_mcp._command_adapter import application_interface_header
    from _application.registry import current_application_catalogue

    header = application_interface_header(current_application_catalogue())
    previous = {**state(), "version": 2, "remainder": ""}  # Exact installed v2 shape.
    previous["vault"] = str(tmp_path)
    previous["tools"] = {name: asdict(tool) for name, tool in header.tools if name != "command_list"}
    with legacy_descriptor(previous) as fd:
        restored = read_state(fd, expected_pid=os.getpid())
    relay = proxy.Proxy(sys.executable, "brain_mcp.server", str(tmp_path))
    relay._restore_transport(restored)
    relay._interface_header = header
    assert relay._catalogue_generation == previous["generation"]
    assert relay._catalogue_notice_generation is None
    assert relay._pending_tool_names() == ["command_list"]
    outgoing = relay._transport_state("restart", type("Input", (), {"remainder": b""})())
    assert outgoing.keys() == previous.keys()
    assert outgoing["version"] == 3 and outgoing["tools"] == previous["tools"]
    assert outgoing["remainder"] == b""


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
    monkeypatch.setattr(relay, "_preflight_handoff", lambda fd, python, **kwargs: bool(read_state(fd, expected_pid=os.getpid())))
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
            assert replaced[0]["remainder"] == retained
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


@pytest.mark.parametrize("method", ["server/discover", "tools/list", "tools/call"])
def test_modern_discovery_identity_survives_existing_handoff_shape(tmp_path, method):
    relay = proxy.Proxy(sys.executable, "brain_mcp.server", str(tmp_path))
    request = {"jsonrpc": "2.0", "id": 1, "method": method, "params": {
        "_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                   "io.modelcontextprotocol/clientInfo": {"name": "codex-mcp-client"}}}}
    relay._send_to_client = lambda response: None
    assert relay._serve_transport_request(request) == (method == "server/discover")
    assert "Brain MCP in Codex" in proxy.rediscovery_action(relay._init_request)["description"]
    value = relay._transport_state("restart", type("Input", (), {"remainder": b""})())
    assert value.keys() == state().keys()
    with state_descriptor(value) as fd:
        restored = read_state(fd, expected_pid=os.getpid())
    replacement = proxy.Proxy(sys.executable, "brain_mcp.server", str(tmp_path))
    replacement._restore_transport(restored)
    assert replacement._init_request == relay._init_request
    assert "Brain MCP in Codex" in proxy.rediscovery_action(replacement._init_request)["description"]


def test_large_first_modern_call_does_not_retain_arguments_in_handoff(tmp_path):
    relay = proxy.Proxy(sys.executable, "brain_mcp.server", str(tmp_path))
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "attachment_create", "arguments": {"content": "x" * MAX_STATE_BYTES},
        "_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                  "io.modelcontextprotocol/clientInfo": {"name": "codex-mcp-client", "title": "x" * MAX_STATE_BYTES}}}}
    assert relay._serve_transport_request(request) is False
    value = relay._transport_state("restart", type("Input", (), {"remainder": b""})())
    assert len(json.dumps({k: v for k, v in value.items() if k != "remainder"})) < 2000
    assert value["remainder"] == b""
    with state_descriptor(value) as fd:
        restored = read_state(fd, expected_pid=os.getpid())
    assert "Brain MCP in Codex" in proxy.rediscovery_action(restored["initialise_request"])["description"]
