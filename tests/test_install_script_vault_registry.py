"""Integration tests for install.sh ↔ vault_registry.py."""

import os
import subprocess
from pathlib import Path

import pytest

from conftest import copy_install_source


def _stub_init(source: Path) -> None:
    """Replace init.py with a no-op stub so --skip-mcp flows don't exercise real MCP."""
    (source / "src" / "brain-core" / "scripts" / "init.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "vault = Path(args[args.index('--vault') + 1])\n"
        "(vault / 'init-args.txt').write_text(' '.join(args))\n"
    )


@pytest.fixture(scope="module")
def install_source(tmp_path_factory):
    """Repo source tree copied once per test module. Source is never mutated by
    tests (each test installs into a per-test vault dir), so sharing is safe and
    cuts ~2 copytree calls off the module's wall time.
    """
    source = tmp_path_factory.mktemp("source")
    copy_install_source(source)
    _stub_init(source)
    return source


def _run_install(source, vault_path, fake_home, *extra):
    env = os.environ.copy()
    env["HOME"] = str(fake_home)
    env.pop("XDG_CONFIG_HOME", None)
    return subprocess.run(
        [
            "bash", str(source / "install.sh"),
            "--non-interactive", "--skip-mcp",
            *extra, str(vault_path),
        ],
        env=env, capture_output=True, text=True, check=True,
    )


def _entries(registry_file: Path):
    if not registry_file.exists():
        return []
    return [
        line for line in registry_file.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]


def test_fresh_install_registers_vault(tmp_path, install_source):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    vault = tmp_path / "brain"

    _run_install(install_source, vault, fake_home)

    registry = fake_home / ".config" / "brain" / "vaults"
    assert registry.exists()
    assert str(vault) in registry.read_text()


def test_upgrade_does_not_duplicate_entry(tmp_path, install_source):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    vault = tmp_path / "brain"

    _run_install(install_source, vault, fake_home)

    # Rewind VERSION so the second run is treated as an upgrade.
    version_file = vault / ".brain-core" / "VERSION"
    major, minor, patch = [int(x) for x in version_file.read_text().strip().split(".")]
    version_file.write_text(f"{major}.{minor}.{max(0, patch - 1)}\n")

    _run_install(install_source, vault, fake_home)

    assert len(_entries(fake_home / ".config" / "brain" / "vaults")) == 1


def test_same_version_rerun_backfills_registry(tmp_path, install_source):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    vault = tmp_path / "brain"

    _run_install(install_source, vault, fake_home)

    registry = fake_home / ".config" / "brain" / "vaults"
    registry.unlink()

    _run_install(install_source, vault, fake_home)

    assert registry.exists()
    assert len(_entries(registry)) == 1


def test_uninstall_removes_entry(tmp_path, install_source):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    vault = tmp_path / "brain"

    _run_install(install_source, vault, fake_home)

    env = os.environ.copy()
    env["HOME"] = str(fake_home)
    env.pop("XDG_CONFIG_HOME", None)
    subprocess.run(
        [
            "bash", str(install_source / "install.sh"),
            "--uninstall", "--non-interactive",
            str(vault),
        ],
        env=env, capture_output=True, text=True, check=True,
    )

    assert _entries(fake_home / ".config" / "brain" / "vaults") == []
