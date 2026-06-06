"""Tests for init.py - MCP setup script."""

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path, PureWindowsPath
from unittest.mock import patch, MagicMock

import pytest

# init.py is self-contained, import it directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "brain-core", "scripts"))
import init
from _common import _shell
from _bootstrap import mcp_transport, workspace_scaffold
from _bootstrap.workspace_binding import workspace_slug


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
    (transport_dir / "requirements.txt").write_text("mcp>=1.0.0\n")
    scripts_dir = bc / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "session.py").write_text("# stub\n")
    # `find_python` dynamically loads `_venv.py` from the vault to resolve
    # the central runtime path; copy it from source so the migration
    # fallback chain (central → legacy → sys.executable → PATH) is
    # observable in tests.
    venv_helper_src = (
        os.path.join(os.path.dirname(__file__), "..", "src", "brain-core",
                     "scripts", "_common", "_venv.py")
    )
    common_dir = scripts_dir / "_common"
    common_dir.mkdir()
    with open(venv_helper_src, encoding="utf-8") as fh:
        (common_dir / "_venv.py").write_text(fh.read())
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
        assert config["env"]["PYTHONPATH"] == str(vault / ".brain-core")
        assert "BRAIN_WORKSPACE_DIR" not in config["env"]

    def test_includes_workspace_dir_when_provided(self, vault, project):
        config = init.build_mcp_config("/usr/bin/python3", vault, workspace_dir=project)
        assert config["env"]["BRAIN_WORKSPACE_DIR"] == str(project)


class TestFindPython:
    def test_returns_canonical_managed_runtime_python(self, vault, monkeypatch):
        monkeypatch.setattr(mcp_transport, "find_launcher_python", lambda: "/usr/bin/python3.12")
        monkeypatch.setattr(
            mcp_transport,
            "ensure_managed_runtime",
            lambda *_args, **_kwargs: {
                "managed_runtime_ready": True,
                "managed_python": "/home/.brain/venvs/py3.12-fake/bin/python",
            },
        )

        assert init.find_python(vault) == "/home/.brain/venvs/py3.12-fake/bin/python"

    def test_errors_when_no_compatible_launcher_exists(self, vault, monkeypatch):
        monkeypatch.setattr(mcp_transport, "find_launcher_python", lambda: None)

        with pytest.raises(SystemExit):
            init.find_python(vault)

    def test_errors_when_bootstrap_cannot_produce_managed_runtime(self, vault, monkeypatch):
        monkeypatch.setattr(mcp_transport, "find_launcher_python", lambda: "/usr/bin/python3.12")
        monkeypatch.setattr(
            mcp_transport,
            "ensure_managed_runtime",
            lambda *_args, **_kwargs: {
                "managed_runtime_ready": False,
                "managed_python": "",
            },
        )

        with pytest.raises(SystemExit):
            init.find_python(vault)

    def test_errors_gracefully_when_managed_runtime_bootstrap_subprocess_fails(
        self, vault, monkeypatch, capsys
    ):
        monkeypatch.setattr(mcp_transport, "find_launcher_python", lambda: "/usr/bin/python3.12")
        monkeypatch.setattr(
            mcp_transport,
            "ensure_managed_runtime",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError(
                    "ensure_central_venv failed: command failed: pip install (exit 1)\n"
                    "pip install failed"
                )
            ),
        )

        with pytest.raises(SystemExit):
            init.find_python(vault)

        err = capsys.readouterr().err
        assert "command failed: pip install" in err
        assert "pip install failed" in err


