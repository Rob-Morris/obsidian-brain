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
import threading
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
    assert result["serverInfo"]["name"] == "brain"
    assert REASON in result["instructions"]


def test_notifications_get_no_response():
    resps = _drive([
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    ])
    assert resps == []


def test_tools_list_advertises_the_same_three_recovery_controls():
    resps = _drive([
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ])
    tools = resps[0]["result"]["tools"]
    assert {tool["name"] for tool in tools} == set(proxy.CONTROL_TOOLS)


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
    assert {tool["name"] for tool in resps[1]["result"]["tools"]} == set(proxy.CONTROL_TOOLS)
    assert resps[2]["error"]["message"] == expected
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
    monkeypatch.setattr(proxy, "resolve_brain_target", lambda **_kwargs: (_ for _ in ()).throw(PermissionError("no search")))
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
    monkeypatch.setattr(proxy, "resolve_brain_target", lambda **_kwargs: (_ for _ in ()).throw(error))
    monkeypatch.setattr(proxy, "_run_degraded_server", lambda reason, **kwargs: calls.append((reason, kwargs)))

    proxy.main()

    assert len(calls) == 1
    reason, kwargs = calls[0]
    assert "failed to load workspace manifest" in reason
    assert "filesystem permissions" in kwargs["guidance"]
    assert "binding or machine default" not in kwargs["guidance"]


def test_main_enters_degraded_mode_on_unwritable_local_state(monkeypatch, tmp_path):
    calls = []
    target = SimpleNamespace(vault_root=str(tmp_path), workspace_dir=None, source="vault_self")

    monkeypatch.setattr(proxy.sys, "argv", ["proxy.py", "unused-python", "brain_mcp.server"])
    monkeypatch.setenv("BRAIN_VAULT_ROOT", "sentinel-vault")
    monkeypatch.setenv("PYTHONPATH", "sentinel-pythonpath")
    monkeypatch.setattr(proxy, "resolve_brain_target", lambda **_kwargs: target)
    monkeypatch.setattr(proxy, "_probe_local_state", lambda _vault_root: (_ for _ in ()).throw(PermissionError("read-only")))
    monkeypatch.setattr(proxy, "_run_degraded_server", lambda reason, **kwargs: calls.append((reason, kwargs)))

    proxy.main()

    assert len(calls) == 1
    reason, kwargs = calls[0]
    assert "filesystem access failed while preparing vault-local state" in reason
    assert "vault filesystem permissions" in kwargs["guidance"]


def test_writable_probe_is_unique_and_preserves_preexisting_state(tmp_path):
    local = tmp_path / ".brain" / "local"
    local.mkdir(parents=True)
    sentinel = local / ".writable-probe"
    sentinel.write_text("owned by another process\n", encoding="utf-8")
    errors = []

    def _probe():
        try:
            proxy._probe_local_state(str(tmp_path))
        except BaseException as exc:  # noqa: BLE001 — concurrent failures are asserted
            errors.append(exc)

    workers = [threading.Thread(target=_probe) for _ in range(16)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    assert errors == []
    assert sentinel.read_text(encoding="utf-8") == "owned by another process\n"
    assert sorted(path.name for path in local.glob(".writable-probe-*")) == []


def test_writable_probe_does_not_remove_a_unique_name_it_did_not_create(
    tmp_path,
    monkeypatch,
):
    local = tmp_path / ".brain" / "local"
    local.mkdir(parents=True)
    monkeypatch.setattr(proxy.os, "getpid", lambda: 123)
    monkeypatch.setattr(
        proxy.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="fixed"),
    )
    sentinel = local / ".writable-probe-123-fixed"
    sentinel.write_text("pre-existing\n", encoding="utf-8")

    with pytest.raises(FileExistsError):
        proxy._probe_local_state(str(tmp_path))

    assert sentinel.read_text(encoding="utf-8") == "pre-existing\n"


