"""Provision the machine-level Brain resolution runtime."""

from __future__ import annotations

import filecmp
from pathlib import Path
import os
import shutil


RESOLUTION_RUNTIME_VERSION = "0.1.0"
RESOLUTION_RUNTIME_ENV = "BRAIN_RESOLUTION_RUNTIME_DIR"
RESOLUTION_RUNTIME_DIRNAME = "resolution-runtime"
RESOLUTION_RUNTIME_ENTRY = "resolve_brain.py"


_DEPLOY_FILES: tuple[tuple[str, str], ...] = (
    ("_machine/resolve_brain.py", RESOLUTION_RUNTIME_ENTRY),
    ("_bootstrap/workspace_binding.py", "_bootstrap/workspace_binding.py"),
    ("_bootstrap/__init__.py", "_bootstrap/__init__.py"),
    ("vault_registry.py", "vault_registry.py"),
    ("_common/_vault.py", "_common/_vault.py"),
    ("_common/_filesystem.py", "_common/_filesystem.py"),
    ("_common/_paths.py", "_common/_paths.py"),
    ("_common/_templates.py", "_common/_templates.py"),
    ("_common/_slugs.py", "_common/_slugs.py"),
    ("_common/_file_lock.py", "_common/_file_lock.py"),
    ("_common/_yaml/__init__.py", "_common/_yaml/__init__.py"),
    ("_common/_yaml/brain.py", "_common/_yaml/brain.py"),
    ("_common/_yaml/engine.py", "_common/_yaml/engine.py"),
)


# Do not deploy _common/__init__.py. The runtime intentionally uses namespace
# package imports so any broad `from _common import ...` regression fails tests
# instead of smuggling the managed-plane aggregator into the machine resolver.

def resolution_runtime_root() -> Path:
    override = os.environ.get(RESOLUTION_RUNTIME_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".brain" / RESOLUTION_RUNTIME_DIRNAME


def resolution_runtime_entry(root: str | Path | None = None) -> Path:
    base = Path(root) if root is not None else resolution_runtime_root()
    return base / RESOLUTION_RUNTIME_ENTRY


def _copy_if_changed(source: Path, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and filecmp.cmp(source, dest, shallow=False):
        return False
    shutil.copy2(source, dest)
    return True


def _write_text_if_changed(path: Path, text: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return False
    path.write_text(text, encoding="utf-8")
    return True


def ensure_resolution_runtime(
    scripts_root: str | Path,
    *,
    runtime_root: str | Path | None = None,
) -> dict:
    """Deploy the stdlib-only resolver runtime from an authored scripts tree."""
    scripts = Path(scripts_root).resolve()
    root = Path(runtime_root).resolve() if runtime_root is not None else resolution_runtime_root()

    missing: list[str] = []
    for rel_source, rel_dest in _DEPLOY_FILES:
        source = scripts / rel_source
        if not source.is_file():
            missing.append(rel_source)

    if missing:
        return {
            "status": "error",
            "path": str(root),
            "version": RESOLUTION_RUNTIME_VERSION,
            "message": "missing authored resolver runtime file(s): " + ", ".join(missing),
            "changed": False,
            "changed_files": [],
        }

    changed_files: list[str] = []
    for rel_source, rel_dest in _DEPLOY_FILES:
        source = scripts / rel_source
        dest = root / rel_dest
        if _copy_if_changed(source, dest):
            changed_files.append(rel_dest)

    if _write_text_if_changed(root / "VERSION", RESOLUTION_RUNTIME_VERSION + "\n"):
        changed_files.append("VERSION")

    return {
        "status": "changed" if changed_files else "noop",
        "path": str(root),
        "entry": str(resolution_runtime_entry(root)),
        "version": RESOLUTION_RUNTIME_VERSION,
        "changed": bool(changed_files),
        "changed_files": changed_files,
    }


def deployed_version(root: str | Path | None = None) -> str | None:
    version_path = (Path(root) if root is not None else resolution_runtime_root()) / "VERSION"
    try:
        value = version_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None
