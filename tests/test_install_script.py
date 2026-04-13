"""Regression tests for install.sh."""

import os
from pathlib import Path
import shutil
import stat
import subprocess


REPO_ROOT = Path(__file__).resolve().parents[1]


def _copy_source_checkout(dest: Path) -> None:
    shutil.copy2(REPO_ROOT / "install.sh", dest / "install.sh")
    shutil.copytree(REPO_ROOT / "template-vault", dest / "template-vault")
    shutil.copytree(REPO_ROOT / "src" / "brain-core", dest / "src" / "brain-core")


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


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
        "  printf '3.12\\n'\n"
        "  exit 0\n"
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
        "printf 'unexpected fake python args: %s\\n' \"$*\" >&2\n"
        "exit 1\n",
    )

    target = tmp_path / "vault"
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
    assert (target / ".mcp.json").is_file()
    assert (target / ".codex" / "config.toml").is_file()
    assert "--project" in (target / "init-args.txt").read_text()
    assert str(target) in (target / "init-args.txt").read_text()
    assert not (target / ".venv" / "bin" / "pip").exists()
    assert not (target / ".venv" / "source-only-marker").exists()
    assert (target / ".venv" / "pip-args.txt").read_text().startswith(
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
        "  printf '3.12\\n'\n"
        "  exit 0\n"
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
        "printf 'unexpected fake python args: %s\\n' \"$*\" >&2\n"
        "exit 1\n",
    )

    target = tmp_path / "vault"
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
    assert (target / ".brain-core" / "VERSION").is_file()
    assert (target / ".venv" / "bin" / "python").is_file()
    assert (target / ".venv" / "pip-args.txt").read_text().startswith(
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
        "  printf '3.12\\n'\n"
        "  exit 0\n"
        "fi\n"
        "printf 'unexpected fake python args: %s\\n' \"$*\" >&2\n"
        "exit 1\n",
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
