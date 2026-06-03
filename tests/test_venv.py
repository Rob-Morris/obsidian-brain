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


def test_cli_python_subcommand_resolves_bare_launcher_names(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    vault = _make_vault(tmp_path)
    helper = Path(_venv.__file__)
    launcher = _fake_launcher(tmp_path / "fakebin" / "python3.12")
    monkeypatch.setenv("PATH", str(launcher.parent))

    out = subprocess.check_output(
        [sys.executable, str(helper), "python", "--vault", str(vault), "--launcher", "python3.12"],
        text=True,
        env={**os.environ, "HOME": str(tmp_path), "PATH": str(launcher.parent)},
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


def _failing_pip_launcher(path: Path) -> Path:
    """Launcher that creates the venv on `-m venv` but fails on `-m pip install`."""
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
        "  exit 1\n"
        "fi\n"
        "exit 1\n"
    )
    path.chmod(0o755)
    return path


def test_ensure_central_venv_writes_sentinel_after_successful_pip(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    vault = _make_vault(tmp_path)
    requirements = _venv.vault_requirements_path(vault)
    launcher = _fake_launcher(tmp_path / "launcher" / "python")

    result = _venv.ensure_central_venv(requirements, launcher=launcher)
    sentinel = Path(result["venv_dir"]) / _venv.DEPS_SENTINEL_NAME
    assert sentinel.is_file()
    assert sentinel.read_text().strip() == result["hash"]


def test_ensure_central_venv_skip_pip_does_not_write_sentinel(monkeypatch, tmp_path):
    """`install_requirements=False` callers don't claim deps are installed."""
    monkeypatch.setenv("HOME", str(tmp_path))
    vault = _make_vault(tmp_path)
    requirements = _venv.vault_requirements_path(vault)
    launcher = _fake_launcher(tmp_path / "launcher" / "python")

    result = _venv.ensure_central_venv(
        requirements, launcher=launcher, install_requirements=False,
    )
    sentinel = Path(result["venv_dir"]) / _venv.DEPS_SENTINEL_NAME
    assert not sentinel.exists()


def test_ensure_central_venv_recovers_from_half_built_runtime(monkeypatch, tmp_path):
    """A venv created but with pip failed must re-run pip on the next ensure."""
    monkeypatch.setenv("HOME", str(tmp_path))
    vault = _make_vault(tmp_path)
    requirements = _venv.vault_requirements_path(vault)

    # First call: pip fails partway through, leaving bin/python but no sentinel.
    failing = _failing_pip_launcher(tmp_path / "failing" / "python")
    with pytest.raises(subprocess.CalledProcessError):
        _venv.ensure_central_venv(requirements, launcher=failing)
    venv_dir = _venv.resolve_vault_venv_dir(vault, launcher=failing)
    assert (venv_dir / "bin" / "python").is_file()  # half-built marker
    assert not (venv_dir / _venv.DEPS_SENTINEL_NAME).exists()

    # Second call: working launcher, same venv dir, should re-run pip and write sentinel.
    # Use BRAIN_VENV_LAUNCHER override so the python_tag matches the first call.
    working = _fake_launcher(tmp_path / "working" / "python")
    monkeypatch.setenv("BRAIN_VENV_LAUNCHER", str(failing))
    # Replace the bin/python with the working launcher so pip actually records args.
    (venv_dir / "bin" / "python").unlink()
    (venv_dir / "bin" / "python").symlink_to(working)
    result = _venv.ensure_central_venv(requirements, launcher=failing)
    assert (venv_dir / _venv.DEPS_SENTINEL_NAME).is_file()
    assert (venv_dir / "pip-args.txt").is_file()


def test_ensure_central_venv_skips_pip_when_sentinel_matches(monkeypatch, tmp_path):
    """Once sentinel is written, repeated ensures don't re-run pip."""
    monkeypatch.setenv("HOME", str(tmp_path))
    vault = _make_vault(tmp_path)
    requirements = _venv.vault_requirements_path(vault)
    launcher = _fake_launcher(tmp_path / "launcher" / "python")

    first = _venv.ensure_central_venv(requirements, launcher=launcher)
    pip_args = Path(first["venv_dir"]) / "pip-args.txt"
    # _fake_launcher's pip stub appends one line per call. After install + sentinel,
    # a second ensure should not append again.
    initial_size = pip_args.stat().st_size
    _venv.ensure_central_venv(requirements, launcher=launcher)
    assert pip_args.stat().st_size == initial_size


def test_find_runnable_python_prefers_central_venv(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    vault = _make_vault(tmp_path)
    launcher = _fake_launcher(tmp_path / "launcher" / "python")

    _venv.ensure_central_venv(_venv.vault_requirements_path(vault), launcher=launcher)
    result = _venv.find_runnable_python(vault, launcher=launcher)
    expected = _venv.resolve_vault_venv_python(vault, launcher=launcher)
    assert result == expected


def test_find_runnable_python_falls_back_to_legacy(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    vault = _make_vault(tmp_path)
    launcher = _fake_launcher(tmp_path / "launcher" / "python")

    legacy_bin = vault / ".venv" / "bin"
    legacy_bin.mkdir(parents=True)
    legacy_py = legacy_bin / "python"
    legacy_py.write_text("#!/bin/sh\nexit 0\n")
    legacy_py.chmod(0o755)

    result = _venv.find_runnable_python(vault, launcher=launcher)
    assert result == legacy_py


def test_find_runnable_python_falls_back_to_launcher(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    vault = _make_vault(tmp_path)
    launcher = _fake_launcher(tmp_path / "launcher" / "python")

    result = _venv.find_runnable_python(vault, launcher=launcher)
    assert result == launcher


def test_find_runnable_python_returns_none_when_nothing_exists(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    vault = _make_vault(tmp_path)
    nonexistent = tmp_path / "nope" / "python"

    result = _venv.find_runnable_python(vault, launcher=nonexistent)
    assert result is None


def test_cli_runnable_python_falls_back_to_launcher_when_central_missing(tmp_path):
    vault = _make_vault(tmp_path)
    helper = Path(_venv.__file__)
    launcher = _fake_launcher(tmp_path / "launcher" / "python")

    result = subprocess.run(
        [sys.executable, str(helper), "runnable-python",
         "--vault", str(vault), "--launcher", str(launcher)],
        text=True, capture_output=True,
        env={**os.environ, "HOME": str(tmp_path)},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(launcher)


def _make_central_venv(home: Path, tag: str, rhash: str) -> Path:
    """Create a stub central venv directory matching the (tag, hash) layout."""
    venv_dir = home / ".brain" / "venvs" / f"{tag}-{rhash}"
    (venv_dir / "bin").mkdir(parents=True, exist_ok=True)
    py = venv_dir / "bin" / "python"
    py.write_text("#!/bin/sh\nexit 0\n")
    py.chmod(0o755)
    return py


def test_find_existing_central_venv_prefers_exact_match(monkeypatch, tmp_path):
    """Exact tag match wins even when a different compatible-minor venv exists."""
    monkeypatch.setenv("HOME", str(tmp_path))
    vault = _make_vault(tmp_path)
    rhash = _venv.requirements_hash(_venv.vault_requirements_path(vault))
    launcher = _fake_launcher(tmp_path / "launcher" / "python")  # reports 3.12 via REAL_PYTHON

    exact = _make_central_venv(tmp_path, "py3.12", rhash)
    _make_central_venv(tmp_path, "py3.13", rhash)

    result = _venv.find_existing_central_venv(vault, launcher=launcher)
    assert result == exact


def test_find_existing_central_venv_falls_back_to_compatible_minor(monkeypatch, tmp_path):
    """Regression for the Brew-churn scenario.

    The user installed Brain when `python3.13` was the selected launcher and
    Brain provisioned `py3.13-<hash>`. Brew has since bumped Python and the
    current launcher is `python3.12`. The exact-tag path
    `py3.12-<hash>/bin/python` does not exist. Without compatible-minor
    fallback, brain CLI dispatch would fall through to the bare launcher
    and bypass the managed runtime, breaking MCP and provoking unnecessary
    re-provisioning. With the fallback in place, the existing
    `py3.13-<hash>` runtime is returned and the user keeps working.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    vault = _make_vault(tmp_path)
    rhash = _venv.requirements_hash(_venv.vault_requirements_path(vault))
    launcher = _fake_launcher(tmp_path / "launcher" / "python")  # reports 3.12

    older_existing = _make_central_venv(tmp_path, "py3.13", rhash)

    result = _venv.find_existing_central_venv(vault, launcher=launcher)
    assert result == older_existing


def test_find_existing_central_venv_ignores_unsupported_minor(monkeypatch, tmp_path):
    """A `py3.10-<hash>` or `py3.11-<hash>` venv (somehow) under the central root must be skipped.

    DD-048's floor is 3.12. Picking a pre-3.12 runtime — even if its
    requirements hash matches — would silently violate the contract.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    vault = _make_vault(tmp_path)
    rhash = _venv.requirements_hash(_venv.vault_requirements_path(vault))
    launcher = _fake_launcher(tmp_path / "launcher" / "python")  # reports 3.12

    _make_central_venv(tmp_path, "py3.10", rhash)
    _make_central_venv(tmp_path, "py3.11", rhash)

    result = _venv.find_existing_central_venv(vault, launcher=launcher)
    assert result is None


def test_find_existing_central_venv_picks_highest_compatible_minor(monkeypatch, tmp_path):
    """When multiple compatible-minor venvs exist for the same hash, highest wins."""
    monkeypatch.setenv("HOME", str(tmp_path))
    vault = _make_vault(tmp_path)
    rhash = _venv.requirements_hash(_venv.vault_requirements_path(vault))

    # Make the exact tag a non-match (e.g. py3.14 launcher) so we exercise the
    # fallback branch with multiple candidates rather than the exact-match
    # short-circuit.
    fake_14 = tmp_path / "launcher" / "python"
    fake_14.parent.mkdir(parents=True, exist_ok=True)
    fake_14.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"-c\" ]; then\n"
        "  echo 3.14\n"
        "fi\n"
    )
    fake_14.chmod(0o755)

    _make_central_venv(tmp_path, "py3.12", rhash)
    py313 = _make_central_venv(tmp_path, "py3.13", rhash)
    _make_central_venv(tmp_path, "py3.10", rhash)  # ignored

    result = _venv.find_existing_central_venv(vault, launcher=fake_14)
    assert result == py313


def test_find_existing_central_venv_ignores_different_hash(monkeypatch, tmp_path):
    """A venv for a different requirements hash never matches, regardless of minor."""
    monkeypatch.setenv("HOME", str(tmp_path))
    vault = _make_vault(tmp_path)
    launcher = _fake_launcher(tmp_path / "launcher" / "python")

    other_hash = "0" * 16
    _make_central_venv(tmp_path, "py3.12", other_hash)
    _make_central_venv(tmp_path, "py3.13", other_hash)

    result = _venv.find_existing_central_venv(vault, launcher=launcher)
    assert result is None


def test_find_runnable_python_uses_compatible_central_minor(monkeypatch, tmp_path):
    """End-to-end: find_runnable_python wires through to the compatible-minor fallback."""
    monkeypatch.setenv("HOME", str(tmp_path))
    vault = _make_vault(tmp_path)
    rhash = _venv.requirements_hash(_venv.vault_requirements_path(vault))
    launcher = _fake_launcher(tmp_path / "launcher" / "python")

    py313 = _make_central_venv(tmp_path, "py3.13", rhash)

    result = _venv.find_runnable_python(vault, launcher=launcher)
    assert result == py313


def test_cli_runnable_python_exits_nonzero_when_no_candidate(tmp_path):
    vault = _make_vault(tmp_path)
    helper = Path(_venv.__file__)
    nonexistent = tmp_path / "nope" / "python"

    result = subprocess.run(
        [sys.executable, str(helper), "runnable-python",
         "--vault", str(vault), "--launcher", str(nonexistent)],
        text=True, capture_output=True,
        env={**os.environ, "HOME": str(tmp_path)},
    )
    assert result.returncode != 0
    assert "no runnable python" in result.stderr
