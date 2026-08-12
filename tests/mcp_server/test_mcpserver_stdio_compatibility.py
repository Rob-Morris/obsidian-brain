"""End-to-end compatibility at the MCPServer protocol boundaries."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import anyio
import httpx2
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import DiscoverResult

from brain_mcp import server


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER = REPO_ROOT / "tests" / "fixtures" / "granular_mcp_stdio_server.py"


def _vault(tmp_path):
    vault = tmp_path / "Brain"
    (vault / ".brain-core").mkdir(parents=True)
    (vault / ".brain-core" / "VERSION").write_text("0.55.8\n", encoding="utf-8")
    return vault


def test_mcpserver_serves_a_2025_06_18_stdio_client(tmp_path):
    vault = _vault(tmp_path)
    messages = (
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "brain-legacy-smoke", "version": "1"},
            },
        },
        {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        },
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "command.list", "arguments": {"page_size": 1}},
        },
    )
    process = subprocess.Popen(
        [sys.executable, str(SERVER)],
        cwd=REPO_ROOT,
        env={**os.environ, "BRAIN_CAPTURE_VAULT": str(vault)},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    responses = {}
    try:
        for message in messages:
            process.stdin.write(json.dumps(message) + "\n")
            process.stdin.flush()
            if message.get("id") is not None:
                response = json.loads(process.stdout.readline())
                responses[response["id"]] = response
    finally:
        process.terminate()
        process.wait(timeout=5)

    assert responses[1]["result"]["protocolVersion"] == "2025-06-18"
    assert len(responses[2]["result"]["tools"]) == 78
    call = responses[3]["result"]
    assert call["isError"] is False
    assert call["structuredContent"]["command"] == "command.list"


def test_mcpserver_serves_a_2026_07_28_stdio_client(tmp_path):
    async def exercise():
        params = StdioServerParameters(
            command=sys.executable,
            args=[str(SERVER)],
            env={**os.environ, "BRAIN_CAPTURE_VAULT": str(_vault(tmp_path))},
        )
        async with stdio_client(params) as streams:
            async with ClientSession(*streams) as session:
                discovery = DiscoverResult.model_validate(
                    await session.send_discover("2026-07-28")
                )
                session.adopt(discovery)
                tools = await session.list_tools()
                result = await session.call_tool("command.list", {"page_size": 1})

        assert session.protocol_version == "2026-07-28"
        assert discovery.supported_versions == ["2026-07-28"]
        assert len(tools.tools) == 78
        assert result.is_error is False
        assert result.structured_content["command"] == "command.list"

    anyio.run(exercise)


def test_public_server_supports_stateless_2026_streamable_http(monkeypatch):
    monkeypatch.delenv("BRAIN_PROXY_PROTOCOL", raising=False)
    public = server._build_public_mcp()
    app = public.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=["127.0.0.1"]
        ),
    )

    async def exercise():
        transport = httpx2.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx2.AsyncClient(
                transport=transport,
                base_url="http://127.0.0.1",
            ) as client:
                async with streamable_http_client(
                    "http://127.0.0.1/mcp",
                    http_client=client,
                ) as streams:
                    async with ClientSession(*streams) as session:
                        discovery = DiscoverResult.model_validate(
                            await session.send_discover("2026-07-28")
                        )
                        session.adopt(discovery)
                        tools = await session.list_tools()
                        result = await session.call_tool("command.list", {})

        assert session.protocol_version == "2026-07-28"
        assert len(tools.tools) == 78
        assert result.is_error is True
        assert result.structured_content["error"]["code"] == (
            "proxy_restart_required"
        )

    anyio.run(exercise)
