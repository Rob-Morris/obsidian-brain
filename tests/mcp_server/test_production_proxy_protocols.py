"""Material payload and invocation ownership through the production relay."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_mcp_proxy import _read_until_id


SERVER = Path(__file__).resolve().parents[1] / "fixtures/production_mcp_proxy.py"
MODERN_META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientInfo": {"name": "protocol-test", "version": "1"},
    "io.modelcontextprotocol/clientCapabilities": {},
}


@pytest.mark.parametrize("modern", [False, True])
def test_frozen_protocol_four_negotiates_refresh_then_receives_restart_gate(command_vault_clone, modern):
    vault = command_vault_clone.vault_root
    invoked = vault.parent / "application-handler-invoked"
    owner = vault / ".brain-core/scripts/_application/artefact/read.py"
    owner.write_text(owner.read_text().replace(
        "def execute(context, request):\n",
        f"def execute(context, request):\n    from pathlib import Path\n    Path({str(invoked)!r}).touch()\n"))
    process = subprocess.Popen(
        [sys.executable, str(SERVER)], cwd=vault,
        env={**os.environ, **command_vault_clone.environment, "BRAIN_CAPTURE_VAULT": str(vault),
             "BRAIN_CAPTURE_PROTOCOL_FOUR": "1"},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    sequence = 0
    def request(method, params):
        nonlocal sequence
        sequence += 1
        if modern:
            params = {**params, "_meta": MODERN_META}
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": sequence, "method": method, "params": params}) + "\n")
        process.stdin.flush()
        return next(reply for reply in _read_until_id(process, sequence, timeout=20)
                    if reply.get("id") == sequence)["result"]
    def call(name, arguments=None):
        return request("tools/call", {"name": name, "arguments": arguments or {}})
    try:
        initial = request("server/discover" if modern else "initialize", {} if modern else {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "v4-test", "version": "1"}})
        if not modern:
            process.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
            process.stdin.flush()
        assert initial["capabilities"]["experimental"]["brainCommandInterface"]["proxy_protocol"] == {"minimum": 4, "maximum": 5}
        before = call("brain_proxy_status")["structuredContent"]["result"]
        version = vault / ".brain-core/VERSION"
        version.write_text(version.read_text().strip() + ".gate-upgrade\n")
        refused = call("artefact_read", {"reference": "Projects/Command Fixture.md"})
        envelope = refused["structuredContent"]
        assert refused["isError"] is True
        assert envelope["schema"] == "brain.proxy-gate-result/1"
        assert envelope["error"]["code"] == "proxy_restart_required"
        assert envelope["error"]["effects"] == "none"
        assert envelope["error"]["details"]["running_proxy_protocol"] == 4
        assert envelope["error"]["details"]["required_proxy_protocol"] == {"minimum": 5, "maximum": 5}
        assert not invoked.exists()
        after = call("brain_proxy_status")["structuredContent"]["result"]
        assert after["interface"]["generation"] == before["interface"]["generation"] + 1
        assert after["server"]["loaded"] == version.read_text().strip()
        assert after["server"]["available"] is True
    finally:
        process.terminate()
        process.wait(timeout=5)


@pytest.mark.parametrize("protocol", ["2025-06-18", "2026-07-28", "modern-without-discover"])
def test_production_proxy_delivers_reads_and_mutations(command_vault_clone, protocol):
    vault = command_vault_clone.vault_root
    process = subprocess.Popen(
        [sys.executable, str(SERVER)],
        cwd=vault,
        env={**os.environ, **command_vault_clone.environment, "BRAIN_CAPTURE_VAULT": str(vault)},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    sequence = 0

    def request(method, params):
        nonlocal sequence
        sequence += 1
        if protocol != "2025-06-18":
            params = {**params, "_meta": {**MODERN_META, **params.get("_meta", {})}}
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": sequence, "method": method, "params": params}) + "\n")
        process.stdin.flush()
        replies = _read_until_id(process, sequence, timeout=20)
        return next(reply for reply in replies if reply.get("id") == sequence)

    def call(name, arguments, **extra):
        return request("tools/call", {"name": name, "arguments": arguments, **extra})["result"]

    try:
        if protocol == "2025-06-18":
            response = request("initialize", {"protocolVersion": protocol, "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}})
            assert response.get("result", {}).get("protocolVersion") == protocol, response
            process.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
            process.stdin.flush()
        elif protocol == "2026-07-28":
            response = request("server/discover", {})
            assert "brainCommandInterface" in response["result"]["capabilities"]["experimental"]

        if protocol == "modern-without-discover":
            first = call("brain_proxy_status", {})
            assert first["isError"] is False
            first_refresh = call("brain_proxy_refresh", {})
            assert first_refresh["isError"] is False, first_refresh
        discovery = request("tools/list", {})["result"]["tools"]
        names = [tool["name"] for tool in discovery]
        assert names.count("brain_proxy_status") == names.count("brain_proxy_refresh") == 1
        before = call("brain_proxy_status", {})["structuredContent"]["result"]
        assert before["server"]["refresh"] == "current"
        assert len(json.dumps(before).encode()) < 1200

        read = call("artefact_read", {"reference": "Projects/Command Fixture.md"})
        envelope = read["structuredContent"]
        assert read["isError"] is False
        assert envelope["result"]["content"] == (vault / "Projects/Command Fixture.md").read_text()
        assert json.loads(read["content"][0]["text"]) == envelope

        injected = call("artefact_read", {"reference": "Projects/Command Fixture.md"}, _meta={"brainInvocation": {"invocationId": "mcp-caller-owned"}})
        assert injected["isError"] is True
        assert "owned by the proxy" in injected["structuredContent"]["error"]["details"]["diagnostic"]

        # Normal content creation starts authorised; no lease request is needed.
        created = call("artefact_create", {"type": "thought", "title": "Protocol round trip"})
        assert created["isError"] is False
        result = created["structuredContent"]
        assert (vault / result["result"]["path"]).exists()
        assert result["committed_effects"][0]["subject"] == result["result"]["path"]
        assert json.loads(created["content"][0]["text"]) == result
        prepared = call("access_prepare", {"preparation": {"kind": "operation", "command_id": "artefact.read",
                        "arguments": {"reference": "Projects/Command Fixture.md"}}})
        assert prepared["isError"] is False
        operation = prepared["structuredContent"]["result"]
        grant = call("access_request", {"consent": {"scope": "operation",
                     **{key: operation[key] for key in ("operation_id", "digest", "review")}}})
        assert grant["isError"] is False
        # Idle drift is detected before this semantic call is accepted. The
        # existing proxy validates and swaps its child, then dispatches once.
        marker = vault / ".brain-core/VERSION"
        marker.write_text(marker.read_text().strip() + ".review\n")
        selected = call("artefact_read", {"reference": "Projects/Command Fixture.md", "brain_operation": operation["operation_id"]})
        assert selected["isError"] is False, selected["structuredContent"].get("error")
        assert selected["structuredContent"]["result"] == envelope["result"]
        after = call("brain_proxy_status", {})["structuredContent"]["result"]
        assert after["server"]["loaded"] == marker.read_text().strip()
        assert after["interface"]["generation"] == before["interface"]["generation"] + 1
        spent = call("artefact_read", {"reference": "Projects/Command Fixture.md", "brain_operation": operation["operation_id"]})
        assert spent["isError"] is True
        marker.write_text(marker.read_text().strip() + ".explicit\n")
        refreshed = call("brain_proxy_refresh", {})
        assert refreshed["isError"] is False
        assert refreshed["structuredContent"]["result"]["server"]["loaded"] == marker.read_text().strip()
        assert json.loads(refreshed["content"][0]["text"]) == refreshed["structuredContent"]
        fresh = call("artefact_read", {"reference": "Projects/Command Fixture.md"})
        assert fresh["isError"] is False
        assert fresh["structuredContent"]["result"] == envelope["result"]

    finally:
        process.terminate()
        process.wait(timeout=5)


def test_incompatible_candidate_keeps_connection_and_can_be_repaired(command_vault_clone):
    vault = command_vault_clone.vault_root
    process = subprocess.Popen(
        [sys.executable, str(SERVER)], cwd=vault,
        env={**os.environ, **command_vault_clone.environment, "BRAIN_CAPTURE_VAULT": str(vault)},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    sequence = 0

    def request(method, params):
        nonlocal sequence
        sequence += 1
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": sequence, "method": method,
                                      "params": {**params, "_meta": MODERN_META}}) + "\n")
        process.stdin.flush()
        return next(reply for reply in _read_until_id(process, sequence, timeout=20) if reply.get("id") == sequence)

    def call(name):
        return request("tools/call", {"name": name, "arguments": {}})["result"]

    try:
        request("server/discover", {})
        before = call("brain_proxy_status")["structuredContent"]["result"]
        contract = vault / ".brain-core/brain_mcp/_interface_protocol.py"
        original = contract.read_text()
        contract.write_text(original.replace("MIN_PROXY_PROTOCOL = 4", "MIN_PROXY_PROTOCOL = 99")
                           .replace("MAX_PROXY_PROTOCOL = 5", "MAX_PROXY_PROTOCOL = 99"))
        version = vault / ".brain-core/VERSION"
        version.write_text(version.read_text().strip() + ".incompatible\n")
        blocked = call("brain_proxy_refresh")
        assert blocked["isError"] is True
        state = blocked["structuredContent"]["result"]
        assert state["server"]["refresh"] == "blocked"
        assert state["server"]["loaded"] == before["server"]["loaded"]
        assert state["server"]["available"] is True
        assert state["interface"]["generation"] == before["interface"]["generation"]
        # The old server is still alive and its connection has not been killed.
        discovery = request("server/discover", {})["result"]
        assert discovery["capabilities"]["experimental"]["brainCommandInterface"]["proxy_protocol"]["minimum"] == 4
        contract.write_text(original)
        recovered = call("brain_proxy_refresh")
        assert recovered["isError"] is False, recovered
        assert recovered["structuredContent"]["result"]["server"]["refresh"] == "current"
        owner = vault / ".brain-core/scripts/_application/artefact/read.py"
        owner.write_text(owner.read_text().replace("COMMAND_VERSION: ClassVar[int] = 4", "COMMAND_VERSION: ClassVar[int] = 5"))
        version.write_text(version.read_text().strip() + ".changed-contract\n")
        read_params = {"name": "artefact_read", "arguments": {"reference": "Projects/Command Fixture.md"}}
        changed = request("tools/call", read_params)["result"]
        assert changed["isError"] is True
        assert changed["structuredContent"]["error"]["code"] == "interface_changed"
        assert changed["structuredContent"]["error"]["effects"] == "none"
        request("tools/list", {})
        rediscovered = request("tools/call", read_params)["result"]
        assert rediscovered["isError"] is False
        assert rediscovered["structuredContent"]["command_version"] == 5
    finally:
        process.terminate()
        process.wait(timeout=5)
