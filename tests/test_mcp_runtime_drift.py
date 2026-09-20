"""Runtime drift is an admission barrier, never a child-only environment swap."""

import json
from pathlib import Path
import sys

import pytest

from brain_mcp import proxy
from brain_mcp._proxy_controls import control_response
from brain_mcp._proxy_handoff import RawLineReader, read_state
from test_mcp_proxy import _FakeChild, _make_inprocess_proxy, _write_vault
from test_mcp_proxy_refresh import drive_lifecycle


def test_runtime_drift_blocks_calls_even_with_current_core_and_live_work(tmp_path, monkeypatch):
    request = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "artefact_create", "arguments": {"type": "thought", "title": "Never dispatched"}}}
    relay, responses = _make_inprocess_proxy(tmp_path, monkeypatch, [json.dumps(request).encode()])
    relay.server_target = "brain_mcp.server"
    relay._child = child = _FakeChild()
    relay._last_launched_version = "1.0.0"
    relay._inflight_requests[1] = ({"id": 1, "method": "tools/call"}, 0)
    required = str(tmp_path / "py3.12-new/bin/python")
    monkeypatch.setattr(relay, "_required_runtime_python", lambda: required)
    monkeypatch.setattr(relay, "_admit_lifecycle", lambda *args, **kwargs: pytest.fail("stale child refreshed"))
    def publish(response):
        assert not child.killed
        assert set(relay._inflight_requests) == {1}
        responses.append(response)
    monkeypatch.setattr(relay, "_send_to_client", publish)
    relay.run()
    assert child.sent == []
    assert set(relay._inflight_requests) == {1}
    result = responses[0]["result"]
    envelope = result["structuredContent"]
    assert json.loads(result["content"][0]["text"]) == envelope
    assert envelope["error"] == {"code": "runtime_restart_required", "effects": "none"}
    assert "MCP must be restarted" in envelope["guidance"]
    state = envelope["result"]
    assert state["runtime"]["loaded"] == sys.executable
    assert state["runtime"]["required"] == required
    assert state["server"]["refresh"] == "runtime_restart_required"
    assert state["proxy"]["restart_required"] is True
    assert state["next_action"] in {"brain_proxy_restart", "restart_mcp"}


@pytest.mark.parametrize("child_alive", [True, False])
def test_drift_prevents_refresh_and_crash_recovery_launch(tmp_path, monkeypatch, child_alive):
    _write_vault(tmp_path)
    relay = proxy.Proxy(sys.executable, "brain_mcp.server", str(tmp_path))
    relay._child = child = _FakeChild() if child_alive else None
    monkeypatch.setattr(relay, "_required_runtime_python", lambda: str(tmp_path / "other/python"))
    monkeypatch.setattr(proxy, "ChildProcess", lambda *args: pytest.fail("stale runtime launched Core"))
    assert drive_lifecycle(relay) == "runtime_restart_required"
    assert relay._start_child() is False
    assert relay._child is child
    if child:
        assert not child.killed


def test_missing_dependency_contract_fails_closed(tmp_path):
    _write_vault(tmp_path)
    relay = proxy.Proxy(sys.executable, "brain_mcp.server", str(tmp_path))
    assert drive_lifecycle(relay) == "runtime_installation_unavailable"
    status = relay._proxy_status()
    assert status["runtime"]["state"] == "installation_unavailable"
    assert status["runtime"]["required"] is None
    assert status["next_action"] == "restart_mcp"


def test_runtime_drift_cannot_be_hidden_by_matching_child_path(tmp_path, monkeypatch):
    relay = proxy.Proxy(str(tmp_path / "new/python"), "brain_mcp.server", str(tmp_path))
    monkeypatch.setattr(relay, "_required_runtime_python", lambda: relay.python_path)
    assert relay._runtime_error() == "runtime_restart_required"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX handoff")
