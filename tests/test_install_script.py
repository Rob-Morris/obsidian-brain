"""Regression tests for install.sh."""

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "brain-core", "scripts"))
import init

from conftest import (
    copy_install_source as _copy_source_checkout,
    write_executable as _write_executable,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_PYTHON = sys.executable


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
    local = source / "template-vault" / ".brain" / "local"
    local.mkdir(parents=True, exist_ok=True)
    (local / "session.md").write_text("stale session\n")
    (local / "compiled-router.json").write_text("{}\n")

    # Stub init.py so the installer can finish without real MCP deps.
    (source / "src" / "brain-core" / "scripts" / "init.py").write_text(
        "import json\n"
        "import sys\n"
        "from pathlib import Path\n"
        "\n"
        "args = sys.argv[1:]\n"
        "vault = Path(args[args.index('--vault') + 1])\n"
        "project = Path(args[args.index('--project') + 1])\n"
        "(vault / 'init-args.txt').write_text(' '.join(args) + '\\n')\n"
        "(project / '.mcp.json').write_text("
        "json.dumps({'mcpServers': {'brain': {'command': 'python'}}}, indent=2) + '\\n'"
        ")\n"
        "(project / '.codex').mkdir(exist_ok=True)\n"
        "(project / '.codex' / 'config.toml').write_text("
        "'[mcp_servers.brain]\\ncommand = \"python\"\\n'"
        ")\n"
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
        "if [ \"$1\" = \"-m\" ] && [ \"$2\" = \"pip\" ]; then\n"
        "  shift 2\n"
        "  venv_dir=$(cd \"$(dirname \"$0\")/..\" && pwd)\n"
        "  printf '%s\\n' \"$*\" > \"$venv_dir/pip-args.txt\"\n"
        "  exit 0\n"
        "fi\n"
        "printf 'unexpected venv python args: %s\\n' \"$*\" >&2\n"
        "exit 1\n"
        "EOF\n"
        "  chmod +x \"$venv_dir/bin/python\"\n"
        "  exit 0\n"
        "fi\n"
        f"exec {REAL_PYTHON} \"$@\"\n",
    )

    target = tmp_path / "vault"
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["HOME"] = str(fake_home)

    result = subprocess.run(
        ["bash", "install.sh", "--non-interactive", str(target)],
        cwd=source,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert (target / ".mcp.json").is_file()
    assert (target / ".codex" / "config.toml").is_file()
    assert "--project" in (target / "init-args.txt").read_text()
    assert str(target) in (target / "init-args.txt").read_text()
    assert "open Claude Code in this directory and use /mcp to approve brain if prompted" in result.stderr
    assert "trust this project and ensure the project-scoped brain MCP is enabled if prompted" in result.stderr
    assert "stale-template-python" not in (target / ".mcp.json").read_text()
    assert '"command": "python"' in (target / ".mcp.json").read_text()
    assert "stale-template-python" not in (target / ".codex" / "config.toml").read_text()
    assert 'command = "python"' in (target / ".codex" / "config.toml").read_text()
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
    assert (central / "pip-args.txt").read_text().startswith(
        "install --quiet --upgrade pip -r "
    )
    assert not (target / ".brain" / "local" / "session.md").exists()
    assert not (target / ".brain" / "local" / "compiled-router.json").exists()
    assert (target / ".brain" / "local" / ".gitkeep").is_file()


def test_install_continues_when_mcp_dependency_install_fails(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _copy_source_checkout(source)

    # If registration runs, it leaves a marker. Dependency failure should skip it.
    (source / "src" / "brain-core" / "scripts" / "init.py").write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "\n"
        "vault = Path(sys.argv[-1])\n"
        "(vault / 'init-ran.txt').write_text('called\\n')\n"
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
        "if [ \"$1\" = \"-m\" ] && [ \"$2\" = \"pip\" ]; then\n"
        "  shift 2\n"
        "  venv_dir=$(cd \"$(dirname \"$0\")/..\" && pwd)\n"
        "  printf '%s\\n' \"$*\" > \"$venv_dir/pip-args.txt\"\n"
        "  printf 'simulated pip failure\\n' >&2\n"
        "  exit 1\n"
        "fi\n"
        "printf 'unexpected venv python args: %s\\n' \"$*\" >&2\n"
        "exit 1\n"
        "EOF\n"
        "  chmod +x \"$venv_dir/bin/python\"\n"
        "  exit 0\n"
        "fi\n"
        f"exec {REAL_PYTHON} \"$@\"\n",
    )

    target = tmp_path / "vault"
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["HOME"] = str(fake_home)

    result = subprocess.run(
        ["bash", "install.sh", "--non-interactive", str(target)],
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
        "install --quiet --upgrade pip -r "
    )
    assert not (target / ".mcp.json").exists()
    assert not (target / ".codex" / "config.toml").exists()
    assert not (target / "init-ran.txt").exists()
    assert "Vault created, but MCP dependency installation failed." in result.stderr


def test_install_can_skip_mcp_setup(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _copy_source_checkout(source)

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "python3.12",
        "#!/bin/sh\n"
        "if [ \"$1\" = \"-c\" ]; then\n"
        f"  exec {REAL_PYTHON} \"$@\"\n"
        "fi\n"
        f"exec {REAL_PYTHON} \"$@\"\n",
    )

    target = tmp_path / "vault"
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

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
    assert "MCP server setup skipped (--skip-mcp)." in result.stderr


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
    _write_executable(
        fake_bin / "python3.12",
        "#!/bin/sh\n"
        "if [ \"$1\" = \"-c\" ]; then\n"
        "  printf '3.12\\n'\n"
        "  exit 0\n"
        "fi\n"
        f"exec {REAL_PYTHON} \"$@\"\n",
    )

    target = tmp_path / "vault"
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

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
    assert "MCP server setup skipped (--skip-mcp)." in result.stderr
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

            from _lifecycle_common import step as _step
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
        "  printf '%s\\n' \"$*\" > \"$venv_dir/pip-args.txt\"\n"
        "  : > \"$venv_dir/yaml-installed\"\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$1\" = \"-c\" ]; then\n"
        "  if [ -f \"$venv_dir/yaml-installed\" ]; then\n"
        "    printf '{\"major\": 3, \"minor\": 12, \"missing\": [], \"compatible\": true, \"ok\": true}\\n'\n"
        "  else\n"
        "    printf '{\"major\": 3, \"minor\": 12, \"missing\": [\"yaml\"], \"compatible\": true, \"ok\": false}\\n'\n"
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
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
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
    assert "MCP server setup skipped (--skip-mcp)." in result.stderr
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
        "install --quiet --upgrade pip -r "
    )
    assert "configure.py semantic --enable --vault" in (central_venv / "invocations.txt").read_text()

    config = yaml.safe_load((target / ".brain" / "local" / "config.yaml").read_text(encoding="utf-8"))
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
    _write_executable(
        fake_bin / "python3.12",
        "#!/bin/sh\n"
        "if [ \"$1\" = \"-c\" ]; then\n"
        "  printf '3.12\\n'\n"
        "  exit 0\n"
        "fi\n"
        f"exec {REAL_PYTHON} \"$@\"\n",
    )

    target = tmp_path / "vault"
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

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
    _write_executable(
        fake_bin / "python3.12",
        "#!/bin/sh\n"
        "if [ \"$1\" = \"-c\" ]; then\n"
        "  printf '3.12\\n'\n"
        "  exit 0\n"
        "fi\n"
        f"exec {REAL_PYTHON} \"$@\"\n",
    )

    target = tmp_path / "vault"
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    install_result = subprocess.run(
        ["bash", "install.sh", "--non-interactive", "--skip-mcp", str(target)],
        cwd=source,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert install_result.returncode == 0, install_result.stderr

    target.joinpath("CLAUDE.md").write_text(
        "# My Vault\n\n"
        f"{init.CLAUDE_MD_BOOTSTRAP_VAULT}\n",
        encoding="utf-8",
    )

    server_config = init.build_mcp_config("python", target, workspace_dir=target)
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
                                    "command": init.build_session_hook_command(target, target),
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
    local_bootstrap.write_text(f"{init.CLAUDE_MD_BOOTSTRAP_VAULT}\n", encoding="utf-8")
    init_state = target / ".brain" / "local" / "init-state.json"
    init_state.parent.mkdir(parents=True, exist_ok=True)
    init_state.write_text(
        json.dumps(
            {
                "version": 1,
                "records": [
                    {
                        "client": "claude",
                        "scope": "local",
                        "target_path": str(target),
                        "config_path": str(settings_path),
                        "server_name": "brain",
                        "server_config": server_config,
                        "bootstrap_path": str(local_bootstrap),
                        "bootstrap_line": init.CLAUDE_MD_BOOTSTRAP_VAULT,
                        "hook_path": str(settings_path),
                        "hook_command": init.build_session_hook_command(target, target),
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

    assert uninstall_result.returncode == 0, uninstall_result.stderr
    assert target.joinpath("CLAUDE.md").read_text(encoding="utf-8") == "# My Vault\n"
    assert not target.joinpath(".claude", "CLAUDE.local.md").exists()
    assert not target.joinpath(".claude", "settings.local.json").exists()
    assert not target.joinpath(".claude").exists()


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
    assert "--no-sync-deps" in (target / "upgrade-args.txt").read_text()


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
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

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
    (source / "src" / "brain-core" / "scripts" / "init.py").write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "\n"
        "vault = Path(sys.argv[sys.argv.index('--vault') + 1])\n"
        "(vault / 'init-ran.txt').write_text('called\\n')\n"
    )

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "python3.12",
        "#!/bin/sh\n"
        "if [ \"$1\" = \"-c\" ]; then\n"
        "  printf '3.12\\n'\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$1\" = \"-m\" ] && [ \"$2\" = \"venv\" ]; then\n"
        "  venv_dir=\"$3\"\n"
        "  mkdir -p \"$venv_dir\"\n"
        "  printf 'unexpected venv creation\\n' > \"$venv_dir/should-not-exist.txt\"\n"
        "  exit 0\n"
        "fi\n"
        f"exec {REAL_PYTHON} \"$@\"\n",
    )

    target = tmp_path / "vault"
    (target / ".brain-core").mkdir(parents=True)
    (target / ".brain-core" / "VERSION").write_text("1.0.0\n")

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    result = subprocess.run(
        ["bash", "install.sh", "--non-interactive", str(target)],
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
    _write_executable(
        fake_bin / "python3.12",
        "#!/bin/sh\n"
        "if [ \"$1\" = \"-c\" ]; then\n"
        "  printf '3.12\\n'\n"
        "  exit 0\n"
        "fi\n"
        f"exec {REAL_PYTHON} \"$@\"\n",
    )

    target = tmp_path / "vault"
    (target / ".brain-core").mkdir(parents=True)
    (target / ".brain-core" / "VERSION").write_text("1.0.0\n")

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

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
