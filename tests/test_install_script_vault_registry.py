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


def _extract_bash_function(install_sh: Path, function_name: str) -> str:
    """Extract a single bash function definition from install.sh.

    Used by the focused unit tests below so we can exercise individual helpers
    without spinning up the full install.sh flow. Relies on the canonical
    `<name>()` / `}` opening + closing format used throughout the script.
    """
    body = install_sh.read_text().splitlines()
    in_fn = False
    out = []
    for line in body:
        if line.startswith(f"{function_name}()"):
            in_fn = True
        if in_fn:
            out.append(line)
            if line == "}":
                break
    return "\n".join(out) + "\n"


def test_find_python_for_script_picks_interpreter_that_can_run_script(tmp_path):
    """Registry fallback must probe the actual call shape, not just liveness.

    The earlier helper probed `-c 'pass'`, which only proved the
    interpreter could start. A stub or stripped interpreter that succeeds on
    `pass` can still fail on `import _common` (or any modern-Python feature
    the target script relies on). The fixed helper probes by actually invoking the
    target script with a read-only argument (`--list`), so the helper only
    accepts interpreters that can in fact run the upcoming call.
    """
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    # python3 that succeeds when invoked as `<py> <script> --list`.
    fake_py = fake_bin / "python3"
    fake_py.write_text(
        "#!/bin/sh\n"
        "# Succeed on `<py> <anything> --list`, fail otherwise.\n"
        "for arg in \"$@\"; do\n"
        "  if [ \"$arg\" = \"--list\" ]; then exit 0; fi\n"
        "done\n"
        "exit 1\n"
    )
    fake_py.chmod(0o755)
    script = tmp_path / "vault_registry.py"
    script.write_text("# placeholder; the fake python doesn't actually parse it\n")

    install_sh = Path(__file__).resolve().parents[1] / "install.sh"
    fn = _extract_bash_function(install_sh, "find_python_for_script")

    cmd = f"{fn}\nfind_python_for_script {script}\n"
    result = subprocess.run(
        ["bash", "-c", cmd],
        env={"PATH": f"{fake_bin}:/bin:/usr/bin"},
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(fake_py)


def test_find_python_for_script_rejects_stub_passing_pass_but_failing_script(tmp_path):
    """Reviewer's exact repro: a stub that passes `-c 'pass'` but cannot run the script.

    Before the fix the helper would accept this and the subsequent registry
    update would fail silently. After the fix the helper rejects it because
    the actual script invocation fails.
    """
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_py = fake_bin / "python3"
    fake_py.write_text(
        "#!/bin/sh\n"
        "# Pass on `-c 'pass'` (the earlier liveness-only probe).\n"
        "if [ \"$1\" = \"-c\" ] && [ \"$2\" = \"pass\" ]; then exit 0; fi\n"
        "# Fail on any other invocation, including the actual script call.\n"
        "exit 1\n"
    )
    fake_py.chmod(0o755)
    script = tmp_path / "vault_registry.py"
    script.write_text("# placeholder\n")

    install_sh = Path(__file__).resolve().parents[1] / "install.sh"
    fn = _extract_bash_function(install_sh, "find_python_for_script")

    cmd = f"{fn}\nfind_python_for_script {script}\n"
    result = subprocess.run(
        ["bash", "-c", cmd],
        env={"PATH": f"{fake_bin}:/bin:/usr/bin"},
        capture_output=True, text=True,
    )
    # The stub passes -c 'pass' but fails the actual script probe → rejected.
    assert result.returncode == 1


def test_find_python_for_script_skips_broken_candidates(tmp_path):
    """A python that fails any invocation (asdf shim with no version, etc.) is skipped."""
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    broken = fake_bin / "python3"
    broken.write_text("#!/bin/sh\nexit 1\n")
    broken.chmod(0o755)
    script = tmp_path / "vault_registry.py"
    script.write_text("# placeholder\n")

    install_sh = Path(__file__).resolve().parents[1] / "install.sh"
    fn = _extract_bash_function(install_sh, "find_python_for_script")

    cmd = f"{fn}\nfind_python_for_script {script}\n"
    result = subprocess.run(
        ["bash", "-c", cmd],
        env={"PATH": f"{fake_bin}:/bin:/usr/bin"},
        capture_output=True, text=True,
    )
    assert result.returncode == 1


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
