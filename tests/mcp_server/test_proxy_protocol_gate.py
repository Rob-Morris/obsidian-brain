"""Replacement-server proxy protocol gate contracts."""

from __future__ import annotations

import asyncio

import pytest
from mcp import types
from mcp.server.fastmcp import FastMCP

from brain_mcp._command_adapter import application_interface_header
from brain_mcp._interface_protocol import (
    INTERFACE_HEADER_EXTENSION,
    PROXY_PROTOCOL,
    PROXY_PROTOCOL_ENV,
    interface_header_from_initialize,
)
from brain_mcp._proxy_protocol_gate import (
    inspect_running_proxy_protocol,
    install_proxy_protocol_gate,
)
from _application.registry import current_application_catalogue


def _header():
    return application_interface_header(current_application_catalogue())


def _request(name="brain_retired_aggregate"):
    return types.CallToolRequest(
        params=types.CallToolRequestParams(name=name, arguments={"ignored": True})
    )


def _call_handler(mcp, request):
    handler = mcp._mcp_server.request_handlers[types.CallToolRequest]
    return asyncio.run(handler(request)).root


@pytest.mark.parametrize(
    "environment, reason, value",
    (
        ({}, "missing", None),
        ({PROXY_PROTOCOL_ENV: "two"}, "malformed", None),
        ({PROXY_PROTOCOL_ENV: "02"}, "malformed", None),
        ({PROXY_PROTOCOL_ENV: "1"}, "too_old", 1),
        ({PROXY_PROTOCOL_ENV: "3"}, "too_new", 3),
    ),
)
def test_incompatible_running_marker_is_explicit(environment, reason, value):
    state = inspect_running_proxy_protocol(_header(), environ=environment)

    assert state.compatible is False
    assert state.reason == reason
    assert state.value == value


def test_compatible_running_marker_passes_through_before_tool_execution():
    mcp = FastMCP("gate-test")
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
    assert result.isError is False


def test_old_proxy_call_is_blocked_before_lookup_even_for_retired_name(monkeypatch):
    mcp = FastMCP("gate-test")
    lookups = []
    original = mcp._mcp_server._get_cached_tool_definition

    async def observed_lookup(name):
        lookups.append(name)
        return await original(name)

    monkeypatch.setattr(mcp._mcp_server, "_get_cached_tool_definition", observed_lookup)
    install_proxy_protocol_gate(mcp, _header(), environ={})
    result = _call_handler(mcp, _request())

    assert lookups == []
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "proxy_restart_required"
    assert result.structuredContent["error"]["effects"] == "none"
    assert result.structuredContent["error"]["details"] == {
        "requested_tool": "brain_retired_aggregate",
        "running_proxy_protocol": None,
        "running_proxy_protocol_raw": None,
        "required_proxy_protocol": {"minimum": 2, "maximum": 2},
        "reason": "missing",
    }


def test_gate_emits_valid_interface_header_while_calls_remain_blocked():
    mcp = FastMCP("gate-test")
    install_proxy_protocol_gate(mcp, _header(), environ={})

    options = mcp._mcp_server.create_initialization_options()
    wire = options.capabilities.experimental[INTERFACE_HEADER_EXTENSION]
    parsed = interface_header_from_initialize(
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
    mcp = FastMCP("gate-test")
    install_proxy_protocol_gate(mcp, _header(), environ={})

    with pytest.raises(RuntimeError, match="already installed"):
        install_proxy_protocol_gate(mcp, _header(), environ={})
    with pytest.raises(RuntimeError, match="already owned"):
        mcp._mcp_server.create_initialization_options(
            experimental_capabilities={INTERFACE_HEADER_EXTENSION: {"wrong": True}}
        )
