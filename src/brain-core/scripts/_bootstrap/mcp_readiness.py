"""Observed stdio readiness using a persisted command and an ordinary read-only tool."""

from __future__ import annotations

import json
from collections import deque
import os
from pathlib import Path
import queue
import subprocess
import threading
import time


def verify_command(server: dict, workspace: Path, expected_vault: Path, *, timeout: float = 30) -> None:
    """Verify protocol, advertised command and resolved identity; no client trust changes."""
    environment = dict(os.environ)
    environment.pop("BRAIN_VAULT_ROOT", None)
    environment.pop("BRAIN_WORKSPACE_DIR", None)
    environment.pop("BRAIN_OWNER_CHANNEL", None)
    environment.update(server.get("env", {}))
    process = subprocess.Popen([server["command"], *server["args"]], cwd=workspace, env=environment,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True)
    messages = queue.Queue()
    diagnostics = deque(maxlen=12)

    def read_diagnostics():
        for line in process.stderr:
            diagnostics.append(line.rstrip()[:1000])

    stderr_reader = threading.Thread(target=read_diagnostics, daemon=True)
    stderr_reader.start()

    def read():
        try:
            for line in process.stdout:
                messages.put(json.loads(line))
        except (ValueError, OSError) as exc:
            messages.put(exc)
        finally:
            messages.put(None)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout

    def exchange(identifier, method, params):
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params}) + "\n")
        process.stdin.flush()
        while True:
            try:
                response = messages.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty as exc:
                raise RuntimeError("MCP normal-call verification timed out") from exc
            if response is None or isinstance(response, Exception):
                stderr_reader.join(timeout=1)
                raise RuntimeError(f"MCP closed or emitted invalid protocol during verification: {response}; " + "\n".join(diagnostics))
            if not isinstance(response, dict):
                raise RuntimeError("MCP emitted a non-object protocol message")
            if response.get("id") == identifier:
                if "error" in response:
                    raise RuntimeError(f"MCP verification failed: {response['error']}")
                return response.get("result", {})

    try:
        exchange(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                  "clientInfo": {"name": "brain-registration-verification", "version": "1"}})
        process.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        process.stdin.flush()
        tools = exchange(2, "tools/list", {}).get("tools", [])
        name = next((tool["name"] for tool in tools if tool.get("name") in ("runtime_read-environment", "runtime.read-environment")), None)
        if name is None:
            raise RuntimeError("MCP endpoint did not advertise the normal environment-read command")
        result = exchange(3, "tools/call", {"name": name, "arguments": {}})
        envelope = result.get("structuredContent")
        if envelope is None and result.get("content"):
            envelope = json.loads(result["content"][0].get("text", "null"))
        if result.get("isError") or not isinstance(envelope, dict) or envelope.get("status") != "ok":
            raise RuntimeError("MCP environment-read command did not succeed")
        facts = {item["name"]: item["value"] for item in envelope["result"]["facts"]}
        if facts.get("vault_root") != str(expected_vault):
            raise RuntimeError("MCP normal call resolved an unexpected Brain identity")
    finally:
        try:
            process.stdin.close()
        except BrokenPipeError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        reader.join(timeout=1)
        stderr_reader.join(timeout=1)
        process.stdout.close()
        process.stderr.close()
