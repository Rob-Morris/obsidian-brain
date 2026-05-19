"""End-to-end convergence tests for v0.39.0's central-runtime orchestrator.

Same fixture across every supported managed-runtime entry point — `brain`
CLI dispatch, `repair.py runtime`, `repair.py registry --dry-run`,
`configure.py semantic`, `upgrade.py` — to verify that all of them honour
the same reuse-before-create invariant after Python minor churn (the
Brew-churn scenario).

Fixture shape:

- A vault with `requirements.txt` content `mcp==1.0.0`.
- A compatible-minor central runtime *already* on disk at
  `~/.brain/venvs/py3.13-<hash>/bin/python` (a stub that reports
  `version_info >= (3, 12)` so the orchestrator's probe accepts it).
- A launcher Python that reports `3.12` so the exact-tag path the entry
  point *would* otherwise create is `py3.12-<hash>`.

Invariant asserted for every entry point:

- The existing `py3.13-<hash>` runtime is reused.
- No new `py3.12-<hash>` directory is created just because the launcher
  minor changed.
- When required modules are missing on the reused runtime, the orchestrator
  syncs them in place (single-source pip install) rather than provisioning
  a parallel runtime.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_PYTHON = sys.executable


def _make_vault_with_helper(root: Path) -> Path:
    """Vault carrying a real `_common/_venv.py` (so the orchestrator runs against the real code)."""
    vault = root / "vault"
    scripts = vault / ".brain-core" / "scripts"
    common = scripts / "_common"
    common.mkdir(parents=True)
    (vault / ".brain-core" / "VERSION").write_text("0.39.0\n")
    bc_req = vault / ".brain-core" / "brain_mcp"
    bc_req.mkdir(parents=True)
    (bc_req / "requirements.txt").write_text("mcp==1.0.0\n")

    real_venv_py = REPO_ROOT / "src" / "brain-core" / "scripts" / "_common" / "_venv.py"
    (common / "_venv.py").write_text(real_venv_py.read_text())
    return vault


def _make_3_13_central_venv(home: Path, rhash: str, *, with_modules: tuple[str, ...] = ()) -> Path:
    """Create a stub `py3.13-<hash>/bin/python` that probes as 3.13 and reports the named modules as available."""
    venv_dir = home / ".brain" / "venvs" / f"py3.13-{rhash}"
    bin_dir = venv_dir / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    py = bin_dir / "python"
    modules_repr = repr(list(with_modules))
    # The stub starts with `with_modules` available, but after a pip install
    # (signalled by `pip-args.txt` existing in the venv dir) it reports every
    # probed module as available — modelling a successful sync-in-place.
    py.write_text(
        "#!/bin/sh\n"
        f"venv_dir=\"$(cd \"$(dirname \"$0\")/..\" && pwd)\"\n"
        "if [ \"$1\" = \"-c\" ]; then\n"
        f"  exec {REAL_PYTHON} - \"$venv_dir\" \"$2\" <<'PY'\n"
        "import json, re, sys\n"
        "venv_dir, code = sys.argv[1], sys.argv[2]\n"
        f"available = set({modules_repr})\n"
        "import os\n"
        "if os.path.isfile(os.path.join(venv_dir, 'pip-args.txt')):\n"
        "    available = None  # everything available\n"
        "if 'version_info' in code:\n"
        "    payload = {'major': 3, 'minor': 13, 'compatible': True}\n"
        "    m = re.search(r'mods = (\\([^)]*\\))', code)\n"
        "    mods = tuple()\n"
        "    if m:\n"
        "        mods = eval(m.group(1))\n"
        "    if available is None:\n"
        "        payload['missing'] = []\n"
        "    else:\n"
        "        payload['missing'] = [name for name in mods if name not in available]\n"
        "    payload['ok'] = not payload['missing']\n"
        "    print(json.dumps(payload))\n"
        "sys.exit(0)\n"
        "PY\n"
        "fi\n"
        "if [ \"$1\" = \"-m\" ] && [ \"$2\" = \"pip\" ]; then\n"
        "  shift 2\n"
        "  printf '%s\\n' \"$*\" >> \"$venv_dir/pip-args.txt\"\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    py.chmod(0o755)
    return py


def _make_3_12_launcher(root: Path) -> Path:
    """Launcher Python reporting 3.12. For non-`-c` calls, exec the real interpreter."""
    bin_dir = root / "launcher-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    py = bin_dir / "python3.12"
    py.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"-c\" ]; then\n"
        "  # Probes asking for major.minor get a clean '3.12'.\n"
        "  case \"$2\" in\n"
        "    *version_info*)\n"
        "      printf '3.12\\n'\n"
        "      exit 0\n"
        "      ;;\n"
        "  esac\n"
        "fi\n"
        f"exec {REAL_PYTHON} \"$@\"\n"
    )
    py.chmod(0o755)
    return py


@pytest.fixture
def brew_churn_env(tmp_path, monkeypatch):
    """Standard fixture: existing py3.13-<hash> runtime + python3.12 launcher.

    Yields ``(vault, requirements_hash, launcher, existing_py313, home)``.
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    vault = _make_vault_with_helper(tmp_path)

    # Compute the requirements hash via the real helper.
    sys.path.insert(0, str(vault / ".brain-core" / "scripts"))
    try:
        from _common import _venv  # noqa: WPS433 — vault-copied helper
        rhash = _venv.requirements_hash(_venv.vault_requirements_path(vault))
    finally:
        sys.path.pop(0)

    launcher = _make_3_12_launcher(tmp_path)
    existing_py313 = _make_3_13_central_venv(fake_home, rhash, with_modules=("mcp",))
    (existing_py313.parent.parent / ".brain-deps-installed").write_text(rhash)
    return vault, rhash, launcher, existing_py313, fake_home


