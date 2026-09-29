"""Proxy forwarding, child-pipe transport and launcher boundaries."""

import json
import os
import sys
import pytest
from brain_mcp import proxy as proxy_mod

from proxy_test_support import (
    PYTHON,
    _FakeChild,
    _fake_proxy_threads,
    _ReadableFakeChild,
    _echo_server_script,
    _find_by_id,
    _launch_proxy,
    _make_inprocess_proxy,
    _make_inprocess_proxy_with_real_threads,
    _make_jsonrpc,
    _make_response,
    _read_all_responses,
    _read_responses,
    _write_vault,
)

pytestmark = pytest.mark.slow


def test_child_process_marks_the_running_proxy_protocol(monkeypatch):
    captured = {}

    class Process:
        pid = 4321
        stderr = ()
        stdout = type("Output", (), {"fileno": lambda self: 123})()

    def popen(command, **kwargs):
        captured.update(command=command, **kwargs)
        return Process()

    monkeypatch.setattr(proxy_mod.subprocess, "Popen", popen)
    _fake_proxy_threads(monkeypatch)
    child = proxy_mod.ChildProcess(PYTHON, "brain_mcp.server")

    child.start()

    assert captured["env"][proxy_mod.PROXY_PROTOCOL_ENV] == str(
        proxy_mod.PROXY_PROTOCOL
    )


def test_non_string_jsonrpc_method_is_forwarded_without_crashing_diagnostics(
    tmp_path,
    monkeypatch,
):
    raw = json.dumps(
        {"jsonrpc": "2.0", "id": 7, "method": {"PatientSSN123": "secret"}}
    ).encode("utf-8")
    proxy, _sent_to_client = _make_inprocess_proxy(tmp_path, monkeypatch, [raw])
    child = _FakeChild()
    with proxy._child_lock:
        proxy._child = child
    events = []
    monkeypatch.setattr(proxy_mod, "_op_event", lambda event, **fields: events.append((event, fields)))

    proxy.run()

    assert child.sent == [json.loads(raw)]
    assert events == [
        (
            "frame.forwarded",
            {"family": "proxy-rpc", "frame_seq": 1, "method": "other"},
        )
    ]


def test_invalid_jsonrpc_request_id_isolated_from_following_request(
    tmp_path,
    monkeypatch,
):
    invalid = {"jsonrpc": "2.0", "id": [], "method": "ping"}
    valid = {"jsonrpc": "2.0", "id": 7, "method": "ping"}
    proxy, sent_to_client = _make_inprocess_proxy(
        tmp_path,
        monkeypatch,
        [json.dumps(invalid).encode("utf-8"), json.dumps(valid).encode("utf-8")],
    )
    child = _FakeChild()
    with proxy._child_lock:
        proxy._child = child

    proxy.run()

    assert child.sent == [valid]
    assert sent_to_client == [
        {
            "jsonrpc": "2.0",
            "id": None,
            "error": {
                "code": -32600,
                "message": "Invalid Request: JSON-RPC id must be a string or integer",
            },
        }
    ]


def test_tools_call_notification_is_rejected_before_interface_acceptance(
    tmp_path,
    monkeypatch,
):
    tool_notification = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": "example_mutate", "arguments": {}},
    }
    valid_notification = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    proxy, sent_to_client = _make_inprocess_proxy(
        tmp_path,
        monkeypatch,
        [
            json.dumps(tool_notification).encode("utf-8"),
            json.dumps(valid_notification).encode("utf-8"),
        ],
    )
    child = _FakeChild()
    with proxy._child_lock:
        proxy._child = child

    def fail_prepare(_request):
        raise AssertionError("tools/call notification reached interface acceptance")

    monkeypatch.setattr(proxy, "_prepare_interface_call", fail_prepare)

    proxy.run()

    assert child.sent == [valid_notification]
    assert sent_to_client == []


class TestWindowsChildPipeReads:
    @staticmethod
    def _fail_select(*args, **kwargs):
        raise AssertionError("Windows pipe reads must not use select")

    def test_initialize_read_uses_blocking_thread_instead_of_select(self, tmp_path, monkeypatch):
        proxy, _ = _make_inprocess_proxy_with_real_threads(tmp_path, monkeypatch)
        child = _ReadableFakeChild([_make_response(1, {"ok": True}).encode("utf-8")])

        monkeypatch.setattr(proxy_mod.sys, "platform", "win32")
        monkeypatch.setattr(proxy_mod.select, "select", self._fail_select)

        assert proxy._read_with_timeout(child, 1) == {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"ok": True},
        }

    def test_reader_thread_uses_blocking_readline_instead_of_select(self, tmp_path, monkeypatch):
        proxy, sent_to_client = _make_inprocess_proxy_with_real_threads(tmp_path, monkeypatch)
        child = _ReadableFakeChild([_make_response(2, {"ok": True}).encode("utf-8")])
        with proxy._child_lock:
            proxy._child = child

        monkeypatch.setattr(proxy_mod.sys, "platform", "win32")
        monkeypatch.setattr(proxy_mod.select, "select", self._fail_select)

        def send_and_stop(obj: dict) -> None:
            sent_to_client.append(obj)
            proxy._initiate_shutdown()

        monkeypatch.setattr(proxy, "_send_to_client", send_and_stop)

        proxy._reader_thread()

        assert sent_to_client == [{
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"ok": True},
        }]


