"""Tests for migrations/migrate_to_0_27_6.py — legacy MCP config repair."""

from __future__ import annotations

import json
from pathlib import Path

import init
from migrate_to_0_27_6 import migrate


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / ".brain-core").mkdir(parents=True)
    (vault / ".brain-core" / "VERSION").write_text("0.27.5\n")
    (vault / ".brain-core" / "session-core.md").write_text("# Session Core\n")
    (vault / ".brain").mkdir()
    (vault / ".brain" / "local").mkdir()
    return vault


def _legacy_json_config(vault: Path, python_path: str) -> dict:
    return {
        "command": python_path,
        "args": [
            str(vault / ".brain-core" / "mcp" / "proxy.py"),
            python_path,
            str(vault / ".brain-core" / "mcp" / "server.py"),
        ],
        "env": {"BRAIN_VAULT_ROOT": str(vault)},
    }


def _write_init_state(vault: Path, records: list[dict]) -> None:
    state_path = vault / ".brain" / "local" / "init-state.json"
    state_path.write_text(json.dumps({"version": 1, "records": records}, indent=2) + "\n")


def test_rewrites_project_claude_config_and_updates_init_state(tmp_path):
    vault = _make_vault(tmp_path)
    project_config = vault / ".mcp.json"
    legacy = _legacy_json_config(vault, "/usr/bin/python3")
    project_config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "brain": legacy,
                    "other": {"command": "other"},
                }
            },
            indent=2,
        )
        + "\n"
    )
    _write_init_state(
        vault,
        [
            {
                "client": "claude",
                "scope": "project",
                "target_path": str(vault),
                "config_path": str(project_config),
                "server_name": "brain",
                "server_config": legacy,
            }
        ],
    )

    result = migrate(str(vault))

    assert result["status"] == "ok"
    data = json.loads(project_config.read_text())
    brain = data["mcpServers"]["brain"]
    assert brain["args"] == [
        "-m",
        "brain_mcp.proxy",
        "/usr/bin/python3",
        "brain_mcp.server",
    ]
    assert brain["env"]["BRAIN_VAULT_ROOT"] == str(vault)
    assert brain["env"]["PYTHONPATH"] == str(vault / ".brain-core")
    assert brain["env"]["BRAIN_WORKSPACE_DIR"] == str(vault)
    assert data["mcpServers"]["other"] == {"command": "other"}

    state = json.loads((vault / ".brain" / "local" / "init-state.json").read_text())
    assert state["records"][0]["server_config"] == brain


def test_rewrites_recorded_user_scope_claude_and_codex_configs(tmp_path, monkeypatch):
    vault = _make_vault(tmp_path)
    home = tmp_path / "home"
    monkeypatch.setattr("pathlib.Path.home", lambda: home)

    legacy_claude = _legacy_json_config(vault, "/opt/python/bin/python")
    legacy_codex = _legacy_json_config(vault, "/opt/python/bin/python")

    claude_path = home / ".claude.json"
    claude_path.parent.mkdir(parents=True, exist_ok=True)
    claude_path.write_text(json.dumps({"mcpServers": {"brain": legacy_claude}}, indent=2) + "\n")

    codex_path = home / ".codex" / "config.toml"
    init.write_codex_config(legacy_codex, codex_path)

    _write_init_state(
        vault,
        [
            {
                "client": "claude",
                "scope": "user",
                "target_path": None,
                "config_path": str(claude_path),
                "server_name": "brain",
                "server_config": legacy_claude,
            },
            {
                "client": "codex",
                "scope": "user",
                "target_path": None,
                "config_path": str(codex_path),
                "server_name": "brain",
                "server_config": legacy_codex,
            },
        ],
    )

    result = migrate(str(vault))

    assert result["status"] == "ok"

    claude = json.loads(claude_path.read_text())["mcpServers"]["brain"]
    assert claude["args"] == [
        "-m",
        "brain_mcp.proxy",
        "/opt/python/bin/python",
        "brain_mcp.server",
    ]
    assert claude["env"]["PYTHONPATH"] == str(vault / ".brain-core")
    assert "BRAIN_WORKSPACE_DIR" not in claude["env"]

    codex = init.read_codex_server_config(codex_path)
    assert codex is not None
    assert codex["args"] == [
        "-m",
        "brain_mcp.proxy",
        "/opt/python/bin/python",
        "brain_mcp.server",
    ]
    assert codex["env"]["PYTHONPATH"] == str(vault / ".brain-core")
    assert "BRAIN_WORKSPACE_DIR" not in codex["env"]


def test_skips_unrelated_or_already_current_config(tmp_path):
    vault = _make_vault(tmp_path)
    current = {
        "command": "/usr/bin/python3",
        "args": ["-m", "brain_mcp.proxy", "/usr/bin/python3", "brain_mcp.server"],
        "env": {
            "BRAIN_VAULT_ROOT": str(vault),
            "PYTHONPATH": str(vault / ".brain-core"),
        },
    }
    (vault / ".mcp.json").write_text(json.dumps({"mcpServers": {"brain": current}}, indent=2) + "\n")

    result = migrate(str(vault))

    assert result["status"] == "skipped"
