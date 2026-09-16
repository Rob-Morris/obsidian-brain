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
    (vault / ".brain-core" / "VERSION").write_text("0.56.0\n", encoding="utf-8")
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
            "params": {"name": "command_list", "arguments": {"page_size": 1}},
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
    assert len(responses[2]["result"]["tools"]) == 72
    call = responses[3]["result"]
    assert call["isError"] is False
    assert call["structuredContent"]["command"] == "command.list"
    assistant, user = call["content"]
    assert assistant["annotations"]["audience"] == ["assistant"]
    assert json.loads(assistant["text"]) == call["structuredContent"]
    assert user["annotations"]["audience"] == ["user"]
    assert user["text"] == "command.list: ok"


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
                result = await session.call_tool("command_list", {"page_size": 1})

        assert session.protocol_version == "2026-07-28"
        assert discovery.supported_versions == ["2026-07-28"]
        assert len(tools.tools) == 72
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
                        result = await session.call_tool("command_list", {})

        assert session.protocol_version == "2026-07-28"
        assert len(tools.tools) == 72
        assert result.is_error is True
        assert result.structured_content["error"]["code"] == (
            "proxy_restart_required"
        )

    anyio.run(exercise)


def test_stdio_status_filter_and_returned_remedy_enable_explicit_blanket_consent(tmp_path):
    async def exercise():
        vault = _vault(tmp_path)
        (vault / "README.md").write_text("This observation still needs its selected consent.\n", encoding="utf-8")
        params = StdioServerParameters(command=sys.executable, args=[str(SERVER)],
            env={**os.environ, "BRAIN_CAPTURE_VAULT": str(vault)})
        async with stdio_client(params) as streams:
            async with ClientSession(*streams) as session:
                session.adopt(DiscoverResult.model_validate(await session.send_discover("2026-07-28")))
                tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                properties = tools["access_status"].input_schema["properties"]
                assert "target_command_id" in properties
                assert "command_id" not in properties
                reduced = await session.call_tool("access_reduce", {
                    "reduction": {"kind": "commands", "commands": ["artefact.delete"]}})
                assert not reduced.is_error

                status = await session.call_tool("access_status", {"target_command_id": "artefact.delete"})
                assert not status.is_error, status
                envelope = status.structured_content
                assert envelope["command"] == "access.status"
                assert envelope["command_version"] == 3
                command = envelope["result"]["command"]
                assert command["command_id"] == "artefact.delete"
                assert command["state"] == "authorisation_required"
                remedy = command["next_action"]
                assert remedy["command_id"] == "access.status"
                repeated = await session.call_tool("access_status", {
                    item["name"]: item["value"] for item in remedy["arguments"]})
                assert not repeated.is_error
                assert repeated.structured_content["result"]["command"]["command_review"] == command["command_review"]

                granted = await session.call_tool("access_request", {"consent": {
                    "scope": "command", "command_id": command["command_id"], "review": command["command_review"]}})
                assert not granted.is_error, granted
                assert granted.structured_content["result"]["state"] == "authorised"
                authorised = await session.call_tool("access_status", {"target_command_id": "artefact.delete"})
                assert authorised.structured_content["result"]["command"]["state"] == "authorised"
                invalid = await session.call_tool("access_status", {"command_id": "artefact.delete"})
                assert invalid.is_error

                # Initial command access cannot authorise an explicitly selected,
                # ungranted operation; its fallback remedy must also be usable.
                prepared = await session.call_tool("access_prepare", {"preparation": {
                    "kind": "operation", "command_id": "vault.read-file", "arguments": {"path": "README.md"}}})
                assert not prepared.is_error, prepared.structured_content
                selected = await session.call_tool("vault_read-file", {"path": "README.md",
                    "brain_operation": prepared.structured_content["result"]["operation_id"]})
                assert selected.is_error
                denial = selected.structured_content["error"]
                assert denial["code"] == "authorisation_required"
                remedy = denial["next_action"]
                assert remedy["command_id"] == "access.status"
                arguments = {item["name"]: item["value"] for item in remedy["arguments"]}
                assert arguments == {"target_command_id": "vault.read-file"}
                recovered = await session.call_tool("access_status", arguments)
                assert not recovered.is_error, recovered
                assert recovered.structured_content["result"]["command"]["command_id"] == "vault.read-file"

    anyio.run(exercise)


def test_public_server_projects_only_the_authenticated_profile_ceiling(
    tmp_path,
    monkeypatch,
):
    vault = _vault(tmp_path)
    shared = vault / ".brain/config.yaml"
    shared.parent.mkdir(parents=True)
    shared.write_text("defaults:\n  default_profile: reader\n", encoding="utf-8")
    monkeypatch.setenv("BRAIN_VAULT_ROOT", str(vault))
    monkeypatch.delenv("BRAIN_OPERATOR_KEY", raising=False)
    public = server._build_public_mcp()

    tools = anyio.run(public.list_tools)
    names = tuple(tool.name for tool in tools)

    assert len(names) == 29
    assert "access_request" in names
    assert "artefact_delete" not in names
    assert "invocation_read" in names
    assert "access_prepare" in names