class TestVaultMatching:
    def test_extracts_configured_vault_root(self, vault):
        config = {"env": {"BRAIN_VAULT_ROOT": str(vault)}}
        assert init.configured_vault_root(config) == vault.resolve()
        assert init.config_targets_vault(config, vault)

    def test_workspace_binding_route_targets_vault_when_binding_is_complete(self, vault, project, monkeypatch):
        workspace = project
        (workspace / ".brain" / "local").mkdir(parents=True)
        (workspace / ".brain" / "local" / "workspace.yaml").write_text(
            "brain: brain\nslug: my-project\n",
            encoding="utf-8",
        )
        config = init.build_mcp_config("/usr/bin/python3", vault, workspace_dir=workspace)
        monkeypatch.setattr(init._mcp_state, "resolve_local_brain_vault", lambda brain_id: vault if brain_id == "brain" else None)
        assert init.config_targets_vault(config, vault)

    def test_workspace_binding_route_does_not_match_when_slug_is_missing(self, vault, project, monkeypatch):
        workspace = project
        (workspace / ".brain" / "local").mkdir(parents=True)
        (workspace / ".brain" / "local" / "workspace.yaml").write_text(
            "brain: brain\n",
            encoding="utf-8",
        )
        config = init.build_mcp_config("/usr/bin/python3", vault, workspace_dir=workspace)
        monkeypatch.setattr(init._mcp_state, "resolve_local_brain_vault", lambda brain_id: vault if brain_id == "brain" else None)
        assert not init.config_targets_vault(config, vault)

    def test_workspace_binding_route_requires_authoritative_local_registry_match(self, vault, project, monkeypatch):
        workspace = project
        (workspace / ".brain" / "local").mkdir(parents=True)
        (workspace / ".brain" / "local" / "workspace.yaml").write_text(
            "brain: brain\nslug: my-project\n",
            encoding="utf-8",
        )
        config = init.build_mcp_config("/usr/bin/python3", vault, workspace_dir=workspace)
        monkeypatch.setattr(init._mcp_state, "resolve_local_brain_vault", lambda _brain_id: None)
        assert not init.config_targets_vault(config, vault)

    def test_missing_or_invalid_root_does_not_match(self, vault):
        assert init.configured_vault_root({}) is None
        assert not init.config_targets_vault({}, vault)
        assert not init.config_targets_vault({"env": {"BRAIN_VAULT_ROOT": ""}}, vault)


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
        payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
        assert payload["mcp_servers"]["brain"]["command"] == config["command"]
        assert payload["mcp_servers"]["brain"]["args"] == config["args"]
        assert payload["mcp_servers"]["brain"]["env"] == config["env"]

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
        payload = tomllib.loads(content)
        assert payload["mcp_servers"]["other"]["command"] == "other"
        assert payload["mcp_servers"]["brain"]["command"] == config["command"]
        assert payload["mcp_servers"]["brain"]["args"] == config["args"]
        assert payload["mcp_servers"]["brain"]["env"] == config["env"]
        assert payload["mcp_servers"]["brain"]["tools"]["search"]["approval_mode"] == "approve"

    def test_write_codex_config_updates_spaced_brain_headers_in_place(self, vault, project):
        config_path = project / ".codex" / "config.toml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(
            '[ mcp_servers.brain ]\n'
            'command = "old"\n'
            'args = ["-m", "old"]\n'
            '\n'
            '[ mcp_servers.brain.env ]\n'
            'BRAIN_VAULT_ROOT = "/old"\n',
            encoding="utf-8",
        )

        config = init.build_mcp_config("/usr/bin/python3", vault)
        init.write_codex_config(config, config_path)

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

    def test_read_codex_server_config_accepts_spaced_brain_headers(self, project):
        config_path = project / ".codex" / "config.toml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(
            '[ mcp_servers.brain ]\n'
            'command = "/usr/bin/python3"\n'
            'args = ["-m", "brain"]\n'
            '\n'
            '[ mcp_servers.brain.env ]\n'
            'BRAIN_VAULT_ROOT = "/vault"\n',
            encoding="utf-8",
        )

        assert init.read_codex_server_config(config_path) == {
            "command": "/usr/bin/python3",
            "args": ["-m", "brain"],
            "env": {"BRAIN_VAULT_ROOT": "/vault"},
        }


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

    def test_existing_empty_file_is_normalised_to_bootstrap_only(self, project):
        claude_md = project / "CLAUDE.md"
        claude_md.write_text("", encoding="utf-8")

        init.ensure_claude_md(project)

        assert claude_md.read_text(encoding="utf-8") == (
            f"{init.CLAUDE_MD_BOOTSTRAP_PROJECT}\n"
        )

    def test_append_routes_through_safe_write(self, project, monkeypatch):
        claude_md = project / "CLAUDE.md"
        claude_md.write_text("# My Project\n\nExisting content.\n")
        calls = []

        def fake_safe_write(path, content):
            calls.append((path, content))
            path.write_text(content, encoding="utf-8")
            return str(path.resolve())

        monkeypatch.setattr(mcp_transport, "safe_write", fake_safe_write)

        init.ensure_claude_md(project)

        assert len(calls) == 1
        assert calls[0][0] == claude_md
        assert "Existing content." in calls[0][1]
        assert init.CLAUDE_MD_BOOTSTRAP_PROJECT in calls[0][1]

