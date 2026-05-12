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
    python _venv.py runnable-python --vault <vault> --launcher <python>
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
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Path rule
# ---------------------------------------------------------------------------

REQUIREMENTS_REL = Path(".brain-core/brain_mcp/requirements.txt")
LEGACY_VAULT_VENV_REL = Path(".venv")
DEPS_SENTINEL_NAME = ".brain-deps-installed"
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


_MIN_SUPPORTED_VERSION = (3, 12)


def _parse_venv_minor(dirname: str, rhash: str) -> Optional[tuple[int, int]]:
    """Parse a central-venv directory name like ``py3.12-<hash>`` into ``(major, minor)``.

    Returns ``None`` when the directory name doesn't match the central-venv
    naming scheme, or when the trailing requirements hash differs from
    ``rhash``. Defensive against junk directories under ``~/.brain/venvs/``.
    """
    suffix = f"-{rhash}"
    if not dirname.endswith(suffix) or not dirname.startswith("py"):
        return None
    version_str = dirname[len("py"):-len(suffix)]
    try:
        parts = version_str.split(".")
        if len(parts) != 2:
            return None
        return (int(parts[0]), int(parts[1]))
    except ValueError:
        return None


def find_existing_central_venv(
    vault_root: Path,
    *,
    launcher: Optional[Path] = None,
) -> Optional[Path]:
    """Find an existing central managed venv for this vault's requirements hash.

    Lookup order:

    1. **Exact match.** If a venv exists at the path keyed by
       ``(python_tag(launcher), requirements_hash)`` — DD-048's strict
       tuple — return it. This is the cheap path and preserves the original
       creation-vs-lookup symmetry whenever the user's selected launcher
       still matches the runtime that was provisioned.
    2. **Same hash, compatible minor.** When the exact path is missing but
       another central venv exists for the same requirements hash under a
       *compatible* Python minor (>= 3.12, per DD-048's floor), return that
       venv's python. Among multiple candidates, the highest compatible
       minor wins — deterministic, no filesystem-order surprises.

    This makes Brain robust to Python minor churn. A typical trigger:
    Homebrew bumps ``python3.12`` to ``python3.13``. The vault still has
    its ``py3.12-<hash>`` central runtime from the original install, but
    the newly-selected launcher is ``python3.13``. Without this lookup,
    ``find_runnable_python`` would miss the existing runtime and fall back
    to the bare launcher (no managed packages → MCP breaks, scripts that
    need ``mcp`` fail). With this lookup, the existing runtime is found.

    Note this *softens* lookup, not creation: ``ensure_central_venv`` still
    creates new venvs keyed strictly by ``(tag, hash)``, so a brand-new
    install always lands at the exact-match path. Cleanup of orphaned
    older-minor venvs (DD-048's "future repair.py orphan-runtimes" item)
    remains a deferred follow-up — this lookup just makes them useful in
    the meantime instead of being silently ignored.

    Returns ``None`` when no exact match and no compatible-minor fallback
    exists.
    """
    # 1. Exact-match lookup — same shape `find_runnable_python` originally had.
    try:
        exact = resolve_vault_venv_python(vault_root, launcher=launcher)
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        exact = None
    if exact is not None and exact.is_file():
        return exact

    # 2. Same-hash compatible-minor fallback.
    rhash = requirements_hash(vault_requirements_path(vault_root))
    root = central_venvs_root()
    if not root.is_dir():
        return None

    candidates: list[tuple[tuple[int, int], Path]] = []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        version = _parse_venv_minor(entry.name, rhash)
        if version is None or version < _MIN_SUPPORTED_VERSION:
            continue
        py = venv_python(entry)
        if py.is_file():
            candidates.append((version, py))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def find_runnable_python(
    vault_root: Path,
    *,
    launcher: Optional[Path] = None,
) -> Optional[Path]:
    """Return the first existing python suitable for running brain-core scripts.

    Fallback chain:

    1. The vault's central managed runtime (`~/.brain/venvs/<tag>-<hash>/bin/python`).
    2. The legacy vault-local venv (`<vault>/.venv/bin/python`) if present.
    3. The launcher itself — any compatible Python 3.12+ on the caller's machine.

    Returns ``None`` only when no candidate exists, which the CLI surfaces as
    a clear error. Scripts that need third-party packages (e.g. `init.py` and
    `mcp`) still re-verify availability themselves; callers that only need
    stdlib + `_common` (most scripts) get a runnable interpreter even before
    the managed runtime is provisioned.

    `launcher` may be omitted; when it is, only the central + legacy candidates
    are considered. Pass a launcher explicitly to enable the third fallback.
    """
    # 1. Existing central managed venv for this vault's requirements. Tries
    #    exact-tag match first; falls back to any compatible-minor venv for
    #    the same requirements hash (see `find_existing_central_venv` for the
    #    Python-minor-churn rationale).
    central = find_existing_central_venv(vault_root, launcher=launcher)
    if central is not None:
        return central
    # 2. Legacy vault-local `<vault>/.venv/bin/python` (pre-DD-048).
    legacy = legacy_vault_venv_python(vault_root)
    if legacy.is_file():
        return legacy
    # 3. Launcher fallback. Accept an absolute path, or a bare name we can
    #    resolve on PATH — `cli/brain` resolves names to absolute paths before
    #    calling, but defending here keeps the helper usable from any caller.
    if launcher is not None:
        launcher_path = Path(launcher)
        if launcher_path.exists():
            return launcher_path
        resolved = shutil.which(str(launcher))
        if resolved:
            return Path(resolved)
    return None


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
    sentinel = venv_dir / DEPS_SENTINEL_NAME

    # Readiness probe: `bin/python` alone is not a guarantee that the venv is
    # healthy. A previous `pip install` may have failed mid-stream, leaving the
    # interpreter present but packages missing. The sentinel file is written
    # only after pip completes successfully and records the requirements hash
    # of the install. If pip is requested and the sentinel is absent or its
    # hash drifts (defensive — the directory is already content-addressed so
    # the hash check is belt-and-braces), re-run pip rather than handing back
    # a half-built runtime.
    if py.is_file():
        if not install_requirements:
            return {
                "venv_dir": str(venv_dir),
                "python": str(py),
                "created": False,
                "python_tag": tag,
                "hash": rhash,
            }
        if sentinel.is_file() and sentinel.read_text().strip() == rhash:
            return {
                "venv_dir": str(venv_dir),
                "python": str(py),
                "created": False,
                "python_tag": tag,
                "hash": rhash,
            }
        created = False
    else:
        venv_dir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([str(launcher), "-m", "venv", str(venv_dir)], check=True, timeout=timeout)
        created = True

    if install_requirements:
        subprocess.run(
            [str(py), "-m", "pip", "install", "--quiet", "--upgrade", "pip", "-r", str(requirements_path)],
            check=True,
            timeout=timeout,
        )
        sentinel.write_text(rhash)
    return {
        "venv_dir": str(venv_dir),
        "python": str(py),
        "created": created,
        "python_tag": tag,
        "hash": rhash,
    }


