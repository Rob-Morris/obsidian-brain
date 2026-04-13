"""Tests for init.py - MCP setup script."""

import json
import os
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
    transport_dir = bc / "brain_mcp"
    transport_dir.mkdir()
    (transport_dir / "proxy.py").write_text("# stub\n")
    (transport_dir / "server.py").write_text("# stub\n")
    scripts_dir = bc / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "session.py").write_text("# stub\n")
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
        assert config["args"] == ["-m", "brain_mcp.proxy", "/usr/bin/python3", "brain_mcp.server"]
        assert config["env"]["BRAIN_VAULT_ROOT"] == str(vault)
        assert config["env"]["PYTHONPATH"] == str(vault / ".brain-core")


class TestClaudeJsonWriters:
    def test_project_json_merges_existing(self, vault, project):
        mcp_path = project / ".mcp.json"
        mcp_path.write_text(json.dumps({"mcpServers": {"other-tool": {"command": "other"}}}))

        config = init.build_mcp_config("/usr/bin/python3", vault)
        init.write_project_mcp_json(config, project)

        data = json.loads(mcp_path.read_text())
        assert "brain" in data["mcpServers"]
        assert "other-tool" in data["mcpServers"]

    def test_user_json_merges_existing(self, vault, tmp_path, monkeypatch):
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


class TestCodexTomlConfig:
    def test_write_codex_config_creates_new_project_file(self, vault, project):
        config = init.build_mcp_config("/usr/bin/python3", vault)
        config_path = project / ".codex" / "config.toml"

        init.write_codex_config(config, config_path)

        assert config_path.is_file()
        assert init.read_codex_server_config(config_path) == config

    def test_write_codex_config_preserves_other_sections_and_brain_tools(self, vault, project):
        config_path = project / ".codex" / "config.toml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(
            'model = "gpt-5.4"\n'
            '\n'
            '[mcp_servers.other]\n'
            'command = "other"\n'
            '\n'
            '[mcp_servers.brain.tools.search]\n'
            'approval_mode = "approve"\n'
        )

        config = init.build_mcp_config("/usr/bin/python3", vault)
        init.write_codex_config(config, config_path)

        content = config_path.read_text()
        assert 'model = "gpt-5.4"' in content
        assert '[mcp_servers.other]' in content
        assert '[mcp_servers.brain.tools.search]' in content
        assert 'approval_mode = "approve"' in content
        assert init.read_codex_server_config(config_path) == config


class TestClientResolution:
    def test_all_local_skips_codex_with_warning(self):
        clients, warnings = init._resolve_clients("all", "local")
        assert clients == ["claude"]
        assert warnings == ["Codex has no supported local scope. Applying Claude local setup only."]

    def test_codex_local_exits(self):
        with pytest.raises(SystemExit):
            init._resolve_clients("codex", "local")


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


class TestStateBookkeeping:
    def test_record_init_target_replaces_same_identity(self, vault, project):
        config_a = init.build_mcp_config("/usr/bin/python3", vault)
        config_b = init.build_mcp_config("/usr/local/bin/python3", vault)

        record_a = {
            "client": "codex",
            "scope": "project",
            "target_path": str(project),
            "config_path": str(project / ".codex" / "config.toml"),
            "server_config": config_a,
        }
        record_b = dict(record_a)
        record_b["server_config"] = config_b

        init._record_init_target(vault, record_a)
        init._record_init_target(vault, record_b)

        state = init._load_init_state(vault)
        assert len(state["records"]) == 1
        assert state["records"][0]["server_config"] == config_b


class TestRemoval:
    def test_remove_claude_project_registration_cleans_bootstrap_and_hook(self, vault, project, monkeypatch):
        monkeypatch.setattr(init, "_has_claude_cli", lambda: False)
        config = init.build_mcp_config("/usr/bin/python3", vault)

        record = init.register_claude(vault, config, "project", project)

        assert (project / ".mcp.json").is_file()
        assert (project / "CLAUDE.md").is_file()
        assert (project / ".claude" / "settings.local.json").is_file()

        removed = init._remove_record(vault, record)

        assert removed is True
        assert not (project / ".mcp.json").exists()
        assert not (project / "CLAUDE.md").exists()
        assert not (project / ".claude").exists()

    def test_remove_codex_skips_mismatched_entry(self, vault, project):
        expected = init.build_mcp_config("/usr/bin/python3", vault)
        other = init.build_mcp_config("/usr/local/bin/python3", vault)
        config_path = project / ".codex" / "config.toml"
        init.write_codex_config(other, config_path)

        record = {
            "client": "codex",
            "scope": "project",
            "target_path": str(project),
            "config_path": str(config_path),
            "server_config": expected,
        }

        removed = init._remove_record(vault, record)

        assert removed is False
        assert config_path.is_file()
        assert init.read_codex_server_config(config_path) == other