class TestEnsureWorkspaceManifest:
    def test_creates_new_manifest(self, project):
        init.ensure_workspace_manifest(project, brain_id="brain")
        content = (project / ".brain" / "local" / "workspace.yaml").read_text()
        assert "brain: brain" in content
        assert "slug: my-project" in content

    def test_preserves_existing_slug_while_adding_binding(self, project):
        manifest = project / ".brain" / "local" / "workspace.yaml"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("slug: custom\n")
        init.ensure_workspace_manifest(project, brain_id="brain")
        assert manifest.read_text() == "brain: brain\nslug: custom\n"

    def test_migrates_legacy_manifest(self, project):
        legacy = project / ".brain" / "workspace.yaml"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text("slug: legacy\n")
        init.ensure_workspace_manifest(project, brain_id="brain")
        new_path = project / ".brain" / "local" / "workspace.yaml"
        assert new_path.is_file()
        assert new_path.read_text() == "brain: brain\nslug: legacy\n"
        assert not legacy.is_file()


class TestEnsureBrainIgnoreRules:
    def test_updates_gitignore_when_repo_has_tracked_ignore(self, project, monkeypatch):
        gitignore = project / ".gitignore"
        gitignore.write_text("node_modules/\n")
        monkeypatch.setattr(workspace_scaffold, "_git_repo_root", lambda _target: project)
        monkeypatch.setattr(workspace_scaffold, "_git_dir", lambda _target: project / ".git")

        init.ensure_brain_ignore_rules(project, "project", ["claude", "codex"], skip_mcp=False)

        content = gitignore.read_text()
        assert "node_modules/" in content
        assert ".brain/local/" in content
        assert ".claude/settings.local.json" in content
        assert ".codex/config.toml" in content

    def test_falls_back_to_git_info_exclude_when_gitignore_missing(self, project, monkeypatch):
        git_dir = project / ".git"
        (git_dir / "info").mkdir(parents=True)
        monkeypatch.setattr(workspace_scaffold, "_git_repo_root", lambda _target: project)
        monkeypatch.setattr(workspace_scaffold, "_git_dir", lambda _target: git_dir)

        init.ensure_brain_ignore_rules(project, "project", ["claude"], skip_mcp=True)

        exclude = (git_dir / "info" / "exclude").read_text()
        assert ".brain/local/" in exclude
        assert ".claude/settings.local.json" not in exclude

    def test_raises_when_ignore_destination_is_unreadable(self, project, monkeypatch):
        gitignore = project / ".gitignore"
        gitignore.write_text("node_modules/\n")
        monkeypatch.setattr(workspace_scaffold, "_git_repo_root", lambda _target: project)
        monkeypatch.setattr(workspace_scaffold, "_git_dir", lambda _target: project / ".git")

        original_read_text = type(gitignore).read_text

        def fake_read_text(path, *args, **kwargs):
            if path == gitignore:
                raise OSError("permission denied")
            return original_read_text(path, *args, **kwargs)

        monkeypatch.setattr(type(gitignore), "read_text", fake_read_text)

        with pytest.raises(init.GitInspectionError, match="failed to read ignore destination"):
            init.ensure_brain_ignore_rules(project, "project", ["claude"], skip_mcp=False)


