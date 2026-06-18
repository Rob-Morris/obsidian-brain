"""Tests for launcher-safe MCP state and SessionStart helpers."""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import PureWindowsPath

import pytest

from _bootstrap import mcp_state
from _bootstrap.mcp_state import (
    _load_init_state,
    build_mcp_config,
    build_session_hook_command,
    config_targets_vault,
    configured_vault_root,
    is_session_hook_command,
    read_codex_server_config,
    record_init_target,
    write_codex_config,
)


def test_build_mcp_config_structure(bootstrap_vault):
    config = build_mcp_config("/usr/bin/python3", bootstrap_vault)

    assert config["command"] == "/usr/bin/python3"
    assert config["args"] == ["-m", "brain_mcp.proxy", "/usr/bin/python3", "brain_mcp.server"]
    assert config["env"]["PYTHONPATH"] == str(bootstrap_vault / ".brain-core")
    assert "BRAIN_WORKSPACE_DIR" not in config["env"]


def test_build_mcp_config_includes_workspace_dir_when_provided(bootstrap_vault, project):
    config = build_mcp_config("/usr/bin/python3", bootstrap_vault, workspace_dir=project)

    assert config["env"]["BRAIN_WORKSPACE_DIR"] == str(project)


def test_configured_vault_root_extracts_legacy_env_root(bootstrap_vault):
    config = {"env": {"BRAIN_VAULT_ROOT": str(bootstrap_vault)}}

    assert configured_vault_root(config) == bootstrap_vault.resolve()
    assert config_targets_vault(config, bootstrap_vault)


def test_config_targets_vault_through_complete_workspace_binding(bootstrap_vault, project, monkeypatch):
    manifest = project / ".brain" / "local" / "workspace.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("brain: brain\nslug: my-project\n", encoding="utf-8")
    config = build_mcp_config("/usr/bin/python3", bootstrap_vault, workspace_dir=project)
    monkeypatch.setattr(
        mcp_state,
        "resolve_local_brain_vault",
        lambda brain_id: bootstrap_vault if brain_id == "brain" else None,
    )

    assert config_targets_vault(config, bootstrap_vault)


def test_config_targets_vault_rejects_workspace_binding_without_slug(bootstrap_vault, project, monkeypatch):
    manifest = project / ".brain" / "local" / "workspace.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("brain: brain\n", encoding="utf-8")
    config = build_mcp_config("/usr/bin/python3", bootstrap_vault, workspace_dir=project)
    monkeypatch.setattr(
        mcp_state,
        "resolve_local_brain_vault",
        lambda brain_id: bootstrap_vault if brain_id == "brain" else None,
    )

    assert not config_targets_vault(config, bootstrap_vault)


def test_config_targets_vault_requires_authoritative_local_registry_match(
    bootstrap_vault, project, monkeypatch
):
    manifest = project / ".brain" / "local" / "workspace.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("brain: brain\nslug: my-project\n", encoding="utf-8")
    config = build_mcp_config("/usr/bin/python3", bootstrap_vault, workspace_dir=project)
    monkeypatch.setattr(mcp_state, "resolve_local_brain_vault", lambda _brain_id: None)

    assert not config_targets_vault(config, bootstrap_vault)


def test_config_targets_vault_rejects_missing_or_invalid_roots(bootstrap_vault):
    assert configured_vault_root({}) is None
    assert not config_targets_vault({}, bootstrap_vault)
    assert not config_targets_vault({"env": {"BRAIN_VAULT_ROOT": ""}}, bootstrap_vault)


def test_write_codex_config_preserves_other_sections_and_brain_tools(bootstrap_vault, project):
    config_path = project / ".codex" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        'model = "gpt-5.4"\n'
        "\n"
        "[mcp_servers.other]\n"
        'command = "other"\n'
        "\n"
        "[mcp_servers.brain.tools.search]\n"
        'approval_mode = "approve"\n',
        encoding="utf-8",
    )

    config = build_mcp_config("/usr/bin/python3", bootstrap_vault)
    write_codex_config(config, config_path)

    content = config_path.read_text(encoding="utf-8")
    assert 'model = "gpt-5.4"' in content
    assert "[mcp_servers.other]" in content
    assert "[mcp_servers.brain.tools.search]" in content
    assert 'approval_mode = "approve"' in content
    payload = tomllib.loads(content)
    assert payload["mcp_servers"]["other"]["command"] == "other"
    assert payload["mcp_servers"]["brain"]["command"] == config["command"]
    assert payload["mcp_servers"]["brain"]["args"] == config["args"]
    assert payload["mcp_servers"]["brain"]["env"] == config["env"]
    assert payload["mcp_servers"]["brain"]["tools"]["search"]["approval_mode"] == "approve"


def test_write_codex_config_updates_spaced_brain_headers_in_place(bootstrap_vault, project):
    config_path = project / ".codex" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "[ mcp_servers.brain ]\n"
        'command = "old"\n'
        'args = ["-m", "old"]\n'
        "\n"
        "[ mcp_servers.brain.env ]\n"
        'BRAIN_VAULT_ROOT = "/old"\n',
        encoding="utf-8",
    )

    config = build_mcp_config("/usr/bin/python3", bootstrap_vault)
    write_codex_config(config, config_path)

    payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert payload["mcp_servers"]["brain"]["command"] == config["command"]
    assert payload["mcp_servers"]["brain"]["args"] == config["args"]
    assert payload["mcp_servers"]["brain"]["env"] == config["env"]
    headers = [
        line.strip()
        for line in config_path.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("[") and line.strip().endswith("]")
    ]
    assert "[ mcp_servers.brain ]" in headers or "[mcp_servers.brain]" in headers
    assert sum(1 for header in headers if header.replace(" ", "") == "[mcp_servers.brain]") == 1
    assert sum(1 for header in headers if header.replace(" ", "") == "[mcp_servers.brain.env]") == 1