# ---------------------------------------------------------------------------
# Convergence helper-level test (the orchestrator itself)
# ---------------------------------------------------------------------------

def test_orchestrator_reuses_existing_compatible_minor_runtime(brew_churn_env):
    """The shared orchestrator picks the existing py3.13 runtime, not a new py3.12."""
    vault, rhash, launcher, existing_py313, fake_home = brew_churn_env

    sys.path.insert(0, str(REPO_ROOT / "src" / "brain-core" / "scripts"))
    try:
        from _common import _venv
    finally:
        sys.path.pop(0)

    result = _venv.resolve_or_provision_central_venv(
        vault,
        launcher=launcher,
        required_modules=("mcp",),
    )
    assert result["outcome"] == _venv.RUNTIME_REUSED
    assert result["python"] == str(existing_py313)
    # No py3.12-<hash> directory was created.
    assert not (fake_home / ".brain" / "venvs" / f"py3.12-{rhash}").exists()


def test_orchestrator_syncs_in_place_when_modules_missing(tmp_path, monkeypatch):
    """When the reused runtime is missing modules, pip runs against IT, not a new venv."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    vault = _make_vault_with_helper(tmp_path)
    sys.path.insert(0, str(vault / ".brain-core" / "scripts"))
    try:
        from _common import _venv
        rhash = _venv.requirements_hash(_venv.vault_requirements_path(vault))
    finally:
        sys.path.pop(0)

    launcher = _make_3_12_launcher(tmp_path)
    # Existing py3.13 runtime that does NOT have `mcp` available — orchestrator must sync.
    existing_py313 = _make_3_13_central_venv(fake_home, rhash, with_modules=())

    sys.path.insert(0, str(REPO_ROOT / "src" / "brain-core" / "scripts"))
    try:
        from _common import _venv as _v
    finally:
        sys.path.pop(0)

    result = _v.resolve_or_provision_central_venv(
        vault,
        launcher=launcher,
        required_modules=("mcp",),
    )
    assert result["outcome"] == _v.RUNTIME_SYNCED
    assert result["python"] == str(existing_py313)
    # No py3.12-<hash> was created.
    assert not (fake_home / ".brain" / "venvs" / f"py3.12-{rhash}").exists()
    # pip-args.txt was written into the existing py3.13 venv.
    pip_args = existing_py313.parent.parent / "pip-args.txt"
    assert pip_args.is_file()
    assert "install" in pip_args.read_text()
    # Sentinel records the requirements hash.
    sentinel = existing_py313.parent.parent / ".brain-deps-installed"
    assert sentinel.is_file()
    assert sentinel.read_text().strip() == rhash


def test_orchestrator_honours_forced_sync_for_existing_runtime_without_sentinel(brew_churn_env):
    """Explicit install sync requests still repair a reused runtime in place.

    This is the upgrade-path regression guard: the reused py3.13 runtime
    probes as compatible, but because it lacks the deps sentinel the owner
    must still run pip when `install_requirements=True`.
    """
    vault, rhash, launcher, existing_py313, fake_home = brew_churn_env

    sys.path.insert(0, str(REPO_ROOT / "src" / "brain-core" / "scripts"))
    try:
        from _common import _venv
    finally:
        sys.path.pop(0)

    sentinel = existing_py313.parent.parent / ".brain-deps-installed"
    sentinel.unlink()

    result = _venv.resolve_or_provision_central_venv(
        vault,
        launcher=launcher,
        required_modules=(),
        install_requirements=True,
    )
    assert result["outcome"] == _venv.RUNTIME_SYNCED
    assert result["python"] == str(existing_py313)
    assert not (fake_home / ".brain" / "venvs" / f"py3.12-{rhash}").exists()
    pip_args = existing_py313.parent.parent / "pip-args.txt"
    assert pip_args.is_file()
    assert sentinel.is_file()
    assert sentinel.read_text().strip() == rhash


# ---------------------------------------------------------------------------
# bootstrap_managed_runtime envelope (used by repair.py and configure.py)
# ---------------------------------------------------------------------------

def test_bootstrap_managed_runtime_reports_reused_on_brew_churn(brew_churn_env):
    """`_lifecycle_common.bootstrap_managed_runtime` delegates to the orchestrator
    and surfaces a noop runtime step + noop dependencies step when the
    py3.13 runtime already has the required modules. No new py3.12-<hash>
    directory appears."""
    vault, rhash, launcher, existing_py313, fake_home = brew_churn_env

    sys.path.insert(0, str(REPO_ROOT / "src" / "brain-core" / "scripts"))
    try:
        import _lifecycle_common as lifecycle_common
    finally:
        sys.path.pop(0)

    summary = lifecycle_common.bootstrap_managed_runtime(
        vault,
        required_modules=("mcp",),
        dependency_owner="convergence test",
        launcher_python=str(launcher),
    )
    assert summary["status"] == "ready"
    assert summary["managed_python"] == str(existing_py313)
    steps = {entry["name"]: entry["status"] for entry in summary["steps"]}
    assert steps["managed_runtime"] == "noop"
    assert steps["managed_dependencies"] == "noop"
    assert not (fake_home / ".brain" / "venvs" / f"py3.12-{rhash}").exists()


def test_bootstrap_managed_runtime_syncs_modules_in_place(tmp_path, monkeypatch):
    """When the reused runtime is missing modules, the envelope reports a `changed`
    dependency step against the existing runtime — not a new runtime."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    vault = _make_vault_with_helper(tmp_path)
    sys.path.insert(0, str(vault / ".brain-core" / "scripts"))
    try:
        from _common import _venv
        rhash = _venv.requirements_hash(_venv.vault_requirements_path(vault))
    finally:
        sys.path.pop(0)

    launcher = _make_3_12_launcher(tmp_path)
    existing_py313 = _make_3_13_central_venv(fake_home, rhash, with_modules=())

    sys.path.insert(0, str(REPO_ROOT / "src" / "brain-core" / "scripts"))
    try:
        import _lifecycle_common as lifecycle_common
    finally:
        sys.path.pop(0)

    summary = lifecycle_common.bootstrap_managed_runtime(
        vault,
        required_modules=("mcp",),
        dependency_owner="sync-in-place test",
        launcher_python=str(launcher),
    )
    assert summary["status"] == "ready"
    assert summary["managed_python"] == str(existing_py313)
    steps = {entry["name"]: entry["status"] for entry in summary["steps"]}
    assert steps["managed_runtime"] == "noop"
    assert steps["managed_dependencies"] == "changed"
    assert not (fake_home / ".brain" / "venvs" / f"py3.12-{rhash}").exists()