class TestBuildSessionHookCommand:
    def test_does_not_embed_bare_python3(self, vault):
        """Regression: bare `python3` fails on asdf-shimmed machines."""
        init._mcp_state.find_launcher_python.cache_clear()
        command = init.build_session_hook_command(vault, vault)
        # The hook must embed a resolved absolute launcher, not a shim name.
        # Allow paths whose basename happens to be python3 — what we forbid is
        # the unresolved `python3` token sitting on its own.
        assert " python3 " not in command, command
        assert not command.startswith("python3 ")
        assert not command.endswith(" python3")

    def test_prefers_path_resolved_launcher_over_sys_executable(self, vault, monkeypatch):
        """The hook is persisted; prefer stable PATH binaries to sys.executable
        because the caller may be a temporary interpreter whose path won't survive."""
        monkeypatch.setattr(
            init._mcp_state,
            "find_launcher_python",
            lambda prefer_path_binaries=False: "/usr/bin/python3.12",
        )
        command = init.build_session_hook_command(vault, vault)
        assert "/usr/bin/python3.12" in command
        assert sys.executable not in command

    def test_falls_back_to_sys_executable_when_path_lacks_compatible_python(
        self, vault, monkeypatch
    ):
        monkeypatch.setattr(
            init._mcp_state,
            "find_launcher_python",
            lambda prefer_path_binaries=False: None,
        )
        command = init.build_session_hook_command(vault, vault)
        assert sys.executable in command

    def test_can_embed_managed_runtime_python(self, vault):
        managed_python = str(vault / ".brain" / "venv" / "Scripts" / "python.exe")
        command = init.build_session_hook_command(vault, vault, python_path=managed_python)
        assert managed_python in command

    def test_windows_quoting_uses_powershell_command_rules(self, vault, monkeypatch):
        managed_python = r"C:\tools&x\.brain\venvs\py3.12\Scripts\python.exe"
        vault_root = PureWindowsPath(r"C:\Users\Rob&x\Documents\Brain")
        workspace = PureWindowsPath(r"C:\Work|x\Brain")
        monkeypatch.setattr(init._mcp_state.sys, "platform", "win32")

        command = init.build_session_hook_command(
            vault_root,
            workspace,
            python_path=managed_python,
        )

        assert command.startswith("Write-Output 'brain_session called:'; & ")
        assert "'C:\\tools&x\\.brain\\venvs\\py3.12\\Scripts\\python.exe'" in command
        assert "'C:\\Users\\Rob&x\\Documents\\Brain\\.brain-core\\scripts\\session.py'" in command
        assert "'C:\\Users\\Rob&x\\Documents\\Brain'" in command
        assert "'C:\\Work|x\\Brain'" in command
        assert '"C:' not in command

    def test_windows_quoting_escapes_embedded_single_quotes(self, vault, monkeypatch):
        managed_python = r"C:\tools\O'Hara\python.exe"
        vault_root = PureWindowsPath(r"C:\Users\O'Hara\Brain")
        monkeypatch.setattr(init._mcp_state.sys, "platform", "win32")

        command = init.build_session_hook_command(
            vault_root,
            vault_root,
            python_path=managed_python,
        )

        assert "'C:\\tools\\O''Hara\\python.exe'" in command
        assert "'C:\\Users\\O''Hara\\Brain'" in command
        assert init._mcp_state.is_session_hook_command(command, vault_root, vault_root)

    def test_posix_hook_prefix_and_quoting_unchanged(self, vault, monkeypatch):
        monkeypatch.setattr(init._mcp_state.sys, "platform", "linux")
        managed_python = "/usr/local/bin/python3.12"

        command = init.build_session_hook_command(vault, vault, python_path=managed_python)

        assert command.startswith("echo brain_session called: && ")
        assert "Write-Output" not in command
        assert managed_python in command


