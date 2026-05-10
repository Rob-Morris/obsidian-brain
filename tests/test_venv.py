"""Tests for the central-venv path resolver in `_common/_venv.py`.

The path rule is single-sourced here; install.sh, init.py, upgrade.py, and
repair.py all delegate. These tests pin down the contract.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "brain-core" / "scripts"))
from _common import _venv  # noqa: E402


REAL_PYTHON = sys.executable


def _fake_launcher(path: Path) -> Path:
    """Stub Python launcher: `-m venv DIR` mkdir's the structure; `-m pip install` records args."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"-c\" ]; then\n"
        f"  exec {REAL_PYTHON} \"$@\"\n"
        "fi\n"
        "if [ \"$1\" = \"-m\" ] && [ \"$2\" = \"venv\" ]; then\n"
        "  mkdir -p \"$3/bin\"\n"
        "  cp \"$0\" \"$3/bin/python\"\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$1\" = \"-m\" ] && [ \"$2\" = \"pip\" ]; then\n"
        "  shift 2\n"
        "  venv_dir=$(cd \"$(dirname \"$0\")/..\" && pwd)\n"
        "  printf '%s\\n' \"$*\" >> \"$venv_dir/pip-args.txt\"\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n"
    )
    path.chmod(0o755)
    return path


def _make_vault(tmp_path: Path, requirements: str = "mcp==1.0.0\n") -> Path:
    vault = tmp_path / "vault"
    bc = vault / ".brain-core" / "brain_mcp"
    bc.mkdir(parents=True)
    (bc / "requirements.txt").write_text(requirements)
    (vault / ".brain-core" / "VERSION").write_text("0.0.0\n")
    return vault


def test_python_tag_uses_launcher_minor_version(tmp_path):
    """`python_tag` asks the launcher its version, so spoofing major.minor works."""
    fake = tmp_path / "fake-py"
    fake.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"-c\" ]; then echo '7.42'; exit 0; fi\n"
        "exit 1\n"
    )
    fake.chmod(0o755)
    assert _venv.python_tag(fake) == "py7.42"


def test_python_tag_default_uses_running_interpreter():
    expected = f"py{sys.version_info.major}.{sys.version_info.minor}"
    assert _venv.python_tag() == expected


def test_requirements_hash_is_content_addressed(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("one\n")
    b.write_text("one\n")
    assert _venv.requirements_hash(a) == _venv.requirements_hash(b)
    b.write_text("two\n")
    assert _venv.requirements_hash(a) != _venv.requirements_hash(b)


def test_central_venvs_root_is_under_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert _venv.central_venvs_root() == tmp_path / ".brain" / "venvs"


def test_resolve_vault_venv_dir_combines_tag_and_hash(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    vault = _make_vault(tmp_path)
    resolved = _venv.resolve_vault_venv_dir(vault)
    parent = resolved.parent
    assert parent == tmp_path / ".brain" / "venvs"
    name = resolved.name
    assert "-" in name
    tag, rhash = name.rsplit("-", 1)
    assert tag.startswith("py")
    assert len(rhash) == 16


def test_ensure_central_venv_creates_then_reuses(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    vault = _make_vault(tmp_path)
    requirements = _venv.vault_requirements_path(vault)
    launcher = _fake_launcher(tmp_path / "launcher" / "python")

    first = _venv.ensure_central_venv(requirements, launcher=launcher)
    assert first["created"] is True
    assert Path(first["python"]).is_file()

    second = _venv.ensure_central_venv(requirements, launcher=launcher)
    assert second["created"] is False
    assert second["venv_dir"] == first["venv_dir"]


def test_ensure_central_venv_skips_pip_when_install_requirements_false(monkeypatch, tmp_path):
    """Stdlib-only repair scopes need an interpreter without installing deps."""
    monkeypatch.setenv("HOME", str(tmp_path))
    vault = _make_vault(tmp_path)
    requirements = _venv.vault_requirements_path(vault)
    launcher = _fake_launcher(tmp_path / "launcher" / "python")

    result = _venv.ensure_central_venv(
        requirements, launcher=launcher, install_requirements=False,
    )
    pip_args = Path(result["venv_dir"]) / "pip-args.txt"
    assert not pip_args.exists()


def test_ensure_central_venv_obeys_launcher_override_env(monkeypatch, tmp_path):
    """The override env var lets tests substitute a stub launcher transparently."""
    monkeypatch.setenv("HOME", str(tmp_path))
    vault = _make_vault(tmp_path)
    requirements = _venv.vault_requirements_path(vault)

    real_launcher = Path("/nonexistent/python")  # Would fail if used
    fake = _fake_launcher(tmp_path / "fakebin" / "python")
    monkeypatch.setenv("BRAIN_VENV_LAUNCHER", str(fake))

    result = _venv.ensure_central_venv(requirements, launcher=real_launcher)
    assert result["created"] is True
    assert (Path(result["venv_dir"]) / "bin" / "python").is_file()


def test_cli_python_subcommand_prints_python_path(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    vault = _make_vault(tmp_path)
    helper = Path(_venv.__file__)
    out = subprocess.check_output(
        [sys.executable, str(helper), "python", "--vault", str(vault)],
        text=True,
        env={**os.environ, "HOME": str(tmp_path)},
    ).strip()
    assert out.endswith("/bin/python")
    assert str(tmp_path) in out


def test_cli_ensure_creates_venv(tmp_path):
    vault = _make_vault(tmp_path)
    helper = Path(_venv.__file__)
    launcher = _fake_launcher(tmp_path / "launcher" / "python")
    result = subprocess.run(
        [sys.executable, str(helper), "ensure", "--vault", str(vault), "--launcher", str(launcher)],
        text=True,
        capture_output=True,
        env={**os.environ, "HOME": str(tmp_path)},
    )
    assert result.returncode == 0, result.stderr
    venv_dir = Path(result.stdout.strip())
    assert (venv_dir / "bin" / "python").is_file()


def test_legacy_vault_venv_dir_points_at_old_layout(tmp_path):
    vault = _make_vault(tmp_path)
    assert _venv.legacy_vault_venv_dir(vault) == vault / ".venv"
