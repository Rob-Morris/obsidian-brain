"""Stdlib-only discovery and result projection for proxy lifecycle controls."""

from ._result_content import result_text_wire


STATUS_TOOL = "brain_proxy_status"
REFRESH_TOOL = "brain_proxy_refresh"
CONTROL_TOOLS = (STATUS_TOOL, REFRESH_TOOL)


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
            (STATUS_TOOL, "Inspect loaded and installed Brain Core/proxy versions and server refresh state, even when the server is unavailable."),
            (REFRESH_TOOL, "Refresh the idle Brain server from this Brain's installed files. Does not install releases or restart the proxy; busy work is left running."),
        )
    ]


def control_response(request_id, tool: str, status: dict, *, code: str | None = None) -> dict:
    """Return a transport result, never a fabricated application receipt."""
    envelope = {"schema": "brain.proxy-result/1", "tool": tool,
                "status": "error" if code else "ok", "result": status}
    if code:
        envelope["error"] = {"code": code, "effects": "none"}
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
