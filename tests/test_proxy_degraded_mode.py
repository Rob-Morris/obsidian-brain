"""Tests for the proxy's degraded (unresolved-Brain) mode.

When the proxy cannot resolve a target Brain at startup it must NOT exit (which
the client reports as a generic "-32000 failed to reconnect"). Instead it runs a
minimal MCP server that completes the handshake and delivers the actionable
resolution error to the agent — on tools/list and on every tools/call.
"""

import io
import json
import os
import subprocess
import sys

import pytest

# `brain_mcp` is a package under src/brain-core (scripts dir is already on the
# path via conftest, but the package root is not).
_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "brain-core"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from brain_mcp import proxy  # noqa: E402

REASON = (
    "the machine default Brain cannot be resolved: Brain 'x' is registered but "
    "its vault at /tmp/gone is missing or moved — clear the default."
)


def _drive(messages):
    """Run the degraded server over BytesIO streams and return parsed responses."""
    payload = "".join(json.dumps(m) + "\n" for m in messages).encode("utf-8")
    stdin = io.BytesIO(payload)
    stdout = io.BytesIO()
    proxy._run_degraded_server(REASON, stdin=stdin, stdout=stdout)
    return [json.loads(line) for line in stdout.getvalue().decode("utf-8").splitlines() if line.strip()]


def test_initialize_succeeds_and_carries_the_reason():
    resps = _drive([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}},
    ])
    assert len(resps) == 1
    result = resps[0]["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["serverInfo"]["name"] == "brain (unavailable)"
    assert REASON in result["instructions"]


def test_notifications_get_no_response():
    resps = _drive([
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    ])
    assert resps == []


def test_tools_list_advertises_the_unavailable_tool_with_the_reason():
    resps = _drive([
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ])
    tools = resps[0]["result"]["tools"]
    assert len(tools) == 1
    assert tools[0]["name"] == "brain_unavailable"
    assert REASON in tools[0]["description"]


def test_tools_call_returns_the_actionable_error():
    resps = _drive([
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "brain_session", "arguments": {}}},
    ])
    err = resps[0]["error"]
    assert REASON in err["message"]
    assert "/mcp" in err["message"]


@pytest.mark.slow
def test_proxy_main_enters_degraded_mode_on_resolution_failure(tmp_path):
    """End-to-end: a startup resolution failure yields a working MCP handshake
    plus the actionable error, instead of the process dying."""
    # An explicit anchor with no binding and no BRAIN_VAULT_ROOT, against an
    # empty (isolated) registry → resolve_brain_target hard-errors → degraded.
    anchor = tmp_path / "workspace-no-binding"
    anchor.mkdir()

    env = os.environ.copy()  # inherits the autouse-isolated XDG_CONFIG_HOME (empty registry)
    env["PYTHONPATH"] = _SRC + os.pathsep + os.path.join(_SRC, "scripts")
    env["BRAIN_WORKSPACE_DIR"] = str(anchor)
    env.pop("BRAIN_VAULT_ROOT", None)

    proc = subprocess.Popen(
        [sys.executable, "-m", "brain_mcp.proxy", "unused-python", "brain_mcp.server"],
        cwd=str(anchor), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env,
    )
    payload = (
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}) + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                      "params": {"name": "brain_session", "arguments": {}}}) + "\n"
    ).encode("utf-8")
    out, err = proc.communicate(payload, timeout=30)

    lines = [json.loads(line) for line in out.decode("utf-8").splitlines() if line.strip()]
    by_id = {m.get("id"): m for m in lines}
    # initialize completed (the client connects rather than seeing -32000)
    assert "result" in by_id[1] and by_id[1]["result"]["serverInfo"]["name"] == "brain (unavailable)"
    # the tools/call carries the actionable, specific cause
    assert "error" in by_id[2]
    assert "could not resolve" in by_id[2]["error"]["message"]