class TestEnsureSessionStartHook:
    def test_creates_hook(self, project, vault):
        init.ensure_session_start_hook(project, vault)
        settings_path = project / ".claude" / "settings.local.json"
        assert settings_path.is_file()
        data = json.loads(settings_path.read_text())
        hooks = data["hooks"]["SessionStart"]
        assert len(hooks) == 1
        command = hooks[0]["hooks"][0]["command"]
        assert command.startswith("echo brain_session called:")
        assert "session.py" in command
        assert str(vault) in command
        assert "--workspace-dir" in command
        assert str(project) in command
        # Hook must use a resolved launcher path, not bare `python3`.
        assert " python3 " not in command
        assert not command.startswith("python3 ")

    def test_creates_powershell_hook_on_windows(self, project, vault, monkeypatch):
        monkeypatch.setattr(init._mcp_state.sys, "platform", "win32")
        monkeypatch.setattr(mcp_transport.sys, "platform", "win32")

        init.ensure_session_start_hook(project, vault, python_path=r"C:\tools&x\python.exe")

        settings_path = project / ".claude" / "settings.local.json"
        data = json.loads(settings_path.read_text())
        hook = data["hooks"]["SessionStart"][0]["hooks"][0]
        assert hook["shell"] == "powershell"
        assert hook["command"].startswith("Write-Output 'brain_session called:'; & ")
        assert "'C:\\tools&x\\python.exe'" in hook["command"]

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

    def test_register_claude_uses_managed_python_for_hook(self, project, vault, monkeypatch):
        managed_python = str(vault / ".brain" / "venv" / "bin" / "python")
        server_config = init.build_mcp_config(managed_python, vault, workspace_dir=project)
        monkeypatch.setattr(mcp_transport, "_has_claude_cli", lambda: False)

        record = init.register_claude(vault, server_config, "project", project)

        settings = json.loads((project / ".claude" / "settings.local.json").read_text())
        command = settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        assert managed_python in command
        assert command == record["hook_command"]

    def test_register_claude_replaces_legacy_machine_python_hook(self, project, vault, monkeypatch):
        machine_python = "/usr/bin/python3.12"
        managed_python = str(vault / ".brain" / "venv" / "bin" / "python")
        legacy_command = (
            "echo 'brain_session called:' "
            f"&& {machine_python} {vault / '.brain-core' / 'scripts' / 'session.py'} "
            f"--vault {vault} --workspace-dir {project} --json"
        )
        settings_path = project / ".claude" / "settings.local.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(json.dumps({
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
        }))
        server_config = init.build_mcp_config(managed_python, vault, workspace_dir=project)
        monkeypatch.setattr(mcp_transport, "_has_claude_cli", lambda: False)

        record = init.register_claude(vault, server_config, "project", project)

        settings = json.loads(settings_path.read_text())
        session_hooks = settings["hooks"]["SessionStart"]
        commands = [
            hook["command"]
            for entry in session_hooks
            for hook in entry["hooks"]
            if hook.get("type") == "command"
        ]
        assert legacy_command not in commands
        assert "echo unrelated" in commands
        assert record["hook_command"] in commands
        assert len([command for command in commands if "session.py" in command]) == 1

    def test_register_claude_replaces_old_windows_hook_with_powershell_hook(
        self, tmp_path, monkeypatch
    ):
        vault = tmp_path / "vault&x"
        project = tmp_path / "project&x"
        vault.mkdir()
        project.mkdir()
        old_python = r"C:\runtime&old\python.exe"
        managed_python = r"C:\runtime&new\python.exe"
        old_command = (
            "echo brain_session called: && "
            + subprocess.list2cmdline([
                old_python,
                str(vault / ".brain-core" / "scripts" / "session.py"),
                "--vault",
                str(vault),
                "--workspace-dir",
                str(project),
                "--json",
            ])
        )
        settings_path = project / ".claude" / "settings.local.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(json.dumps({
            "hooks": {
                "SessionStart": [
                    {"hooks": [{"type": "command", "command": old_command}]}
                ]
            }
        }))
        server_config = init.build_mcp_config(managed_python, vault, workspace_dir=project)
        monkeypatch.setattr(init._mcp_state.sys, "platform", "win32")
        monkeypatch.setattr(mcp_transport.sys, "platform", "win32")
        monkeypatch.setattr(mcp_transport, "_has_claude_cli", lambda: False)

        record = init.register_claude(vault, server_config, "project", project)

        settings = json.loads(settings_path.read_text())
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


