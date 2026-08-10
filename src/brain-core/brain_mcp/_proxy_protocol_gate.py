"""MCP transport gate for incompatible running proxy processes."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Mapping

from mcp import types
from mcp.server.fastmcp import FastMCP

from ._interface_protocol import (
    CommandInterfaceHeader,
    INTERFACE_HEADER_EXTENSION,
    PROXY_PROTOCOL_ENV,
    command_interface_wire,
)


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
    """Inspect only the running process marker, never proxy code on disk."""

    environ = os.environ if environ is None else environ
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
            elif value < header.minimum_proxy_protocol:
                reason = "too_old"
            elif value > header.maximum_proxy_protocol:
                reason = "too_new"
    return RunningProxyProtocol(
        raw=raw,
        value=value,
        minimum=header.minimum_proxy_protocol,
        maximum=header.maximum_proxy_protocol,
        compatible=reason is None,
        reason=reason,
    )


def install_proxy_protocol_gate(
    mcp: FastMCP,
    header: CommandInterfaceHeader,
    *,
    environ: Mapping[str, str] | None = None,
) -> RunningProxyProtocol:
    """Emit the interface header and intercept calls before FastMCP lookup."""

    low_level = mcp._mcp_server
    if getattr(low_level, "_brain_proxy_protocol_gate_installed", False):
        raise RuntimeError("proxy protocol gate is already installed")
    state = inspect_running_proxy_protocol(header, environ=environ)
    wire_header = command_interface_wire(header)

    create_options = low_level.create_initialization_options

    def create_initialization_options(
        notification_options=None,
        experimental_capabilities=None,
    ):
        capabilities = dict(experimental_capabilities or {})
        existing = capabilities.get(INTERFACE_HEADER_EXTENSION)
        if existing is not None and existing != wire_header:
            raise RuntimeError("brainCommandInterface capability is already owned")
        capabilities[INTERFACE_HEADER_EXTENSION] = wire_header
        return create_options(notification_options, capabilities)

    original_call = low_level.request_handlers[types.CallToolRequest]

    async def gated_call(request: types.CallToolRequest):
        if state.compatible:
            return await original_call(request)
        return types.ServerResult(_proxy_restart_required(request, state))

    low_level.create_initialization_options = create_initialization_options
    low_level.request_handlers[types.CallToolRequest] = gated_call
    low_level._brain_proxy_protocol_gate_installed = True
    return state


def _proxy_restart_required(
    request: types.CallToolRequest,
    state: RunningProxyProtocol,
) -> types.CallToolResult:
    tool_name = request.params.name
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
    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text",
                text=(
                    "proxy_restart_required: "
                    f"{message} Running protocol={state.raw!r}; "
                    f"required={state.minimum}..{state.maximum}."
                ),
            )
        ],
        structuredContent=payload,
        isError=True,
    )
