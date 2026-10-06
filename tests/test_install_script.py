"""Regression tests for install.sh."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "brain-core", "scripts"))
from _bootstrap.mcp_state import build_mcp_config, build_session_hook_command
from _bootstrap.mcp_transport import CLAUDE_MD_BOOTSTRAP_VAULT
from _common._yaml import load_mapping_text

from brain_test_support import (
    copy_install_source as _copy_source_checkout,
    launcher_discovery_path,
    write_executable as _write_executable,
    offline_install_env,
    write_fake_launcher,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_PYTHON = sys.executable


@pytest.fixture(autouse=True)
def isolate_installer_machine_state(tmp_path, monkeypatch):
    home = tmp_path / "installer-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "machine-state"))
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""))


def test_install_ignores_machine_local_template_state(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _copy_source_checkout(source)

    # Simulate local-only artefacts in the source checkout.
    _write_executable(
        source / "template-vault" / ".venv" / "bin" / "pip",
        "#!/definitely/not/a/python\n",
    )
    (source / "template-vault" / ".venv" / "source-only-marker").write_text(
        "copied from source\n"
    )
    (source / "template-vault" / ".mcp.json").write_text(
        '{\n  "mcpServers": {\n    "brain": {\n      "command": "stale-template-python"\n    }\n  }\n}\n'
    )
    leaked_codex = source / "template-vault" / ".codex" / "config.toml"
    leaked_codex.parent.mkdir(parents=True, exist_ok=True)
    leaked_codex.write_text(
        '[mcp_servers.brain]\ncommand = "stale-template-python"\n'
    )
    leaked_grok = source / "template-vault" / ".grok" / "config.toml"
    leaked_grok.parent.mkdir(parents=True, exist_ok=True)
    leaked_grok.write_text('[mcp_servers.brain]\ncommand = "stale-template-python"\n')
    leaked_rule = leaked_grok.parent / "rules" / "brain.md"
    leaked_rule.parent.mkdir(parents=True)
    leaked_rule.write_text("stale template rule")
    local = source / "template-vault" / ".brain" / "local"
    local.mkdir(parents=True, exist_ok=True)
    (local / "session.md").write_text("stale session\n")
    (local / "compiled-router.json").write_text("{}\n")

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    write_fake_launcher(fake_bin / "python3.12", cversion=None, venv="ok")

    target = tmp_path / "vault"
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{launcher_discovery_path()}"
    env["HOME"] = str(fake_home)

    result = subprocess.run(
        ["bash", "install.sh", "--non-interactive", "--client", "all", str(target)],
        cwd=source,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert (target / ".mcp.json").is_file(), result.stdout + result.stderr
    assert (target / ".codex" / "config.toml").is_file()
    assert (target / ".grok/config.toml").is_file()
    assert "stale-template-python" not in (target / ".grok/config.toml").read_text()
    assert "session_start" in (target / ".grok/rules/brain.md").read_text()
    assert "stale template rule" not in (target / ".grok/rules/brain.md").read_text()
    assert "grok mcp doctor brain" in result.stderr

    claude_config = json.loads((target / ".mcp.json").read_text())["mcpServers"]["brain"]
    assert claude_config["env"]["BRAIN_WORKSPACE_DIR"] == str(target)
    assert "open Claude Code in this directory and use /mcp to approve `brain` if prompted" in result.stderr
    assert "trust this project and ensure the project-scoped `brain` MCP is enabled if prompted" in result.stderr
    assert "stale-template-python" not in (target / ".mcp.json").read_text()
    assert "stale-template-python" not in (target / ".codex" / "config.toml").read_text()
    # Template-vault `.venv/` leakage is still scrubbed
    assert not (target / ".venv" / "bin" / "pip").exists()
    assert not (target / ".venv" / "source-only-marker").exists()
    # The central venv is now machine-local (under HOME) rather than vault-local
    venvs_root = fake_home / ".brain" / "venvs"
    assert venvs_root.is_dir()
    venv_dirs = [p for p in venvs_root.iterdir() if p.is_dir()]
    assert len(venv_dirs) == 1, f"expected exactly one central venv, got {venv_dirs}"
    central = venv_dirs[0]
    assert (central / "bin" / "python").is_file()
    assert str(central / "bin" / "python") in (target / ".mcp.json").read_text()
    assert (central / "pip-args.txt").read_text().startswith(
        "install --quiet --no-deps --only-binary=:all: -r "
    )
    assert not (target / ".brain" / "local" / "session.md").exists()
    assert not (target / ".brain" / "local" / "compiled-router.json").exists()
    assert (target / ".brain" / "local" / ".gitkeep").is_file()


def test_windows_installer_static_wiring_uses_python_core_launcher():
    # Static wiring check only; native PowerShell runtime smoke belongs to the
    # Windows validation phase.
    script = (REPO_ROOT / "install.ps1").read_text(encoding="utf-8")
    cmd = (REPO_ROOT / "install.cmd").read_text(encoding="utf-8")

    assert '$PSNativeCommandUseErrorActionPreference = $false' in script
    assert 'Join-Path $repoRoot "src\\brain-core\\scripts\\install.py"' in script
    assert 'Join-Path $repoRoot "src\\brain-core\\scripts\\vault_registry.py"' in script
    assert "--get-default" in script
    assert "Override current default brain" in script
    assert '"--source-root", $repoRoot' in script
    assert '"--launcher", $python' in script
    assert '"--mcp-scope", $McpScope' in script
    assert '"--client", $Client' in script
    assert "install.sh" not in script
    assert 'install.ps1" %*' in cmd


def test_install_sh_errors_on_unexpected_install_core_exit(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _copy_source_checkout(source)

    (source / "src" / "brain-core" / "scripts" / "install.py").write_text(
        "import sys\nsys.exit(127)\n",
        encoding="utf-8",
    )

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    write_fake_launcher(fake_bin / "python3.12")

    target = tmp_path / "vault"
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{launcher_discovery_path()}"

    result = subprocess.run(
        ["bash", "install.sh", "--non-interactive", "--client", "all", str(target)],
        cwd=source,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode != 0
    assert "Brain install exited unexpectedly (code 127)." in result.stderr


def test_install_continues_when_mcp_dependency_install_fails(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _copy_source_checkout(source)

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    write_fake_launcher(fake_bin / "python3.12", cversion=None, venv="fail")

    target = tmp_path / "vault"
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{launcher_discovery_path()}"
    env["HOME"] = str(fake_home)

    result = subprocess.run(
        ["bash", "install.sh", "--non-interactive", "--client", "all", str(target)],
        cwd=source,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert (target / ".brain-core" / "VERSION").is_file()
    venvs_root = fake_home / ".brain" / "venvs"
    assert venvs_root.is_dir()
    venv_dirs = [p for p in venvs_root.iterdir() if p.is_dir()]
    assert len(venv_dirs) == 1
    central = venv_dirs[0]
    assert (central / "bin" / "python").is_file()
    assert (central / "pip-args.txt").read_text().startswith(
        "install --quiet --no-deps --only-binary=:all: -r "
    )
    assert not (target / ".mcp.json").exists()
    assert not (target / ".codex" / "config.toml").exists()
    assert not (target / "init-ran.txt").exists()
    assert "Could not provision managed runtime" in result.stderr
    assert "Brain install completed with follow-up work" in result.stderr


def test_install_can_skip_mcp_setup(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _copy_source_checkout(source)

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    write_fake_launcher(fake_bin / "python3.12", cversion=None)

    target = tmp_path / "vault"
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{launcher_discovery_path()}"
    env["HOME"] = str(tmp_path / "home")
    offline_install_env(env, tmp_path)

    result = subprocess.run(
        ["bash", "install.sh", "--non-interactive", "--skip-mcp", str(target)],
        cwd=source,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert (target / ".brain-core" / "VERSION").is_file()
    assert not (target / ".venv").exists()
    assert not (target / ".mcp.json").exists()
    assert not (target / ".codex" / "config.toml").exists()
    assert "MCP registration skipped." in result.stderr
    # Skip skips MCP registration only: the managed runtime is still provisioned.
    venvs = [path for path in (tmp_path / "home" / ".brain" / "venvs").iterdir() if path.is_dir()]
    assert len(venvs) == 1 and (venvs[0] / "bin" / "python").is_file()


def test_install_can_enable_semantic_after_skipping_mcp(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _copy_source_checkout(source)

    (source / "src" / "brain-core" / "scripts" / "configure.py").write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "\n"
        "args = sys.argv[1:]\n"
        "vault = Path(args[args.index('--vault') + 1])\n"
        "(vault / 'semantic-configured.txt').write_text(' '.join(args) + '\\n')\n"
    )

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    write_fake_launcher(fake_bin / "python3.12")

    target = tmp_path / "vault"
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{launcher_discovery_path()}"
    env["HOME"] = str(tmp_path / "home")
    offline_install_env(env, tmp_path)

    result = subprocess.run(
        ["bash", "install.sh", "--non-interactive", "--skip-mcp", "--enable-semantic", str(target)],
        cwd=source,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert (target / "semantic-configured.txt").is_file()
    assert "semantic --enable --vault" in (target / "semantic-configured.txt").read_text()
    assert "MCP registration skipped." in result.stderr
    assert "Semantic retrieval is enabled for this vault." in result.stderr


def test_install_enable_semantic_uses_real_configure_boundary(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _copy_source_checkout(source)

    provision_py = source / "src" / "brain-core" / "scripts" / "_semantic" / "provision.py"
    provision_py.write_text(
        textwrap.dedent(
            """
            from dataclasses import dataclass
            from pathlib import Path

            from _bootstrap.runtime import step as _step
            import _semantic.config as semantic_config


            class SemanticProvisionError(RuntimeError):
                pass


            @dataclass(frozen=True)
            class _FakeModelOutcome:
                downloaded: bool
                manifest_changed: bool


            @dataclass(frozen=True)
            class SemanticProvisionOutcome:
                runtime_changed: bool
                model_outcome: _FakeModelOutcome
                marker_changed: bool
                marker_installed: bool
                assets_changed: bool
                assets_error: str | None
                notes: list[str]


            def provision_semantic_runtime(vault_root, *, python_executable, runtime_ok=None, refresh_assets=True):
                vault = Path(vault_root)
                (vault / "semantic-provision-ran.txt").write_text(f"{python_executable}\\n", encoding="utf-8")
                marker_changed = semantic_config.set_semantic_engine_installed(vault_root, installed=True)
                return SemanticProvisionOutcome(
                    runtime_changed=False,
                    model_outcome=_FakeModelOutcome(downloaded=False, manifest_changed=False),
                    marker_changed=marker_changed,
                    marker_installed=True,
                    assets_changed=True,
                    assets_error=None,
                    notes=[],
                )


            def append_runtime_steps(steps, outcome):
                steps.append(_step("semantic_runtime", "noop", "Semantic runtime dependencies are already provisioned."))
                steps.append(_step("semantic_model", "noop", "Semantic model snapshot is already provisioned."))


            def append_asset_step(steps, notes, outcome):
                steps.append(_step("semantic_assets", "changed", "Rebuilt semantic assets."))
                notes.extend(outcome.notes)


            def append_marker_step(steps, outcome):
                steps.append(_step("semantic_runtime_marker", "changed" if outcome.marker_changed else "noop", "Marked semantic runtime as provisioned."))
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "python3.12",
        "#!/bin/sh\n"
        "if [ \"$1\" = \"-c\" ]; then\n"
        f"  exec {REAL_PYTHON} \"$@\"\n"
        "fi\n"
        "if [ \"$1\" = \"-m\" ] && [ \"$2\" = \"venv\" ]; then\n"
        "  venv_dir=\"$3\"\n"
        "  mkdir -p \"$venv_dir/bin\"\n"
        "  cat > \"$venv_dir/bin/python\" <<'EOF'\n"
        "#!/bin/sh\n"
        "venv_dir=$(cd \"$(dirname \"$0\")/..\" && pwd)\n"
        "if [ \"$1\" = \"-m\" ] && [ \"$2\" = \"pip\" ]; then\n"
        "  shift 2\n"
        "  printf '%s\\n' \"$*\" >> \"$venv_dir/pip-args.txt\"\n"
        "  : > \"$venv_dir/yaml-installed\"\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$1\" = \"-c\" ]; then\n"
        "  if [ -f \"$venv_dir/yaml-installed\" ]; then\n"
        "    printf '{\"major\": 3, \"minor\": 12, \"missing\": [], \"compatible\": true, \"ok\": true}\\n'\n"
        "  else\n"
        "    printf '{\"major\": 3, \"minor\": 12, \"missing\": [\"mcp\"], \"compatible\": true, \"ok\": false}\\n'\n"
        "  fi\n"
        "  exit 0\n"
        "fi\n"
        "printf '%s\\n' \"$*\" >> \"$venv_dir/invocations.txt\"\n"
        f"FAKE_PYTHON_EXEC=\"$0\" exec {REAL_PYTHON} -c 'import os, runpy, sys; sys.executable = os.environ[\"FAKE_PYTHON_EXEC\"]; sys.argv = sys.argv[1:]; sys.path.insert(0, os.path.dirname(sys.argv[0])); runpy.run_path(sys.argv[0], run_name=\"__main__\")' \"$@\"\n"
        "EOF\n"
        "  chmod +x \"$venv_dir/bin/python\"\n"
        "  exit 0\n"
        "fi\n"
        f"FAKE_PYTHON_EXEC=\"$0\" exec {REAL_PYTHON} -c 'import os, runpy, sys; sys.executable = os.environ[\"FAKE_PYTHON_EXEC\"]; sys.argv = sys.argv[1:]; sys.path.insert(0, os.path.dirname(sys.argv[0])); runpy.run_path(sys.argv[0], run_name=\"__main__\")' \"$@\"\n",
    )

    target = tmp_path / "vault"
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{launcher_discovery_path()}"
    env["HOME"] = str(fake_home)
    env.pop("XDG_CONFIG_HOME", None)

    result = subprocess.run(
        ["bash", "install.sh", "--non-interactive", "--skip-mcp", "--enable-semantic", str(target)],
        cwd=source,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert "MCP registration skipped." in result.stderr
    assert "Semantic retrieval is enabled for this vault." in result.stderr
    assert (target / "semantic-provision-ran.txt").is_file()

    # Provisioning runs with the central runtime python, located under the
    # isolated HOME at `~/.brain/venvs/py<X.Y>-<sha16>/bin/python`.
    venvs_root = fake_home / ".brain" / "venvs"
    venv_dirs = [p for p in venvs_root.iterdir() if p.is_dir()]
    assert len(venv_dirs) == 1, f"expected a single central venv under {venvs_root}, got {venv_dirs}"
    central_venv = venv_dirs[0]
    central_python = central_venv / "bin" / "python"
    assert str(central_python) in (target / "semantic-provision-ran.txt").read_text()
    assert (central_venv / "pip-args.txt").read_text().startswith(
        "install --quiet --no-deps --only-binary=:all: -r "
    )
    assert "configure.py semantic --enable --vault" in (central_venv / "invocations.txt").read_text()

    config = load_mapping_text((target / ".brain" / "local" / "config.yaml").read_text(encoding="utf-8"))
    assert config["defaults"]["flags"]["semantic_retrieval"] is True
    assert config["defaults"]["local_runtime"]["semantic_engine_installed"] is True


def test_install_keeps_vault_when_semantic_setup_fails(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _copy_source_checkout(source)

    (source / "src" / "brain-core" / "scripts" / "configure.py").write_text(
        "import sys\n"
        "print('simulated semantic setup failure', file=sys.stderr)\n"
        "sys.exit(1)\n"
    )

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    write_fake_launcher(fake_bin / "python3.12")

    target = tmp_path / "vault"
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{launcher_discovery_path()}"
    env["HOME"] = str(tmp_path / "home")
    offline_install_env(env, tmp_path)

    result = subprocess.run(
        ["bash", "install.sh", "--non-interactive", "--skip-mcp", "--enable-semantic", str(target)],
        cwd=source,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert (target / ".brain-core" / "VERSION").is_file()
    assert "Vault is ready, but semantic retrieval setup was incomplete." in result.stderr
    assert "configure.py\" semantic --enable --vault" in result.stderr


def test_uninstall_preserves_user_claude_md_content_and_cleans_vault_local_claude_state(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _copy_source_checkout(source)

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    write_fake_launcher(fake_bin / "python3.12")

    target = tmp_path / "vault"
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{launcher_discovery_path()}"
    env["HOME"] = str(tmp_path / "home")
    offline_install_env(env, tmp_path)

    install_result = subprocess.run(
        ["bash", "install.sh", "--non-interactive", "--skip-mcp", str(target)],
        cwd=source,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert install_result.returncode == 0, install_result.stderr
    assert "Created managed runtime." in install_result.stderr, "the offline stand-in provisioned the runtime"

    target.joinpath("CLAUDE.md").write_text(
        "# My Vault\n\n"
        f"{CLAUDE_MD_BOOTSTRAP_VAULT}\n",
        encoding="utf-8",
    )

    server_config = build_mcp_config("python", target, workspace_dir=target)
    settings_path = target / ".claude" / "settings.local.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {
                "mcpServers": {"brain": server_config},
                "hooks": {
                    "SessionStart": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": build_session_hook_command(target, target, python_path="python"),
                                }
                            ]
                        }
                    ]
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    local_bootstrap = target / ".claude" / "CLAUDE.local.md"
    local_bootstrap.write_text(f"{CLAUDE_MD_BOOTSTRAP_VAULT}\n", encoding="utf-8")
    init_state = target / ".brain" / "local" / "init-state.json"
    init_state.parent.mkdir(parents=True, exist_ok=True)
    init_state.write_text(
        json.dumps(
            {
                "version": 2,
                "records": [
                    {
                        "schema": "brain.mcp-registration/2",
                        "client": "claude",
                        "scope": "local",
                        "target_path": str(target),
                        "config_path": str(settings_path),
                        "server_name": "brain",
                        "server_config": server_config,
                        "bootstrap_path": str(local_bootstrap),
                        "bootstrap_line": CLAUDE_MD_BOOTSTRAP_VAULT,
                        "hook_path": str(settings_path),
                        "hook_command": build_session_hook_command(target, target, python_path="python"),
                        "method": "test",
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    uninstall_result = subprocess.run(
        ["bash", "install.sh", "--uninstall", "--non-interactive", str(target)],
        cwd=source,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert uninstall_result.returncode == 0, uninstall_result.stdout + uninstall_result.stderr
    assert target.joinpath("CLAUDE.md").read_text(encoding="utf-8") == "# My Vault\n"
    assert not target.joinpath(".claude", "CLAUDE.local.md").exists()
    assert not target.joinpath(".claude", "settings.local.json").exists()
    assert not list(target.joinpath(".claude").iterdir())


def test_uninstall_delegates_to_canonical_owner_and_stops_on_failure(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _copy_source_checkout(source)
    target = tmp_path / "vault"
    (target / ".brain-core").mkdir(parents=True)
    (target / ".brain-core/VERSION").write_text("0.70.0\n")
    log = tmp_path / "uninstall-request.json"
    _write_executable(
        source / "cli/brain",
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "from pathlib import Path\n"
        "if sys.argv[1:] == ['--version']:\n"
        "    print('brain 4.0.0')\n"
        "else:\n"
        f"    Path({str(log)!r}).write_text(json.dumps(sys.argv[1:]))\n"
        "    raise SystemExit(2)\n",
    )
    result = subprocess.run(
        ["bash", str(source / "install.sh"), "--uninstall", "--non-interactive", str(target)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode != 0
    assert json.loads(log.read_text()) == ["uninstall", "--vault", str(target), "--request-json", "{}", "--json"]
    assert (target / ".brain-core/VERSION").exists()
    assert "Uninstall stopped" in result.stderr


def test_uninstall_stops_if_launcher_cannot_start(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _copy_source_checkout(source)
    target = tmp_path / "vault"
    (target / ".brain-core").mkdir(parents=True)
    (target / ".brain-core/VERSION").write_text("0.70.0\n")
    _write_executable(source / "cli/brain", "#!/bin/sh\nexit 4\n")
    result = subprocess.run(
        ["bash", str(source / "install.sh"), "--uninstall", "--non-interactive", str(target)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode != 0
    assert (target / ".brain-core/VERSION").exists()


def test_install_rejects_legacy_force_flag(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _copy_source_checkout(source)

    result = subprocess.run(
        ["bash", "install.sh", "--force", str(tmp_path / "vault")],
        cwd=source,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 1
    assert "--non-interactive" in result.stderr


def test_upgrade_non_interactive_does_not_pass_force_to_upgrade_script(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _copy_source_checkout(source)

    (source / "src" / "brain-core" / "VERSION").write_text("1.0.1\n")
    (source / "src" / "brain-core" / "scripts" / "upgrade.py").write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "\n"
        "args = sys.argv[1:]\n"
        "vault = Path(args[args.index('--vault') + 1])\n"
        "(vault / 'upgrade-args.txt').write_text(' '.join(args) + '\\n')\n"
    )

    target = tmp_path / "vault"
    (target / ".brain-core").mkdir(parents=True)
    (target / ".brain-core" / "VERSION").write_text("1.0.0\n")

    result = subprocess.run(
        ["bash", "install.sh", "--non-interactive", "--skip-mcp", str(target)],
        cwd=source,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert "--force" not in (target / "upgrade-args.txt").read_text()
    # --skip-mcp skips MCP registration only; the upgrade still syncs the runtime.
    assert "--no-sync-deps" not in (target / "upgrade-args.txt").read_text()


def test_upgrade_wrapper_preserves_repeated_stale_brain_exclusions(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _copy_source_checkout(source)

    (source / "src" / "brain-core" / "VERSION").write_text("1.0.1\n")
    (source / "src" / "brain-core" / "scripts" / "upgrade.py").write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "vault = Path(args[args.index('--vault') + 1])\n"
        "(vault / 'upgrade-args.json').write_text(json.dumps(args))\n"
    )
    target = tmp_path / "vault"
    (target / ".brain-core").mkdir(parents=True)
    (target / ".brain-core" / "VERSION").write_text("1.0.0\n")

    result = subprocess.run(
        [
            "bash",
            "install.sh",
            "--non-interactive",
            "--skip-mcp",
            "--exclude-stale-brain",
            "stale-a",
            "--exclude-stale-brain",
            "stale-b",
            str(target),
        ],
        cwd=source,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    args = json.loads((target / "upgrade-args.json").read_text())
    assert [
        args[index + 1]
        for index, arg in enumerate(args)
        if arg == "--exclude-stale-brain"
    ] == ["stale-a", "stale-b"]


def test_upgrade_wrapper_uses_resolved_managed_python(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _copy_source_checkout(source)

    (source / "src" / "brain-core" / "VERSION").write_text("1.0.1\n")

    target = tmp_path / "vault"
    (target / ".brain-core").mkdir(parents=True)
    (target / ".brain-core" / "VERSION").write_text("1.0.0\n")

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "python3",
        "#!/bin/sh\n"
        "if [ \"$1\" = \"-c\" ]; then\n"
        "  printf '3.11\\n'\n"
        "  exit 0\n"
        "fi\n"
        "printf 'unexpected python3 invocation: %s\\n' \"$*\" >&2\n"
        "exit 1\n",
    )
    _write_executable(
        fake_bin / "python3.12",
        "#!/bin/sh\n"
        "if [ \"$1\" = \"-c\" ]; then\n"
        "  printf '3.12\\n'\n"
        "  exit 0\n"
        "fi\n"
        "script=\"$1\"\n"
        "shift\n"
        "vault=''\n"
        "args=''\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  if [ \"$1\" = \"--vault\" ]; then\n"
        "    vault=\"$2\"\n"
        "  fi\n"
        "  args=\"$args $1\"\n"
        "  shift\n"
        "done\n"
        "printf '%s\\n' \"$0\" > \"$vault/upgrade-python.txt\"\n"
        "printf '%s%s\\n' \"$script\" \"$args\" > \"$vault/upgrade-args.txt\"\n"
        "exit 0\n",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{launcher_discovery_path()}"

    result = subprocess.run(
        ["bash", "install.sh", "--non-interactive", "--skip-mcp", str(target)],
        cwd=source,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert (target / "upgrade-python.txt").read_text().strip().endswith("python3.12")
    assert "unexpected python3 invocation" not in result.stderr


def test_upgrade_wrapper_does_not_rerun_mcp_setup(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _copy_source_checkout(source)

    (source / "src" / "brain-core" / "VERSION").write_text("1.0.1\n")
    (source / "src" / "brain-core" / "scripts" / "upgrade.py").write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "\n"
        "args = sys.argv[1:]\n"
        "vault = Path(args[args.index('--vault') + 1])\n"
        "(vault / 'upgrade-ran.txt').write_text('ok\\n')\n"
        "print('upgrade.py owns the upgrade flow', file=sys.stderr)\n"
    )
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    write_fake_launcher(fake_bin / "python3.12", venv="marker")

    target = tmp_path / "vault"
    (target / ".brain-core").mkdir(parents=True)
    (target / ".brain-core" / "VERSION").write_text("1.0.0\n")

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{launcher_discovery_path()}"

    result = subprocess.run(
        ["bash", "install.sh", "--non-interactive", "--client", "all", str(target)],
        cwd=source,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert (target / "upgrade-ran.txt").is_file()
    assert not (target / "init-ran.txt").exists()
    assert not (target / ".venv" / "should-not-exist.txt").exists()
    assert "upgrade.py owns the upgrade flow" in result.stderr


def test_upgrade_wrapper_does_not_run_semantic_configuration(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _copy_source_checkout(source)

    (source / "src" / "brain-core" / "VERSION").write_text("1.0.1\n")
    (source / "src" / "brain-core" / "scripts" / "upgrade.py").write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "\n"
        "args = sys.argv[1:]\n"
        "vault = Path(args[args.index('--vault') + 1])\n"
        "(vault / 'upgrade-ran.txt').write_text('ok\\n')\n"
    )
    (source / "src" / "brain-core" / "scripts" / "configure.py").write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "\n"
        "args = sys.argv[1:]\n"
        "vault = Path(args[args.index('--vault') + 1])\n"
        "(vault / 'semantic-configured.txt').write_text('called\\n')\n"
    )

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    write_fake_launcher(fake_bin / "python3.12")

    target = tmp_path / "vault"
    (target / ".brain-core").mkdir(parents=True)
    (target / ".brain-core" / "VERSION").write_text("1.0.0\n")

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{launcher_discovery_path()}"

    result = subprocess.run(
        ["bash", "install.sh", "--non-interactive", "--enable-semantic", str(target)],
        cwd=source,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert (target / "upgrade-ran.txt").is_file()
    assert not (target / "semantic-configured.txt").exists()
    assert "Upgrade mode does not change local capability configuration." in result.stderr


_PLANNED_PREVIEW = json.dumps({
    "status": "ok", "old_version": "1.0.1", "new_version": "1.0.1", "dry_run": True,
    "warnings": [{"stage": "version_guard", "code": "core_mismatch", "message": "The installed Brain Core differs"}],
})


def _recording_upgrade_script(source, *, preview=_PLANNED_PREVIEW, preview_exit=0):
    """A stand-in upgrade.py that records its argv and answers the equal-version preview with ``preview``."""
    (source / "src" / "brain-core" / "scripts" / "upgrade.py").write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "\n"
        "args = sys.argv[1:]\n"
        "vault = Path(args[args.index('--vault') + 1])\n"
        "(vault / 'upgrade-args.txt').write_text(' '.join(args) + '\\n')\n"
        "if '--dry-run' in args:\n"
        f"    print({preview!r})\n"
        f"    sys.exit({preview_exit})\n"
    )


def _installed_vault(tmp_path, version):
    target = tmp_path / "vault"
    (target / ".brain-core").mkdir(parents=True)
    (target / ".brain-core" / "VERSION").write_text(version + "\n")
    return target


def test_install_refuses_a_downgrade_without_a_force_hint(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _copy_source_checkout(source)
    (source / "src" / "brain-core" / "VERSION").write_text("1.0.0\n")
    _recording_upgrade_script(source)
    target = _installed_vault(tmp_path, "1.0.1")

    result = subprocess.run(
        ["bash", "install.sh", "--non-interactive", "--skip-mcp", str(target)],
        cwd=source, capture_output=True, text=True, timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert "does not downgrade" in result.stderr
    assert "migrations only run forward" in result.stderr
    assert "--force" not in result.stderr
    assert not (target / "upgrade-args.txt").exists()


def test_install_delegates_an_equal_version_to_the_upgrade_script(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _copy_source_checkout(source)
    (source / "src" / "brain-core" / "VERSION").write_text("1.0.1\n")
    _recording_upgrade_script(source)
    target = _installed_vault(tmp_path, "1.0.1")

    result = subprocess.run(
        ["bash", "install.sh", "--non-interactive", "--skip-mcp", str(target)],
        cwd=source, capture_output=True, text=True, timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert "installed core differs from this source" in result.stderr
    assert "Upgrading v1.0.1 → v1.0.1" not in result.stderr
    args = (target / "upgrade-args.txt").read_text()
    assert "--source" in args and "--force" not in args and "--dry-run" not in args


@pytest.mark.parametrize(("preview", "preview_exit", "expected"), [
    pytest.param(json.dumps({"status": "ok", "result": {"status": "noop", "message": "Already at 1.0.1."}}), 0,
                 "already at v1.0.1. No core upgrade needed", id="launcher-noop"),
    pytest.param(json.dumps({"status": "ok", "result": {"status": "planned"}}), 0,
                 "installed core differs from this source", id="launcher-planned"),
    pytest.param(json.dumps({"status": "error", "error": {"message": "stale registry entries require explicit exclusion"}}), 1,
                 "Upgrade refused: stale registry entries require explicit exclusion", id="launcher-error"),
    pytest.param(json.dumps({"status": "error", "reason": "cutover_preflight", "message": "Upgrade refused — CLI cutover preflight failed: boom"}), 1,
                 "Upgrade refused: Upgrade refused — CLI cutover preflight failed: boom", id="upgrader-error"),
    pytest.param("", 1, "Upgrade refused: upgrade.py --dry-run exited 1", id="no-output-failure"),
    pytest.param("", 0, "upgrade.py preview produced no result", id="no-output-success"),
    pytest.param("not json", 0, "upgrade.py preview produced no result", id="unparseable"),
])
def test_install_reads_the_equal_version_preview_in_every_envelope(tmp_path, preview, preview_exit, expected):
    """The preview may be the upgrader's own result or a launcher envelope; silence is an error, not a re-apply."""
    source = tmp_path / "source"
    source.mkdir()
    _copy_source_checkout(source)
    (source / "src" / "brain-core" / "VERSION").write_text("1.0.1\n")
    _recording_upgrade_script(source, preview=preview, preview_exit=preview_exit)
    target = _installed_vault(tmp_path, "1.0.1")

    result = subprocess.run(
        ["bash", "install.sh", "--non-interactive", "--skip-mcp", str(target)],
        cwd=source, capture_output=True, text=True, timeout=60,
    )

    assert expected in result.stderr, result.stderr
    re_applied = "--dry-run" not in (target / "upgrade-args.txt").read_text()
    if expected.startswith("installed core differs"):
        assert result.returncode == 0 and re_applied
    else:
        assert result.returncode == (0 if "already at" in expected else 1)
        assert not re_applied, "nothing else may fall through to the re-apply"


def test_install_shows_the_preview_stderr_when_it_is_refused(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _copy_source_checkout(source)
    (source / "src" / "brain-core" / "VERSION").write_text("1.0.1\n")
    (source / "src" / "brain-core" / "scripts" / "upgrade.py").write_text(
        "import sys\n"
        "print('affected Brain: other-brain', file=sys.stderr)\n"
        "sys.exit(1)\n"
    )
    target = _installed_vault(tmp_path, "1.0.1")

    result = subprocess.run(
        ["bash", "install.sh", "--non-interactive", "--skip-mcp", str(target)],
        cwd=source, capture_output=True, text=True, timeout=60,
    )

    assert result.returncode == 1
    assert "affected Brain: other-brain" in result.stderr
    assert "Upgrade refused: upgrade.py --dry-run exited 1" in result.stderr


def test_install_reports_already_at_beside_a_stale_registry_row(tmp_path):
    """A skipped run never commits the CLI cutover, so its whole-registry preflight cannot refuse the preview."""
    source = tmp_path / "source"
    source.mkdir()
    _copy_source_checkout(source)
    target = tmp_path / "vault"
    target.mkdir()
    shutil.copytree(source / "src" / "brain-core", target / ".brain-core")
    home = Path(os.environ["HOME"])
    registry = home / ".config" / "brain" / "vaults"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        "# brain registry v2 — one Brain per line, <brain-id>\\t<kind>\\t<value>\n"
        f"gone\tlocal\t{tmp_path / 'gone'}\n"
    )
    _write_executable(home / ".local" / "bin" / "brain", '#!/bin/sh\nBRAIN_CLI_VERSION="2.0.0"\n')

    result = subprocess.run(
        ["bash", "install.sh", "--non-interactive", "--skip-mcp", str(target)],
        cwd=source, capture_output=True, text=True, timeout=120,
    )

    assert result.returncode == 0, result.stderr
    version = (source / "src" / "brain-core" / "VERSION").read_text().strip()
    assert f"already at v{version}. No core upgrade needed" in result.stderr
    assert "Upgrade refused" not in result.stderr


def test_install_reports_already_at_for_a_matching_core_at_an_equal_version(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _copy_source_checkout(source)
    target = tmp_path / "vault"
    target.mkdir()
    shutil.copytree(source / "src" / "brain-core", target / ".brain-core")

    result = subprocess.run(
        ["bash", "install.sh", "--non-interactive", "--skip-mcp", str(target)],
        cwd=source, capture_output=True, text=True, timeout=120,
    )

    assert result.returncode == 0, result.stderr
    version = (source / "src" / "brain-core" / "VERSION").read_text().strip()
    assert f"already at v{version}. No core upgrade needed" in result.stderr
    assert "Upgraded to" not in result.stderr
    assert "Upgrading v" not in result.stderr
    assert "--force" not in result.stderr


def test_install_stops_when_the_upgrade_refuses_content_ahead_of_the_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _copy_source_checkout(source)
    version = (source / "src" / "brain-core" / "VERSION").read_text().strip()
    target = _installed_vault(tmp_path, version)
    (target / ".brain" / "local").mkdir(parents=True)
    (target / ".brain" / "local" / "migrations.json").write_text(
        json.dumps({"schema_version": 1, "migrations": {"99.0.0": {"status": "ok"}}})
    )

    result = subprocess.run(
        ["bash", "install.sh", "--non-interactive", "--skip-mcp", str(target)],
        cwd=source, capture_output=True, text=True, timeout=120,
    )

    assert result.returncode != 0
    assert "Upgrade refused" in result.stderr
    assert "records 99.0.0 above this source" in result.stderr
    assert "brain upgrade" not in result.stderr
    assert "Upgraded to" not in result.stderr


def test_install_rejects_skip_cli_at_an_equal_version(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _copy_source_checkout(source)
    (source / "src" / "brain-core" / "VERSION").write_text("1.0.1\n")
    _recording_upgrade_script(source)
    target = _installed_vault(tmp_path, "1.0.1")

    result = subprocess.run(
        ["bash", "install.sh", "--non-interactive", "--skip-mcp", "--skip-cli", str(target)],
        cwd=source, capture_output=True, text=True, timeout=60,
    )

    assert result.returncode != 0
    assert "--skip-cli cannot be used" in result.stderr
    assert not (target / "upgrade-args.txt").exists()


def test_install_does_not_prompt_for_an_equal_version_re_apply(tmp_path):
    """Interactive mode: an equal version with a differing core re-applies without the upgrade prompt."""
    import pty

    source = tmp_path / "source"
    source.mkdir()
    _copy_source_checkout(source)
    (source / "src" / "brain-core" / "VERSION").write_text("1.0.1\n")
    _recording_upgrade_script(source)
    target = _installed_vault(tmp_path, "1.0.1")
    master, slave = pty.openpty()
    try:
        os.write(master, b"n\n")  # would decline the prompt if one appeared
        result = subprocess.run(
            ["bash", "install.sh", "--skip-mcp", str(target)],
            cwd=source, stdin=slave, capture_output=True, text=True, timeout=120,
        )
    finally:
        os.close(master)
        os.close(slave)

    assert result.returncode == 0, result.stderr
    assert "Upgrade skipped" not in result.stderr
    assert "Would you like to upgrade" not in result.stderr
    assert "installed core differs" in result.stderr
    assert (target / "upgrade-args.txt").exists()


def test_equal_version_preview_carries_the_same_options_as_the_run(tmp_path):
    """The preview is the run's own dry run: acknowledgement and exclusions are not dropped."""
    source = tmp_path / "source"
    source.mkdir()
    _copy_source_checkout(source)
    (source / "src" / "brain-core" / "VERSION").write_text("1.0.1\n")
    (source / "src" / "brain-core" / "scripts" / "upgrade.py").write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "vault = Path(args[args.index('--vault') + 1])\n"
        "log = vault / 'upgrade-calls.json'\n"
        "calls = json.loads(log.read_text()) if log.exists() else []\n"
        "calls.append(args)\n"
        "log.write_text(json.dumps(calls))\n"
        "if '--dry-run' in args:\n"
        "    print(json.dumps({'status': 'ok', 'dry_run': True}))\n"
    )
    target = _installed_vault(tmp_path, "1.0.1")

    result = subprocess.run(
        [
            "bash", "install.sh", "--non-interactive", "--skip-mcp",
            "--acknowledge-global-cli-cutover", "--exclude-stale-brain", "stale-a", str(target),
        ],
        cwd=source, capture_output=True, text=True, timeout=60,
    )

    assert result.returncode == 0, result.stderr
    preview, run = json.loads((target / "upgrade-calls.json").read_text())
    assert "--dry-run" in preview and "--json" in preview and "--unattended" in preview
    assert "--acknowledge-global-cli-cutover" in preview and preview[preview.index("--exclude-stale-brain") + 1] == "stale-a"
    assert "--dry-run" not in run
    assert [arg for arg in run if arg not in ("--unattended",)] == [arg for arg in preview if arg not in ("--dry-run", "--json", "--unattended")]
