"""A live dependency upgrade drains work and replaces both Python processes."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from test_mcp_proxy import _read_until_id
from test_production_proxy_protocols import MODERN_META


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX handoff")
@pytest.mark.parametrize("modern", [False, True])
@pytest.mark.parametrize("failure", [None, "exec", "missing-runtime", "resolution-after-retirement"])
def test_runtime_upgrade_drains_then_handoffs_both_processes(command_vault_clone, modern, failure):
    vault = command_vault_clone.vault_root
    gate = vault.parent / "release-read"
    entered = vault.parent / "read-entered"
    owner = vault / ".brain-core/scripts/_application/artefact/read.py"
    owner.write_text(owner.read_text().replace(
        "def read_result(context: InvocationContext, request: ArtefactReadRequest):\n",
        "def read_result(context: InvocationContext, request: ArtefactReadRequest):\n"
        "    import time\n    from pathlib import Path\n"
        f"    Path({str(entered)!r}).touch()\n"
        "    deadline = time.monotonic() + 40\n"
        f"    while not Path({str(gate)!r}).exists():\n"
        "        assert time.monotonic() < deadline, 'test read release timed out'\n"
        "        time.sleep(.01)\n"))
    process = subprocess.Popen(
        [sys.executable, str(FIXTURES / "production_mcp_proxy.py")], cwd=vault,
        env={**os.environ, **command_vault_clone.environment, "BRAIN_CAPTURE_VAULT": str(vault),
             "BRAIN_CAPTURE_EXEC_FAILURE": "1" if failure == "exec" else "0",
             "BRAIN_CAPTURE_POST_RETIREMENT_RUNTIME_FAILURE": "1" if failure == "resolution-after-retirement" else "0"},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    sequence = 0

    def send(method, params):
        nonlocal sequence
        sequence += 1
        if modern:
            params = {**params, "_meta": MODERN_META}
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": sequence,
                                       "method": method, "params": params}) + "\n")
        process.stdin.flush()
        return sequence

    def receive(identifier):
        replies = _read_until_id(process, identifier, timeout=30)
        return next(reply for reply in replies if reply.get("id") == identifier)["result"]

    def call(name, arguments=None):
        return receive(send("tools/call", {"name": name, "arguments": arguments or {}}))

    try:
        receive(send("server/discover" if modern else "initialize", {} if modern else {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "runtime-test", "version": "1"}}))
        if not modern:
            process.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
            process.stdin.flush()
        before = call("brain_proxy_status")["structuredContent"]["result"]
        assert before["runtime"]["state"] == "current"
        pending = send("tools/call", {"name": "artefact_read", "arguments": {
            "reference": "Projects/Command Fixture.md"}})
        deadline = time.monotonic() + 15
        while not entered.exists():
            assert time.monotonic() < deadline, "read never entered the application"
            time.sleep(.01)
        requirements = vault / ".brain-core/brain_mcp/requirements.txt"
        requirements.write_text(requirements.read_text() + "\n# dependency upgrade fixture\n")
        version = vault / ".brain-core/VERSION"
        version.write_text(version.read_text().strip() + ".runtime\n")
        if failure != "missing-runtime":
            subprocess.run([sys.executable, str(FIXTURES / "managed_proxy_runtime.py"), str(vault)],
                           check=True, capture_output=True, text=True)
        drift_code = "runtime_installation_unavailable" if failure == "missing-runtime" else "runtime_restart_required"
        blocked = call("artefact_create", {"type": "thought", "title": "Must not execute"})
        envelope = blocked["structuredContent"]
        assert envelope["error"] == {"code": drift_code, "effects": "none"}
        assert json.loads(blocked["content"][0]["text"]) == envelope
        drift = envelope["result"]
        assert drift["runtime"]["loaded"] == before["runtime"]["loaded"]
        assert drift["runtime"]["required"] != before["runtime"]["required"]
        assert drift["server"]["loaded"] == before["server"]["loaded"]
        assert drift["server"]["refresh"] == "runtime_restart_required"
        assert drift["interface"]["generation"] == before["interface"]["generation"]
        # Unified lifecycle admission refuses outstanding work before preparing
        # a candidate. Runtime drift remains visible in the accompanying status.
        busy_refresh = call("brain_proxy_refresh")["structuredContent"]
        assert busy_refresh["error"]["code"] == "server_busy"
        assert busy_refresh["result"]["runtime"]["restart_required"] is True
        assert call("brain_proxy_restart")["structuredContent"]["error"]["code"] == "server_busy"
        gate.touch()
        assert receive(pending)["isError"] is False

        restarted = call("brain_proxy_restart")
        result = restarted["structuredContent"]
        if failure:
            expected = {"exec": "proxy_exec_failed", "missing-runtime": "runtime_installation_unavailable",
                        "resolution-after-retirement": "runtime_installation_unavailable"}[failure]
            assert result["error"]["code"] == expected, result
            assert "restart mcp in the host" in result["guidance"].lower()
            if failure == "resolution-after-retirement":
                assert result["error"]["effects"] == "consent_ended"
                assert result["result"]["handoff"] == {"consent": "fresh", "state": "failed"}
                assert json.loads(restarted["content"][0]["text"]) == result
            assert call("artefact_read", {"reference": "Projects/Command Fixture.md"})["structuredContent"]["error"] == {
                "code": "runtime_installation_unavailable" if failure in {"resolution-after-retirement", "missing-runtime"} else "runtime_restart_required",
                "effects": "none"}
            state = call("brain_proxy_status")["structuredContent"]["result"]
            assert state["runtime"]["loaded"] == before["runtime"]["loaded"]
            assert state["runtime"]["restart_required"] is True
            assert state["server"]["available"] is (failure == "missing-runtime")
        else:
            assert restarted["isError"] is False, result
            assert result["result"]["handoff"] == {"consent": "fresh", "state": "completed"}
            state = call("brain_proxy_status")["structuredContent"]["result"]
            assert state["runtime"]["loaded"] == state["runtime"]["child"] == drift["runtime"]["required"]
            assert state["runtime"]["state"] == "current"
            assert state["server"]["loaded"] == version.read_text().strip()
            assert state["next_action"] is None
            assert call("artefact_read", {"reference": "Projects/Command Fixture.md"})["isError"] is False
        assert process.poll() is None
    finally:
        gate.touch()
        process.stdin.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        if process.returncode:
            print(process.stderr.read()[-5000:])