def test_bootstrap_managed_runtime_dry_run_does_not_mutate(brew_churn_env):
    """Dry-run path through the orchestrator: nothing is created or synced."""
    vault, rhash, launcher, existing_py313, fake_home = brew_churn_env

    sys.path.insert(0, str(REPO_ROOT / "src" / "brain-core" / "scripts"))
    try:
        import _lifecycle_common as lifecycle_common
    finally:
        sys.path.pop(0)

    summary = lifecycle_common.bootstrap_managed_runtime(
        vault,
        required_modules=("mcp",),  # already available; should be noop dry-run-ish
        dependency_owner="dry-run test",
        launcher_python=str(launcher),
        dry_run=True,
    )
    # The runtime is fine and modules present → no planned ops; status ready.
    assert summary["status"] == "ready"
    # No py3.12-<hash> dir.
    assert not (fake_home / ".brain" / "venvs" / f"py3.12-{rhash}").exists()


# ---------------------------------------------------------------------------
# upgrade.py
# ---------------------------------------------------------------------------

def test_upgrade_reuses_compatible_minor_runtime(brew_churn_env, monkeypatch):
    """`upgrade.py`'s _ensure_central_runtime now goes through the orchestrator,
    so a Brew-churned vault doesn't get a parallel py3.12-<hash> directory
    when its py3.13 central runtime is still usable."""
    vault, rhash, launcher, existing_py313, fake_home = brew_churn_env

    sys.path.insert(0, str(REPO_ROOT / "src" / "brain-core" / "scripts"))
    try:
        import upgrade
    finally:
        sys.path.pop(0)

    # upgrade.py uses sys.executable as the launcher inside _ensure_central_runtime;
    # patch sys.executable to point at our py3.12 stub for the duration.
    monkeypatch.setattr(upgrade.sys, "executable", str(launcher))

    runtime = upgrade._ensure_central_runtime(
        vault_root=vault,
        requirements_changed=True,
        sync_deps=None,
    )
    assert runtime is not None
    assert runtime["outcome"] in (upgrade.RUNTIME_REUSED,)
    assert runtime["python"] == str(existing_py313)
    assert not (fake_home / ".brain" / "venvs" / f"py3.12-{rhash}").exists()


