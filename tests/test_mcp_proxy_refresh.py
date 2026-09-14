"""Idle lifecycle handover, transport discovery and model-visible drift."""

import json
import threading
from dataclasses import replace

import pytest

from brain_mcp import proxy
from brain_mcp._proxy_controls import add_control_discovery, control_response, tool_definitions
from brain_mcp._command_adapter import application_interface_header
from _application.registry import current_application_catalogue
from test_mcp_proxy import _FakeChild, _make_inprocess_proxy, _write_vault


def test_refresh_leaves_inflight_work_running(tmp_path):
    _write_vault(tmp_path)
    relay = proxy.Proxy("python", "server", str(tmp_path))
    child = _FakeChild()
    relay._child = child
    relay._client_protocol = "modern"
    relay._inflight_requests[1] = ({"method": "tools/call", "id": 1}, 0)
    assert relay._request_refresh(explicit=True) == "server_busy"
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
    relay._refresh_child()
    assert relay._child is child and not child.killed
    assert relay._interface_header is sentinel
    assert relay._refresh_code == "server_refresh_blocked"
    assert relay._refresh_done.is_set() and not relay._restart_in_progress


def test_refresh_uses_existing_recovery_owner(tmp_path, monkeypatch):
    _write_vault(tmp_path)
    relay = proxy.Proxy("python", "server", str(tmp_path))
    relay._client_protocol = "modern"
    calls = []
    monkeypatch.setattr(relay, "_start_child", lambda: calls.append(threading.current_thread().name) or True)
    relay._start_recovery_loop()
    try:
        assert relay._request_refresh(explicit=True) is None
        assert calls == ["child-recovery"]
    finally:
        relay._initiate_shutdown()
        relay._recovery_thread_handle.join(timeout=2)


def test_controls_are_not_duplicated_on_child_pagination():
    original = {"jsonrpc": "2.0", "id": 1, "result": {"tools": [{"name": "artefact_read"}], "nextCursor": "next"}}
    first = add_control_discovery(original, {"method": "tools/list", "params": {}})
    assert len(first["result"]["tools"]) == 3
    assert first["result"]["nextCursor"] == "next"
    assert len(original["result"]["tools"]) == 1
    assert add_control_discovery(original, {"method": "tools/list", "params": {"cursor": "next"}}) == original
    assert len(json.dumps(tool_definitions()).encode()) < 1100


def test_drift_is_model_visible_without_corrupting_envelope():
    envelope = {"schema": "brain.command-result/1", "status": "ok", "warnings": [], "result": {"text": "hello"}}
    original = {"jsonrpc": "2.0", "id": 1, "result": {
        "structuredContent": envelope, "content": proxy.result_text_wire("artefact.read: ok", envelope)}}
    decorated = proxy._decorate_with_drift_note(original, "0.10.0", "0.10.1")
    result = decorated["result"]
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
    assert result["structuredContent"]["warnings"][0]["code"] == "follow_up_required"
    assert "Restart MCP" in result["content"][0]["text"]
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
    assert [tool["name"] for tool in responses[0]["result"]["tools"]] == ["brain_proxy_status", "brain_proxy_refresh"]
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
