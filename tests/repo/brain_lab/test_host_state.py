from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from brain_lab.host_state import capture_host_state


def test_host_state_detects_machine_config_and_vault_changes(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    config = home / ".config" / "brain"
    config.mkdir(parents=True)
    (config / "vaults").write_text("brain\tlocal\t/vault\n", encoding="utf-8")
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("one", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))

    before = capture_host_state(home=home, vaults=[vault])
    (config / "default").write_text("brain\n", encoding="utf-8")
    (vault / "note.md").write_text("two", encoding="utf-8")
    after = capture_host_state(home=home, vaults=[vault])

    assert before["fingerprint"] != after["fingerprint"]
    assert before["files"]["brain_machine_config"] != after["files"]["brain_machine_config"]


def test_host_state_git_fingerprint_includes_dirty_state(tmp_path: Path):
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "Test"], check=True)
    (repository / "file").write_text("one", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "file"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "initial"], check=True)
    before = capture_host_state(home=tmp_path / "home", worktrees=[repository])
    (repository / "file").write_text("two", encoding="utf-8")
    after = capture_host_state(home=tmp_path / "home", worktrees=[repository])

    assert before["fingerprint"] != after["fingerprint"]


def _write_claude_state(home: Path, payload) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / ".claude.json").write_text(json.dumps(payload), encoding="utf-8")


def test_host_state_ignores_claude_code_churn_outside_brain_entries(tmp_path: Path):
    home = tmp_path / "home"
    server = {"command": "/runtime/python", "args": ["-m", "brain_mcp.proxy"]}
    _write_claude_state(home, {
        "numStartups": 1,
        "mcpServers": {"brain": server, "other": {"command": "x"}},
        "projects": {"/work": {"history": [], "mcpServers": {"brain": server}}},
    })
    before = capture_host_state(home=home)
    _write_claude_state(home, {
        "numStartups": 2,
        "tipsHistory": {"tip": 3},
        "mcpServers": {"brain": server, "other": {"command": "y"}},
        "projects": {"/work": {"history": ["prompt"], "mcpServers": {"brain": server}}, "/new": {}},
    })
    after = capture_host_state(home=home)

    assert before["fingerprint"] == after["fingerprint"]


@pytest.mark.parametrize("change", ["user", "project", "added_project"])
def test_host_state_detects_brain_server_changes(tmp_path: Path, change: str):
    home = tmp_path / "home"
    server = {"command": "/runtime/python"}
    payload = {"mcpServers": {"brain": server}, "projects": {"/work": {"mcpServers": {"brain": server}}}}
    _write_claude_state(home, payload)
    before = capture_host_state(home=home)
    moved = {"command": "/elsewhere/python"}
    if change == "user":
        payload["mcpServers"]["brain"] = moved
    elif change == "project":
        payload["projects"]["/work"]["mcpServers"]["brain"] = moved
    else:
        payload["projects"]["/new"] = {"mcpServers": {"brain": server}}
    _write_claude_state(home, payload)
    after = capture_host_state(home=home)

    assert before["files"]["claude_global"] != after["files"]["claude_global"]
    assert before["fingerprint"] != after["fingerprint"]


def test_host_state_falls_back_to_file_hash_when_claude_state_is_unreadable(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude.json").write_text("{not json", encoding="utf-8")
    before = capture_host_state(home=home)
    (home / ".claude.json").write_text("{still not json", encoding="utf-8")
    after = capture_host_state(home=home)

    assert before["files"]["claude_global"]["unreadable"] is True
    assert before["fingerprint"] != after["fingerprint"]