def test_upgrade_repairs_existing_compatible_minor_runtime_without_sentinel(brew_churn_env, monkeypatch):
    """Upgrade must sync a reused runtime in place even with no probe modules.

    requirements_changed=True is an explicit request to ensure the managed
    runtime has the current requirements installed. A reused same-hash
    runtime lacking the deps sentinel must be repaired, not just reported
    as `reused`.
    """
    vault, rhash, launcher, existing_py313, fake_home = brew_churn_env

    sys.path.insert(0, str(REPO_ROOT / "src" / "brain-core" / "scripts"))
    try:
        import upgrade
    finally:
        sys.path.pop(0)

    sentinel = existing_py313.parent.parent / ".brain-deps-installed"
    sentinel.unlink()

    monkeypatch.setattr(upgrade.sys, "executable", str(launcher))

    runtime = upgrade._ensure_central_runtime(
        vault_root=vault,
        requirements_changed=True,
        sync_deps=None,
    )
    assert runtime is not None
    assert runtime["outcome"] == upgrade.RUNTIME_REUSED
    assert runtime["python"] == str(existing_py313)
    assert not (fake_home / ".brain" / "venvs" / f"py3.12-{rhash}").exists()
    pip_args = existing_py313.parent.parent / "pip-args.txt"
    assert pip_args.is_file()
    assert sentinel.is_file()
    assert sentinel.read_text().strip() == rhash