# ---------------------------------------------------------------------------
# CLI (used by install.sh)
# ---------------------------------------------------------------------------

def _main(argv: Optional[list[str]] = None) -> int:
    """CLI surface for `install.sh`, `cli/brain`, and shell composition.

    Three subcommands:

    - `python --vault X [--launcher Y]` — print the central venv's python
      path (strict). Used in shell pipelines after `ensure`, e.g.
      `"$(_venv.py python --vault .)" .brain-core/scripts/init.py ...`.
      The returned path may not exist if the central venv has not been
      provisioned yet; callers wanting a usable interpreter regardless
      should use `runnable-python` instead.
    - `runnable-python --vault X --launcher Y` — print the first existing
      runnable python in the fallback chain: central venv → legacy
      `<vault>/.venv` → launcher. Used by `cli/brain` so dispatched
      subcommands remain usable in supported no-runtime states (e.g.
      `bash install.sh --skip-mcp <vault>`). Exits non-zero if no
      candidate exists.
    - `ensure --vault X --launcher Y` — create the central venv if missing
      and (re-)run pip when the readiness sentinel is absent. Prints the
      venv directory.
    """
    parser = argparse.ArgumentParser(prog="_venv.py")
    sub = parser.add_subparsers(dest="cmd", required=True)

    py_cmd = sub.add_parser("python", help="Print the central venv's python path.")
    py_cmd.add_argument("--vault", required=True)
    py_cmd.add_argument("--launcher", default=None)

    runnable = sub.add_parser(
        "runnable-python",
        help="Print the first existing python: central venv → legacy → launcher.",
    )
    runnable.add_argument("--vault", required=True)
    runnable.add_argument("--launcher", required=True)

    ensure = sub.add_parser("ensure", help="Create the central venv if missing.")
    ensure.add_argument("--vault", required=True)
    ensure.add_argument("--launcher", required=True)

    args = parser.parse_args(argv)
    vault_root = Path(args.vault)
    launcher = Path(args.launcher) if args.launcher else None

    if args.cmd == "python":
        print(resolve_vault_venv_python(vault_root, launcher=launcher))
        return 0
    if args.cmd == "runnable-python":
        runnable_python = find_runnable_python(vault_root, launcher=launcher)
        if runnable_python is None:
            print(
                f"no runnable python found for vault {vault_root} "
                f"(checked central venv, legacy .venv, launcher {launcher})",
                file=sys.stderr,
            )
            return 1
        print(runnable_python)
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
