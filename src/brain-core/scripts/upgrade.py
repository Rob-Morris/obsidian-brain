#!/usr/bin/env python3
"""
upgrade.py — In-place brain-core upgrade.

Copies source brain-core files into a vault's .brain-core/ directory,
reports what changed, and optionally runs as a dry-run.

Self-contained — no imports from _common (this script replaces _common
during execution). Duplicates only find_vault_root().

Usage:
  python3 upgrade.py --source /path/to/src/brain-core [--vault /path] [--dry-run] [--force] [--json]

The script does copy + diff only. Post-upgrade steps (recompile router,
rebuild index) are the caller's responsibility. The CLI prints a reminder;
the MCP server wrapper handles them automatically.
"""

import argparse
import filecmp
import importlib
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Optional


def _safe_write(path, content):
    """Atomic write: tmp → fsync → os.replace."""
    target = os.path.realpath(str(path))
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    tmp = f"{target}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BRAIN_CORE_MARKER = os.path.join(".brain-core", "VERSION")
BRAIN_CORE_DIR = ".brain-core"
IGNORE_DIRS = {"__pycache__"}
IGNORE_FILES = {".DS_Store"}


# ---------------------------------------------------------------------------
# Vault root discovery (self-contained, no _common import)
# ---------------------------------------------------------------------------

def _is_vault_root(path: Path) -> bool:
    return (path / BRAIN_CORE_MARKER).is_file()


def _find_vault_root_from_script() -> Optional[Path]:
    """Walk up from this script's location to find a vault root."""
    current = Path(__file__).resolve().parent
    for _ in range(10):
        if _is_vault_root(current):
            return current
        current = current.parent
    return None


def find_vault_root(vault_arg: Optional[str] = None) -> Path:
    """Resolve vault root from argument, env var, or script location."""
    if vault_arg:
        p = Path(vault_arg).resolve()
        if _is_vault_root(p):
            return p
        raise ValueError(f"Not a vault root (no {BRAIN_CORE_MARKER}): {p}")

    env_root = os.environ.get("BRAIN_VAULT_ROOT")
    if env_root:
        p = Path(env_root).resolve()
        if _is_vault_root(p):
            return p
        raise ValueError(f"BRAIN_VAULT_ROOT is not a vault root (no {BRAIN_CORE_MARKER}): {p}")

    root = _find_vault_root_from_script()
    if root:
        return root

    raise ValueError(
        "Could not find vault root.\n"
        "Run from inside a vault, use --vault, or set BRAIN_VAULT_ROOT."
    )


# ---------------------------------------------------------------------------
# Version reading
# ---------------------------------------------------------------------------

def _read_version(path: str) -> Optional[str]:
    """Read VERSION file, return stripped content or None."""
    version_file = os.path.join(path, "VERSION")
    try:
        with open(version_file, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def _parse_version(v: str) -> tuple:
    """Parse a version string into a comparable tuple."""
    parts = []
    for p in v.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(p)
    return tuple(parts)


# ---------------------------------------------------------------------------
# File tree diffing
# ---------------------------------------------------------------------------

def _walk_tree(root: str) -> set[str]:
    """Walk a directory tree and return relative paths of all files, excluding ignored."""
    paths = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        for f in filenames:
            if f in IGNORE_FILES or f.endswith(".pyc"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, f), root)
            paths.add(rel)
    return paths


def _diff_trees(source: str, target: str) -> dict:
    """Compare source and target trees, return categorised file lists."""
    source_files = _walk_tree(source)
    target_files = _walk_tree(target)

    added = sorted(source_files - target_files)
    removed = sorted(target_files - source_files)
    common = source_files & target_files

    modified = []
    unchanged = 0
    for rel in sorted(common):
        src_path = os.path.join(source, rel)
        tgt_path = os.path.join(target, rel)
        if not filecmp.cmp(src_path, tgt_path, shallow=False):
            modified.append(rel)
        else:
            unchanged += 1

    return {
        "files_added": added,
        "files_modified": modified,
        "files_removed": removed,
        "files_unchanged": unchanged,
    }


# ---------------------------------------------------------------------------
# Migration runner
# ---------------------------------------------------------------------------