class TestClaudeProjectApproval:
    def test_reports_unapproved_project_with_user_scope_shadow_risk(self, project, fake_home):
        (fake_home / ".claude.json").write_text(json.dumps({
            "mcpServers": {
                "brain": {
                    "command": "/usr/bin/python3",
                    "env": {
                        "BRAIN_VAULT_ROOT": "/tmp/other-vault",
                    },
                }
            },
            "projects": {
                str(project): {
                    "enabledMcpjsonServers": [],
                    "disabledMcpjsonServers": [],
                }
            },
        }))

        notes = init.claude_project_followup_notes(project)

        assert any("has not approved project-scoped" in note for note in notes)
        assert any("/mcp" in note for note in notes)
        assert any("user-scoped" in note for note in notes)
        assert any("claude mcp list" in note for note in notes)
        assert any("enabledMcpjsonServers" in note for note in notes)

    def test_reports_disabled_project_server(self, project, fake_home):
        (fake_home / ".claude.json").write_text(json.dumps({
            "projects": {
                str(project): {
                    "enabledMcpjsonServers": [],
                    "disabledMcpjsonServers": ["brain"],
                }
            }
        }))

        notes = init.claude_project_followup_notes(project)

        assert any("disabled" in note for note in notes)
        assert any("re-enable" in note for note in notes)

    def test_returns_no_notes_when_project_scope_is_approved(self, project, fake_home):
        (fake_home / ".claude.json").write_text(json.dumps({
            "projects": {
                str(project): {
                    "enabledMcpjsonServers": ["brain"],
                    "disabledMcpjsonServers": [],
                }
            }
        }))

        assert init.claude_project_followup_notes(project) == []


class TestClientScopeWarnings:
    def test_codex_project_warning_qualifies_precedence(self, vault, project, fake_home, capsys):
        config = init.build_mcp_config("/usr/bin/python3", vault, workspace_dir=project)
        init.write_codex_config(config, fake_home / ".codex" / "config.toml")

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

        init.record_init_target(vault, record_a)
        init.record_init_target(vault, record_b)

        state = init._load_init_state(vault)
        assert len(state["records"]) == 1
        assert state["records"][0]["server_config"] == config_b


class TestRemoval:
    def test_remove_claude_project_registration_cleans_bootstrap_and_hook(self, vault, project, monkeypatch):
        monkeypatch.setattr(mcp_transport, "_has_claude_cli", lambda: False)
        config = init.build_mcp_config("/usr/bin/python3", vault)

        record = init.register_claude(vault, config, "project", project)

        assert (project / ".mcp.json").is_file()
        assert (project / "CLAUDE.md").is_file()
        assert (project / ".claude" / "settings.local.json").is_file()

        removed = mcp_transport._remove_record(vault, record)

        assert removed is True
        assert not (project / ".mcp.json").exists()
        assert not (project / "CLAUDE.md").exists()
        assert not (project / ".claude").exists()

    def test_remove_claude_project_registration_preserves_user_claude_md_content(self, vault, project, monkeypatch):
        monkeypatch.setattr(mcp_transport, "_has_claude_cli", lambda: False)
        (project / "CLAUDE.md").write_text("# My Project\n\nExisting content.\n")
        config = init.build_mcp_config("/usr/bin/python3", vault)

        record = init.register_claude(vault, config, "project", project)

        removed = mcp_transport._remove_record(vault, record)

        assert removed is True
        assert not (project / ".mcp.json").exists()
        assert (project / "CLAUDE.md").read_text() == "# My Project\n\nExisting content.\n"
        assert not (project / ".claude").exists()

    def test_remove_claude_local_registration_cleans_local_bootstrap_and_hook(self, vault, project, monkeypatch):
        monkeypatch.setattr(mcp_transport, "_has_claude_cli", lambda: False)
        config = init.build_mcp_config("/usr/bin/python3", vault)

        record = init.register_claude(vault, config, "local", project)

        removed = mcp_transport._remove_record(vault, record)

        assert removed is True
        assert not (project / ".claude").exists()

    def test_remove_claude_project_uses_recorded_bootstrap_line(self, vault, project, monkeypatch):
        monkeypatch.setattr(mcp_transport, "_has_claude_cli", lambda: False)
        config = init.build_mcp_config("/usr/bin/python3", vault)
        record = init.register_claude(vault, config, "project", project)

        recorded_line = record["bootstrap_line"]
        assert recorded_line in (project / "CLAUDE.md").read_text()

        monkeypatch.setattr(
            init,
            "bootstrap_line_for_target",
            lambda _target: "@.brain-core/index.md (newer-format-bootstrap)",
        )

        removed = mcp_transport._remove_record(vault, record)

        assert removed is True
        claude_md = project / "CLAUDE.md"
        if claude_md.exists():
            assert recorded_line not in claude_md.read_text()

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

        removed = mcp_transport._remove_record(vault, record)

        assert removed is False
        assert config_path.is_file()
        assert init.read_codex_server_config(config_path) == other


