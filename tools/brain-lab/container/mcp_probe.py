#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import threading
import tomllib
from pathlib import Path


CANONICAL_CONTRACT = "canonical"
LEGACY_CONTRACT = "legacy"


def _read_message(stream, output: queue.Queue, errors: queue.Queue) -> None:
    try:
        while line := stream.readline():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                message = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            output.put(message)
    except Exception as exc:
        errors.put(str(exc))


def _await_response(messages: queue.Queue, errors: queue.Queue, request_id: int, timeout: float) -> dict:
    while True:
        if not errors.empty():
            raise RuntimeError(errors.get_nowait())
        try:
            message = messages.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError(f"timed out waiting for MCP response {request_id}") from exc
        if message.get("id") == request_id:
            if "error" in message:
                raise RuntimeError(f"MCP request {request_id} failed: {message['error']}")
            return message


def _validate_server(server: object, config: Path) -> tuple[list[str], dict[str, str]]:
    if not isinstance(server, dict) or not isinstance(server.get("command"), str):
        raise RuntimeError(f"Brain project MCP server is invalid: {config}")
    args = server.get("args", [])
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise RuntimeError(f"Brain project MCP server arguments are invalid: {config}")
    configured_environment = server.get("env", {})
    if not isinstance(configured_environment, dict):
        raise RuntimeError(f"Brain project MCP server environment is invalid: {config}")
    environment = os.environ.copy()
    environment.update({str(key): str(value) for key, value in configured_environment.items()})
    return [server["command"], *args], environment


def _load_server(vault: Path) -> tuple[list[str], dict[str, str]]:
    claude_config = vault / ".mcp.json"
    if claude_config.is_file():
        payload = json.loads(claude_config.read_text(encoding="utf-8"))
        servers = payload.get("mcpServers")
        if isinstance(servers, dict) and "brain" in servers:
            return _validate_server(servers["brain"], claude_config)

    codex_config = vault / ".codex" / "config.toml"
    if codex_config.is_file():
        payload = tomllib.loads(codex_config.read_text(encoding="utf-8"))
        servers = payload.get("mcp_servers")
        if isinstance(servers, dict) and "brain" in servers:
            return _validate_server(servers["brain"], codex_config)

    raise RuntimeError(
        "Brain project MCP server is missing from .mcp.json and .codex/config.toml"
    )


def _tool_call_payload(call_result: dict) -> dict:
    structured = call_result.get("structuredContent")
    if isinstance(structured, dict):
        return structured

    content = call_result.get("content")
    if not isinstance(content, list):
        raise RuntimeError("MCP tools/call returned no structured or text payload")
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        text = item.get("text")
        if not isinstance(text, str):
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise RuntimeError("MCP tools/call returned no JSON object payload")


def _probe_request(contract: str) -> tuple[str, dict]:
    if contract == LEGACY_CONTRACT:
        return "brain_init", {}
    if contract == CANONICAL_CONTRACT:
        return "command.list", {"dependency_tier": "portable", "page_size": 1}
    raise ValueError(f"unsupported MCP probe contract: {contract}")


def _validate_probe_payload(contract: str, payload: dict) -> None:
    if contract == LEGACY_CONTRACT:
        if payload.get("version") != "1" or not isinstance(payload.get("readiness"), str):
            raise RuntimeError("MCP brain_init tools/call returned no legacy bootstrap snapshot")
        return
    if payload.get("command") != "command.list":
        raise RuntimeError("MCP command.list tools/call returned no canonical envelope")


def probe(vault: Path, timeout: float, contract: str = CANONICAL_CONTRACT) -> dict:
    tool_name, arguments = _probe_request(contract)
    argv, environment = _load_server(vault)
    process = subprocess.Popen(
        argv,
        cwd=vault,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert process.stdin is not None and process.stdout is not None
    messages: queue.Queue = queue.Queue()
    errors: queue.Queue = queue.Queue()
    reader = threading.Thread(target=_read_message, args=(process.stdout, messages, errors), daemon=True)
    reader.start()

    def send(message: dict) -> None:
        process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        process.stdin.flush()

    try:
        send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "brain-lab", "version": "1"},
                },
            }
        )
        initialised = _await_response(messages, errors, 1, timeout)
        send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        listed = _await_response(messages, errors, 2, timeout)
        tools = listed.get("result", {}).get("tools")
        if not isinstance(tools, list) or not tools:
            raise RuntimeError("MCP tools/list returned no tools")
        if not any(tool.get("name") == tool_name for tool in tools):
            raise RuntimeError(f"MCP tools/list did not expose {tool_name}")
        send(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments,
                },
            }
        )
        called = _await_response(messages, errors, 3, timeout)
        call_result = called.get("result")
        if not isinstance(call_result, dict) or call_result.get("isError") is True:
            raise RuntimeError(f"MCP {tool_name} tools/call returned an error")
        _validate_probe_payload(contract, _tool_call_payload(call_result))
        return {
            "server": initialised.get("result", {}).get("serverInfo", {}),
            "read_only_round_trip": f"tools/call:{tool_name}",
            "tool_count": len(tools),
        }
    finally:
        try:
            process.stdin.close()
        except OSError:
            pass
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument(
        "--contract",
        choices=(LEGACY_CONTRACT, CANONICAL_CONTRACT),
        default=CANONICAL_CONTRACT,
        help="MCP interface generation to verify (default: canonical)",
    )
    args = parser.parse_args()
    result = probe(args.vault.resolve(), args.timeout, args.contract)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
