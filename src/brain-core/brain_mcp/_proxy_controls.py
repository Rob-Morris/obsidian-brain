"""Stdlib-only discovery and result projection for proxy lifecycle controls."""

from ._result_content import result_text_wire


STATUS_TOOL = "brain_proxy_status"
REFRESH_TOOL = "brain_proxy_refresh"
RESTART_TOOL = "brain_proxy_restart"
CONTROL_TOOLS = (STATUS_TOOL, REFRESH_TOOL, RESTART_TOOL)


def rediscovery_action() -> dict:
    """Name the host-owned protocol operation, not an application tool."""
    return {
        "instruction": "rediscover_tools",
        "owner": "mcp_host",
        "method": "tools/list",
        "params": {},
        "description": (
            "Use the host's tool-refresh action (tools/list; follow every nextCursor); "
            "if unavailable, ask the user to reconnect Brain MCP. "
            "command_list/command_describe and brain_proxy_refresh/brain_proxy_restart cannot do this. "
            "Then inspect brain_proxy_status({}), resolve remaining recovery, and use the new contract. "
            "Do not retry unchanged or repeat rediscovery if still blocked; report the diagnostic."
        ),
    }


def tool_definitions() -> list[dict]:
    """Describe transport controls independently of the child catalogue."""
    return [
        {
            "name": name,
            "description": description,
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "annotations": {
                "readOnlyHint": name == STATUS_TOOL,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        }
        for name, description in (
            (STATUS_TOOL, "Inspect Core, proxy, runtime and pending host tool rediscovery, with required recovery even without a server."),
            (RESTART_TOOL, "Recover Core/proxy/runtime, not host tool discovery. Idle POSIX image replacement preserves stdio and ends exceptional consent. Never installs."),
            (REFRESH_TOOL, "Refresh idle Core within the same runtime, not the host tool catalogue. Inspect returned recovery; runtime changes require restart. Never installs."),
        )
    ]


def control_response(request_id, tool: str, status: dict, *, code: str | None = None, effects: str = "none") -> dict:
    """Return a transport result, never a fabricated application receipt."""
    envelope = {"schema": "brain.proxy-result/1", "tool": tool,
                "status": "error" if code else "ok", "result": status}
    if code:
        envelope["error"] = {"code": code, "effects": effects}
        if code in {"runtime_restart_required", "runtime_installation_unavailable"}:
            envelope["guidance"] = (
                "MCP must be restarted before retrying this application call. "
                "Use brain_proxy_restart when idle; if unsupported or unsuccessful, restart MCP in the host. "
                "Repair an unavailable installed runtime first."
            )
        elif tool == RESTART_TOOL and code not in {"server_busy", "refresh_in_progress", "invalid_arguments"}:
            envelope["guidance"] = "Proxy handoff did not complete. Restart MCP in the host to recover."
    return {"jsonrpc": "2.0", "id": request_id, "result": {
        "content": result_text_wire(f"{tool}: {code or 'ok'}", envelope),
        "structuredContent": envelope, "isError": code is not None,
    }}


def add_control_discovery(response: dict, request: dict) -> dict:
    """Append controls once, preserving the child's cursor and declarations."""
    result = response.get("result")
    if not isinstance(result, dict):
        return response
    params = request.get("params")
    cursor = params.get("cursor") if isinstance(params, dict) else None
    if request.get("method") == "tools/list" and not cursor:
        tools = result.get("tools")
        if isinstance(tools, list):
            result = {**result, "tools": tools + tool_definitions()}
    if request.get("method") == "initialize":
        capabilities = result.get("capabilities", {})
        result = {**result, "capabilities": {**capabilities,
                  "tools": {**capabilities.get("tools", {}), "listChanged": True}}}
    return {**response, "result": result}
