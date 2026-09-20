"""Deterministic lifecycle concurrency and subscription ownership boundaries."""

import json
import os
import subprocess
import sys
import threading
import time

import pytest

from brain_mcp import proxy
from brain_mcp._proxy_session import SUBSCRIPTION_ID, SubscriptionEvent
from test_mcp_proxy import _FakeChild, _ReadableFakeChild, _write_vault
from test_mcp_proxy_refresh import lifecycle_request
from types import SimpleNamespace


@pytest.mark.parametrize("phase", ["negotiation", "resync"])
def test_control_admitted_during_subscription_work_is_not_crash_recovery(tmp_path, monkeypatch, phase):
    relay = proxy.Proxy(sys.executable, "brain_mcp.server", str(tmp_path))
    relay._client_protocol = "modern"
    relay._child = _FakeChild()
    entered, release = threading.Event(), threading.Event()
    dispatched = []

    def pause(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return True

    def finish(kind):
        dispatched.append(kind)
        relay._initiate_shutdown()

    if phase == "negotiation":
        monkeypatch.setattr(relay, "_discover_child", pause)
    else:
        relay._initial_protocol_selected.set()
        monkeypatch.setattr(relay, "_sync_subscription_child", pause)
    monkeypatch.setattr(relay, "_prepare_lifecycle", lambda: finish("control"))
    monkeypatch.setattr(relay, "_recover_from_exit", lambda code: finish("crash"))
    relay._start_recovery_loop()
    try:
        relay._request_subscription_sync()
        assert entered.wait(3)
        assert relay._admit_lifecycle(lifecycle_request(), None) is None
        release.set()
        relay._recovery_thread_handle.join(3)
        assert not relay._recovery_thread_handle.is_alive()
        assert dispatched == ["control"]
    finally:
        release.set()
        relay._initiate_shutdown()
        relay._recovery_thread_handle.join(3)


def test_subscription_first_negotiation_is_worker_owned_and_serialised(tmp_path, monkeypatch):
    relay = proxy.Proxy(sys.executable, "brain_mcp.server", str(tmp_path))
    relay._child = child = _FakeChild()
    entered, release = threading.Event(), threading.Event()
    calls = []

    def discover(candidate):
        calls.append(threading.current_thread().name)
        entered.set()
        assert release.wait(3)
        relay._interface_header = object()
        return True

    monkeypatch.setattr(relay, "_discover_child", discover)
    relay._start_recovery_loop()
    contender = None
    try:
        assert relay._serve_transport_request({"id": "first", "method": "subscriptions/listen", "params": {
            "_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28"},
            "notifications": {"toolsListChanged": True}}})
        assert relay._outbound.get(timeout=1)["method"] == "notifications/subscriptions/acknowledged"
        assert entered.wait(3)
        assert relay._serve_transport_request({"id": 2, "method": "ping"})
        assert relay._outbound.get(timeout=1)["result"] == {}
        contender = threading.Thread(target=relay._establish_initial_protocol, args=(child,))
        contender.start()
        release.set()
        contender.join(3)
        assert not contender.is_alive()
        assert calls == ["child-recovery"]
        assert relay._initial_protocol_selected.is_set()
        assert len([frame for frame in child.sent if frame["method"] == "subscriptions/listen"]) == 1
    finally:
        release.set()
        relay._initiate_shutdown()
        relay._recovery_thread_handle.join(3)
        if contender is not None:
            contender.join(3)


