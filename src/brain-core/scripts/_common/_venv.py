"""
_venv — Resolve and create the central Brain managed runtime.

Brain installs a single Python virtualenv per `(python_minor, requirements.txt)`
pair under `~/.brain/venvs/py<X.Y>-<sha16>/`, shared across vaults. This
replaces the per-vault `<vault>/.venv/` layout, which on iCloud-hosted vaults
caused 30s+ MCP cold-start failures from materialising thousands of
`site-packages/` files on every fresh process.

A single source of truth for the path rule lives here. `install.sh`,
`init.py`, `upgrade.py`, and `repair.py` all resolve the venv via this
module — there is no other valid encoding of the rule in the codebase.

This module is also runnable as a CLI for `install.sh` (which is bash and
needs to defer path resolution to Python):

    python _venv.py python --vault <vault> [--launcher <python>]
    python _venv.py ensure --vault <vault> --launcher <python>

Bootstrap-layer constraint: this module is invoked by `install.sh` before
the managed runtime exists, and must therefore use stdlib only — no imports
from sibling `_common` modules, no third-party packages. The same discipline
applies to `init.py`, `upgrade.py`, and `repair.py`.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Path rule
# ---------------------------------------------------------------------------

REQUIREMENTS_REL = Path(".brain-core/brain_mcp/requirements.txt")
LEGACY_VAULT_VENV_REL = Path(".venv")
_HASH_LEN = 16
_LAUNCHER_OVERRIDE_ENV = "BRAIN_VENV_LAUNCHER"


def central_venvs_root() -> Path:
    """Where Brain stores all managed venvs on this machine."""
    return Path.home() / ".brain" / "venvs"


def vault_requirements_path(vault_root: Path) -> Path:
    return Path(vault_root) / REQUIREMENTS_REL


def legacy_vault_venv_dir(vault_root: Path) -> Path:
    """Pre-D2 vault-local venv path. Used only for migration detection."""
    return Path(vault_root) / LEGACY_VAULT_VENV_REL


def legacy_vault_venv_python(vault_root: Path) -> Path:
    """Pre-D2 vault-local python path. Used only for migration fallback."""
    return venv_python(legacy_vault_venv_dir(vault_root))


def requirements_hash(requirements_path: Path) -> str:
    """Stable, content-addressed identifier for a requirements file."""
    return hashlib.sha256(Path(requirements_path).read_bytes()).hexdigest()[:_HASH_LEN]


def python_tag(launcher: Optional[Path] = None) -> str:
    """Return the `pyX.Y` tag for the given launcher, or the running interpreter.

    Different Python minor versions cannot share a venv, so the tag is
    part of the directory name. Asking the launcher rather than parsing the
    name protects against nonstandard launcher binaries. `BRAIN_VENV_LAUNCHER`
    overrides the launcher (test seam — see module docstring).
    """
    override = os.environ.get(_LAUNCHER_OVERRIDE_ENV)
    if override:
        launcher = Path(override)
    if launcher is None or os.path.realpath(launcher) == os.path.realpath(sys.executable):
        info = sys.version_info
        return f"py{info.major}.{info.minor}"
    out = subprocess.check_output(
        [str(launcher), "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
        text=True,
    ).strip()
    return f"py{out}"


def venv_dir_for(requirements_path: Path, *, launcher: Optional[Path] = None) -> Path:
    """Resolve the central venv directory for a given requirements file."""
    return central_venvs_root() / f"{python_tag(launcher)}-{requirements_hash(requirements_path)}"


def venv_python(venv_dir: Path) -> Path:
    return Path(venv_dir) / "bin" / "python"


def resolve_vault_venv_dir(vault_root: Path, *, launcher: Optional[Path] = None) -> Path:
    """Convenience: hash a vault's requirements and return its central venv dir."""
    return venv_dir_for(vault_requirements_path(vault_root), launcher=launcher)


def resolve_vault_venv_python(vault_root: Path, *, launcher: Optional[Path] = None) -> Path:
    return venv_python(resolve_vault_venv_dir(vault_root, launcher=launcher))