class TestMessageForwarding:
    """Proxy forwards JSON-RPC requests to echo server and returns responses."""

    def test_forward_request_and_response(self, tmp_path):
        """Proxy forwards a request to an echo server and relays the response."""
        _write_vault(tmp_path)
        server_script = _echo_server_script(tmp_path)
        proc = _launch_proxy(tmp_path, server_script)
        try:
            # 1. Send initialize (required first)
            proc.stdin.write(_make_jsonrpc("initialize", id=1,
                                           params={"protocolVersion": "2024-11-05",
                                                   "clientInfo": {"name": "test", "version": "0"}}))
            proc.stdin.flush()

            # 2. Read initialize response
            init_msgs = _read_responses(proc, timeout=5.0, count=1)
            assert len(init_msgs) == 1, f"Expected init response, got: {init_msgs}"
            assert init_msgs[0].get("id") == 1
            assert "result" in init_msgs[0]

            # 3. Send a real request
            proc.stdin.write(_make_jsonrpc("ping", id=2, params={"name": "ping"}))
            proc.stdin.flush()

            # 4. Read response
            msgs = _read_responses(proc, timeout=5.0, count=1)
            assert len(msgs) >= 1, "No response received for tools/call"
            resp = _find_by_id(msgs, 2)
            if resp is None:
                # May need to drain more
                more = _read_all_responses(proc, timeout=3.0)
                resp = _find_by_id(more, 2)
            assert resp is not None, f"No response with id=2 found. Got: {msgs}"
            assert "result" in resp, f"Expected result in response, got: {resp}"
            assert resp["result"]["content"][0]["text"] == "echo:ping"

        finally:
            proc.terminate()
            proc.wait(timeout=5)


class TestMainEnvCaptureOrder:
    """proxy.main() must capture BRAIN_WORKSPACE_DIR / BRAIN_VAULT_ROOT BEFORE it
    mutates os.environ, so the heal layer receives the original (pre-mutation)
    values — guarding against a refactor that reintroduces the v0.46.0
    wrong-default footgun. proxy.main() is otherwise never exercised by the suite."""

    def test_main_captures_env_before_mutation(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["proxy.py", "/usr/bin/python3", "brain_mcp.server"])
        monkeypatch.setenv("BRAIN_WORKSPACE_DIR", "/orig/workspace")
        monkeypatch.setenv("BRAIN_VAULT_ROOT", "/orig/vault")

        captured = {}

        class _Stop(Exception):
            pass

        def fake_resolve_and_heal(*, workspace_env, vault_root_env, start_dir):
            captured["workspace_env"] = workspace_env
            captured["vault_root_env"] = vault_root_env
            # At call time os.environ must still hold the ORIGINAL value — main
            # must not have mutated BRAIN_VAULT_ROOT yet.
            captured["env_at_call"] = os.environ.get("BRAIN_VAULT_ROOT")
            raise _Stop()

        monkeypatch.setattr(proxy_mod, "resolve_brain_target", fake_resolve_and_heal)

        with pytest.raises(_Stop):
            proxy_mod.main()

        assert captured["workspace_env"] == "/orig/workspace"
        assert captured["vault_root_env"] == "/orig/vault"
        assert captured["env_at_call"] == "/orig/vault"


@pytest.mark.parametrize('failure', ['before_spawn', 'after_spawn'])
def test_private_seed_permission_is_consumed_only_by_a_launched_child(tmp_path, monkeypatch, failure):
    proxy, _responses = _make_inprocess_proxy(tmp_path, monkeypatch, [])
    from _bootstrap.consent_owner import ConsentOwner
    proxy._owner = ConsentOwner(tmp_path)
    options_seen = []

    class LaunchProbe(_FakeChild):
        def __init__(self, *_args):
            super().__init__()
            self.pid = None

        def start(self, **options):
            options_seen.append(options)
            if len(options_seen) == 1:
                if failure == 'after_spawn':
                    self.pid = 42
                raise OSError('simulated launch failure')
            self.pid = 43

    monkeypatch.setattr(proxy_mod, 'ChildProcess', LaunchProbe)
    try:
        assert proxy._start_child() is False
        assert proxy._start_child() is True
        assert options_seen[0]['owner_initialisation_allowed'] is True
        assert options_seen[1]['owner_initialisation_allowed'] is (failure == 'before_spawn')
        assert options_seen[0]['transport_identity'] == options_seen[1]['transport_identity']
    finally:
        proxy._initiate_shutdown()
