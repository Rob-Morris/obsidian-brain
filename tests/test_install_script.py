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
        "vault = Path(sys.argv[-1])\n"
        "(vault / '.mcp.json').write_text("
        "json.dumps({'mcpServers': {'brain': {'command': 'python'}}}, indent=2) + '\\n'"
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
        ["bash", "install.sh", "--force", str(target)],
        cwd=source,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert (target / ".mcp.json").is_file()
    assert not (target / ".venv" / "bin" / "pip").exists()
    assert not (target / ".venv" / "source-only-marker").exists()
    assert (target / ".venv" / "pip-args.txt").read_text().startswith(
        "install --quiet --upgrade pip -r "
    )
    assert not (target / ".brain" / "local" / "session.md").exists()
    assert not (target / ".brain" / "local" / "compiled-router.json").exists()
    assert (target / ".brain" / "local" / ".gitkeep").is_file()
