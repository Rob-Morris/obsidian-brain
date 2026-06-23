"""Integration tests for install.sh ↔ vault_registry.py."""

import os
import subprocess
from pathlib import Path

import pytest

from brain_test_support import copy_install_source, launcher_discovery_path


@pytest.fixture(scope="module")
def install_source(tmp_path_factory):
    """Repo source tree copied once per test module. Source is never mutated by
    tests (each test installs into a per-test vault dir), so sharing is safe and
    cuts ~2 copytree calls off the module's wall time.
    """
    source = tmp_path_factory.mktemp("source")
    copy_install_source(source)
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
        env={"PATH": f"{fake_bin}{os.pathsep}{launcher_discovery_path()}"},
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
        env={"PATH": f"{fake_bin}{os.pathsep}{launcher_discovery_path()}"},
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
        env={"PATH": f"{fake_bin}{os.pathsep}{launcher_discovery_path()}"},
        capture_output=True, text=True,
    )
    assert result.returncode == 1


def _stub_venv(source: Path) -> None:
    """Replace _common/_venv.py with a no-op stub (so the venv spin succeeds)."""
    venv_script = source / "src" / "brain-core" / "scripts" / "_common" / "_venv.py"
    venv_script.write_text(
        "# no-op stub\n"
        "import sys\n"
        "if __name__ == '__main__':\n"
        "    sys.exit(0)\n"
    )


def _run_install_no_skip_mcp(source, vault_path, fake_home, *extra):
    """Run install.sh with --non-interactive but WITHOUT --skip-mcp."""
    env = os.environ.copy()
    env["HOME"] = str(fake_home)
    env.pop("XDG_CONFIG_HOME", None)
    return subprocess.run(
        [
            "bash", str(source / "install.sh"),
            "--non-interactive",
            *extra, str(vault_path),
        ],
        env=env, capture_output=True, text=True, check=True,
    )


def test_non_interactive_vault_self_no_workspace_yaml(tmp_path):
    """--non-interactive without --skip-mcp selects project scope for the core.

    The Python core owns the vault-self transport call; this shell-level test
    verifies the launcher does not call legacy MCP setup directly and instead passes
    the correct scope to install.py.
    """
    # Own source copy to avoid polluting the module-scoped fixture.
    source = tmp_path / "source"
    source.mkdir()
    copy_install_source(source)
    install_py = source / "src" / "brain-core" / "scripts" / "install.py"
    install_py.write_text(
        "import sys\nfrom pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "vault = Path(args[0])\n"
        "vault.mkdir(parents=True, exist_ok=True)\n"
        "(vault / '.brain-core').mkdir()\n"
        "(vault / '.brain-core' / 'VERSION').write_text('test\\n')\n"
        "(vault / 'install-args.txt').write_text(' '.join(args))\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    vault = tmp_path / "brain"

    _run_install_no_skip_mcp(source, vault, fake_home)

    args = (vault / "install-args.txt").read_text(encoding="utf-8")
    assert "--mcp-scope project" in args
    assert "--client all" in args


def test_explicit_id_registers_under_given_id(tmp_path, install_source):
    """--id <brain-id> threads through to vault_registry --register --id.

    The vault dir name ('brain') deliberately differs from the --id value
    ('custom-xyz') so the test fails if --id is ignored (auto-derived id
    would be 'brain', not 'custom-xyz').
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    vault = tmp_path / "brain"  # basename → auto-derived id would be "brain"

    env = os.environ.copy()
    env["HOME"] = str(fake_home)
    env.pop("XDG_CONFIG_HOME", None)
    subprocess.run(
        [
            "bash", str(install_source / "install.sh"),
            "--non-interactive", "--skip-mcp",
            "--id", "custom-xyz",  # different from "brain" — catches if ignored
            str(vault),
        ],
        env=env, capture_output=True, text=True, check=True,
    )

    registry = fake_home / ".config" / "brain" / "vaults"
    assert registry.exists()
    registry_text = registry.read_text()
    # Must be registered under the explicit id, not the basename-derived one.
    assert "custom-xyz" in registry_text, (
        f"Expected 'custom-xyz' in registry but got:\n{registry_text}"
    )
    assert str(vault) in registry_text


def test_explicit_id_collision_is_surfaced(tmp_path, install_source):
    """When --id <brain-id> collides with an existing registry entry, install.sh
    surfaces a visible warning instead of silently swallowing the error.

    Verifies:
    - The install does NOT abort (scaffolding succeeded; check=True passes).
    - The colliding id still resolves to its original vault, not the new one.
    - stderr contains an explicit "NOT registered" signal mentioning the id.
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()

    # Pre-seed the registry with custom-xyz → some other vault so the id is taken.
    config_dir = fake_home / ".config" / "brain"
    config_dir.mkdir(parents=True)
    other_vault = tmp_path / "other-vault"
    other_vault.mkdir()
    registry = config_dir / "vaults"
    registry.write_text(
        "# brain registry v2 — one Brain per line, <brain-id>\\t<kind>\\t<value>\n"
        f"custom-xyz\tlocal\t{other_vault}\n"
    )

    vault = tmp_path / "new-brain"

    env = os.environ.copy()
    env["HOME"] = str(fake_home)
    env.pop("XDG_CONFIG_HOME", None)
    result = subprocess.run(
        [
            "bash", str(install_source / "install.sh"),
            "--non-interactive", "--skip-mcp",
            "--id", "custom-xyz",
            str(vault),
        ],
        env=env, capture_output=True, text=True,
        check=True,  # install must NOT abort — scaffolding succeeded
    )

    # The colliding id must still point to the original vault, not the new one.
    registry_text = registry.read_text()
    assert str(other_vault) in registry_text, (
        f"Original vault should still be registered under 'custom-xyz':\n{registry_text}"
    )
    assert str(vault) not in registry_text, (
        f"New vault must NOT have been registered under the colliding id:\n{registry_text}"
    )

    # The warning must be visible on stderr — not silence.
    combined = result.stderr + result.stdout
    assert "NOT registered" in combined, (
        f"Expected an explicit 'NOT registered' warning for id collision "
        f"but got stderr:\n{result.stderr}"
    )
    assert "custom-xyz" in combined, (
        f"Expected the colliding id 'custom-xyz' to appear in the warning "
        f"but got stderr:\n{result.stderr}"
    )


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