def test_read_codex_server_config_accepts_spaced_brain_headers(project):
    config_path = project / ".codex" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "[ mcp_servers.brain ]\n"
        'command = "/usr/bin/python3"\n'
        'args = ["-m", "brain"]\n'
        "\n"
        "[ mcp_servers.brain.env ]\n"
        'BRAIN_VAULT_ROOT = "/bootstrap_vault"\n',
        encoding="utf-8",
    )

    assert read_codex_server_config(config_path) == {
        "command": "/usr/bin/python3",
        "args": ["-m", "brain"],
        "env": {"BRAIN_VAULT_ROOT": "/bootstrap_vault"},
    }


def test_session_hook_command_does_not_embed_bare_python3(bootstrap_vault):
    mcp_state.find_launcher_python.cache_clear()

    command = build_session_hook_command(bootstrap_vault, bootstrap_vault)

    assert " python3 " not in command, command
    assert not command.startswith("python3 ")
    assert not command.endswith(" python3")


def test_session_hook_prefers_path_resolved_launcher(bootstrap_vault, monkeypatch):
    monkeypatch.setattr(
        mcp_state,
        "find_launcher_python",
        lambda prefer_path_binaries=False: "/usr/bin/python3.12",
    )

    command = build_session_hook_command(bootstrap_vault, bootstrap_vault)

    assert "/usr/bin/python3.12" in command
    assert sys.executable not in command


def test_session_hook_falls_back_to_sys_executable(bootstrap_vault, monkeypatch):
    monkeypatch.setattr(mcp_state, "find_launcher_python", lambda prefer_path_binaries=False: None)

    command = build_session_hook_command(bootstrap_vault, bootstrap_vault)

    assert sys.executable in command


def test_session_hook_can_embed_managed_runtime_python(bootstrap_vault):
    managed_python = str(bootstrap_vault / ".brain" / "venv" / "Scripts" / "python.exe")

    command = build_session_hook_command(bootstrap_vault, bootstrap_vault, python_path=managed_python)

    assert managed_python in command


def test_windows_session_hook_uses_powershell_quoting(bootstrap_vault, monkeypatch):
    managed_python = r"C:\tools&x\.brain\venvs\py3.12\Scripts\python.exe"
    vault_root = PureWindowsPath(r"C:\Users\Rob&x\Documents\Brain")
    workspace = PureWindowsPath(r"C:\Work|x\Brain")
    monkeypatch.setattr(mcp_state.sys, "platform", "win32")

    command = build_session_hook_command(vault_root, workspace, python_path=managed_python)

    assert command.startswith("Write-Output 'brain_session called:'; & ")
    assert "'C:\\tools&x\\.brain\\venvs\\py3.12\\Scripts\\python.exe'" in command
    assert "'C:\\Users\\Rob&x\\Documents\\Brain\\.brain-core\\scripts\\session.py'" in command
    assert "'C:\\Users\\Rob&x\\Documents\\Brain'" in command
    assert "'C:\\Work|x\\Brain'" in command
    assert '"C:' not in command


def test_windows_session_hook_escapes_embedded_single_quotes(bootstrap_vault, monkeypatch):
    managed_python = r"C:\tools\O'Hara\python.exe"
    vault_root = PureWindowsPath(r"C:\Users\O'Hara\Brain")
    monkeypatch.setattr(mcp_state.sys, "platform", "win32")

    command = build_session_hook_command(vault_root, vault_root, python_path=managed_python)

    assert "'C:\\tools\\O''Hara\\python.exe'" in command
    assert "'C:\\Users\\O''Hara\\Brain'" in command
    assert is_session_hook_command(command, vault_root, vault_root)


def test_posix_session_hook_prefix_and_quoting(bootstrap_vault, monkeypatch):
    monkeypatch.setattr(mcp_state.sys, "platform", "linux")
    managed_python = "/usr/local/bin/python3.12"

    command = build_session_hook_command(bootstrap_vault, bootstrap_vault, python_path=managed_python)

    assert command.startswith("echo brain_session called: && ")
    assert "Write-Output" not in command
    assert managed_python in command


def test_record_init_target_replaces_same_identity(bootstrap_vault, project):
    config_a = build_mcp_config("/usr/bin/python3", bootstrap_vault)
    config_b = build_mcp_config("/usr/local/bin/python3", bootstrap_vault)
    record_a = {
        "client": "codex",
        "scope": "project",
        "target_path": str(project),
        "config_path": str(project / ".codex" / "config.toml"),
        "server_config": config_a,
    }
    record_b = dict(record_a)
    record_b["server_config"] = config_b

    record_init_target(bootstrap_vault, record_a)
    record_init_target(bootstrap_vault, record_b)

    state = _load_init_state(bootstrap_vault)
    assert len(state["records"]) == 1
    assert state["records"][0]["server_config"] == config_b
