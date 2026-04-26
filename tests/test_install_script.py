"""Regression tests for install.sh."""

import os
from pathlib import Path
import subprocess
import sys

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
        f"exec {REAL_PYTHON} \"$@\"\n",
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
    assert "open Claude Code in this directory and use /mcp to approve brain if prompted" in result.stderr
    assert "trust this project and ensure the project-scoped brain MCP is enabled if prompted" in result.stderr
    assert "stale-template-python" not in (target / ".mcp.json").read_text()
    assert '"command": "python"' in (target / ".mcp.json").read_text()
    assert "stale-template-python" not in (target / ".codex" / "config.toml").read_text()
    assert 'command = "python"' in (target / ".codex" / "config.toml").read_text()
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
        f"exec {REAL_PYTHON} \"$@\"\n",
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
