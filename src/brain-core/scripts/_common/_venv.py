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
from functools import lru_cache
import hashlib
import json
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


@lru_cache(maxsize=None)
def _python_tag_for_launcher(launcher_realpath: str) -> str:
    out = subprocess.check_output(
        [launcher_realpath, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
        text=True,
    ).strip()
    return f"py{out}"


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
    return _python_tag_for_launcher(os.path.realpath(launcher))


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
        subprocess.run(
            [str(launcher), "-m", "venv", str(venv_dir)],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        created = True

    if install_requirements:
        subprocess.run(
            [str(py), "-m", "pip", "install", "--quiet", "--upgrade", "pip", "-r", str(requirements_path)],
            check=True,
            capture_output=True,
            text=True,
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
# Runtime orchestration
# ---------------------------------------------------------------------------

# Outcomes for resolve_or_provision_central_venv. Named so producer and
# consumer cannot drift.
RUNTIME_REUSED = "reused"
RUNTIME_SYNCED = "synced"
RUNTIME_CREATED = "created"
RUNTIME_PLANNED = "planned"
RUNTIME_ERROR = "error"


def _probe_runtime(python_path: str, *, modules: tuple[str, ...] = ()) -> dict:
    """Probe a Python interpreter for compatibility and required modules.

    Returns a dict with keys ``major``, ``minor``, ``missing``, ``compatible``,
    and ``ok``. Defensive against non-functional binaries (asdf shims, stubs)
    and non-dict probe payloads — anything that isn't an affirmative success
    becomes ``compatible=False`` / ``ok=False``.

    Duplicated narrow shape of `_bootstrap.runtime.probe_python` to keep
    `_venv.py` free of an import cycle through the runtime bootstrap seam.
    """
    code = (
        "import importlib.util, json, sys; "
        f"mods = {modules!r}; "
        "missing = [name for name in mods if importlib.util.find_spec(name) is None]; "
        "payload = {"
        "  'major': sys.version_info[0],"
        "  'minor': sys.version_info[1],"
        "  'missing': missing,"
        "}; "
        "payload['compatible'] = (payload['major'], payload['minor']) >= (3, 12); "
        "payload['ok'] = payload['compatible'] and not missing; "
        "print(json.dumps(payload))"
    )
    try:
        result = subprocess.run(
            [python_path, "-c", code],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"ok": False, "compatible": False, "missing": list(modules)}
    if result.returncode != 0:
        return {"ok": False, "compatible": False, "missing": list(modules)}
    try:
        payload = json.loads(result.stdout.strip() or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "compatible": False, "missing": list(modules)}
    if not isinstance(payload, dict):
        return {"ok": False, "compatible": False, "missing": list(modules)}
    return payload


def _decode_subprocess_output(value: str | bytes | None) -> str:
    """Normalise captured subprocess output to text."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _format_subprocess_error(exc: subprocess.SubprocessError) -> str:
    """Render a subprocess failure with command and captured output."""
    if not isinstance(exc, (subprocess.CalledProcessError, subprocess.TimeoutExpired)):
        return str(exc)

    command = (
        subprocess.list2cmdline([str(part) for part in exc.cmd])
        if isinstance(exc.cmd, (list, tuple))
        else str(exc.cmd)
    )
    stderr = _decode_subprocess_output(getattr(exc, "stderr", None))
    stdout = _decode_subprocess_output(getattr(exc, "stdout", None))
    if isinstance(exc, subprocess.TimeoutExpired):
        details = [f"command timed out: {command} (after {exc.timeout}s)"]
    else:
        details = [f"command failed: {command} (exit {exc.returncode})"]
    if stderr.strip():
        details.append(stderr.strip())
    elif stdout.strip():
        details.append(stdout.strip())
    else:
        details.append(str(exc))
    return "\n".join(details)


def resolve_or_provision_central_venv(
    vault_root: Path,
    *,
    launcher: Path,
    launcher_probe: Optional[dict] = None,
    required_modules: tuple[str, ...] = (),
    install_requirements: Optional[bool] = None,
    dry_run: bool = False,
    timeout: int = _DEFAULT_VENV_TIMEOUT,
) -> dict:
    """Single owner of "find a usable central managed runtime for this vault".

    All lifecycle entry points — the `brain` CLI's dispatch helper, `repair.py`,
    `configure.py`, `upgrade.py` — should delegate to this function instead of
    resolving an exact-tag path themselves. Convergence: the same vault never
    routes to different `~/.brain/venvs/<tag>-<hash>/` directories depending
    on which entry point triggered the lookup.

    Order of operations:

    1. **Find existing.** `find_existing_central_venv` tries the exact-tag
       path first (DD-048 strict tuple), then any other compatible-minor
       venv (>= 3.12) sharing the same requirements hash.
    2. **Probe.** If an existing runtime is found, probe it for compatibility
       and the caller's required modules.
    3. **Sync in place when possible.** If the existing runtime is compatible
       but modules are missing, run `pip install -r requirements.txt` against
       *that* runtime. No new directory created. The `DEPS_SENTINEL_NAME`
       sentinel is refreshed.
    4. **Create exact-tag only when no compatible runtime exists.** Falls
       through to `ensure_central_venv(launcher=launcher)`, which lands the
       new venv at the exact `(python_tag(launcher), requirements_hash)`
       path. Creation stays strict so brand-new installs are deterministic.
    5. **Dry-run.** When `dry_run=True`, no mutation happens. The result
       describes what would be done.

    Returns a result dict with these keys:

      - ``outcome``: ``RUNTIME_REUSED`` | ``RUNTIME_SYNCED`` |
        ``RUNTIME_CREATED`` | ``RUNTIME_PLANNED`` | ``RUNTIME_ERROR``.
      - ``python``: absolute path to the chosen interpreter, or ``None`` on
        error.
      - ``venv_dir``: absolute path to the venv directory, or ``None`` on
        error.
      - ``python_tag``: tag of the chosen runtime (e.g. ``py3.13``) — *not*
        necessarily the same as the launcher's tag when a compatible-minor
        runtime was reused.
      - ``hash``: requirements hash.
      - ``missing_modules``: tuple of modules that were missing on probe
        (always present when ``required_modules`` is non-empty; empty tuple
        when the runtime had everything).
      - ``synced_modules``: tuple (only when ``outcome == RUNTIME_SYNCED``).
      - ``planned_action``: ``"create"`` | ``"sync"`` (only when ``outcome
        == RUNTIME_PLANNED``).
      - ``message``: human-readable note for ``RUNTIME_ERROR`` and
        ``RUNTIME_PLANNED``.
    """
    requirements = vault_requirements_path(vault_root)
    if not requirements.is_file():
        return {
            "outcome": RUNTIME_ERROR,
            "python": None,
            "venv_dir": None,
            "message": f"requirements not found: {requirements}",
        }

    rhash = requirements_hash(requirements)
    should_install_requirements = (
        install_requirements if install_requirements is not None else bool(required_modules)
    )

    # Step 1: find existing compatible runtime.
    existing = find_existing_central_venv(vault_root, launcher=launcher)
    if existing is not None:
        venv_dir = existing.parent.parent
        tag = venv_dir.name.rsplit("-", 1)[0]
        sentinel = venv_dir / DEPS_SENTINEL_NAME

        # Step 2: probe.
        if launcher_probe is not None and os.path.realpath(str(existing)) == os.path.realpath(str(launcher)):
            probe = launcher_probe
        else:
            probe = _probe_runtime(str(existing), modules=required_modules)
        if not probe.get("compatible"):
            # Existing python file exists but is not a working 3.12+ — treat
            # as if no compatible runtime is present and fall through to
            # creation. This handles corrupted venvs.
            existing = None
        else:
            missing = tuple(probe.get("missing", []))
            sentinel_matches = sentinel.is_file() and sentinel.read_text().strip() == rhash
            force_sync_existing = should_install_requirements and not sentinel_matches
            if not missing and not force_sync_existing:
                return {
                    "outcome": RUNTIME_REUSED,
                    "python": str(existing),
                    "venv_dir": str(venv_dir),
                    "python_tag": tag,
                    "hash": rhash,
                    "missing_modules": (),
                }
            # Step 3: sync in place.
            if dry_run:
                if missing:
                    message = f"Would sync missing modules into {venv_dir}: {', '.join(missing)}"
                else:
                    message = (
                        f"Would sync requirements into existing runtime at {venv_dir} "
                        f"because {DEPS_SENTINEL_NAME} is missing or stale"
                    )
                return {
                    "outcome": RUNTIME_PLANNED,
                    "python": str(existing),
                    "venv_dir": str(venv_dir),
                    "python_tag": tag,
                    "hash": rhash,
                    "missing_modules": missing,
                    "planned_action": "sync",
                    "message": message,
                }
            try:
                subprocess.run(
                    [str(existing), "-m", "pip", "install", "--quiet",
                     "--upgrade", "pip", "-r", str(requirements)],
                    check=True, capture_output=True, text=True, timeout=timeout,
                )
            except subprocess.SubprocessError as exc:
                return {
                    "outcome": RUNTIME_ERROR,
                    "python": str(existing),
                    "venv_dir": str(venv_dir),
                    "python_tag": tag,
                    "hash": rhash,
                    "missing_modules": missing,
                    "message": "pip install failed against existing runtime: "
                    + _format_subprocess_error(exc),
                }
            verify = _probe_runtime(str(existing), modules=required_modules)
            still_missing = tuple(verify.get("missing", []))
            if still_missing:
                return {
                    "outcome": RUNTIME_ERROR,
                    "python": str(existing),
                    "venv_dir": str(venv_dir),
                    "python_tag": tag,
                    "hash": rhash,
                    "missing_modules": still_missing,
                    "message": f"sync completed but modules still missing: {', '.join(still_missing)}",
                }
            sentinel.write_text(rhash)
            return {
                "outcome": RUNTIME_SYNCED,
                "python": str(existing),
                "venv_dir": str(venv_dir),
                "python_tag": tag,
                "hash": rhash,
                "missing_modules": (),
                "synced_modules": missing,
            }

    # Step 4/5: no compatible runtime exists — create the exact-tag venv.
    new_tag = python_tag(launcher)
    new_dir = central_venvs_root() / f"{new_tag}-{rhash}"
    new_py = venv_python(new_dir)
    if dry_run:
        return {
            "outcome": RUNTIME_PLANNED,
            "python": str(new_py),
            "venv_dir": str(new_dir),
            "python_tag": new_tag,
            "hash": rhash,
            "missing_modules": tuple(required_modules),
            "planned_action": "create",
            "message": f"Would create a new central managed runtime at {new_dir}",
        }
    try:
        created = ensure_central_venv(
            requirements,
            launcher=launcher,
            install_requirements=should_install_requirements,
            timeout=timeout,
        )
    except subprocess.SubprocessError as exc:
        return {
            "outcome": RUNTIME_ERROR,
            "python": None,
            "venv_dir": None,
            "message": "ensure_central_venv failed: " + _format_subprocess_error(exc),
        }
    except OSError as exc:
        return {
            "outcome": RUNTIME_ERROR,
            "python": None,
            "venv_dir": None,
            "message": f"ensure_central_venv failed: {exc}",
        }
    return {
        "outcome": RUNTIME_CREATED if created["created"] else RUNTIME_REUSED,
        "python": created["python"],
        "venv_dir": created["venv_dir"],
        "python_tag": created["python_tag"],
        "hash": created["hash"],
        "missing_modules": (),
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
