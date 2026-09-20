"""Idle lifecycle handover, transport discovery and model-visible drift."""

import json
import os
import threading
from dataclasses import replace

import pytest

from brain_mcp import proxy
from brain_mcp._proxy_controls import add_control_discovery, control_response, tool_definitions
from brain_mcp._command_adapter import application_interface_header
from _application.registry import current_application_catalogue
from test_mcp_proxy import _FakeChild, _make_inprocess_proxy, _write_vault
from brain_mcp._proxy_handoff import RawLineReader


def lifecycle_request(name="brain_proxy_refresh", request_id=1):
    return {"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
            "params": {"name": name, "arguments": {}}}


def drive_lifecycle(relay, name="brain_proxy_refresh"):
    """Drive the wired admission/preparation/completion path deterministically."""
    if relay._client_protocol is None:
        relay._client_protocol = "modern"
    reader = RawLineReader(0)
    incoming, outgoing = os.pipe()
    relay._wake_write = outgoing
    try:
        code = relay._admit_lifecycle(lifecycle_request(name), reader)
        if code:
            return code
        relay._prepare_lifecycle()
        relay._finish_prepared_handoff(reader)
        response = relay._outbound.get_nowait()["result"]["structuredContent"]
        return response.get("error", {}).get("code")
    finally:
        relay._wake_write = None
        os.close(incoming)
        os.close(outgoing)


def test_refresh_leaves_inflight_work_running(tmp_path):
    _write_vault(tmp_path)
    relay = proxy.Proxy("python", "server", str(tmp_path))
    child = _FakeChild()
    relay._child = child
    relay._client_protocol = "modern"
    relay._inflight_requests[1] = ({"method": "tools/call", "id": 1}, 0)
    assert relay._admit_lifecycle(lifecycle_request(), None) == "server_busy"
    assert relay._child is child and not child.killed
    assert relay._inflight_requests.keys() == {1}
    assert not relay._recovery_trigger.is_set()


def test_failed_candidate_keeps_old_child_and_header(tmp_path, monkeypatch):
    _write_vault(tmp_path)
    relay = proxy.Proxy("python", "server", str(tmp_path))
    child = _FakeChild()
    relay._child = child
    sentinel = object()
    relay._interface_header = sentinel
    relay._restart_in_progress = True

    def reject():
        relay._interface_header = None
        relay._interface_header_error = "incompatible"
        return False

    monkeypatch.setattr(relay, "_start_child", reject)
    code = relay._refresh_child()
    assert relay._child is child and not child.killed
    assert relay._interface_header is sentinel
    assert code == "server_refresh_blocked"


def test_refresh_uses_existing_recovery_owner(tmp_path, monkeypatch):
    _write_vault(tmp_path)
    relay = proxy.Proxy("python", "server", str(tmp_path))
    relay._client_protocol = "modern"
    calls = []
    monkeypatch.setattr(relay, "_start_child", lambda: calls.append(threading.current_thread().name) or True)
    relay._start_recovery_loop()
    try:
        assert relay._admit_lifecycle(lifecycle_request(), None) is None
        result = relay._outbound.get(timeout=3)["result"]
        assert result["isError"] is False
        assert calls == ["child-recovery"]
    finally:
        relay._initiate_shutdown()
        relay._recovery_thread_handle.join(timeout=2)


@pytest.mark.parametrize("tool", ["brain_proxy_refresh", "brain_proxy_restart"])
@pytest.mark.parametrize("invalid", ["missing", "incompatible"])
def test_same_version_invalid_legacy_interface_is_retried_after_repair(tmp_path, monkeypatch, tool, invalid):
    from brain_mcp._interface_protocol import command_interface_wire
    from brain_mcp._proxy_handoff import public_session
    import sys

    _write_vault(tmp_path)
    relay = proxy.Proxy(sys.executable, "fake-server", str(tmp_path))
    relay._client_protocol = "legacy"
    relay._initial_protocol_selected.set()
    relay._child = original = _FakeChild()
    relay._last_launched_version = proxy._read_brain_version_from_disk(str(tmp_path))
    header = application_interface_header(current_application_catalogue())
    valid = {"result": {"protocolVersion": "2025-06-18", "capabilities": {
        "experimental": {"brainCommandInterface": command_interface_wire(header)}}}}
    bad = {"result": {"protocolVersion": "2025-06-18", "capabilities": {}}} if invalid == "missing" else {
        "result": {"protocolVersion": "2025-06-18", "capabilities": {"experimental": {
            "brainCommandInterface": command_interface_wire(replace(header, interface_epoch=99))}}}}
    relay._init_request = {"id": 1, "method": "initialize"}
    relay._init_response = add_control_discovery(bad, relay._init_request)
    relay._public_session = public_session(relay._init_response)
    assert not relay._capture_interface_header(bad)
    candidates = []

    def candidate(*args):
        child = _FakeChild()
        candidates.append(child)
        return child

    monkeypatch.setattr(proxy, "ChildProcess", candidate)
    monkeypatch.setattr(relay, "_read_with_timeout", lambda *_args: bad)
    assert drive_lifecycle(relay, tool) == "server_refresh_blocked"
    assert relay._child is original and not original.killed
    assert candidates[0].killed
    monkeypatch.setattr(relay, "_read_with_timeout", lambda *_args: valid)
    assert relay._admit_lifecycle(lifecycle_request(tool), None) is None
    relay._prepare_lifecycle()
    assert relay._outbound.get_nowait()["method"] == "notifications/tools/list_changed"
    assert relay._outbound.get_nowait()["result"]["isError"] is False
    assert len(candidates) == 2 and relay._child is candidates[-1]
    assert original.killed and relay._interface_header == header
    assert relay._proxy_status()["lifecycle"]["phase"] == "ready"
    assert relay._last_launched_version == proxy._read_brain_version_from_disk(str(tmp_path))