_MIGRATION_RE_PATTERN = r"^migrate_to_(\d+(?:_\d+)*)\.py$"


def _discover_migrations(migrations_dir: str) -> list[tuple[tuple, str]]:
    """Find all migration scripts and return sorted (version_tuple, path) pairs."""
    pattern = re.compile(_MIGRATION_RE_PATTERN)
    migrations = []
    if not os.path.isdir(migrations_dir):
        return migrations
    for name in os.listdir(migrations_dir):
        m = pattern.match(name)
        if m:
            version_str = m.group(1).replace("_", ".")
            version_tuple = _parse_version(version_str)
            migrations.append((version_tuple, os.path.join(migrations_dir, name)))
    return sorted(migrations)


def _run_migrations(vault_root: str, old_version: Optional[str], new_version: str) -> list[dict]:
    """Run pending migrations between old_version and new_version.

    Discovers migration scripts in .brain-core/scripts/migrations/,
    runs those whose version is > old_version and <= new_version.
    Returns list of migration results.
    """
    migrations_dir = os.path.join(vault_root, BRAIN_CORE_DIR, "scripts", "migrations")
    all_migrations = _discover_migrations(migrations_dir)
    if not all_migrations:
        return []

    old_tuple = _parse_version(old_version) if old_version else (0,)
    new_tuple = _parse_version(new_version)

    results = []
    for version_tuple, script_path in all_migrations:
        if version_tuple <= old_tuple or version_tuple > new_tuple:
            continue
        version_str = ".".join(str(p) for p in version_tuple)
        try:
            spec = importlib.util.spec_from_file_location(
                f"migration_{version_str}", script_path,
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            result = mod.migrate(vault_root)
            result["version"] = version_str
            results.append(result)
        except Exception as e:
            results.append({
                "version": version_str,
                "status": "error",
                "message": str(e),
            })
    return results


_MIGRATED_VERSION_FILE = os.path.join(".brain", "local", ".migrated-version")


def run_pending_migrations(vault_root: str) -> list[dict]:
    """Run any migrations needed for the current vault version.

    Reads the installed version from .brain-core/VERSION and runs
    all migrations up to that version. For use by MCP server startup
    and other non-upgrade entry points.

    Writes a marker file (.brain/local/.migrated-version) after success
    so subsequent startups skip discovery entirely when the version
    hasn't changed.
    """
    target = os.path.join(vault_root, BRAIN_CORE_DIR)
    current_version = _read_version(target)
    if not current_version:
        return []

    # Fast path: skip if already migrated to this version
    marker = os.path.join(vault_root, _MIGRATED_VERSION_FILE)
    try:
        with open(marker, "r", encoding="utf-8") as f:
            if f.read().strip() == current_version:
                return []
    except OSError:
        pass

    results = _run_migrations(vault_root, "0.0.0", current_version)

    # Record that all migrations up to current_version have been applied
    _safe_write(marker, current_version + "\n")

    return results


# ---------------------------------------------------------------------------
# Core upgrade function
# ---------------------------------------------------------------------------

def upgrade(vault_root: str, source: str, *, force: bool = False, dry_run: bool = False) -> dict:
    """Upgrade .brain-core/ in a vault from a source directory.

    Args:
        vault_root: Path to the vault root.
        source: Path to the source brain-core directory.
        force: Allow same-version or downgrade upgrades.
        dry_run: Report changes without modifying files.

    Returns:
        Dict with status, version info, and file change lists.
    """
    # Validate source
    if not os.path.isdir(source):
        return {"status": "error", "message": f"Source directory not found: {source}"}

    new_version = _read_version(source)
    if new_version is None:
        return {"status": "error", "message": f"No VERSION file in source: {source}"}

    target = os.path.join(vault_root, BRAIN_CORE_DIR)
    old_version = _read_version(target)

    # Version checks
    if old_version and new_version and not force:
        if old_version == new_version:
            return {
                "status": "skipped",
                "old_version": old_version,
                "new_version": new_version,
                "message": f"Already at {new_version}. Use --force to re-apply.",
            }
        if _parse_version(new_version) < _parse_version(old_version):
            return {
                "status": "skipped",
                "old_version": old_version,
                "new_version": new_version,
                "message": f"Downgrade {old_version} → {new_version}. Use --force to proceed.",
            }

    # Diff
    diff = _diff_trees(source, target)

    result = {
        "status": "ok",
        "old_version": old_version,
        "new_version": new_version,
        "files_added": diff["files_added"],
        "files_modified": diff["files_modified"],
        "files_removed": diff["files_removed"],
        "files_unchanged": diff["files_unchanged"],
        "dry_run": dry_run,
    }

    if dry_run:
        result["message"] = f"Dry run: {old_version or '(none)'} → {new_version}"
        return result

    # Copy only added and modified files (avoids touching unchanged files,
    # which prevents sync-service conflict copies on iCloud/Dropbox/etc.)
    for rel in diff["files_added"] + diff["files_modified"]:
        src = os.path.join(source, rel)
        dst = os.path.join(target, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)

    # Remove obsolete files and clean up empty parent directories
    for rel in diff["files_removed"]:
        abs_path = os.path.join(target, rel)
        try:
            os.remove(abs_path)
        except OSError:
            continue
        dir_path = os.path.dirname(abs_path)
        try:
            while dir_path != target:
                os.rmdir(dir_path)  # only removes if empty
                dir_path = os.path.dirname(dir_path)
        except OSError:
            pass

    # Run pending migrations
    migrations = _run_migrations(vault_root, old_version, new_version)
    if migrations:
        result["migrations"] = migrations

    result["message"] = f"Upgraded {old_version or '(none)'} → {new_version}"
    return result


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def info(msg: str) -> None:
    print(f"  {msg}", file=sys.stderr)


def fatal(msg: str) -> None:
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upgrade brain-core in a vault.",
        epilog=(
            "Examples:\n"
            "  python3 upgrade.py --source /path/to/src/brain-core\n"
            "      Upgrade from an explicit source\n\n"
            "  python3 upgrade.py --source src/brain-core --vault /path/to/vault --dry-run\n"
            "      Preview changes without applying\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--source", required=True,
        help="Path to source brain-core directory (e.g. src/brain-core)",
    )
    parser.add_argument(
        "--vault",
        help="Path to vault root (default: auto-detect)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would change without modifying files",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Allow same-version or downgrade upgrades",
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output JSON instead of human-readable text",
    )
    args = parser.parse_args()

    try:
        vault_root = find_vault_root(args.vault)
    except ValueError as e:
        fatal(str(e))

    source = str(Path(args.source).resolve())
    result = upgrade(str(vault_root), source, force=args.force, dry_run=args.dry_run)

    if args.json_output:
        print(json.dumps(result, indent=2))
        return

    # Human-readable output
    if result["status"] == "error":
        fatal(result["message"])

    if result["status"] == "skipped":
        info(result["message"])
        sys.exit(0)

    info(result["message"])
    if result["files_added"]:
        info(f"  Added:     {len(result['files_added'])} files")
        for f in result["files_added"]:
            info(f"    + {f}")
    if result["files_modified"]:
        info(f"  Modified:  {len(result['files_modified'])} files")
        for f in result["files_modified"]:
            info(f"    ~ {f}")
    if result["files_removed"]:
        info(f"  Removed:   {len(result['files_removed'])} files")
        for f in result["files_removed"]:
            info(f"    - {f}")
    info(f"  Unchanged: {result['files_unchanged']} files")

    if not args.dry_run:
        print(file=sys.stderr)

        # Check if requirements changed — prompt for dependency sync
        req_file = "mcp/requirements.txt"
        if req_file in result.get("files_added", []) + result.get("files_modified", []):
            info("Dependencies changed — sync your vault's Python environment:")
            info("  .venv/bin/pip install -r .brain-core/mcp/requirements.txt")
            print(file=sys.stderr)

        info("Post-upgrade: recompile the router and rebuild the index.")
        info("  python3 .brain-core/scripts/compile_router.py")
        info("  python3 .brain-core/scripts/build_index.py")
        info("  (or use brain_action('compile') + brain_action('build_index') via MCP)")


if __name__ == "__main__":
    main()
