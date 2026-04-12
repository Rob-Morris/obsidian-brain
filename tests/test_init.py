"""Tests for init.py — MCP setup script."""

import json
import os
import subprocess
import sys

import pytest

# init.py is self-contained, import it directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "brain-core", "scripts"))
import init


@pytest.fixture
def vault(tmp_path):
    """Minimal vault for init testing."""
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.10.0\n")
    (bc / "session-core.md").write_text("# Session Core\n")
    mcp_dir = bc / "mcp"
    mcp_dir.mkdir()
    (mcp_dir / "server.py").write_text("# stub\n")
    return tmp_path


@pytest.fixture
def project(tmp_path):
    """Empty project directory."""
    proj = tmp_path / "my-project"
    proj.mkdir()
    return proj


class TestFindVaultRoot:
    def test_from_explicit_arg(self, vault):
        result = init.find_vault_root(str(vault))
        assert result == vault

    def test_from_env_var(self, vault, monkeypatch):
        monkeypatch.setenv("BRAIN_VAULT_ROOT", str(vault))
        result = init.find_vault_root()
        assert result == vault

    def test_invalid_vault_exits(self, tmp_path):
        with pytest.raises(SystemExit):
            init.find_vault_root(str(tmp_path))


class TestBuildMcpConfig:
    def test_structure(self, vault):
        config = init.build_mcp_config("/usr/bin/python3", vault)
        assert config["command"] == "/usr/bin/python3"
        assert str(vault) in config["args"][0]
        assert config["env"]["BRAIN_VAULT_ROOT"] == str(vault)


class TestWriteProjectMcpJson:
    def test_creates_new(self, vault, project):
        config = init.build_mcp_config("/usr/bin/python3", vault)
        init.write_project_mcp_json(config, project)
        mcp_path = project / ".mcp.json"
        assert mcp_path.is_file()
        data = json.loads(mcp_path.read_text())
        assert "brain" in data["mcpServers"]

    def test_merges_existing(self, vault, project):
        mcp_path = project / ".mcp.json"
        mcp_path.write_text(json.dumps({
            "mcpServers": {"other-tool": {"command": "other"}}
        }))
        config = init.build_mcp_config("/usr/bin/python3", vault)
        init.write_project_mcp_json(config, project)
        data = json.loads(mcp_path.read_text())
        assert "brain" in data["mcpServers"]
        assert "other-tool" in data["mcpServers"]

    def test_idempotent(self, vault, project):
        config = init.build_mcp_config("/usr/bin/python3", vault)
        init.write_project_mcp_json(config, project)
        init.write_project_mcp_json(config, project)
        data = json.loads((project / ".mcp.json").read_text())
        assert len(data["mcpServers"]) == 1


class TestWriteUserClaudeJson:
    def test_creates_new(self, vault, tmp_path, monkeypatch):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        config = init.build_mcp_config("/usr/bin/python3", vault)
        init.write_user_claude_json(config)
        data = json.loads((tmp_path / ".claude.json").read_text())
        assert "brain" in data["mcpServers"]

    def test_merges_existing(self, vault, tmp_path, monkeypatch):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        (tmp_path / ".claude.json").write_text(json.dumps({
            "mcpServers": {"github": {"command": "gh"}},
            "theme": "dark",
        }))
        config = init.build_mcp_config("/usr/bin/python3", vault)
        init.write_user_claude_json(config)
        data = json.loads((tmp_path / ".claude.json").read_text())
        assert "brain" in data["mcpServers"]
        assert "github" in data["mcpServers"]
        assert data["theme"] == "dark"


class TestEnsureClaudeMd:
    def test_creates_new_for_project(self, project):
        init.ensure_claude_md(project)
        content = (project / "CLAUDE.md").read_text()
        assert init.CLAUDE_MD_BOOTSTRAP_PROJECT in content

    def test_creates_new_for_vault(self, vault):
        init.ensure_claude_md(vault)
        content = (vault / "CLAUDE.md").read_text()
        assert init.CLAUDE_MD_BOOTSTRAP_VAULT in content

    def test_appends_to_existing(self, project):
        (project / "CLAUDE.md").write_text("# My Project\n\nExisting content.\n")
        init.ensure_claude_md(project)
        content = (project / "CLAUDE.md").read_text()
        assert "Existing content." in content
        assert init.CLAUDE_MD_BOOTSTRAP_PROJECT in content

    def test_idempotent(self, project):
        init.ensure_claude_md(project)
        init.ensure_claude_md(project)
        content = (project / "CLAUDE.md").read_text()
        assert content.count(init.CLAUDE_MD_BOOTSTRAP_PROJECT) == 1



class TestEnsureSessionStartHook:
    def test_creates_hook(self, project, vault):
        init.ensure_session_start_hook(project, vault)
        settings_path = project / ".claude" / "settings.local.json"
        assert settings_path.is_file()
        data = json.loads(settings_path.read_text())
        hooks = data["hooks"]["SessionStart"]
        assert len(hooks) == 1
        assert "session.py" in hooks[0]["hooks"][0]["command"]
        assert str(vault) in hooks[0]["hooks"][0]["command"]

    def test_idempotent(self, project, vault):
        init.ensure_session_start_hook(project, vault)
        init.ensure_session_start_hook(project, vault)
        data = json.loads((project / ".claude" / "settings.local.json").read_text())
        assert len(data["hooks"]["SessionStart"]) == 1

    def test_preserves_existing_hooks(self, project, vault):
        settings_dir = project / ".claude"
        settings_dir.mkdir(parents=True)
        (settings_dir / "settings.local.json").write_text(json.dumps({
            "hooks": {
                "PostToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]}]
            }
        }))
        init.ensure_session_start_hook(project, vault)
        data = json.loads((settings_dir / "settings.local.json").read_text())
        assert "PostToolUse" in data["hooks"]
        assert "SessionStart" in data["hooks"]
