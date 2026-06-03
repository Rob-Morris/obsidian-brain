"""Tests for the proxy's degraded startup mode.

When the proxy cannot complete startup it must NOT exit (which the client
reports as a generic "-32000 failed to reconnect"). Instead it runs a minimal
MCP server that completes the handshake and delivers the actionable startup
error to the agent — on tools/list and on every tools/call.
"""

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src", "brain-core"))

from brain_mcp import proxy  # noqa: E402

REASON = (
    "the machine default Brain cannot be resolved: Brain 'x' is registered but "
    "its vault at /tmp/gone is missing or moved — clear the default."
)


def _drive(messages):
    """Run the degraded server over BytesIO streams and return parsed responses."""
    return _drive_with(REASON, messages)


def _drive_with(reason, messages, **kwargs):
    """Run the degraded server with custom detail fields and return parsed responses."""
    payload = "".join(json.dumps(m) + "\n" for m in messages).encode("utf-8")
    stdin = io.BytesIO(payload)
    stdout = io.BytesIO()
    proxy._run_degraded_server(reason, stdin=stdin, stdout=stdout, **kwargs)
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
    assert "restart MCP" in err["message"]


def test_custom_degraded_lead_and_guidance_are_visible_on_the_wire():
    lead = "Brain MCP resolved the target vault but could not start."
    reason = "filesystem access failed while opening proxy log for /vault: denied"
    guidance = "Fix the vault filesystem permissions or mount state, then restart MCP."

    resps = _drive_with(reason, [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "brain_session", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 4, "method": "resources/read"},
    ], lead=lead, guidance=guidance)

    expected = f"{lead} {reason} {guidance}"
    assert resps[0]["result"]["instructions"] == expected
    assert resps[1]["result"]["tools"][0]["description"] == expected
    assert resps[2]["error"]["message"] == expected
    assert "degraded startup mode" in resps[3]["error"]["message"]
    assert expected in resps[3]["error"]["message"]


def test_non_object_json_is_ignored():
    resps = _drive([
        5,
        [1, 2],
        {"jsonrpc": "2.0", "id": 1, "method": "ping"},
    ])
    assert resps == [{"jsonrpc": "2.0", "id": 1, "result": {}}]