def test_controls_are_not_duplicated_on_child_pagination():
    original = {"jsonrpc": "2.0", "id": 1, "result": {"tools": [{"name": "artefact_read"}], "nextCursor": "next"}}
    first = add_control_discovery(original, {"method": "tools/list", "params": {}})
    assert len(first["result"]["tools"]) == 4
    assert first["result"]["nextCursor"] == "next"
    assert len(original["result"]["tools"]) == 1
    assert add_control_discovery(original, {"method": "tools/list", "params": {"cursor": "next"}}) == original
    assert len(json.dumps(tool_definitions()).encode()) < 1500


def test_drift_is_model_visible_without_corrupting_envelope():
    envelope = {"schema": "brain.command-result/1", "status": "ok", "warnings": [], "result": {"text": "hello"}}
    original = {"jsonrpc": "2.0", "id": 1, "result": {
        "structuredContent": envelope, "content": proxy.result_text_wire("artefact.read: ok", envelope)}}
    decorated = proxy._decorate_with_drift_note(original, "0.10.0", "0.10.1")
    result = decorated["result"]
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
    assert result["structuredContent"]["warnings"][0]["code"] == "follow_up_required"
    assert "brain_proxy_restart" in result["content"][0]["text"]
    assert original["result"]["structuredContent"]["warnings"] == []
    assert len(result["content"][0]["text"].encode()) - len(json.dumps(envelope).encode()) < 240


def test_transport_results_never_claim_application_receipts():
    response = control_response(5, "brain_proxy_refresh", {}, code="server_busy")["result"]
    envelope = response["structuredContent"]
    assert response["isError"] is True
    assert json.loads(response["content"][0]["text"]) == envelope
    assert envelope["schema"] == "brain.proxy-result/1"
    assert envelope["error"] == {"code": "server_busy", "effects": "none"}


def test_control_discovery_and_status_survive_child_give_up(tmp_path, monkeypatch):
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "brain_proxy_status", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "brain_proxy_refresh", "arguments": {"path": "/untrusted"}}},
    ]
    relay, responses = _make_inprocess_proxy(tmp_path, monkeypatch, [json.dumps(request).encode() for request in requests])
    relay._gave_up = True
    relay.run()
    assert [tool["name"] for tool in responses[0]["result"]["tools"]] == ["brain_proxy_status", "brain_proxy_restart", "brain_proxy_refresh"]
    assert responses[1]["result"]["structuredContent"]["result"]["server"]["refresh"] == "unavailable"
    assert responses[2]["result"]["structuredContent"]["error"]["code"] == "invalid_arguments"


def test_changed_contract_requires_its_own_discovery_page(tmp_path):
    _write_vault(tmp_path)
    relay = proxy.Proxy("python", "server", str(tmp_path))
    original = application_interface_header(current_application_catalogue())
    relay._interface_header = original
    relay._advertised_tools = dict(original.tools)
    changed = replace(original, tools=tuple(
        (name, replace(tool, command_version=tool.command_version + 1)) if name == "artefact_read" else (name, tool)
        for name, tool in original.tools))
    relay._interface_header = changed
    request = {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {
        "name": "artefact_read", "arguments": {"reference": "note"}}}
    with pytest.raises(ValueError, match="contract changed"):
        relay._prepare_interface_call(request)
    with relay._inflight_lock:
        relay._record_tool_discovery({"result": {"tools": [{"name": "session_start"}]}}, {"method": "tools/list"})
    with pytest.raises(ValueError, match="contract changed"):
        relay._prepare_interface_call(request)
    with relay._inflight_lock:
        relay._record_tool_discovery({"result": {"tools": [{"name": "artefact_read"}]}}, {"method": "tools/list"})
    _, accepted = relay._prepare_interface_call(request)
    assert accepted.command_version == changed.tool("artefact_read").command_version
