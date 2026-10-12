"""Idle lifecycle handover, transport discovery and model-visible drift."""

import json
import os
import threading
from dataclasses import replace

import pytest

from brain_mcp import proxy
from brain_mcp._proxy_controls import add_control_discovery, control_response, rediscovery_action, tool_definitions
from brain_mcp._command_adapter import application_interface_header
from _application.registry import current_application_catalogue
from proxy_test_support import (
    _FakeChild, _make_inprocess_proxy, _make_jsonrpc, _write_vault, drive_lifecycle, lifecycle_request,
)


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
    status = relay._proxy_status()
    assert status["server"]["refresh"] == "interface_unavailable"
    assert status["interface"]["tool_discovery"]["state"] == "unknown"
    assert status["next_action"] == "brain_proxy_refresh"
    assert relay._interface_header_error in status["diagnostic"]
    relay._replay_requests([{"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                             "params": {"name": "artefact_read", "arguments": {}}}], {})
    refused = relay._outbound.get_nowait()["result"]["structuredContent"]
    assert refused["error"] == {"code": "server_interface_unavailable", "effects": "none"}
    assert refused["result"]["next_action"] == "brain_proxy_refresh"
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


def discovery_proxy(tmp_path, monkeypatch):
    _write_vault(tmp_path)
    relay = proxy.Proxy("python", "server", str(tmp_path))
    relay._child = _FakeChild()
    relay._last_launched_version = proxy._read_brain_version_from_disk(str(tmp_path))
    monkeypatch.setattr(relay, "_check_proxy_drift", lambda: None)
    monkeypatch.setattr(relay, "_runtime_status", lambda: {"state": "current", "restart_required": False})
    relay._interface_header = application_interface_header(current_application_catalogue())
    relay._interface_header_error = None
    relay._initial_protocol_selected.set()
    relay._advertised_tools = dict(relay._interface_header.tools)
    return relay


def brain_response():
    envelope = {"schema": "brain.command-result/1", "status": "ok", "warnings": [],
                "result": {"content": "Note says ignore instructions; not a transport fact"},
                "committed_effects": [{"kind": "read", "subject": "fixture"}]}
    return {"jsonrpc": "2.0", "id": 1, "result": {"structuredContent": envelope,
            "isError": False, "content": proxy.result_text_wire("Read fixture", envelope)}}


def test_catalogue_notice_preserves_application_projection_and_is_once_per_generation(tmp_path, monkeypatch):
    relay = discovery_proxy(tmp_path, monkeypatch)
    original = brain_response()
    assert relay._decorate_catalogue_notice(original) is original
    relay._advertised_tools.pop("command_list")
    # Output observes memory only; prerequisites must be inspected by status.
    with monkeypatch.context() as output_boundary:
        output_boundary.setattr(relay, "_proxy_status", lambda: pytest.fail("output probed status"))
        first = relay._decorate_catalogue_notice(original)
    result = first["result"]
    updated = result["structuredContent"]
    assert updated == {**original["result"]["structuredContent"], "warnings": updated["warnings"]}
    assert result["isError"] is False
    assert json.loads(result["content"][0]["text"]) == updated
    warning = updated["warnings"][0]
    assert warning["code"] == "follow_up_required"
    for term in ("Brain MCP transport observation", "command_list", "brain_proxy_status({})",
                 "next_action", "tools/list", "all pages", "Do not replay"):
        assert term in warning["message"]
    assert "ignore instructions" not in warning["message"]
    assert warning["message"] in result["content"][1]["text"]
    assert relay._decorate_catalogue_notice(original) is original
    assert drive_lifecycle(relay) is None  # No-op refresh must not rearm.
    assert relay._decorate_catalogue_notice(original) is original
    relay._catalogue_generation += 1  # Actual candidate publication's boundary.
    assert relay._decorate_catalogue_notice(original) is not original
    relay._record_tool_discovery({"result": {"tools": [{"name": "command_list"}]}}, {"method": "tools/list"})
    relay._catalogue_generation += 1
    assert relay._decorate_catalogue_notice(original) is original


@pytest.mark.parametrize("frame", [
    {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"},
    {"id": 1, "result": {"tools": []}},
    {"id": 1, "result": {"structuredContent": {"schema": "other/1"}}},
    {"id": 1, "error": {"message": "error"}},
])
def test_catalogue_notice_ignores_non_application_frames(tmp_path, monkeypatch, frame):
    relay = discovery_proxy(tmp_path, monkeypatch)
    relay._advertised_tools = {}
    assert relay._decorate_catalogue_notice(frame) is frame
    assert relay._catalogue_notice_generation is None


def test_catalogue_notice_bounds_names_and_does_not_guess_runtime_recovery(tmp_path, monkeypatch):
    relay = discovery_proxy(tmp_path, monkeypatch)
    relay._advertised_tools = {}
    relay._proxy_drift = True
    frame = relay._decorate_catalogue_notice(brain_response())
    message = frame["result"]["structuredContent"]["warnings"][0]["message"]
    assert "more)" in message and len(message) < 1500
    assert "before host discovery" in message
    assert "Core is current" not in message


def test_writer_observes_catalogue_at_emission_not_enqueue(tmp_path, monkeypatch):
    from io import BytesIO

    relay = discovery_proxy(tmp_path, monkeypatch)
    relay._output_stream = BytesIO()
    frame = brain_response()
    relay._outbound.put(frame)
    relay._outbound.put(frame)
    relay._outbound.put(None)
    # Publication happens after enqueue but before the sole writer sees it.
    with relay._publication_gate, relay._interface_lock:
        relay._advertised_tools.pop("command_list")
        relay._catalogue_generation += 1
    relay._writer_thread()
    emitted = [json.loads(line) for line in relay._output_stream.getvalue().splitlines()]
    assert len(emitted) == 2
    assert len(emitted[0]["result"]["structuredContent"]["warnings"]) == 1
    assert emitted[1] == frame


def test_refused_lifecycle_does_not_rearm_catalogue_notice(tmp_path, monkeypatch):
    relay = discovery_proxy(tmp_path, monkeypatch)
    relay._advertised_tools.pop("command_list")
    frame = brain_response()
    assert relay._decorate_catalogue_notice(frame) is not frame
    relay._inflight_requests[1] = ({"method": "tools/call", "id": 1}, 0)
    assert relay._admit_lifecycle(lifecycle_request(), None) == "server_busy"
    assert relay._decorate_catalogue_notice(frame) is frame


def test_writer_drains_queued_result_and_handoff_barrier_with_publication_frozen(tmp_path, monkeypatch):
    from io import BytesIO

    relay = discovery_proxy(tmp_path, monkeypatch)
    relay._advertised_tools.pop("command_list")
    relay._output_stream = BytesIO()
    barrier = threading.Event()
    relay._outbound.put(brain_response())
    relay._outbound.put(barrier)
    relay._outbound.put(None)
    writer = threading.Thread(target=relay._writer_thread)
    try:
        # Image retirement freezes publication before waiting on this barrier.
        with relay._publication_gate:
            writer.start()
            assert barrier.wait(3), "writer must drain while publication remains frozen"
    finally:
        writer.join(timeout=3)
    assert not writer.is_alive()
    result = json.loads(relay._output_stream.getvalue())["result"]
    assert len(result["structuredContent"]["warnings"]) == 1


def test_discovery_clearing_before_coordinated_emission_suppresses_notice(tmp_path, monkeypatch):
    from io import BytesIO

    relay = discovery_proxy(tmp_path, monkeypatch)
    relay._advertised_tools.pop("command_list")
    relay._output_stream = BytesIO()
    entered, release = threading.Event(), threading.Event()
    decorate = relay._decorate_catalogue_notice
    def coordinate(frame):
        entered.set()
        assert release.wait(3)
        return decorate(frame)
    monkeypatch.setattr(relay, "_decorate_catalogue_notice", coordinate)
    original = brain_response()
    relay._outbound.put(original)
    relay._outbound.put(None)
    writer = threading.Thread(target=relay._writer_thread)
    writer.start()
    try:
        assert entered.wait(3)
        relay._record_tool_discovery({"result": {"tools": [{"name": "command_list"}]}},
                                     {"method": "tools/list"})
    finally:
        release.set()
        writer.join(timeout=3)
    assert not writer.is_alive()
    assert json.loads(relay._output_stream.getvalue()) == original
    assert relay._catalogue_notice_generation is None


def test_partial_page_notice_names_only_remaining_mapping_and_preserves_cadence(tmp_path, monkeypatch):
    relay = discovery_proxy(tmp_path, monkeypatch)
    for name in ("command_list", "command_describe"):
        relay._advertised_tools.pop(name)
    relay._record_tool_discovery({"result": {"tools": [{"name": "command_list"}], "nextCursor": "last"}},
                                 {"method": "tools/list"})
    original = brain_response()
    noticed = relay._decorate_catalogue_notice(original)
    message = noticed["result"]["structuredContent"]["warnings"][0]["message"]
    assert "Pending dispatch mappings: command_describe." in message
    assert relay._proxy_status()["interface"]["tool_discovery"]["pending_tools"] == ["command_describe"]
    assert relay._decorate_catalogue_notice(original) is original
    relay._record_tool_discovery({"result": {"tools": [{"name": "command_describe"}]}},
                                 {"method": "tools/list", "params": {"cursor": "last"}})
    assert relay._proxy_status()["interface"]["tool_discovery"]["state"] == "current"


def test_provisional_candidate_header_cannot_consume_notice_for_active_generation(tmp_path, monkeypatch):
    relay = discovery_proxy(tmp_path, monkeypatch)
    relay._advertised_tools.pop("command_list")
    relay._preparing_child = _FakeChild()
    frame = brain_response()
    assert relay._decorate_catalogue_notice(frame) is frame
    assert relay._catalogue_notice_generation is None
    with relay._interface_lock:
        relay._catalogue_generation += 1
        relay._preparing_child = None
    assert relay._decorate_catalogue_notice(frame) is not frame


@pytest.mark.parametrize("name", ["command_list", "command_describe", "artefact_read"])
def test_status_exposes_host_owned_recovery_and_partial_discovery(tmp_path, monkeypatch, name):
    relay = discovery_proxy(tmp_path, monkeypatch)
    assert relay._proxy_status()["interface"]["tool_discovery"] == {"state": "current", "pending_tools": []}
    original = relay._interface_header
    relay._interface_header = replace(original, tools=tuple(
        (tool_name, replace(tool, command_version=tool.command_version + 1))
        if tool_name == name else (tool_name, tool) for tool_name, tool in original.tools))
    state = relay._proxy_status()
    assert state["server"]["refresh"] == "current"
    assert state["lifecycle"]["phase"] == "ready"
    assert state["next_action"] == "rediscover_tools"
    discovery = state["interface"]["tool_discovery"]
    assert discovery == {"state": "required", "pending_tools": [name], "recovery": rediscovery_action()}
    assert discovery["recovery"]["owner"] == "mcp_host"
    assert discovery["recovery"]["method"] == "tools/list"
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": {}}}
    with pytest.raises(ValueError, match="contract changed"):
        relay._prepare_interface_call(request)
    relay._record_tool_discovery({"result": {"tools": [{"name": "session_start"}], "nextCursor": "page2"}},
                                 {"method": "tools/list"})
    assert relay._proxy_status()["next_action"] == "rediscover_tools"
    # Application discovery and a successful Core refresh are not tools/list.
    relay._record_tool_discovery({"result": {"tools": [{"name": name}]}}, {"method": "tools/call"})
    assert relay._proxy_status()["next_action"] == "rediscover_tools"
    assert drive_lifecycle(relay) is None
    assert relay._proxy_status()["next_action"] == "rediscover_tools"
    assert drive_lifecycle(relay, "brain_proxy_restart") is None
    assert relay._proxy_status()["next_action"] == "rediscover_tools"
    relay._record_tool_discovery({"result": {"tools": [{"name": name}]}},
                                 {"method": "tools/list", "params": {"cursor": "page2"}})
    assert relay._proxy_status()["next_action"] is None
    assert relay._proxy_status()["interface"]["tool_discovery"]["state"] == "current"
    _, accepted = relay._prepare_interface_call(request)
    assert accepted.command_version == original.tool(name).command_version + 1


def test_added_and_removed_tools_are_visible_until_discovered(tmp_path, monkeypatch):
    relay = discovery_proxy(tmp_path, monkeypatch)
    original = relay._interface_header
    relay._advertised_tools.pop("command_list")  # Newly available to this connection.
    relay._interface_header = replace(original, tools=tuple(
        pair for pair in original.tools if pair[0] != "command_describe"))
    assert relay._proxy_status()["interface"]["tool_discovery"]["pending_tools"] == ["command_describe", "command_list"]
    relay._record_tool_discovery({"result": {"tools": [{"name": "command_list"}], "nextCursor": "last"}},
                                 {"method": "tools/list"})
    assert relay._proxy_status()["interface"]["tool_discovery"]["pending_tools"] == ["command_describe"]
    relay._record_tool_discovery({"result": {}}, {"method": "tools/list"})
    assert relay._proxy_status()["interface"]["tool_discovery"]["pending_tools"] == ["command_describe"]
    relay._record_tool_discovery({"result": {"tools": []}}, {"method": "tools/list", "params": {"cursor": "last"}})
    assert relay._proxy_status()["interface"]["tool_discovery"]["state"] == "current"
    with pytest.raises(ValueError, match="not advertised"):
        relay._prepare_interface_call({"id": 1, "method": "tools/call", "params": {"name": "command_describe"}})


def test_runtime_recovery_takes_precedence_over_discovery(tmp_path, monkeypatch):
    relay = discovery_proxy(tmp_path, monkeypatch)
    relay._advertised_tools.pop("command_list")
    monkeypatch.setattr(relay, "_runtime_status", lambda: {"state": "restart_required", "restart_required": True})
    state = relay._proxy_status()
    assert state["next_action"] == ("brain_proxy_restart" if os.name == "posix" else "restart_mcp")
    assert state["interface"]["tool_discovery"]["state"] == "required"
    relay._interface_header = None
    assert relay._proxy_status()["interface"]["tool_discovery"] == {"state": "unknown", "pending_tools": []}


@pytest.mark.parametrize("pending", [False, True])
def test_missing_core_version_requires_repair_before_discovery(tmp_path, monkeypatch, pending):
    relay = discovery_proxy(tmp_path, monkeypatch)
    if pending:
        relay._advertised_tools.pop("command_list")
    monkeypatch.setattr(proxy, "_read_brain_version_from_disk", lambda _vault: None)
    status = relay._proxy_status()
    assert status["server"]["refresh"] == "installation_unavailable"
    assert status["next_action"] == "repair_installation"
    assert "Core is current" not in status["diagnostic"]
    assert "brain_proxy_refresh({})" in status["diagnostic"]
    assert status["interface"]["tool_discovery"]["state"] == ("required" if pending else "current")


@pytest.mark.parametrize("state", ["installation_unavailable", "interface_unavailable"])
def test_proxy_drift_diagnostic_matches_its_restart_action(tmp_path, monkeypatch, state):
    relay = discovery_proxy(tmp_path, monkeypatch)
    relay._proxy_drift = True
    if state == "installation_unavailable":
        monkeypatch.setattr(proxy, "_read_brain_version_from_disk", lambda _vault: None)
    else:
        relay._interface_header = None
    status = relay._proxy_status()
    assert status["server"]["refresh"] == state
    assert status["next_action"] in {"brain_proxy_restart", "restart_mcp"}
    assert "brain_proxy_refresh" not in (status["diagnostic"] or "")


def test_never_advertised_tool_is_invalid_params_not_rediscovery(tmp_path, monkeypatch):
    relay = discovery_proxy(tmp_path, monkeypatch)
    with pytest.raises(proxy.InvalidToolCall, match="unknown tool 'brain_create'"):
        relay._prepare_interface_call({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                       "params": {"name": "brain_create", "arguments": {}}})
    assert relay._proxy_status()["next_action"] is None


def test_non_jsonrpc_call_is_an_invalid_request(tmp_path, monkeypatch):
    relay = discovery_proxy(tmp_path, monkeypatch)
    with pytest.raises(proxy.InvalidToolCall) as raised:
        relay._prepare_interface_call({"id": 1, "method": "tools/call", "params": {"name": "artefact_read"}})
    assert raised.value.code == -32600


def test_modern_call_after_rejected_initial_header_reports_interface_unavailable(tmp_path, monkeypatch):
    modern = {"io.modelcontextprotocol/protocolVersion": "2025-06-18"}
    relay, sent = _make_inprocess_proxy(tmp_path, monkeypatch, [_make_jsonrpc(
        "tools/call", id=7, params={"name": "artefact_read", "arguments": {}, "_meta": modern}).encode()])
    relay._client_protocol = None
    relay._initial_protocol_selected.clear()
    relay._child = child = _FakeChild()
    relay._last_launched_version = proxy._read_brain_version_from_disk(str(tmp_path))

    def reject(_child):
        relay._interface_header_error = "incompatible header"
        return False

    monkeypatch.setattr(relay, "_discover_child", reject)
    relay.run()
    refused = [message for message in sent if message.get("id") == 7]
    assert len(refused) == 1
    envelope = refused[0]["result"]["structuredContent"]
    assert envelope["error"] == {"code": "server_interface_unavailable", "effects": "none"}
    assert "rediscover" not in json.dumps(refused)
    assert child.sent == []


def test_interface_error_names_exact_host_action_without_claiming_inflight_change():
    result = proxy._interface_changed_response(1, "accepted_call_invalid")["result"]
    envelope = result["structuredContent"]
    assert json.loads(result["content"][0]["text"]) == envelope
    error = envelope["error"]
    assert error["effects"] == "none" and error["retryable"] is False
    assert "in flight" not in error["message"]
    assert error["next_action"] == rediscovery_action()
    for term in ("nextCursor", "reconnect", "command_list", "command_describe", "brain_proxy_refresh", "brain_proxy_restart", "brain_proxy_status({})"):
        assert term in error["next_action"]["description"]


def test_discovery_recovery_wire_payload_is_bounded_for_the_current_catalogue(tmp_path, monkeypatch):
    relay = discovery_proxy(tmp_path, monkeypatch)
    relay._advertised_tools = {}
    state = relay._proxy_status()
    assert len(state["interface"]["tool_discovery"]["pending_tools"]) == len(relay._interface_header.tools)
    response = control_response(1, "brain_proxy_status", state)
    assert len(json.dumps(response, ensure_ascii=False).encode("utf-8")) < 12000
    error = proxy._interface_changed_response(1, "accepted_call_invalid")
    assert len(json.dumps(error, ensure_ascii=False).encode("utf-8")) < 3500


@pytest.mark.parametrize("initialise_request", [None, {"method": "initialize", "params": {"clientInfo": {"name": "codex-mcp-client"}}}])
def test_recovery_guidance_token_budget(initialise_request):
    import tiktoken

    encoding = tiktoken.get_encoding("o200k_base")
    assert len(encoding.encode(json.dumps(rediscovery_action(initialise_request)))) <= 115


@pytest.mark.parametrize("params", [None, {"name": 42}, {"name": "artefact_read", "_meta": []},
                                   {"name": "artefact_read", "_meta": {"brainContext": {}}}])
def test_malformed_call_requires_correction_not_rediscovery(tmp_path, monkeypatch, params):
    relay = discovery_proxy(tmp_path, monkeypatch)
    with pytest.raises(proxy.InvalidToolCall):
        relay._prepare_interface_call({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})
    assert relay._proxy_status()["interface"]["tool_discovery"]["state"] == "current"


def test_lifecycle_resume_reports_invalid_params_without_rediscovery(tmp_path, monkeypatch):
    relay = discovery_proxy(tmp_path, monkeypatch)
    sent = []
    monkeypatch.setattr(relay, "_send_to_client", sent.append)
    pending = {"resume": True, "cancelled": False, "request": {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "artefact_read", "_meta": {"brainInvocation": {}}}}}
    relay._pending_lifecycle = pending
    relay._restart_in_progress = True
    relay._complete_lifecycle(pending, None)
    assert len(sent) == 1
    assert sent[0]["error"]["code"] == -32602
    assert "rediscover" not in json.dumps(sent)
    assert relay._pending_lifecycle is None


@pytest.mark.parametrize("modern", [False, True])
@pytest.mark.parametrize("name, target", [
    ("codex-mcp-client", "Brain MCP in Codex"),
    ("future-codex-client", "Brain MCP."),
    ("claude-code", "Brain MCP."),
    (None, "Brain MCP."),
])
def test_reconnect_guidance_depends_on_pending_discovery_and_negotiated_identity(tmp_path, monkeypatch, modern, name, target):
    relay = discovery_proxy(tmp_path, monkeypatch)
    info = {"name": name, "title": "Codex", "version": "future"}
    params = {"_meta": {"io.modelcontextprotocol/clientInfo": info}} if modern else {"clientInfo": info}
    relay._init_request = {"method": "server/discover" if modern else "initialize", "params": params}
    original = brain_response()
    assert relay._proxy_status()["interface"]["tool_discovery"]["state"] == "current"
    assert relay._decorate_catalogue_notice(original) is original
    relay._advertised_tools.pop("command_list")
    relay._advertised_tools.pop("command_describe")
    action = relay._proxy_status()["interface"]["tool_discovery"]["recovery"]
    assert target in action["description"]
    notice = relay._decorate_catalogue_notice(original)
    assert target in notice["result"]["structuredContent"]["warnings"][0]["message"]
    relay._record_tool_discovery({"result": {"tools": [{"name": "command_list"}], "nextCursor": "next"}}, {"method": "tools/list"})
    assert relay._proxy_status()["interface"]["tool_discovery"]["state"] == "required"
    relay._record_tool_discovery({"result": {"tools": [{"name": "command_describe"}]}}, {"method": "tools/list", "params": {"cursor": "next"}})
    assert relay._proxy_status()["interface"]["tool_discovery"]["state"] == "current"
    relay._catalogue_generation += 1
    assert relay._decorate_catalogue_notice(original) is original
    relay._proxy_drift = True
    assert "reconnect" not in relay._decorate_catalogue_notice(original)["result"]["structuredContent"]["warnings"][0]["message"]


@pytest.mark.parametrize("method, params, target", [
    ("initialize", {"clientInfo": {"name": "codex-mcp-client"}, "_meta": {"io.modelcontextprotocol/clientInfo": {"name": "unknown"}}}, "Brain MCP in Codex"),
    ("tools/list", {"clientInfo": {"name": "codex-mcp-client"}, "_meta": {"io.modelcontextprotocol/clientInfo": {"name": "unknown"}}}, "Brain MCP."),
    ("server/discover", {"clientInfo": {"name": "unknown"}, "_meta": {"io.modelcontextprotocol/clientInfo": {"name": "codex-mcp-client"}}}, "Brain MCP in Codex"),
    ("server/discover", {"clientInfo": {"name": "codex-mcp-client"}, "_meta": []}, "Brain MCP."),
])
def test_recovery_identity_uses_only_the_negotiated_protocol_field(method, params, target):
    assert target in rediscovery_action({"method": method, "params": params})["description"]