def test_main_enters_degraded_mode_on_noncanonical_python(monkeypatch, tmp_path):
    calls = []
    (tmp_path / ".brain-core" / "brain_mcp").mkdir(parents=True)
    (tmp_path / ".brain-core" / "brain_mcp" / "requirements.txt").write_text("mcp>=1.0.0\n")
    (tmp_path / ".brain-core" / "brain_mcp" / "requirements-semantic.txt").write_text("mcp>=1.0.0\n")
    target = SimpleNamespace(vault_root=str(tmp_path), workspace_dir=None, source="vault_self")

    monkeypatch.setattr(proxy.sys, "argv", ["proxy.py", "/usr/bin/python3.12", "brain_mcp.server"])
    monkeypatch.setattr(proxy, "resolve_brain_target", lambda **_kwargs: target)
    monkeypatch.setattr(proxy, "_run_degraded_server", lambda reason, **kwargs: calls.append((reason, kwargs)))

    monkeypatch.setattr(proxy, "find_existing_central_venv", lambda _vault: tmp_path / "managed/bin/python")

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

    monkeypatch.setattr(proxy.sys, "argv", ["proxy.py", managed_python, "brain_mcp.server"])
    monkeypatch.setattr(proxy, "resolve_brain_target", lambda **_kwargs: target)
    monkeypatch.setattr(proxy, "find_existing_central_venv", lambda _vault: Path(managed_python))
    monkeypatch.setattr(proxy, "_run_degraded_server", lambda reason, **kwargs: calls.append(("degraded", reason, kwargs)))
    monkeypatch.setattr(proxy, "_serve_proxy", lambda python, server, vault, **kwargs: calls.append(("serve", python, server, vault)))

    proxy.main()

    assert calls == [("serve", managed_python, "brain_mcp.server", str(tmp_path))]


@pytest.mark.parametrize("workspace", [None, "project"])
def test_main_pins_resolved_scope_but_retains_original_selection(monkeypatch, tmp_path, workspace):
    original = str(tmp_path / "anchor")
    resolved = str(tmp_path / workspace) if workspace else None
    target = SimpleNamespace(vault_root=str(tmp_path), workspace_dir=resolved, source="workspace_env")
    managed_python = str(tmp_path / "managed" / "python")
    monkeypatch.setenv("BRAIN_WORKSPACE_DIR", original)
    monkeypatch.setattr(proxy.sys, "argv", ["proxy.py", managed_python, "brain_mcp.server"])
    monkeypatch.setattr(proxy, "resolve_brain_target", lambda **_kwargs: target)
    monkeypatch.setattr(proxy, "find_existing_central_venv", lambda _vault: Path(managed_python))
    observed = []

    def observe_scope(python, server, vault, **kwargs):
        instance = proxy.Proxy(python, server, vault, resolution_inputs=kwargs["resolution_inputs"])
        observed.append((instance._workspace, instance._resolution_inputs["workspace_env"], instance._assess_startup()))

    monkeypatch.setattr(proxy, "_serve_proxy", observe_scope)
    proxy.main()
    assert observed == [(resolved, original, None)]


def test_main_degrades_when_runtime_resolution_subprocess_fails(monkeypatch, tmp_path):
    calls = []
    target = SimpleNamespace(vault_root=str(tmp_path), workspace_dir=None, source="vault_self")

    monkeypatch.setattr(proxy.sys, "argv", ["proxy.py", "/usr/bin/python3.12", "brain_mcp.server"])
    monkeypatch.setattr(proxy, "resolve_brain_target", lambda **_kwargs: target)
    monkeypatch.setattr(
        proxy,
        "find_existing_central_venv",
        lambda _vault: (_ for _ in ()).throw(subprocess.SubprocessError("launcher failed")),
    )
    monkeypatch.setattr(proxy, "_run_degraded_server", lambda reason, **kwargs: calls.append("degraded"))
    monkeypatch.setattr(proxy, "_serve_proxy", lambda python, server, vault: calls.append(("serve", python, server, vault)))

    proxy.main()

    assert calls == ["degraded"]


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
    assert "result" in by_id[1] and by_id[1]["result"]["serverInfo"]["name"] == "brain"
    # the tools/call carries the actionable, specific cause
    assert "error" in by_id[2]
    assert "could not resolve" in by_id[2]["error"]["message"]
