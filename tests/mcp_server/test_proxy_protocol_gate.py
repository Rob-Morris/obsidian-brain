"""Replacement-server proxy protocol gate contracts."""

from __future__ import annotations

import asyncio
import json

import pytest
from mcp.server import MCPServer
from mcp_types import CallToolRequestParams

from brain_mcp._command_adapter import application_interface_header
from brain_mcp._interface_protocol import (
    INTERFACE_HEADER_EXTENSION,
    PROXY_PROTOCOL,
    PROXY_PROTOCOL_ENV,
    interface_header_from_response,
)
from brain_mcp._proxy_protocol_gate import (
    inspect_running_proxy_protocol,
    install_proxy_protocol_gate,
)
from _application.registry import current_application_catalogue


def _header():
    return application_interface_header(current_application_catalogue())


def _request(name="brain_retired_aggregate"):
    return CallToolRequestParams(name=name, arguments={"ignored": True})


def _call_handler(mcp, request):
    handler = mcp._lowlevel_server.get_request_handler("tools/call")
    return asyncio.run(handler.handler(None, request))


@pytest.mark.parametrize(
    "environment, reason, value",
    (
        ({}, "missing", None),
        ({PROXY_PROTOCOL_ENV: "two"}, "malformed", None),
        ({PROXY_PROTOCOL_ENV: "02"}, "malformed", None),
        ({PROXY_PROTOCOL_ENV: "1"}, "too_old", 1),
        ({PROXY_PROTOCOL_ENV: "2"}, "too_old", 2),
        ({PROXY_PROTOCOL_ENV: "3"}, "too_old", 3),
        ({PROXY_PROTOCOL_ENV: "5"}, "too_new", 5),
    ),
)
def test_incompatible_running_marker_is_explicit(environment, reason, value):
    state = inspect_running_proxy_protocol(_header(), environ=environment)

    assert state.compatible is False
    assert state.reason == reason
    assert state.value == value


def test_compatible_running_marker_passes_through_before_tool_execution():
    mcp = MCPServer("gate-test")
    calls = []

    @mcp.tool(name="brain_test_read")
    def read():
        calls.append("executed")
        return "ok"

    state = install_proxy_protocol_gate(
        mcp,
        _header(),
        environ={PROXY_PROTOCOL_ENV: str(PROXY_PROTOCOL)},
    )
    result = _call_handler(mcp, _request("brain_test_read"))

    assert state.compatible is True
    assert calls == ["executed"]
    assert result.is_error is False


def test_old_proxy_call_is_blocked_before_lookup_even_for_retired_name(monkeypatch):
    mcp = MCPServer("gate-test")
    lookups = []
    original = mcp._tool_manager.get_tool

    def observed_lookup(name):
        lookups.append(name)
        return original(name)

    monkeypatch.setattr(mcp._tool_manager, "get_tool", observed_lookup)
    install_proxy_protocol_gate(mcp, _header(), environ={})
    result = _call_handler(mcp, _request())

    assert lookups == []
    assert result.is_error is True
    assert result.structured_content["error"]["code"] == "proxy_restart_required"
    user, assistant = result.content
    assert assistant.annotations.audience == ["assistant"]
    assert user.annotations.audience == ["user"]
    assert json.loads(assistant.text) == result.structured_content
    assert user.text.startswith("proxy_restart_required:")
    assert result.structured_content["error"]["effects"] == "none"
    assert result.structured_content["error"]["details"] == {
        "requested_tool": "brain_retired_aggregate",
        "running_proxy_protocol": None,
        "running_proxy_protocol_raw": None,
        "required_proxy_protocol": {"minimum": 4, "maximum": 4},
        "reason": "missing",
    }


def test_gate_emits_valid_interface_header_while_calls_remain_blocked():
    mcp = MCPServer("gate-test")
    install_proxy_protocol_gate(mcp, _header(), environ={})

    options = mcp._lowlevel_server.create_initialization_options()
    wire = options.capabilities.experimental[INTERFACE_HEADER_EXTENSION]
    parsed = interface_header_from_response(
        {
            "result": {
                "capabilities": {
                    "experimental": {INTERFACE_HEADER_EXTENSION: wire},
                },
            },
        }
    )

    assert parsed.fingerprint == _header().fingerprint


def test_gate_installation_and_extension_ownership_are_single_owner():
    mcp = MCPServer("gate-test")
    install_proxy_protocol_gate(mcp, _header(), environ={})

    with pytest.raises(RuntimeError, match="already installed"):
        install_proxy_protocol_gate(mcp, _header(), environ={})
    with pytest.raises(RuntimeError, match="already owned"):
        mcp._lowlevel_server.create_initialization_options(
            experimental_capabilities={INTERFACE_HEADER_EXTENSION: {"wrong": True}}
        )


def test_protocol_two_gate_survives_legacy_proxy_drift_decoration():
    mcp = MCPServer("old-proxy-transition")
    install_proxy_protocol_gate(mcp, _header(), environ={PROXY_PROTOCOL_ENV: "2"})
    result = _call_handler(mcp, _request()).model_dump(by_alias=True, mode="json")

    # Frozen 0.9.0 behavior: append the upgrade note to the first text block,
    # regardless of audience. A new decorator with old labels is insufficient.
    for block in result["content"]:
        if block["type"] == "text":
            block["text"] += "\n\nNote: MCP proxy has been upgraded (0.9.0 → 0.9.1). Restart MCP."
            break

    assert "Restart MCP" in result["content"][0]["text"]
    assert json.loads(result["content"][1]["text"]) == result["structuredContent"]
    assert result["structuredContent"]["error"]["code"] == "proxy_restart_required"
    assert result["structuredContent"]["error"]["effects"] == "none"
