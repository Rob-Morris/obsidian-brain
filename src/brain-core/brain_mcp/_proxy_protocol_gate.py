"""MCP transport gate for incompatible running proxy processes."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Mapping

from mcp import types
from mcp.server import MCPServer
from mcp_types import CallToolRequestParams

from ._interface_protocol import (
    CommandInterfaceHeader,
    INTERFACE_HEADER_EXTENSION,
    MIN_DISPATCH_PROXY_PROTOCOL,
    PROXY_PROTOCOL_ENV,
    command_interface_wire,
)
from ._result_content import result_text_content


PROXY_GATE_RESULT_SCHEMA = "brain.proxy-gate-result/1"


@dataclass(frozen=True, slots=True)
class RunningProxyProtocol:
    raw: str | None
    value: int | None
    minimum: int
    maximum: int
    compatible: bool
    reason: str | None


def inspect_running_proxy_protocol(
    header: CommandInterfaceHeader,
    *,
    environ: Mapping[str, str] | None = None,
) -> RunningProxyProtocol:
    """Gate dispatch independently of the wider restart-negotiation range."""

    environ = os.environ if environ is None else environ
    minimum = max(header.minimum_proxy_protocol, MIN_DISPATCH_PROXY_PROTOCOL)
    raw = environ.get(PROXY_PROTOCOL_ENV)
    value = None
    reason = None
    if raw is None:
        reason = "missing"
    else:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            reason = "malformed"
        else:
            if value < 1 or str(value) != raw:
                value = None
                reason = "malformed"
            elif value < minimum:
                reason = "too_old"
            elif value > header.maximum_proxy_protocol:
                reason = "too_new"
    return RunningProxyProtocol(
        raw=raw,
        value=value,
        minimum=minimum,
        maximum=header.maximum_proxy_protocol,
        compatible=reason is None,
        reason=reason,
    )


def install_proxy_protocol_gate(
    mcp: MCPServer,
    header: CommandInterfaceHeader,
    *,
    environ: Mapping[str, str] | None = None,
) -> RunningProxyProtocol:
    """Emit the interface header and intercept calls before MCPServer lookup."""

    low_level = mcp._lowlevel_server
    if getattr(low_level, "_brain_proxy_protocol_gate_installed", False):
        raise RuntimeError("proxy protocol gate is already installed")
    state = inspect_running_proxy_protocol(header, environ=environ)
    wire_header = command_interface_wire(header)

    original_capabilities = low_level.get_capabilities

    def get_capabilities(
        notification_options=None,
        experimental_capabilities=None,
        extensions=None,
        *,
        protocol_version=None,
    ):
        capabilities = dict(experimental_capabilities or {})
        existing = capabilities.get(INTERFACE_HEADER_EXTENSION)
        if existing is not None and existing != wire_header:
            raise RuntimeError("brainCommandInterface capability is already owned")
        capabilities[INTERFACE_HEADER_EXTENSION] = wire_header
        return original_capabilities(
            notification_options,
            capabilities,
            extensions,
            protocol_version=protocol_version,
        )

    original_call = low_level.get_request_handler("tools/call")
    if original_call is None:
        raise RuntimeError("MCPServer tools/call handler is missing")

    async def gated_call(context, params: CallToolRequestParams):
        if state.compatible:
            return await original_call.handler(context, params)
        return _proxy_restart_required(params, state)

    low_level.get_capabilities = get_capabilities
    low_level.add_request_handler("tools/call", CallToolRequestParams, gated_call)
    low_level._brain_proxy_protocol_gate_installed = True
    return state


def _proxy_restart_required(
    params: CallToolRequestParams,
    state: RunningProxyProtocol,
) -> types.CallToolResult:
    tool_name = params.name
    message = (
        "Restart MCP to load the upgraded Brain proxy before calling any tool."
    )
    payload = {
        "schema": PROXY_GATE_RESULT_SCHEMA,
        "status": "error",
        "error": {
            "code": "proxy_restart_required",
            "message": message,
            "effects": "none",
            "retryable": False,
            "details": {
                "requested_tool": tool_name,
                "running_proxy_protocol": state.value,
                "running_proxy_protocol_raw": state.raw,
                "required_proxy_protocol": {
                    "minimum": state.minimum,
                    "maximum": state.maximum,
                },
                "reason": state.reason,
            },
            "next_action": {
                "instruction": "restart_mcp",
                "description": message,
            },
        },
    }
    content = result_text_content(
        (
            "proxy_restart_required: "
            f"{message} Running protocol={state.raw!r}; "
            f"required={state.minimum}..{state.maximum}."
        ),
        payload,
    )
    # Older proxies append drift guidance to the first block. Keep that
    # restart instruction human-readable without damaging the JSON fallback.
    return types.CallToolResult(
        content=[content[1], content[0]],
        structuredContent=payload,
        isError=True,
    )