def test_broken_pipe_stops_degraded_loop_without_raising():
    class BrokenStdout(io.BytesIO):
        def write(self, _data):
            raise BrokenPipeError

    stdin = io.BytesIO((json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n").encode("utf-8"))
    proxy._run_degraded_server(REASON, stdin=stdin, stdout=BrokenStdout())


def test_main_enters_degraded_mode_on_resolution_oserror(monkeypatch):
    calls = []

    monkeypatch.setattr(proxy.sys, "argv", ["proxy.py", "unused-python", "brain_mcp.server"])
    monkeypatch.setattr(proxy, "resolve_and_heal", lambda **_kwargs: (_ for _ in ()).throw(PermissionError("no search")))
    monkeypatch.setattr(proxy, "_run_degraded_server", lambda reason, **kwargs: calls.append((reason, kwargs)))

    proxy.main()

    assert len(calls) == 1
    reason, kwargs = calls[0]
    assert "filesystem access failed while resolving Brain target" in reason
    assert "filesystem permissions" in kwargs["guidance"]


def test_main_uses_filesystem_guidance_for_wrapped_binding_filesystem_error(monkeypatch):
    calls = []
    error = proxy.WorkspaceBindingError(
        "failed to load workspace manifest: denied",
        code=proxy.WORKSPACE_ERROR_FILESYSTEM_ACCESS,
    )

    monkeypatch.setattr(proxy.sys, "argv", ["proxy.py", "unused-python", "brain_mcp.server"])
    monkeypatch.setattr(proxy, "resolve_and_heal", lambda **_kwargs: (_ for _ in ()).throw(error))
    monkeypatch.setattr(proxy, "_run_degraded_server", lambda reason, **kwargs: calls.append((reason, kwargs)))

    proxy.main()

    assert len(calls) == 1
    reason, kwargs = calls[0]
    assert "failed to load workspace manifest" in reason
    assert "filesystem permissions" in kwargs["guidance"]
    assert "binding or machine default" not in kwargs["guidance"]


def test_main_enters_degraded_mode_on_logging_oserror(monkeypatch, tmp_path):
    calls = []
    target = SimpleNamespace(vault_root=str(tmp_path), workspace_dir=None, source="vault_self")

    monkeypatch.setattr(proxy.sys, "argv", ["proxy.py", "unused-python", "brain_mcp.server"])
    monkeypatch.setenv("BRAIN_VAULT_ROOT", "sentinel-vault")
    monkeypatch.setenv("PYTHONPATH", "sentinel-pythonpath")
    monkeypatch.setattr(proxy, "resolve_and_heal", lambda **_kwargs: target)
    monkeypatch.setattr(proxy, "_setup_logging", lambda _vault_root: (_ for _ in ()).throw(PermissionError("read-only")))
    monkeypatch.setattr(proxy, "_run_degraded_server", lambda reason, **kwargs: calls.append((reason, kwargs)))

    proxy.main()

    assert len(calls) == 1
    reason, kwargs = calls[0]
    assert "filesystem access failed while opening proxy log" in reason
    assert "vault filesystem permissions" in kwargs["guidance"]


def test_main_enters_degraded_mode_on_noncanonical_python(monkeypatch, tmp_path):
    calls = []
    (tmp_path / ".brain-core" / "brain_mcp").mkdir(parents=True)
    (tmp_path / ".brain-core" / "brain_mcp" / "requirements.txt").write_text("mcp>=1.0.0\n")
    target = SimpleNamespace(vault_root=str(tmp_path), workspace_dir=None, source="vault_self")

    monkeypatch.setattr(proxy.sys, "argv", ["proxy.py", "/usr/bin/python3.12", "brain_mcp.server"])
    monkeypatch.setattr(proxy, "resolve_and_heal", lambda **_kwargs: target)
    monkeypatch.setattr(proxy, "_run_degraded_server", lambda reason, **kwargs: calls.append((reason, kwargs)))

    proxy.main()

    assert len(calls) == 1
    reason, kwargs = calls[0]
    assert "non-canonical Python" in reason
    assert "repair.py" in kwargs["guidance"]
    assert " mcp --vault " in kwargs["guidance"]


def test_main_allows_canonical_python_launch(monkeypatch, tmp_path):
    calls = []
    target = SimpleNamespace(vault_root=str(tmp_path), workspace_dir=None, source="vault_self")
    managed_python = str(tmp_path / ".brain" / "venvs" / "py3.12" / "bin" / "python")

    class FakeProxy:
        def __init__(self, python_path, server_target, vault_root):
            calls.append(("init", python_path, server_target, vault_root))

        def _start_writer_loop(self):
            calls.append(("writer",))

        def _start_recovery_loop(self):
            calls.append(("recovery",))

        def _start_reader_loop(self):
            calls.append(("reader",))

        def _start_child(self):
            calls.append(("child",))
            return True

        def run(self):
            calls.append(("run",))

    monkeypatch.setattr(proxy.sys, "argv", ["proxy.py", managed_python, "brain_mcp.server"])
    monkeypatch.setattr(proxy, "resolve_and_heal", lambda **_kwargs: target)
    monkeypatch.setattr(proxy, "resolve_vault_venv_python", lambda _vault: Path(managed_python))
    monkeypatch.setattr(proxy, "_run_degraded_server", lambda reason, **kwargs: calls.append(("degraded", reason, kwargs)))
    monkeypatch.setattr(proxy, "Proxy", FakeProxy)

    proxy.main()

    assert not any(call[0] == "degraded" for call in calls)
    assert ("run",) in calls


def test_main_skips_launch_validation_when_runtime_resolution_subprocess_fails(monkeypatch, tmp_path):
    calls = []
    target = SimpleNamespace(vault_root=str(tmp_path), workspace_dir=None, source="vault_self")

    class FakeProxy:
        def __init__(self, *_args):
            pass

        def _start_writer_loop(self):
            pass

        def _start_recovery_loop(self):
            pass

        def _start_reader_loop(self):
            pass

        def _start_child(self):
            return True

        def run(self):
            calls.append("run")

    monkeypatch.setattr(proxy.sys, "argv", ["proxy.py", "/usr/bin/python3.12", "brain_mcp.server"])
    monkeypatch.setattr(proxy, "resolve_and_heal", lambda **_kwargs: target)
    monkeypatch.setattr(
        proxy,
        "resolve_vault_venv_python",
        lambda _vault: (_ for _ in ()).throw(subprocess.SubprocessError("launcher failed")),
    )
    monkeypatch.setattr(proxy, "_run_degraded_server", lambda reason, **kwargs: calls.append("degraded"))
    monkeypatch.setattr(proxy, "Proxy", FakeProxy)

    proxy.main()

    assert calls == ["run"]


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