def test_apply_mcp_transport_action_quotes_remove_command_on_win32(tmp_path, monkeypatch):
    vault = tmp_path / "Brain Vault"
    project = tmp_path / "My Project"
    vault.mkdir()
    project.mkdir()
    monkeypatch.setattr(_shell.sys, "platform", "win32")
    monkeypatch.setattr(mcp_transport, "_resolve_managed_python", lambda _vault: r"C:\Program Files\Python312\python.exe")
    monkeypatch.setattr(mcp_transport, "_warn_if_user_scope_exists", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(mcp_transport, "_converge_workspace_manifest", lambda *_args, **_kwargs: MagicMock(message="ok"))
    monkeypatch.setattr(mcp_transport, "ensure_brain_ignore_rules", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        mcp_transport,
        "register_claude",
        lambda *_args, **_kwargs: {"client": "claude", "scope": "project"},
    )
    monkeypatch.setattr(mcp_transport, "record_init_target", lambda *_args, **_kwargs: None)

    result = mcp_transport.apply_mcp_transport_action(
        vault,
        client_arg="claude",
        scope="project",
        target_dir=project,
        remove=False,
    )

    assert f'"{vault}"' in result["remove_command"]
    assert f'"{project}"' in result["remove_command"]


class TestSkipMcpMode:
    def test_skip_mcp_scaffolds_folder_without_runtime_or_client_writes(
        self, vault, project, monkeypatch
    ):
        monkeypatch.setattr(init, "find_python", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not resolve runtime")))
        monkeypatch.setattr(init, "register_claude", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not register claude")))
        monkeypatch.setattr(init, "register_codex", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not register codex")))
        monkeypatch.setattr(mcp_transport, "resolve_local_brain_alias", lambda _vault_root: "brain")
        monkeypatch.setattr(
            sys,
            "argv",
            ["init.py", "--vault", str(vault), "--project", str(project), "--skip-mcp"],
        )

        init.main()

        assert (project / ".brain" / "local" / "workspace.yaml").is_file()
        assert not (project / ".mcp.json").exists()
        assert not (project / ".codex" / "config.toml").exists()
        assert not (project / "CLAUDE.md").exists()
        assert not (project / ".claude" / "settings.local.json").exists()

    def test_skip_mcp_vault_root_skips_workspace_binding(self, vault, monkeypatch):
        """--skip-mcp on the vault root must NOT write workspace.yaml (refuse-guard).

        A vault root is a Brain, not a workspace of itself.  The --skip-mcp path
        no longer writes a self-binding; it only writes ignore rules.
        """
        monkeypatch.setattr(init, "find_python", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not resolve runtime")))
        monkeypatch.setattr(
            sys,
            "argv",
            ["init.py", "--vault", str(vault), "--project", str(vault), "--skip-mcp"],
        )

        init.main()

        # No workspace.yaml should be written for a vault root.
        manifest_path = vault / ".brain" / "local" / "workspace.yaml"
        assert not manifest_path.exists(), (
            f"workspace.yaml was written for a vault root; it should not be."
        )

    def test_skip_mcp_rejects_user_scope(self, vault, monkeypatch):
        monkeypatch.setattr(
            sys,
            "argv",
            ["init.py", "--vault", str(vault), "--user", "--skip-mcp"],
        )

        with pytest.raises(SystemExit):
            init.main()


class TestVaultSelfMode:
    """Wiring from init.main() through apply_mcp_transport_action with vault_self=True."""

    # Minimal return dict shaped to satisfy the main() result consumer.
    _APPLY_RESULT = {
        "action": "configure",
        "status": "changed",
        "scope": "project",
        "scope_label": "project (/fake/vault)",
        "target_dir": None,
        "clients": ["claude", "codex"],
        "warnings": [],
        "python_path": "/fake/python3",
        "results": [
            {"client": "claude", "method": "direct write"},
            {"client": "codex", "method": "direct write"},
        ],
        "claude_project_notes": [],
        "verification_notes": ["Verify: /mcp"],
        "remove_command": "configure mcp --remove",
    }

    def test_vault_self_flag_reaches_apply_mcp_transport_action(self, vault, monkeypatch):
        """--vault-self must call apply_mcp_transport_action with vault_self=True."""
        monkeypatch.setattr(
            sys,
            "argv",
            ["init.py", "--vault", str(vault), "--project", str(vault),
             "--vault-self", "--client", "all"],
        )
        apply_result = dict(self._APPLY_RESULT)
        apply_result["target_dir"] = vault

        with patch.object(init, "apply_mcp_transport_action", return_value=apply_result) as mock_apply:
            init.main()

        mock_apply.assert_called_once()
        call_kwargs = mock_apply.call_args.kwargs
        assert call_kwargs.get("vault_self") is True, (
            f"Expected vault_self=True but got: {call_kwargs.get('vault_self')!r}"
        )

    def test_vault_self_with_user_scope_is_rejected(self, vault, monkeypatch):
        """--vault-self --user must call fatal() (SystemExit)."""
        monkeypatch.setattr(
            sys,
            "argv",
            ["init.py", "--vault", str(vault), "--vault-self", "--user", "--client", "all"],
        )

        with pytest.raises(SystemExit):
            init.main()

    def test_vault_self_with_remove_is_rejected(self, vault, monkeypatch):
        """--vault-self --remove must call fatal() (SystemExit)."""
        monkeypatch.setattr(
            sys,
            "argv",
            ["init.py", "--vault", str(vault), "--project", str(vault),
             "--vault-self", "--remove", "--client", "all"],
        )

        with pytest.raises(SystemExit):
            init.main()


class TestSkipMcpVaultPredicate:
    """init.py --skip-mcp uses the narrow is_brain_vault predicate to decide
    whether the target is a vault (skip the self-binding) or a workspace (bind)."""

    def test_skip_mcp_on_vault_root_skips_binding(self, vault, monkeypatch):
        """A vault root (.brain-core/VERSION) must NOT be bound to itself."""
        calls = []
        monkeypatch.setattr(init, "ensure_workspace_manifest", lambda *a, **k: calls.append((a, k)))
        monkeypatch.setattr(init, "_ensure_brain_ignore_rules_or_fatal", lambda *a, **k: None)
        monkeypatch.setattr(
            sys, "argv",
            ["init.py", "--vault", str(vault), "--project", str(vault), "--skip-mcp"],
        )
        init.main()
        assert calls == []  # vault root → no self-binding attempt

    def test_skip_mcp_on_agents_md_workspace_writes_binding(self, vault, tmp_path, monkeypatch):
        """An AGENTS.md-only workspace (not a vault) must still be bound — the
        narrow predicate keeps it from being mistaken for a vault."""
        ws = tmp_path / "devrepo"
        ws.mkdir()
        (ws / "AGENTS.md").write_text("# bootstrap\n")
        calls = []
        monkeypatch.setattr(init, "ensure_workspace_manifest", lambda *a, **k: calls.append((a, k)))
        monkeypatch.setattr(init, "_ensure_brain_ignore_rules_or_fatal", lambda *a, **k: None)
        monkeypatch.setattr(
            sys, "argv",
            ["init.py", "--vault", str(vault), "--project", str(ws), "--skip-mcp"],
        )
        init.main()
        assert len(calls) == 1  # AGENTS.md-only workspace → binding written
