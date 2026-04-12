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
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
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
IGNORE_FILES = {".DS_Store", "upgrade.py"}


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


def _run_migrations(
    vault_root: str,
    old_version: Optional[str],
    new_version: str,
    *,
    force: bool = False,
) -> list[dict]:
    """Run pending migrations between old_version and new_version.

    Discovers migration scripts in .brain-core/scripts/migrations/,
    runs those whose version is > old_version and <= new_version, unless
    force=True, in which case every migration up to new_version is re-run.

    Per-migration execution is recorded in `.brain/local/migrations.json`.
    Historical migrations up to old_version are backfilled into that ledger the
    first time a vault with pre-ledger history is upgraded.
    """
    migrations_dir = os.path.join(vault_root, BRAIN_CORE_DIR, "scripts", "migrations")
    all_migrations = _discover_migrations(migrations_dir)
    if not all_migrations:
        return []

    new_tuple = _parse_version(new_version)
    ledger = _seed_migration_ledger(
        vault_root,
        old_version,
        source=f"installed-version:{old_version}",
    )

    if force:
        pending = [
            (vt, sp) for vt, sp in all_migrations
            if vt <= new_tuple
        ]
    else:
        old_tuple = _parse_version(old_version) if old_version else (0,)
        pending = []
        for version_tuple, script_path in all_migrations:
            if version_tuple <= old_tuple or version_tuple > new_tuple:
                continue
            if _migration_version_str(version_tuple) in ledger["migrations"]:
                continue
            pending.append((version_tuple, script_path))
    if not pending:
        return []

    # Reload modules that migration scripts import.  After an upgrade the
    # new files are on disk but sys.modules still holds the old versions,
    # so ``from _common import safe_write`` (etc.) would fail or pick up
    # stale code.  Reload once before the first migration, not per-script.
    _reload_migration_deps()

    results = []
    for version_tuple, script_path in pending:
        version_str = _migration_version_str(version_tuple)
        try:
            spec = importlib.util.spec_from_file_location(
                f"migration_{version_str}", script_path,
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            result = mod.migrate(vault_root)
            result["version"] = version_str
            results.append(result)
            ledger = _record_migration_result(
                vault_root, ledger, version_str, script_path, result,
            )
        except Exception as e:
            results.append({
                "version": version_str,
                "status": "error",
                "message": str(e),
            })
    return results


# Modules that migration scripts commonly import.  Kept as a small
# allow-list so we don't reload the entire process.
_MIGRATION_DEPS = ["_common", "rename"]


def _reload_migration_deps():
    """Reload cached modules that migration scripts depend on.

    Called before executing migrations so that ``from _common import …``
    picks up the freshly-copied files rather than the stale module cache.
    Only reloads modules already in sys.modules — if a module hasn't been
    imported yet the fresh file will be loaded naturally.
    """
    for name in _MIGRATION_DEPS:
        mod = sys.modules.get(name)
        if mod is not None:
            importlib.reload(mod)


_MIGRATED_VERSION_FILE = os.path.join(".brain", "local", ".migrated-version")
_MIGRATION_LEDGER_FILE = os.path.join(".brain", "local", "migrations.json")
_LAST_UPGRADE_FILE = os.path.join(".brain", "local", "last-upgrade.json")


def _migration_version_str(version_tuple: tuple) -> str:
    """Render a discovered migration version tuple as a dotted string."""
    return ".".join(str(p) for p in version_tuple)


def _empty_migration_ledger() -> dict:
    """Return the default on-disk migration ledger structure."""
    return {
        "schema_version": 1,
        "migrations": {},
    }


def _load_migration_ledger(vault_root: str) -> dict:
    """Load the local migration ledger, tolerating missing/corrupt files."""
    ledger_path = os.path.join(vault_root, _MIGRATION_LEDGER_FILE)
    try:
        with open(ledger_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return _empty_migration_ledger()

    if not isinstance(data, dict):
        return _empty_migration_ledger()

    migrations = data.get("migrations")
    if not isinstance(migrations, dict):
        migrations = {}

    return {
        "schema_version": 1,
        "migrations": {
            str(version): entry
            for version, entry in migrations.items()
            if isinstance(entry, dict)
        },
    }


def _write_migration_ledger(vault_root: str, ledger: dict) -> None:
    """Persist the local migration ledger."""
    ledger_path = os.path.join(vault_root, _MIGRATION_LEDGER_FILE)
    _safe_write(ledger_path, json.dumps(ledger, indent=2) + "\n")


def _seed_migration_ledger(
    vault_root: str,
    upto_version: Optional[str],
    *,
    source: str,
) -> dict:
    """Backfill ledger entries for migrations already implied by old state.

    This keeps older vaults from re-running historical migrations the first time
    they see the new per-migration ledger.
    """
    ledger = _load_migration_ledger(vault_root)
    if not upto_version:
        return ledger

    migrations_dir = os.path.join(vault_root, BRAIN_CORE_DIR, "scripts", "migrations")
    all_migrations = _discover_migrations(migrations_dir)
    if not all_migrations:
        return ledger

    upto_tuple = _parse_version(upto_version)
    changed = False
    recorded_at = datetime.now(timezone.utc).isoformat()
    for version_tuple, script_path in all_migrations:
        if version_tuple > upto_tuple:
            continue
        version_str = _migration_version_str(version_tuple)
        if version_str in ledger["migrations"]:
            continue
        ledger["migrations"][version_str] = {
            "status": "backfilled",
            "recorded_at": recorded_at,
            "recorded_from": source,
            "script": os.path.basename(script_path),
        }
        changed = True

    if changed:
        _write_migration_ledger(vault_root, ledger)

    return ledger


def _record_migration_result(
    vault_root: str,
    ledger: dict,
    version_str: str,
    script_path: str,
    result: dict,
) -> dict:
    """Record a successful or skipped migration in the local ledger."""
    status = result.get("status")
    if status == "error":
        return ledger

    ledger["migrations"][version_str] = {
        "status": status or "ok",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "recorded_from": "runner",
        "script": os.path.basename(script_path),
    }
    _write_migration_ledger(vault_root, ledger)
    return ledger


def _all_migrations_recorded(vault_root: str, target_version: str) -> bool:
    """Return True when every migration up to target_version is in the ledger."""
    migrations_dir = os.path.join(vault_root, BRAIN_CORE_DIR, "scripts", "migrations")
    all_migrations = _discover_migrations(migrations_dir)
    if not all_migrations:
        return True

    target_tuple = _parse_version(target_version)
    recorded = set(_load_migration_ledger(vault_root)["migrations"])
    required = {
        _migration_version_str(version_tuple)
        for version_tuple, _ in all_migrations
        if version_tuple <= target_tuple
    }
    return required <= recorded


def _write_migrated_version_marker(vault_root: str, version: str) -> None:
    """Write the coarse-grained migrated-version fast-path marker."""
    marker = os.path.join(vault_root, _MIGRATED_VERSION_FILE)
    _safe_write(marker, version + "\n")


def run_pending_migrations(vault_root: str, *, force: bool = False) -> list[dict]:
    """Run any migrations needed for the current vault version.

    Reads the installed version from .brain-core/VERSION and runs
    all migrations up to that version. For use by MCP server startup
    and other non-upgrade entry points.

    Stores per-migration execution in `.brain/local/migrations.json` and also
    writes a coarse-grained `.brain/local/.migrated-version` marker after all
    migrations up to the current version are recorded. `force=True` ignores
    both markers and re-runs all migrations up to the current version.
    """
    target = os.path.join(vault_root, BRAIN_CORE_DIR)
    current_version = _read_version(target)
    if not current_version:
        return []

    # Fast path: skip if already migrated to this version
    marker = os.path.join(vault_root, _MIGRATED_VERSION_FILE)
    if not force:
        try:
            with open(marker, "r", encoding="utf-8") as f:
                if f.read().strip() == current_version:
                    _seed_migration_ledger(
                        vault_root,
                        current_version,
                        source=f"version-marker:{current_version}",
                    )
                    return []
        except OSError:
            pass

    results = _run_migrations(vault_root, "0.0.0", current_version, force=force)

    if _all_migrations_recorded(vault_root, current_version):
        _write_migrated_version_marker(vault_root, current_version)

    return results


# ---------------------------------------------------------------------------
# Backup / restore — keeps .brain-core/ recoverable during upgrades
# ---------------------------------------------------------------------------

_COMPILE_TIMEOUT = 60  # seconds (longer than server's 30s startup timeout
                       # because upgrade runs interactively and compile is
                       # the validation gate — worth waiting longer)


def _copytree_ignore(_dir, entries):
    """Ignore filter for shutil.copytree — matches IGNORE_DIRS/IGNORE_FILES."""
    return [e for e in entries if e in IGNORE_DIRS or e in IGNORE_FILES or e.endswith(".pyc")]


def _backup_brain_core(target: str) -> str:
    """Copy .brain-core/ to a temp directory outside the vault.

    Returns the backup directory path. The caller is responsible for
    cleanup (success) or restore (failure).
    """
    backup_dir = tempfile.mkdtemp(prefix="brain-core-backup-")
    shutil.copytree(target, os.path.join(backup_dir, BRAIN_CORE_DIR),
                    ignore=_copytree_ignore)
    return backup_dir


def _restore_brain_core(backup_dir: str, target: str) -> None:
    """Restore .brain-core/ from a backup, file-by-file to be iCloud-safe."""
    backup_src = os.path.join(backup_dir, BRAIN_CORE_DIR)

    # Remove files that weren't in the backup (i.e. newly added by upgrade)
    backup_files = _walk_tree(backup_src)
    current_files = _walk_tree(target)
    for rel in current_files - backup_files:
        abs_path = os.path.join(target, rel)
        try:
            os.remove(abs_path)
        except OSError:
            pass

    # Restore original files
    for rel in backup_files:
        src = os.path.join(backup_src, rel)
        dst = os.path.join(target, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)


def _write_upgrade_log(vault_root: str, result: dict) -> None:
    """Write upgrade result to .brain/local/last-upgrade.json for diagnostics."""
    log_path = os.path.join(vault_root, _LAST_UPGRADE_FILE)
    entry = {**result, "timestamp": datetime.now(timezone.utc).isoformat()}
    try:
        _safe_write(log_path, json.dumps(entry, indent=2) + "\n")
    except OSError:
        pass  # best-effort — don't let log failure mask the real error


def _validate_compile(vault_root: str) -> Optional[str]:
    """Run compile_router.py against the vault as a validation step.

    Returns None on success, or an error message on failure.
    """
    script = os.path.join(vault_root, BRAIN_CORE_DIR, "scripts", "compile_router.py")
    if not os.path.isfile(script):
        return f"compile_router.py not found at {script}"

    try:
        proc = subprocess.run(
            [sys.executable, script],
            cwd=vault_root,
            capture_output=True,
            text=True,
            timeout=_COMPILE_TIMEOUT,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.strip()
            return f"compile failed (exit {proc.returncode}): {stderr}"
    except subprocess.TimeoutExpired:
        return f"compile timed out after {_COMPILE_TIMEOUT}s"
    except OSError as e:
        return f"compile could not run: {e}"

    return None


# ---------------------------------------------------------------------------
# Post-upgrade definition sync
# ---------------------------------------------------------------------------

def _post_upgrade_sync(vault_root: str, *, sync: Optional[bool]) -> Optional[dict]:
    """Run artefact definition sync after upgrade.

    Sync is optional — failures are captured, never raised. The upgrade
    is the critical operation; sync is a convenience step.

    Safe updates (upstream changed, no local changes) always apply.
    Conflicts (both sides changed) are returned as warnings.

    Returns None if skipped entirely, or a dict with one of:
      'sync_result'  — sync ran and produced a result
      'sync_error'   — sync was attempted but failed
    """
    # Read preference
    prefs_path = os.path.join(vault_root, ".brain", "preferences.json")
    try:
        with open(prefs_path, "r", encoding="utf-8") as f:
            prefs = json.load(f)
    except (OSError, json.JSONDecodeError):
        prefs = {}
    preference = prefs.get("artefact_sync", "ask")

    # Explicit flag overrides preference
    if sync is False:
        return None
    if preference == "skip" and sync is not True:
        return None

    try:
        # Import sync_definitions from the freshly-upgraded scripts
        scripts_dir = os.path.join(vault_root, ".brain-core", "scripts")
        spec = importlib.util.spec_from_file_location(
            "sync_definitions",
            os.path.join(scripts_dir, "sync_definitions.py"),
        )
        sync_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sync_mod)

        if sync is False or preference == "skip":
            return None

        # Run sync — safe updates (no local changes) always apply;
        # conflicts are returned as warnings for the caller to present.
        # force=True when explicitly requested via --sync flag.
        sync_result = sync_mod.sync_definitions(
            vault_root, force=(sync is True),
        )
        if sync_result["updated"] or sync_result["warnings"]:
            return {"sync_result": sync_result}
        return None
    except Exception as e:
        return {"sync_error": f"Definition sync failed: {e}"}


# ---------------------------------------------------------------------------
# Core upgrade function
# ---------------------------------------------------------------------------

def upgrade(vault_root: str, source: str, *, force: bool = False, dry_run: bool = False, sync: Optional[bool] = None) -> dict:
    """Upgrade .brain-core/ in a vault from a source directory.

    Flow: backup → copy → compile (validate) → migrate → sync definitions.
    If copy or compile fails, .brain-core/ is restored from the backup.
    Migrations only run after compile succeeds.

    Args:
        vault_root: Path to the vault root.
        source: Path to the source brain-core directory.
        force: Allow same-version or downgrade upgrades.
        dry_run: Report changes without modifying files.
        sync: Override artefact_sync preference (True=force, False=skip,
              None=follow preference).

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

    # --- Backup .brain-core/ before modifying anything ---
    backup_dir = _backup_brain_core(target)

    def _rollback(msg):
        _restore_brain_core(backup_dir, target)
        shutil.rmtree(backup_dir, ignore_errors=True)
        err_result = {
            "status": "error",
            "old_version": old_version,
            "new_version": new_version,
            "message": f"Upgrade rolled back — {msg}",
        }
        _write_upgrade_log(vault_root, err_result)
        return err_result

    try:
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

        # --- Validate: compile must succeed before migrations run ---
        compile_err = _validate_compile(vault_root)
        if compile_err:
            return _rollback(compile_err)

    except Exception as e:
        return _rollback(f"copy failed: {e}")

    shutil.rmtree(backup_dir, ignore_errors=True)

    # --- Migrations run only after copy + compile succeeded ---
    migrations = _run_migrations(
        vault_root, old_version, new_version, force=force,
    )
    if migrations:
        result["migrations"] = migrations
    if _all_migrations_recorded(vault_root, new_version):
        _write_migrated_version_marker(vault_root, new_version)

    # --- Post-upgrade definition sync ---
    sync_info = _post_upgrade_sync(vault_root, sync=sync)
    if sync_info is not None:
        result.update(sync_info)

    result["message"] = f"Upgraded {old_version or '(none)'} → {new_version}"
    _write_upgrade_log(vault_root, result)
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
    sync_group = parser.add_mutually_exclusive_group()
    sync_group.add_argument(
        "--sync", action="store_const", const=True, default=None, dest="sync",
        help="Sync artefact definitions after upgrade (overrides preference)",
    )
    sync_group.add_argument(
        "--no-sync", action="store_const", const=False, dest="sync",
        help="Skip definition sync after upgrade (overrides preference)",
    )
    args = parser.parse_args()

    try:
        vault_root = find_vault_root(args.vault)
    except ValueError as e:
        fatal(str(e))

    source = str(Path(args.source).resolve())
    result = upgrade(str(vault_root), source, force=args.force, dry_run=args.dry_run, sync=args.sync)

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
        req_file = "brain_mcp/requirements.txt"
        if req_file in result.get("files_added", []) + result.get("files_modified", []):
            info("Dependencies changed — sync your vault's Python environment:")
            info("  .venv/bin/python -m pip install -r .brain-core/brain_mcp/requirements.txt")
            print(file=sys.stderr)

        info("Post-upgrade: rebuild the search index.")
        info("  python3 .brain-core/scripts/build_index.py")
        info("  (or use brain_action('build_index') via MCP)")

        # Sync results
        if "sync_error" in result:
            info(f"Definition sync failed: {result['sync_error']}")
            info("Run sync_definitions.py manually after investigating.")
        elif "sync_result" in result:
            sr = result["sync_result"]
            if sr.get("updated"):
                info("Definition sync:")
                for item in sr["updated"]:
                    info(f"  ~ {item['type']} / {item['role']} → {item['target']}")
            if sr.get("warnings"):
                info("Conflicts (local changes differ from upstream — manual review needed):")
                for item in sr["warnings"]:
                    info(f"  ? {item['type']} / {item['role']} → {item['target']}")


if __name__ == "__main__":
    main()