# ---------------------------------------------------------------------------
# Create / ensure
# ---------------------------------------------------------------------------

_DEFAULT_VENV_TIMEOUT = 600


def ensure_central_venv(
    requirements_path: Path,
    *,
    launcher: Path,
    install_requirements: bool = True,
    timeout: int = _DEFAULT_VENV_TIMEOUT,
) -> dict:
    """Create the central venv for these requirements if missing.

    Idempotent: if `<dir>/bin/python` already exists, returns without re-running
    pip. Always uses `launcher` (an external Python 3.12+ interpreter) to
    create the venv, so the resulting `pyX.Y` tag always matches `launcher`.

    `install_requirements=False` skips the `pip install -r` step — used by
    repair scopes that need a usable interpreter but explicitly don't need
    third-party packages (e.g. `router`, `index`). The directory is still
    keyed by the requirements hash so siblings of the same vault converge
    on the same path even when bootstrap paths skip the install step.

    `timeout` bounds each `subprocess.run` call (venv create, pip install)
    so a stuck pip resolver cannot hang the install/upgrade flow forever.

    Returns `{"venv_dir", "python", "created", "python_tag", "hash"}`.
    Raises subprocess errors on failure.
    """
    requirements_path = Path(requirements_path)
    if not requirements_path.is_file():
        raise FileNotFoundError(f"requirements not found: {requirements_path}")

    override = os.environ.get(_LAUNCHER_OVERRIDE_ENV)
    if override:
        launcher = Path(override)

    tag = python_tag(launcher)
    rhash = requirements_hash(requirements_path)
    venv_dir = central_venvs_root() / f"{tag}-{rhash}"
    py = venv_python(venv_dir)

    if py.is_file():
        return {
            "venv_dir": str(venv_dir),
            "python": str(py),
            "created": False,
            "python_tag": tag,
            "hash": rhash,
        }

    venv_dir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(launcher), "-m", "venv", str(venv_dir)], check=True, timeout=timeout)
    if install_requirements:
        subprocess.run(
            [str(py), "-m", "pip", "install", "--quiet", "--upgrade", "pip", "-r", str(requirements_path)],
            check=True,
            timeout=timeout,
        )
    return {
        "venv_dir": str(venv_dir),
        "python": str(py),
        "created": True,
        "python_tag": tag,
        "hash": rhash,
    }


# ---------------------------------------------------------------------------
# CLI (used by install.sh)
# ---------------------------------------------------------------------------

def _main(argv: Optional[list[str]] = None) -> int:
    """CLI surface for `install.sh` and shell composition.

    Two subcommands:

    - `python --vault X [--launcher Y]` — print the central venv's python
      path. Used in shell pipelines that compose with `init.py`, e.g.
      `"$(_venv.py python --vault .)" .brain-core/scripts/init.py ...`.
    - `ensure --vault X --launcher Y` — create the central venv if missing
      and print its directory.

    No `path` subcommand: callers want either an importable Python API
    (use the helper functions directly) or the shell-composable python
    path; the bare directory has no production caller.
    """
    parser = argparse.ArgumentParser(prog="_venv.py")
    sub = parser.add_subparsers(dest="cmd", required=True)

    py_cmd = sub.add_parser("python", help="Print the central venv's python path.")
    py_cmd.add_argument("--vault", required=True)
    py_cmd.add_argument("--launcher", default=None)

    ensure = sub.add_parser("ensure", help="Create the central venv if missing.")
    ensure.add_argument("--vault", required=True)
    ensure.add_argument("--launcher", required=True)

    args = parser.parse_args(argv)
    vault_root = Path(args.vault)
    launcher = Path(args.launcher) if args.launcher else None

    if args.cmd == "python":
        print(resolve_vault_venv_python(vault_root, launcher=launcher))
        return 0
    if args.cmd == "ensure":
        result = ensure_central_venv(
            vault_requirements_path(vault_root),
            launcher=launcher,
        )
        print(result["venv_dir"])
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(_main())
