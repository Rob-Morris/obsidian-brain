"""Tests for launcher-safe Claude/Codex MCP transport helpers."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock

import pytest

from _common import _shell
from _bootstrap import mcp_state, mcp_transport
from _bootstrap.mcp_state import (
    build_mcp_config,
    build_session_hook_command,
    read_codex_server_config,
    write_codex_config,
)


def test_project_claude_json_merges_existing(bootstrap_vault, project):
    mcp_path = project / ".mcp.json"
    mcp_path.write_text(
        json.dumps({"mcpServers": {"other-tool": {"command": "other"}}}),
        encoding="utf-8",
    )
    config = build_mcp_config("/usr/bin/python3", bootstrap_vault)

    mcp_transport.write_project_mcp_json(config, project)

    data = json.loads(mcp_path.read_text(encoding="utf-8"))
    assert "brain" in data["mcpServers"]
    assert "other-tool" in data["mcpServers"]


def test_user_claude_json_merges_existing(bootstrap_vault, fake_home):
    (fake_home / ".claude.json").write_text(
        json.dumps({"mcpServers": {"github": {"command": "gh"}}, "theme": "dark"}),
        encoding="utf-8",
    )
    config = build_mcp_config("/usr/bin/python3", bootstrap_vault)

    mcp_transport.write_user_claude_json(config)

    data = json.loads((fake_home / ".claude.json").read_text(encoding="utf-8"))
    assert "brain" in data["mcpServers"]
    assert "github" in data["mcpServers"]
    assert data["theme"] == "dark"


def test_all_local_skips_codex_with_warning():
    clients, warnings = mcp_transport._resolve_clients_or_error("all", "local")

    assert clients == ["claude"]
    assert warnings == ["Codex has no supported local scope. Applying Claude local setup only."]


def test_codex_local_exits():
    with pytest.raises(mcp_transport.InitTransportError):
        mcp_transport._resolve_clients_or_error("codex", "local")


def test_ensure_claude_md_creates_project_bootstrap(project):
    mcp_transport.ensure_claude_md(project)

    content = (project / "CLAUDE.md").read_text(encoding="utf-8")
    assert mcp_transport.CLAUDE_MD_BOOTSTRAP_PROJECT in content


def test_ensure_claude_md_creates_vault_bootstrap(bootstrap_vault):
    mcp_transport.ensure_claude_md(bootstrap_vault)

    content = (bootstrap_vault / "CLAUDE.md").read_text(encoding="utf-8")
    assert mcp_transport.CLAUDE_MD_BOOTSTRAP_VAULT in content


def test_ensure_claude_md_appends_to_existing(project):
    (project / "CLAUDE.md").write_text("# My Project\n\nExisting content.\n", encoding="utf-8")

    mcp_transport.ensure_claude_md(project)

    content = (project / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Existing content." in content
    assert mcp_transport.CLAUDE_MD_BOOTSTRAP_PROJECT in content


def test_ensure_claude_md_is_idempotent(project):
    mcp_transport.ensure_claude_md(project)
    mcp_transport.ensure_claude_md(project)

    content = (project / "CLAUDE.md").read_text(encoding="utf-8")
    assert content.count(mcp_transport.CLAUDE_MD_BOOTSTRAP_PROJECT) == 1


def test_ensure_claude_md_normalises_empty_file(project):
    claude_md = project / "CLAUDE.md"
    claude_md.write_text("", encoding="utf-8")

    mcp_transport.ensure_claude_md(project)

    assert claude_md.read_text(encoding="utf-8") == (
        f"{mcp_transport.CLAUDE_MD_BOOTSTRAP_PROJECT}\n"
    )


def test_ensure_claude_md_append_routes_through_safe_write(project, monkeypatch):
    claude_md = project / "CLAUDE.md"
    claude_md.write_text("# My Project\n\nExisting content.\n", encoding="utf-8")
    calls = []

    def fake_safe_write(path, content):
        calls.append((path, content))
        path.write_text(content, encoding="utf-8")
        return str(path.resolve())

    monkeypatch.setattr(mcp_transport, "safe_write", fake_safe_write)

    mcp_transport.ensure_claude_md(project)

    assert len(calls) == 1
    assert calls[0][0] == claude_md
    assert "Existing content." in calls[0][1]
    assert mcp_transport.CLAUDE_MD_BOOTSTRAP_PROJECT in calls[0][1]


def test_ensure_session_start_hook_creates_hook(project, bootstrap_vault):
    mcp_transport.ensure_session_start_hook(project, bootstrap_vault)

    settings_path = project / ".claude" / "settings.local.json"
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    hooks = data["hooks"]["SessionStart"]
    assert len(hooks) == 1
    command = hooks[0]["hooks"][0]["command"]
    assert command.startswith("echo brain_session called:")
    assert "session.py" in command
    assert str(bootstrap_vault) in command
    assert "--workspace-dir" in command
    assert str(project) in command
    assert " python3 " not in command
    assert not command.startswith("python3 ")


def test_ensure_session_start_hook_creates_powershell_hook_on_windows(
    project, bootstrap_vault, monkeypatch
):
    monkeypatch.setattr(mcp_state.sys, "platform", "win32")
    monkeypatch.setattr(mcp_transport.sys, "platform", "win32")

    mcp_transport.ensure_session_start_hook(project, bootstrap_vault, python_path=r"C:\tools&x\python.exe")

    data = json.loads((project / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    hook = data["hooks"]["SessionStart"][0]["hooks"][0]
    assert hook["shell"] == "powershell"
    assert hook["command"].startswith("Write-Output 'brain_session called:'; & ")
    assert "'C:\\tools&x\\python.exe'" in hook["command"]


def test_ensure_session_start_hook_is_idempotent(project, bootstrap_vault):
    mcp_transport.ensure_session_start_hook(project, bootstrap_vault)
    mcp_transport.ensure_session_start_hook(project, bootstrap_vault)

    data = json.loads((project / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    assert len(data["hooks"]["SessionStart"]) == 1


def test_ensure_session_start_hook_preserves_existing_hooks(project, bootstrap_vault):
    settings_dir = project / ".claude"
    settings_dir.mkdir(parents=True)
    (settings_dir / "settings.local.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PostToolUse": [
                        {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]}
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    mcp_transport.ensure_session_start_hook(project, bootstrap_vault)

    data = json.loads((settings_dir / "settings.local.json").read_text(encoding="utf-8"))
    assert "PostToolUse" in data["hooks"]
    assert "SessionStart" in data["hooks"]


def test_register_claude_uses_managed_python_for_hook(project, bootstrap_vault, monkeypatch):
    managed_python = str(bootstrap_vault / ".brain" / "venv" / "bin" / "python")
    server_config = build_mcp_config(managed_python, bootstrap_vault, workspace_dir=project)
    monkeypatch.setattr(mcp_transport, "_has_claude_cli", lambda: False)

    record = mcp_transport.register_claude(bootstrap_vault, server_config, "project", project)

    settings = json.loads((project / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    command = settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert managed_python in command
    assert command == record["hook_command"]


def test_register_claude_replaces_legacy_machine_python_hook(project, bootstrap_vault, monkeypatch):
    machine_python = "/usr/bin/python3.12"
    managed_python = str(bootstrap_vault / ".brain" / "venv" / "bin" / "python")
    legacy_command = (
        "echo 'brain_session called:' "
        f"&& {machine_python} {bootstrap_vault / '.brain-core' / 'scripts' / 'session.py'} "
        f"--vault {bootstrap_vault} --workspace-dir {project} --json"
    )
    settings_path = project / ".claude" / "settings.local.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "hooks": [
                                {"type": "command", "command": legacy_command},
                                {"type": "command", "command": "echo unrelated"},
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    server_config = build_mcp_config(managed_python, bootstrap_vault, workspace_dir=project)
    monkeypatch.setattr(mcp_transport, "_has_claude_cli", lambda: False)

    record = mcp_transport.register_claude(bootstrap_vault, server_config, "project", project)

    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    commands = [
        hook["command"]
        for entry in settings["hooks"]["SessionStart"]
        for hook in entry["hooks"]
        if hook.get("type") == "command"
    ]
    assert legacy_command not in commands
    assert "echo unrelated" in commands
    assert record["hook_command"] in commands
    assert len([command for command in commands if "session.py" in command]) == 1


def test_register_claude_replaces_old_windows_hook_with_powershell_hook(
    tmp_path, monkeypatch
):
    bootstrap_vault = tmp_path / "bootstrap_vault&x"
    project = tmp_path / "project&x"
    bootstrap_vault.mkdir()
    project.mkdir()
    old_python = r"C:\runtime&old\python.exe"
    managed_python = r"C:\runtime&new\python.exe"
    old_command = (
        "echo brain_session called: && "
        + subprocess.list2cmdline(
            [
                old_python,
                str(bootstrap_vault / ".brain-core" / "scripts" / "session.py"),
                "--vault",
                str(bootstrap_vault),
                "--workspace-dir",
                str(project),
                "--json",
            ]
        )
    )
    settings_path = project / ".claude" / "settings.local.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps({"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": old_command}]}]}}),
        encoding="utf-8",
    )
    server_config = build_mcp_config(managed_python, bootstrap_vault, workspace_dir=project)
    monkeypatch.setattr(mcp_state.sys, "platform", "win32")
    monkeypatch.setattr(mcp_transport.sys, "platform", "win32")
    monkeypatch.setattr(mcp_transport, "_has_claude_cli", lambda: False)

    record = mcp_transport.register_claude(bootstrap_vault, server_config, "project", project)

    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    hooks = [
        hook
        for entry in settings["hooks"]["SessionStart"]
        for hook in entry["hooks"]
        if hook.get("type") == "command"
    ]
    assert len(hooks) == 1
    assert hooks[0]["shell"] == "powershell"
    assert hooks[0]["command"] == record["hook_command"]
    assert old_command not in [hook["command"] for hook in hooks]
    assert "'C:\\runtime&new\\python.exe'" in hooks[0]["command"]


def test_claude_project_followup_reports_unapproved_shadow_risk(project, fake_home):
    (fake_home / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "brain": {
                        "command": "/usr/bin/python3",
                        "env": {"BRAIN_VAULT_ROOT": "/tmp/other-vault"},
                    }
                },
                "projects": {
                    str(project): {
                        "enabledMcpjsonServers": [],
                        "disabledMcpjsonServers": [],
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    notes = mcp_transport.claude_project_followup_notes(project)

    assert any("has not approved project-scoped" in note for note in notes)
    assert any("/mcp" in note for note in notes)
    assert any("user-scoped" in note for note in notes)
    assert any("claude mcp list" in note for note in notes)
    assert any("enabledMcpjsonServers" in note for note in notes)


def test_claude_project_followup_reports_disabled_project_server(project, fake_home):
    (fake_home / ".claude.json").write_text(
        json.dumps(
            {
                "projects": {
                    str(project): {
                        "enabledMcpjsonServers": [],
                        "disabledMcpjsonServers": ["brain"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    notes = mcp_transport.claude_project_followup_notes(project)

    assert any("disabled" in note for note in notes)
    assert any("re-enable" in note for note in notes)


def test_claude_project_followup_returns_no_notes_when_approved(project, fake_home):
    (fake_home / ".claude.json").write_text(
        json.dumps(
            {
                "projects": {
                    str(project): {
                        "enabledMcpjsonServers": ["brain"],
                        "disabledMcpjsonServers": [],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    assert mcp_transport.claude_project_followup_notes(project) == []


def test_codex_project_warning_qualifies_precedence(bootstrap_vault, project, fake_home, capsys):
    config = build_mcp_config("/usr/bin/python3", bootstrap_vault, workspace_dir=project)
    write_codex_config(config, fake_home / ".codex" / "config.toml")

    mcp_transport._warn_if_user_scope_exists("codex", "project", config)
    err = capsys.readouterr().err

    assert "already registered globally" in err
    assert "once this project is trusted" in err
    assert "enabled" in err


def test_mcp_followup_notes_are_shared_for_project_scope(project):
    notes = mcp_transport.mcp_followup_notes(["claude", "codex"], "project", project)

    assert any("/mcp" in note for note in notes)
    assert any("brain_session" in note for note in notes)
    assert any("codex mcp list" in note for note in notes)


def test_mcp_followup_notes_are_shared_for_user_scope():
    notes = mcp_transport.mcp_followup_notes(["claude", "codex"], "user", None)

    assert any("claude mcp list" in note for note in notes)
    assert any("codex mcp list" in note for note in notes)
    assert not any("brain_session" in note for note in notes)


def test_apply_mcp_transport_action_quotes_remove_command_on_win32(tmp_path, monkeypatch):
    bootstrap_vault = tmp_path / "Brain Vault"
    project = tmp_path / "My Project"
    bootstrap_vault.mkdir()
    project.mkdir()
    monkeypatch.setattr(_shell.sys, "platform", "win32")
    monkeypatch.setattr(
        mcp_transport,
        "_resolve_managed_python",
        lambda _vault: r"C:\Program Files\Python312\python.exe",
    )
    monkeypatch.setattr(mcp_transport, "_warn_if_user_scope_exists", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        mcp_transport,
        "_converge_workspace_manifest",
        lambda *_args, **_kwargs: MagicMock(message="ok"),
    )
    monkeypatch.setattr(mcp_transport, "ensure_brain_ignore_rules", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        mcp_transport,
        "register_claude",
        lambda *_args, **_kwargs: {"client": "claude", "scope": "project"},
    )
    monkeypatch.setattr(mcp_transport, "record_init_target", lambda *_args, **_kwargs: None)

    result = mcp_transport.apply_mcp_transport_action(
        bootstrap_vault,
        client_arg="claude",
        scope="project",
        target_dir=project,
        remove=False,
    )

    assert f'"{bootstrap_vault}"' in result["remove_command"]
    assert f'"{project}"' in result["remove_command"]