@pytest.mark.parametrize("stop", ["cancel", "eof"])
def test_preparing_candidate_does_not_block_controls_or_survive_stop(tmp_path, monkeypatch, stop):
    _write_vault(tmp_path)
    relay = proxy.Proxy(sys.executable, "brain_mcp.server", str(tmp_path))
    relay._client_protocol = "modern"
    candidate = _FakeChild()
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(proxy, "ChildProcess", lambda *args: candidate)
    monkeypatch.setattr(relay, "_runtime_status", lambda: {"loaded": sys.executable, "child": sys.executable,
        "required": sys.executable, "state": "current", "restart_required": False})

    def negotiate(child):
        entered.set()
        assert release.wait(3)
        return True

    monkeypatch.setattr(relay, "_discover_child", negotiate)
    relay._start_recovery_loop()
    try:
        assert relay._admit_lifecycle(lifecycle_request("brain_proxy_restart"), None) is None
        assert entered.wait(3)
        assert relay._proxy_status()["lifecycle"]["phase"] == "recovering"
        assert relay._serve_transport_request({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        assert relay._outbound.get(timeout=1) == {"jsonrpc": "2.0", "id": 2, "result": {}}
        assert relay._admit_lifecycle(lifecycle_request(request_id=3), None) == "refresh_in_progress"
        if stop == "cancel":
            assert relay._serve_transport_request({"method": "notifications/cancelled", "params": {"requestId": 1}})
        else:
            relay._initiate_shutdown()
        release.set()
        if stop == "cancel":
            response = relay._outbound.get(timeout=3)
            assert response["result"]["structuredContent"]["error"]["code"] == "request_cancelled"
            assert relay._pending_lifecycle is None
        else:
            relay._recovery_thread_handle.join(3)
            assert relay._outbound.empty()
        assert candidate.killed
        assert relay._get_child() is None
        assert relay._catalogue_generation == 0
    finally:
        release.set()
        relay._initiate_shutdown()
        relay._recovery_thread_handle.join(3)


@pytest.mark.parametrize("ending", ["result", "error", "cancel"])
def test_current_child_stream_end_explicitly_ends_host_streams(tmp_path, monkeypatch, ending):
    relay = proxy.Proxy(sys.executable, "brain_mcp.server", str(tmp_path))
    relay._client_protocol = "modern"
    relay._initial_protocol_selected.set()
    terminal = {"id": "internal", ending: {}} if ending != "cancel" else {
        "method": "notifications/cancelled", "params": {"requestId": "internal"}}
    child = _ReadableFakeChild([json.dumps(terminal).encode()])
    child.subscription_id = "internal"
    relay._child = child
    relay._subscriptions.open("host", {"toolsListChanged": True}, lambda _: None)
    monkeypatch.setattr(proxy.sys, "platform", "win32")
    messages = []

    def publish(frame):
        messages.append(frame)
        relay._shutdown = True

    monkeypatch.setattr(relay, "_send_to_client", publish)
    relay._reader_thread()
    assert messages == [{"jsonrpc": "2.0", "method": "notifications/cancelled",
                         "params": {"requestId": "host", "reason": "child subscription ended; re-listen and refetch"}}]
    assert child.subscription_id is None
    assert not relay._subscriptions.snapshot()
    assert not relay._inflight_requests


def test_reused_host_stream_id_cannot_receive_old_queued_events(tmp_path):
    relay = proxy.Proxy(sys.executable, "brain_mcp.server", str(tmp_path))
    relay._subscriptions.open(1, {"toolsListChanged": True}, lambda _: None)
    relay._subscriptions.emit({"method": "notifications/tools/list_changed"}, relay._send_to_client)
    previous = relay._outbound.get_nowait()
    assert isinstance(previous, SubscriptionEvent)
    relay._subscriptions.cancel(1)
    relay._subscriptions.open(1, {"toolsListChanged": True}, lambda _: None)
    assert not relay._subscriptions.current(previous.key)
    relay._subscriptions.emit({"method": "notifications/tools/list_changed"}, relay._send_to_client)
    current = relay._outbound.get_nowait()
    assert relay._subscriptions.current(current.key)
    assert current.frame["params"]["_meta"][SUBSCRIPTION_ID] == 1


def test_changed_binding_refuses_activation_and_survives_state_restore(tmp_path, monkeypatch):
    _write_vault(tmp_path)
    monkeypatch.delenv("BRAIN_WORKSPACE_DIR", raising=False)
    inputs = {"workspace_env": None, "vault_root_env": None, "start_dir": tmp_path}
    relay = proxy.Proxy(sys.executable, "brain_mcp.server", str(tmp_path), resolution_inputs=inputs)
    monkeypatch.setattr(proxy, "resolve_brain_target", lambda **kwargs: SimpleNamespace(vault_root=str(tmp_path / "other"), workspace_dir=None))
    assert relay._assess_startup() == "target_changed"
    assert relay.vault_root == str(tmp_path)
    state = relay._transport_state(1, proxy.RawLineReader(0))
    restored = proxy.Proxy(sys.executable, "brain_mcp.server", str(tmp_path))
    restored._restore_transport(state)
    assert restored._resolution_inputs == inputs
    assert restored._assess_startup() == "target_changed"
    assert restored._get_child() is None


def test_child_subscription_writes_leave_publication_gate_available(tmp_path):
    relay = proxy.Proxy(sys.executable, "brain_mcp.server", str(tmp_path))
    relay._client_protocol = "modern"
    relay._initial_protocol_selected.set()
    relay._child = child = _FakeChild()
    written = threading.Event()
    failures = []

    def send(frame):
        if not relay._publication_gate.acquire(blocking=False):
            failures.append("child output cannot drain during bridge write")
        else:
            relay._publication_gate.release()
        child.sent.append(frame)
        written.set()

    child.send = send
    relay._subscriptions.open("host", {"resourceSubscriptions": ["brain://resource"]}, lambda _: None)
    relay._start_recovery_loop()
    try:
        relay._request_subscription_sync()
        assert written.wait(3)
        assert not failures
        assert child.sent[-1]["method"] == "subscriptions/listen"
    finally:
        relay._initiate_shutdown()
        relay._recovery_thread_handle.join(3)


def test_confirmed_child_ack_refetches_resource_changed_during_resync(tmp_path, monkeypatch):
    relay = proxy.Proxy(sys.executable, "brain_mcp.server", str(tmp_path))
    relay._client_protocol = "modern"
    relay._initial_protocol_selected.set()
    acknowledged = []
    relay._subscriptions.open("host", {"resourceSubscriptions": ["brain://resource"]}, acknowledged.append)
    assert acknowledged[0]["method"] == "notifications/subscriptions/acknowledged"
    # A URI may change before the worker installs its new upstream coverage.
    # There is no upstream event to relay in that interval. The real registration
    # barrier must trigger a refetch, not just successful writing to the pipe.
    ack = {"method": "notifications/subscriptions/acknowledged", "params": {
        "notifications": {"resourceSubscriptions": ["brain://resource"]}, "_meta": {SUBSCRIPTION_ID: "internal"}}}
    child = _ReadableFakeChild([json.dumps(ack).encode()])
    child.subscription_id = "internal"
    relay._child = child
    monkeypatch.setattr(proxy.sys, "platform", "win32")
    events = []

    def publish(event):
        events.append(event)
        relay._shutdown = True

    monkeypatch.setattr(relay, "_send_to_client", publish)
    relay._reader_thread()
    assert len(events) == 1
    assert events[0].frame == {"jsonrpc": "2.0", "method": "notifications/resources/updated",
                               "params": {"uri": "brain://resource", "_meta": {SUBSCRIPTION_ID: "host"}}}


@pytest.mark.parametrize("windows_wait", [False, True])
def test_nonreading_child_cannot_strand_bridge_writer(monkeypatch, windows_wait):
    child = proxy.ChildProcess(sys.executable, "unused")
    child._proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], stdin=subprocess.PIPE)
    if windows_wait:
        monkeypatch.setattr(proxy.sys, "platform", "win32")
    try:
        began = time.monotonic()
        with pytest.raises(TimeoutError):
            child.send_control({"payload": "x" * (2 * 1024 * 1024)}, timeout=.05)
        assert time.monotonic() - began < 2
        child.reap(3)
        assert child.poll() is not None
        assert os.get_blocking(child._proc.stdin.fileno())
        assert child._send_lock.acquire(blocking=False)
        child._send_lock.release()
    finally:
        child.kill()
        child.reap(3)