def test_runtime_only_handoff_pins_new_interpreter_before_retirement(tmp_path, monkeypatch):
    _write_vault(tmp_path)
    relay = proxy.Proxy(sys.executable, "brain_mcp.server", str(tmp_path))
    relay._public_session = {}
    relay._client_protocol = "modern"
    required = str(tmp_path / "new/bin/python")
    monkeypatch.setattr(relay, "_check_proxy_drift", lambda: None)
    monkeypatch.setattr(relay, "_required_runtime_python", lambda: required)
    observed = []
    def preflight(fd, python):
        state = read_state(fd, expected_pid=proxy.os.getpid())
        observed.append((python, state["python"]))
        return True
    def replace(fd, state, **kwargs):
        observed.append(state["python"])
        return "verified"
    monkeypatch.setattr(relay, "_preflight_handoff", preflight)
    monkeypatch.setattr(relay, "_replace_idle_image", replace)
    assert drive_lifecycle(relay, "brain_proxy_restart") == "verified"
    assert observed == [(required, required), required]
    assert relay.python_path == sys.executable


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX handoff")
def test_runtime_change_during_preflight_keeps_existing_instance(tmp_path, monkeypatch):
    _write_vault(tmp_path)
    relay = proxy.Proxy(sys.executable, "brain_mcp.server", str(tmp_path))
    relay._public_session = {}
    relay._client_protocol = "modern"
    relay._child = child = _FakeChild()
    selection = [str(tmp_path / "new/bin/python")]
    monkeypatch.setattr(relay, "_check_proxy_drift", lambda: None)
    monkeypatch.setattr(relay, "_required_runtime_python", lambda: selection[0])
    def preflight(fd, python):
        selection[0] = str(tmp_path / "newer/bin/python")
        return True
    monkeypatch.setattr(relay, "_preflight_handoff", preflight)
    monkeypatch.setattr(relay, "_replace_idle_image", lambda *args, **kwargs: pytest.fail("retired on changed installation"))
    assert drive_lifecycle(relay, "brain_proxy_restart") == "installation_changed"
    assert not relay._shutdown and not child.killed


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX handoff")
def test_new_hash_uses_installed_compatible_minor_for_status_and_handoff(tmp_path, monkeypatch):
    from _common import _venv
    _write_vault(tmp_path)
    exports = tmp_path / ".brain-core/brain_mcp"
    exports.mkdir()
    requirements = exports / "requirements.txt"
    requirements.write_text("old dependency contract\n")
    (exports / "requirements-semantic.txt").write_text("semantic dependencies\n")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(_venv, "python_tag", lambda launcher=None: "py3.12")
    old = _venv.resolve_vault_venv_python(tmp_path)
    old.parent.mkdir(parents=True)
    old.touch()
    monkeypatch.setattr(proxy.sys, "executable", str(old))
    relay = proxy.Proxy(str(old), "brain_mcp.server", str(tmp_path))
    assert relay._runtime_status()["state"] == "current"

    requirements.write_text("new dependency contract\n")
    digest = _venv.requirements_hash(requirements)
    installed = _venv.central_venvs_root() / f"py3.13-{digest}" / "bin/python"
    installed.parent.mkdir(parents=True)
    installed.touch()
    assert not _venv.resolve_vault_venv_python(tmp_path).exists()
    assert _venv.find_existing_central_venv(tmp_path) == installed
    status = relay._proxy_status()
    assert status["runtime"]["loaded"] == str(old)
    assert status["runtime"]["required"] == str(installed)
    assert status["runtime"]["state"] == "restart_required"
    relay._public_session = {}
    relay._client_protocol = "modern"
    observed = []
    def preflight(fd, python):
        observed.append((python, read_state(fd, expected_pid=proxy.os.getpid())["python"]))
        return True
    monkeypatch.setattr(relay, "_preflight_handoff", preflight)
    monkeypatch.setattr(relay, "_replace_idle_image", lambda *args, **kwargs: "selected")
    assert drive_lifecycle(relay, "brain_proxy_restart") == "selected"
    assert observed == [(str(installed), str(installed))]
    assert not _venv.resolve_vault_venv_python(tmp_path).exists()


@pytest.mark.parametrize("code", ["proxy_handoff_unsupported", "proxy_handoff_preflight_failed", "proxy_exec_failed"])
def test_failed_handoff_has_model_visible_host_restart_guidance(code):
    result = control_response(1, "brain_proxy_restart", {}, code=code)["result"]
    assert "Restart MCP in the host" in result["structuredContent"]["guidance"]
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
