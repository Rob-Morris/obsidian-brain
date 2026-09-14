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
        selected = call("artefact_read", {"reference": "Projects/Command Fixture.md", "brain_operation": operation["operation_id"]})
        assert selected["isError"] is False, selected["structuredContent"].get("error")
        assert selected["structuredContent"]["result"] == envelope["result"]
        # Exit 10 cannot prove other accepted calls did not enter. The relay
        # returns an owned unknown outcome, then accepts a separate fresh call.
        marker = vault / ".brain-core/VERSION"
        marker.write_text(marker.read_text().strip() + ".review\n")
        interrupted = call("artefact_read", {"reference": "Projects/Command Fixture.md"})
        assert interrupted["isError"] is True
        error = interrupted["structuredContent"]["error"]
        assert error["code"] == "command_outcome_unknown"
        assert error["effects"] == "none" and error["retryable"] is False
        lookup = call("invocation_read", error["outcome_reference"])
        assert lookup["isError"] is False
        assert lookup["structuredContent"]["result"]["state"] == "still_unknown"
        fresh = call("artefact_read", {"reference": "Projects/Command Fixture.md"})
        assert fresh["isError"] is False
        assert fresh["structuredContent"]["result"] == envelope["result"]

    finally:
        process.terminate()
        process.wait(timeout=5)
