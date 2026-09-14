"""Actual exec keeps stdio framing, honours consent lifetime and reaps old children."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_mcp_proxy import _read_until_id
from brain_mcp._proxy_handoff import public_session


SERVER = Path(__file__).resolve().parents[1] / "fixtures/production_mcp_proxy.py"
META = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientInfo": {"name": "handoff-test", "version": "1"},
        "io.modelcontextprotocol/clientCapabilities": {}}


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX exec handoff")
@pytest.mark.parametrize("modern", [False, True])
@pytest.mark.parametrize("failure", [None, "exec", "preflight", "public-contract"])
def test_real_proxy_handoff_preserves_pipelined_and_split_input(command_vault_clone, modern, failure):
    exec_failure = failure == "exec"
    refused = failure in {"preflight", "public-contract"}
    vault = command_vault_clone.vault_root
    process = subprocess.Popen([sys.executable, str(SERVER)], cwd=vault,
                               env={**os.environ, **command_vault_clone.environment, "BRAIN_CAPTURE_VAULT": str(vault), "BRAIN_CAPTURE_EXEC_FAILURE": "1" if exec_failure else "0"},
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    sequence = 0

    def frame(method, params):
        nonlocal sequence
        sequence += 1
        if modern:
            params = {**params, "_meta": META}
        return sequence, (json.dumps({"jsonrpc": "2.0", "id": sequence, "method": method, "params": params}) + "\n").encode()

    def receive(request_id):
        messages = _read_until_id(process, request_id, timeout=25)
        matches = [message for message in messages if message.get("id") == request_id]
        assert len(matches) == 1, (messages, process.poll())
        return matches[0]

    def request(method, params):
        request_id, data = frame(method, params)
        process.stdin.write(data)
        process.stdin.flush()
        return receive(request_id)

    def call(name, arguments):
        return request("tools/call", {"name": name, "arguments": arguments})["result"]

    try:
        initial = request("server/discover" if modern else "initialize", {} if modern else {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}})
        if not modern:
            process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
            process.stdin.flush()
        assert not initial.get("error"), initial
        assert any(tool["name"] == "brain_proxy_restart" for tool in request("tools/list", {})["result"]["tools"])
        before = call("brain_proxy_status", {})["structuredContent"]["result"]
        unchanged = call("brain_proxy_restart", {})
        assert unchanged["isError"] is False
        assert "handoff" not in unchanged["structuredContent"]["result"]
        prepared = call("access_prepare", {"preparation": {"kind": "operation", "command_id": "artefact.read",
                        "arguments": {"reference": "Projects/Command Fixture.md"}}})["structuredContent"]["result"]
        consent = call("access_request", {"consent": {"scope": "operation",
                       **{key: prepared[key] for key in ("operation_id", "digest", "review")}}})
        assert consent["isError"] is False
        proxy_file = vault / ".brain-core/brain_mcp/proxy.py"
        if failure == "public-contract":
            gate = vault / ".brain-core/brain_mcp/_proxy_protocol_gate.py"
            gate.write_text(gate.read_text().replace("capabilities[INTERFACE_HEADER_EXTENSION] = wire_header",
                            'capabilities[INTERFACE_HEADER_EXTENSION] = wire_header; capabilities["handoff-test"] = True'))
        for number in range(2):
            proxy_file.write_text(proxy_file.read_text() + f"\n# installed handoff test {number}\n")
            if failure == "preflight":
                proxy_file.write_text(proxy_file.read_text() + "not valid Python !\n")
            restart_id, restart = frame("tools/call", {"name": "brain_proxy_restart", "arguments": {}})
            read_id, read = frame("tools/call", {"name": "artefact_read", "arguments": {"reference": "Projects/Command Fixture.md"}})
            split_id, split = frame("tools/call", {"name": "brain_proxy_status", "arguments": {}})
            # Both a complete unread frame and half the following frame cross exec.
            cut = len(split) // 2
            process.stdin.write(restart + read + split[:cut])
            process.stdin.flush()
            result = receive(restart_id)["result"]
            assert result["isError"] is (failure is not None), result
            if exec_failure:
                assert result["structuredContent"]["error"] == {"code": "proxy_exec_failed", "effects": "consent_ended"}
            if refused:
                assert result["structuredContent"]["error"] == {"code": "proxy_handoff_preflight_failed", "effects": "none"}
                assert "handoff" not in result["structuredContent"]["result"]
            else:
                assert result["structuredContent"]["result"]["handoff"] == {"consent": "fresh", "state": "failed" if exec_failure else "completed"}
            assert process.poll() is None
            payload = receive(read_id)["result"]
            assert payload["isError"] is False, payload
            process.stdin.write(split[cut:])
            process.stdin.flush()
            after = receive(split_id)["result"]["structuredContent"]["result"]
            assert after["interface"]["generation"] == before["interface"]["generation"] + (0 if refused else number + 1)
            assert after["proxy"]["restart_required"] is (failure is not None)
        stale = call("artefact_read", {"reference": "Projects/Command Fixture.md", "brain_operation": prepared["operation_id"]})
        assert stale["isError"] is (not refused)
        ordinary = call("artefact_read", {"reference": "Projects/Command Fixture.md"})
        assert ordinary["isError"] is False
        discovery = request("server/discover", {}) if modern else initial
        assert public_session(discovery) == public_session(initial)
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        if process.returncode:
            print(process.stderr.read().decode(errors="replace")[-5000:])
