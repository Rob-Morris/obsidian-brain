#!/usr/bin/env python3
"""
upgrade.py — Canonical in-place brain-core upgrade entry point.

Applies source brain-core files into a vault's .brain-core/ directory,
validates the compile step, runs versioned migrations, syncs artefact
definitions, and optionally provisions the central managed runtime when the
requirements file changes. Follow-up commands are printed as absolute,
caller-independent commands so the script can be run from any working
directory.

Self-contained — no imports from _common (this script replaces _common
during execution). Duplicates only find_vault_root().

Usage:
  python3 upgrade.py --source /path/to/src/brain-core [--vault /path] [--dry-run] [--force] [--json]
"""

import argparse
import ast
import filecmp
import importlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _safe_write(path, content):
    """Atomic file write: tmp -> fsync -> os.replace.

    Duplicated from _common/_filesystem.safe_write because upgrade.py
    rewrites _common/ mid-execution and cannot rely on importing it.
    Keep this body structurally aligned with the canonical helper while
    preserving byte writes for rollback snapshots, and mirror relevant
    fixes into the peer init.py copy when the shared pattern changes.
    """
    target = os.path.realpath(str(path))
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=os.path.basename(target) + ".",
        suffix=".tmp",
        dir=os.path.dirname(target) or ".",
    )
    mode = "wb" if isinstance(content, bytes) else "w"
    kwargs = {} if mode == "wb" else {"encoding": "utf-8"}
    try:
        with os.fdopen(fd, mode, **kwargs) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        try:
            os.unlink(tmp_path)
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
REQ_FILE_REL = os.path.join("brain_mcp", "requirements.txt")
VENV_HELPER_REL = os.path.join(".brain-core", "scripts", "_common", "_venv.py")
DEPENDENCY_SYNC_TIMEOUT = 300
SEMANTIC_REPAIR_TIMEOUT = 1800

# Outcome tags for `_ensure_central_runtime` (named so producer + consumer cannot drift).
RUNTIME_CREATED = "created"
RUNTIME_REUSED = "reused"
RUNTIME_SKIPPED_DISABLED = "skipped_disabled"
RUNTIME_ERROR = "error"


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


def _snapshot_file(path: str, snapshots: dict[str, dict]) -> None:
    """Capture the original state of ``path`` once for rollback."""
    if path in snapshots:
        return
    if os.path.exists(path):
        with open(path, "rb") as f:
            snapshots[path] = {"exists": True, "content": f.read()}
    else:
        snapshots[path] = {"exists": False}


def _snapshot_tree(root: str, snapshots: dict[str, dict], *, roots: Optional[set[str]] = None) -> None:
    """Capture every file currently under ``root`` for rollback."""
    if roots is not None:
        roots.add(root)
    if not os.path.exists(root):
        return
    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in filenames:
            _snapshot_file(os.path.join(dirpath, filename), snapshots)


def _restore_snapshots(snapshots: dict[str, dict], *, roots: Optional[set[str]] = None) -> None:
    """Restore files captured by ``_snapshot_file`` and remove new files under roots."""
    if roots:
        known_paths = set(snapshots)
        for root in roots:
            if not os.path.exists(root):
                continue
            for dirpath, dirnames, filenames in os.walk(root, topdown=False):
                for filename in filenames:
                    path = os.path.join(dirpath, filename)
                    if path in known_paths:
                        continue
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                for dirname in dirnames:
                    try:
                        os.rmdir(os.path.join(dirpath, dirname))
                    except OSError:
                        pass
    for path, state in snapshots.items():
        if not state.get("exists"):
            try:
                os.remove(path)
            except OSError:
                pass
            continue
        _safe_write(path, state["content"])


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
_DEFAULT_MIGRATION_TARGET = "post_compile"
_PRECOMPILE_PATCH_TARGET = "pre_compile_patch"
_MIGRATION_RECORD_SEP = "@"
_TARGET_HANDLERS_ATTR = "TARGET_HANDLERS"
_MIGRATION_TARGETS = {
    _DEFAULT_MIGRATION_TARGET: {
        "stage": "after compile validation succeeds",
    },
    _PRECOMPILE_PATCH_TARGET: {
        "stage": "after copy, before compile validation",
    },
}


class MigrationDefinitionError(ValueError):
    """Raised when a migration file violates the static discovery contract."""


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


def _require_known_migration_target(target: str) -> None:
    """Reject unknown migration targets early."""
    if target not in _MIGRATION_TARGETS:
        known = ", ".join(sorted(_MIGRATION_TARGETS))
        raise ValueError(f"Unknown migration target {target!r}. Expected one of: {known}")


def _load_migration_module(version_str: str, script_path: str, target: str):
    """Load a migration module from disk with a target-specific module name."""
    spec = importlib.util.spec_from_file_location(
        f"migration_{version_str}_{target}", script_path,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _resolve_migration_handler(mod, target: str):
    """Return the callable handler for a migration target, or None.

    ``TARGET_HANDLERS`` values are validated as string literals during AST
    discovery, so only the named-string and default-``migrate`` paths are
    reachable here.
    """
    target_handlers = getattr(mod, _TARGET_HANDLERS_ATTR, {}) or {}
    handler_name = target_handlers.get(target)
    if isinstance(handler_name, str):
        handler = getattr(mod, handler_name, None)
        if callable(handler):
            return handler
    if target == _DEFAULT_MIGRATION_TARGET:
        handler = getattr(mod, "migrate", None)
        if callable(handler):
            return handler
    return None


def _load_migration_ast(script_path: str) -> ast.AST:
    """Parse a migration file without importing it from current on-disk contents."""
    try:
        with open(script_path, "r", encoding="utf-8") as f:
            return ast.parse(f.read(), filename=script_path)
    except OSError as exc:
        raise MigrationDefinitionError(
            f"{os.path.basename(script_path)} could not be read for migration discovery: {exc}",
        ) from exc
    except SyntaxError as exc:
        raise MigrationDefinitionError(
            f"{os.path.basename(script_path)} has invalid Python syntax for migration discovery: {exc.msg}",
        ) from exc


def _top_level_function_names(tree: ast.AST) -> set[str]:
    """Return top-level function names declared in a module AST."""
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _find_target_handlers_assignment(tree: ast.AST, script_path: str) -> Optional[ast.AST]:
    """Return the single TARGET_HANDLERS assignment value node, if present."""
    matches = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(
                isinstance(target, ast.Name) and target.id == _TARGET_HANDLERS_ATTR
                for target in node.targets
            ):
                matches.append(node.value)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == _TARGET_HANDLERS_ATTR:
                if node.value is None:
                    raise MigrationDefinitionError(
                        f"{os.path.basename(script_path)} declares {_TARGET_HANDLERS_ATTR} without a value",
                    )
                matches.append(node.value)

    if len(matches) > 1:
        raise MigrationDefinitionError(
            f"{os.path.basename(script_path)} declares {_TARGET_HANDLERS_ATTR} more than once",
        )
    return matches[0] if matches else None


def _extract_target_handler_names(script_path: str) -> dict[str, str]:
    """Return statically-declared target handlers from ``TARGET_HANDLERS``."""
    tree = _load_migration_ast(script_path)
    value_node = _find_target_handlers_assignment(tree, script_path)
    if value_node is None:
        return {}
    if not isinstance(value_node, ast.Dict):
        raise MigrationDefinitionError(
            f"{os.path.basename(script_path)} must declare {_TARGET_HANDLERS_ATTR} as a dict literal",
        )

    handlers: dict[str, str] = {}
    function_names = _top_level_function_names(tree)
    for key_node, handler_node in zip(value_node.keys, value_node.values):
        if not isinstance(key_node, ast.Constant) or not isinstance(key_node.value, str):
            raise MigrationDefinitionError(
                f"{os.path.basename(script_path)} must use string literal keys in {_TARGET_HANDLERS_ATTR}",
            )
        target_name = key_node.value
        if target_name not in _MIGRATION_TARGETS:
            known = ", ".join(sorted(_MIGRATION_TARGETS))
            raise MigrationDefinitionError(
                f"{os.path.basename(script_path)} declares unknown migration target {target_name!r}; "
                f"expected one of: {known}",
            )
        if not isinstance(handler_node, ast.Constant) or not isinstance(handler_node.value, str):
            raise MigrationDefinitionError(
                f"{os.path.basename(script_path)} must map {target_name!r} to a string literal "
                "naming a top-level function",
            )
        handler_name = handler_node.value
        if handler_name not in function_names:
            raise MigrationDefinitionError(
                f"{os.path.basename(script_path)} maps {target_name!r} to missing function "
                f"{handler_name!r}",
            )
        handlers[target_name] = handler_name
    return handlers


def _module_defines_function(script_path: str, func_name: str) -> bool:
    """Return True when ``script_path`` defines a top-level function."""
    tree = _load_migration_ast(script_path)
    return func_name in _top_level_function_names(tree)


def _discover_script_modules(scripts_dir: str) -> set[str]:
    """List importable top-level modules/packages in a scripts directory."""
    names = set()
    if not os.path.isdir(scripts_dir):
        return names
    for entry in os.listdir(scripts_dir):
        if entry in {"__pycache__", "upgrade.py"}:
            continue
        full_path = os.path.join(scripts_dir, entry)
        if entry.endswith(".py"):
            names.add(entry[:-3])
        elif os.path.isfile(os.path.join(full_path, "__init__.py")):
            names.add(entry)
    return names


class _MigrationImportContext:
    """Temporarily force migration imports to resolve from one scripts tree."""

    def __init__(self, script_path: str):
        self.scripts_dir = os.path.dirname(os.path.dirname(script_path))
        self.module_names = _discover_script_modules(self.scripts_dir)
        self.saved_modules: dict[str, object] = {}
        self.saved_sys_path: list[str] = []

    def _managed_module_keys(self) -> list[str]:
        """Return loaded module keys owned by this scripts tree."""
        managed = []
        for key in sys.modules:
            for name in self.module_names:
                if key == name or key.startswith(f"{name}."):
                    managed.append(key)
                    break
        return managed

    def __enter__(self):
        self.saved_modules = {
            key: sys.modules[key]
            for key in self._managed_module_keys()
        }
        for key in self.saved_modules:
            sys.modules.pop(key, None)
        self.saved_sys_path = list(sys.path)
        sys.path[:] = [p for p in sys.path if p != self.scripts_dir]
        sys.path.insert(0, self.scripts_dir)
        return self

    def __exit__(self, exc_type, exc, tb):
        for key in self._managed_module_keys():
            sys.modules.pop(key, None)
        sys.modules.update(self.saved_modules)
        sys.path[:] = self.saved_sys_path
        return False


def _discover_target_migrations(
    migrations_dir: str, *, target: str,
) -> list[tuple[tuple, str, str, str]]:
    """Return versioned migration handlers for the requested target."""
    _require_known_migration_target(target)
    discovered = []
    for version_tuple, script_path in _discover_migrations(migrations_dir):
        version_str = _migration_version_str(version_tuple)
        declared_handlers = _extract_target_handler_names(script_path)
        if target == _DEFAULT_MIGRATION_TARGET:
            handler_name = "migrate" if _module_defines_function(script_path, "migrate") else None
        else:
            handler_name = declared_handlers.get(target)
        if handler_name:
            discovered.append((version_tuple, version_str, script_path, handler_name))
    return discovered


def _migration_record_key(version_str: str, target: str) -> str:
    """Return the ledger key for a migration target/version pair."""
    if target == _DEFAULT_MIGRATION_TARGET:
        return version_str
    return f"{version_str}{_MIGRATION_RECORD_SEP}{target}"


def _select_pending_migrations(
    all_migrations: list,
    old_version: Optional[str],
    new_version: str,
    ledger: dict,
    *,
    force: bool,
    target: str,
) -> list:
    """Filter discovered migrations to those eligible to run.

    Single source of truth for the rule that determines which migrations a
    real run would invoke; `_run_migrations` and `_pending_migrations_summary`
    both call this so dry-run previews can never drift from real execution.

    Returns the same 4-tuple shape as `_discover_target_migrations`
    ((version_tuple, version_str, script_path, handler)) so callers can take
    what they need.
    """
    new_tuple = _parse_version(new_version)
    if force:
        return [m for m in all_migrations if m[0] <= new_tuple]
    old_tuple = _parse_version(old_version) if old_version else (0,)
    pending = []
    for entry in all_migrations:
        version_tuple, version_str, _sp, _h = entry
        if version_tuple <= old_tuple or version_tuple > new_tuple:
            continue
        if _migration_record_key(version_str, target) in ledger["migrations"]:
            continue
        pending.append(entry)
    return pending


def _run_migrations(
    vault_root: str,
    old_version: Optional[str],
    new_version: str,
    *,
    force: bool = False,
    target: str = _DEFAULT_MIGRATION_TARGET,
    context: Optional[dict] = None,
    raise_on_error: bool = False,
) -> tuple[list[dict], dict]:
    """Run pending migrations between old_version and new_version.

    Discovers migration scripts in .brain-core/scripts/migrations/,
    runs those whose version is > old_version and <= new_version, unless
    force=True, in which case every migration up to new_version is re-run.

    Per-migration execution is recorded in `.brain/local/migrations.json`.
    Historical migrations up to old_version are backfilled into that ledger the
    first time a vault with pre-ledger history is upgraded.

    Returns (results, ledger) so callers can check completeness without
    re-discovering migrations or re-loading the ledger from disk.
    """
    _require_known_migration_target(target)
    migrations_dir = os.path.join(vault_root, BRAIN_CORE_DIR, "scripts", "migrations")
    all_migrations = _discover_target_migrations(migrations_dir, target=target)
    if not all_migrations:
        return [], _load_migration_ledger(vault_root)

    ledger = _seed_migration_ledger(
        vault_root,
        old_version,
        source=f"installed-version:{old_version}",
        target=target,
    )

    pending = _select_pending_migrations(
        all_migrations, old_version, new_version, ledger,
        force=force, target=target,
    )
    if not pending:
        return [], ledger

    results = []
    for _version_tuple, version_str, script_path, _handler_name in pending:
        try:
            with _MigrationImportContext(script_path):
                mod = _load_migration_module(version_str, script_path, target)
                handler = _resolve_migration_handler(mod, target)
                if handler is None:
                    raise RuntimeError(
                        f"Migration {os.path.basename(script_path)} no longer exposes target {target!r}",
                    )
                if target == _DEFAULT_MIGRATION_TARGET:
                    result = handler(vault_root)
                else:
                    patch_context = context if context is not None else {}
                    result = handler(vault_root, context=patch_context)
                    if (
                        target == _PRECOMPILE_PATCH_TARGET
                        and context is not None
                        and "validate_compile" in context
                    ):
                        context["compile_error"] = context["validate_compile"]()
                if result.get("status") == "error":
                    raise RuntimeError(result.get("message", "migration returned status=error"))
                result["version"] = version_str
                result["target"] = target
            results.append(result)
            ledger = _record_migration_result(
                vault_root, ledger, version_str, script_path, result, target=target,
            )
        except Exception as e:
            if raise_on_error:
                raise RuntimeError(
                    f"Migration {os.path.basename(script_path)} target {target!r} failed: {e}",
                ) from e
            results.append({
                "version": version_str,
                "target": target,
                "status": "error",
                "message": str(e),
            })
    return results, ledger

def _pending_migrations_summary(
    vault_root: str,
    old_version: Optional[str],
    new_version: str,
    *,
    force: bool = False,
    target: str = _DEFAULT_MIGRATION_TARGET,
    migrations_dir: Optional[str] = None,
) -> list[dict]:
    """Return a preview of which migrations would run, without executing them.

    Shares `_select_pending_migrations` with `_run_migrations` so the preview
    can never drift from real execution. Returns version-only metadata —
    per-migration content preview would require every migration script to
    expose a dry-run interface (Bug B's deeper fix).

    Unlike `_run_migrations`, this loads the ledger without seeding so the
    preview is fully side-effect-free; the version-comparison filter handles
    pre-ledger history correctly without seeding for both force and non-force
    paths.

    The migrations_dir override lets callers point at the source's migrations
    (e.g. during dry-run preview, before the upgrade copy step has run) so the
    preview reflects scripts in the about-to-be-installed brain-core, not the
    older set still in the vault.
    """
    _require_known_migration_target(target)
    if migrations_dir is None:
        migrations_dir = os.path.join(vault_root, BRAIN_CORE_DIR, "scripts", "migrations")
    all_migrations = _discover_target_migrations(migrations_dir, target=target)
    if not all_migrations:
        return []

    ledger = _load_migration_ledger(vault_root)
    pending = _select_pending_migrations(
        all_migrations, old_version, new_version, ledger,
        force=force, target=target,
    )
    return [{"version": vs, "target": target} for _vt, vs, _sp, _h in pending]


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
    target: str = _DEFAULT_MIGRATION_TARGET,
) -> dict:
    """Backfill ledger entries for migrations already implied by old state.

    This keeps older vaults from re-running historical migrations the first time
    they see the new per-migration ledger.
    """
    ledger = _load_migration_ledger(vault_root)
    if not upto_version:
        return ledger

    migrations_dir = os.path.join(vault_root, BRAIN_CORE_DIR, "scripts", "migrations")
    all_migrations = _discover_target_migrations(migrations_dir, target=target)
    if not all_migrations:
        return ledger

    upto_tuple = _parse_version(upto_version)
    changed = False
    recorded_at = datetime.now(timezone.utc).isoformat()
    for version_tuple, version_str, script_path, _handler in all_migrations:
        if version_tuple > upto_tuple:
            continue
        record_key = _migration_record_key(version_str, target)
        if record_key in ledger["migrations"]:
            continue
        ledger["migrations"][record_key] = {
            "version": version_str,
            "target": target,
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
    *,
    target: str = _DEFAULT_MIGRATION_TARGET,
) -> dict:
    """Record a successful or skipped migration in the local ledger."""
    status = result.get("status")
    if status == "error":
        return ledger

    ledger["migrations"][_migration_record_key(version_str, target)] = {
        "version": version_str,
        "target": target,
        "status": status or "ok",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "recorded_from": "runner",
        "script": os.path.basename(script_path),
    }
    _write_migration_ledger(vault_root, ledger)
    return ledger


def _all_migrations_recorded(
    vault_root: str,
    target_version: str,
    ledger: Optional[dict] = None,
    *,
    target: str = _DEFAULT_MIGRATION_TARGET,
) -> bool:
    """Return True when every migration up to target_version is in the ledger.

    Pass a pre-loaded *ledger* to avoid re-reading the file from disk.
    """
    migrations_dir = os.path.join(vault_root, BRAIN_CORE_DIR, "scripts", "migrations")
    all_migrations = _discover_target_migrations(migrations_dir, target=target)
    if not all_migrations:
        return True

    target_tuple = _parse_version(target_version)
    if ledger is None:
        ledger = _load_migration_ledger(vault_root)
    recorded = set(ledger["migrations"])
    required = {
        _migration_record_key(version_str, target)
        for version_tuple, version_str, _script_path, _handler in all_migrations
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

    # Fast path: skip if already migrated to this version.
    # Still seed the per-migration ledger so pre-ledger vaults get their
    # history backfilled — without this, reinstalling .brain-core/ after
    # the marker is gone would replay all historical migrations.
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

    results, ledger = _run_migrations(vault_root, None, current_version, force=force)

    if _all_migrations_recorded(vault_root, current_version, ledger=ledger):
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


def _write_upgrade_progress(
    vault_root: str,
    *,
    old_version: Optional[str],
    new_version: str,
    dry_run: bool,
    stage: str,
    message: str,
) -> None:
    """Write an in-progress upgrade stage snapshot for post-mortem diagnosis.

    The CLI can appear stuck even after the real upgrader process has died or
    after a later follow-up step (such as dependency sync) has taken over the
    wall-clock time. Recording the current stage up front makes the vault-local
    diagnostic file useful even when the caller never receives the final JSON or
    stderr footer.
    """
    _write_upgrade_log(
        vault_root,
        {
            "status": "running",
            "stage": stage,
            "old_version": old_version,
            "new_version": new_version,
            "dry_run": dry_run,
            "message": message,
        },
    )


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

def _post_upgrade_sync(
    vault_root: str,
    *,
    sync: Optional[bool],
    dry_run: bool = False,
    scripts_dir: Optional[str] = None,
) -> Optional[dict]:
    """Run artefact definition sync after upgrade.

    Sync is optional — failures are captured, never raised. The upgrade
    is the critical operation; sync is a convenience step.

    Safe updates (upstream changed, no local changes) always apply.
    Conflicts (both sides changed) are returned as warnings.

    When dry_run=True, runs sync_definitions(dry_run=True) and returns the
    preview under 'sync_preview' instead of executing — gives upgrade --dry-run
    a real preview of what sync would do (Bug B: previously dry-run hid sync
    side effects entirely).

    The scripts_dir override lets dry-run import sync_definitions from the
    source (about-to-be-installed scripts) rather than the vault's still-old
    scripts, matching the migrations_dir pattern in
    `_pending_migrations_summary`. Without it, cross-version dry-run would
    fall back to a sync_error when source's sync_definitions is incompatible
    or when the vault has no scripts yet.

    Returns None if skipped entirely, or a dict with one of:
      'sync_result'   — sync ran and produced a result
      'sync_preview'  — dry_run produced a preview without applying
      'sync_error'    — sync was attempted but failed
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
        if scripts_dir is None:
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
            vault_root, force=(sync is True), dry_run=dry_run,
        )
        if not (sync_result["updated"] or sync_result["warnings"]):
            return None
        if dry_run:
            return {"sync_preview": sync_result}
        return {"sync_result": sync_result}
    except Exception as e:
        return {"sync_error": f"Definition sync failed: {e}"}


def _ensure_central_runtime(
    vault_root: Path,
    *,
    requirements_changed: bool,
    sync_deps: Optional[bool],
) -> Optional[dict]:
    """Ensure the central Brain managed runtime exists for this vault's requirements.

    Idempotent: when a venv already exists at the resolved central path
    (`~/.brain/venvs/<py-tag>-<req-hash>/`), this is a no-op. When the hash
    has changed or no central venv exists yet, a new one is created and pip
    installs the requirements. Errors are informational and never fail the
    upgrade.

    Auto mode (`sync_deps=None`) skips when nothing changed. `sync_deps=True`
    forces resolution even on identical requirements. `sync_deps=False` opts
    out entirely. The resolution uses the freshly-installed `_venv.py`
    helper from the vault's `.brain-core/scripts/_common/`, so the path rule
    is single-sourced.
    """
    if sync_deps is False:
        return {"requirements_changed": requirements_changed, "outcome": RUNTIME_SKIPPED_DISABLED}
    if not requirements_changed and sync_deps is not True:
        return None

    venv_helper_path = vault_root / VENV_HELPER_REL
    if not venv_helper_path.is_file():
        return {
            "requirements_changed": requirements_changed,
            "outcome": RUNTIME_ERROR,
            "message": f"_venv helper missing at {venv_helper_path}",
        }

    spec = importlib.util.spec_from_file_location("brain_venv", str(venv_helper_path))
    venv_mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(venv_mod)
    except Exception as e:
        return {
            "requirements_changed": requirements_changed,
            "outcome": RUNTIME_ERROR,
            "message": f"could not load _venv helper: {e}",
        }

    summary = {
        "requirements_changed": requirements_changed,
        "venvs_root": str(venv_mod.central_venvs_root()),
    }
    try:
        result = venv_mod.ensure_central_venv(
            venv_mod.vault_requirements_path(vault_root),
            launcher=Path(sys.executable),
            timeout=DEPENDENCY_SYNC_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return {**summary, "outcome": RUNTIME_ERROR, "message": f"timed out after {DEPENDENCY_SYNC_TIMEOUT}s"}
    except subprocess.CalledProcessError as e:
        return {**summary, "outcome": RUNTIME_ERROR, "message": f"venv setup failed: {e}"}
    except OSError as e:
        return {**summary, "outcome": RUNTIME_ERROR, "message": str(e)}

    legacy = venv_mod.legacy_vault_venv_dir(vault_root)
    if legacy.is_dir():
        summary["legacy_vault_venv"] = str(legacy)

    summary.update({
        "outcome": RUNTIME_CREATED if result["created"] else RUNTIME_REUSED,
        "venv_dir": result["venv_dir"],
        "python": result["python"],
        "python_tag": result["python_tag"],
        "hash": result["hash"],
    })
    return summary


def _load_post_upgrade_semantic_config(vault_root: Path):
    """Load the upgraded vault's semantic config helper from its scripts tree."""
    scripts_dir = vault_root / ".brain-core" / "scripts"
    module_path = scripts_dir / "_semantic" / "config.py"
    if not module_path.is_file():
        raise ImportError(f"semantic config helper missing at {module_path}")

    spec = importlib.util.spec_from_file_location(
        "_brain_upgrade_semantic_config",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"failed to load semantic config helper at {module_path}")

    sys.path.insert(0, str(scripts_dir))
    try:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if sys.path and sys.path[0] == str(scripts_dir):
            sys.path.pop(0)


def _semantic_intent_active_fallback(config_path: Path) -> bool:
    """Best-effort fallback when the upgraded semantic config helper cannot load."""
    try:
        text = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    patterns = (
        r"(?im)^\s*semantic_retrieval:\s*true\s*$",
        r"(?im)^\s*semantic_processing:\s*true\s*$",
        r"(?im)^\s*semantic_engine_installed:\s*true\s*$",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def _semantic_intent_active(vault_root: Path) -> bool:
    """Return True when the vault expects semantic lifecycle ownership."""
    config_path = vault_root / ".brain" / "local" / "config.yaml"
    if not config_path.is_file():
        return False
    try:
        semantic_config = _load_post_upgrade_semantic_config(vault_root)
    except ImportError:
        return _semantic_intent_active_fallback(config_path)
    return bool(
        semantic_config.embeddings_enabled(vault_root)
        or semantic_config.semantic_engine_installed(vault_root)
    )


def _maybe_repair_semantic_model(vault_root: Path) -> Optional[dict]:
    """Run semantic repair after upgrade when semantic intent is active."""
    if not _semantic_intent_active(vault_root):
        return None

    repair_script = vault_root / ".brain-core" / "scripts" / "repair.py"
    command = [
        sys.executable,
        str(repair_script),
        "semantic",
        "--vault",
        str(vault_root),
        "--json",
    ]
    summary = {
        "command": command,
    }
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=SEMANTIC_REPAIR_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return {**summary, "outcome": "error", "message": f"timed out after {SEMANTIC_REPAIR_TIMEOUT}s"}
    except OSError as e:
        return {**summary, "outcome": "error", "message": str(e)}

    stdout = proc.stdout.strip()
    payload = None
    if stdout:
        try:
            loaded = json.loads(stdout)
        except json.JSONDecodeError:
            loaded = None
        if isinstance(loaded, dict):
            payload = loaded

    if proc.returncode != 0:
        message = proc.stderr.strip() or stdout or f"repair.py exited {proc.returncode}"
        return {**summary, "outcome": "error", "message": message, "result": payload}

    return {**summary, "outcome": "ok", "result": payload}


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

        # Preview migrations and definition sync that would run after the
        # version copy. Without this, dry-run reports only file-copy changes
        # and silently hides the migration + sync side effects (Bug B). The
        # source migrations dir is used so the preview reflects the
        # about-to-be-installed scripts, not whichever older set is still in
        # the vault.
        source_migrations_dir = os.path.join(source, "scripts", "migrations")
        precompile_preview = _pending_migrations_summary(
            vault_root, old_version, new_version,
            force=force, target=_PRECOMPILE_PATCH_TARGET,
            migrations_dir=source_migrations_dir,
        )
        if precompile_preview:
            result["precompile_patch_migrations_preview"] = precompile_preview

        migrations_preview = _pending_migrations_summary(
            vault_root, old_version, new_version, force=force,
            migrations_dir=source_migrations_dir,
        )
        if migrations_preview:
            result["migrations_preview"] = migrations_preview

        sync_info = _post_upgrade_sync(
            vault_root, sync=sync, dry_run=True,
            scripts_dir=os.path.join(source, "scripts"),
        )
        if sync_info is not None:
            result.update(sync_info)

        return result

    _write_upgrade_progress(
        vault_root,
        old_version=old_version,
        new_version=new_version,
        dry_run=False,
        stage="backup_brain_core",
        message=f"Preparing upgrade {old_version or '(none)'} → {new_version}",
    )

    # --- Backup .brain-core/ before modifying anything ---
    backup_dir = _backup_brain_core(target)

    precompile_snapshots: dict[str, dict] = {}
    precompile_snapshot_roots: set[str] = set()
    postcompile_snapshots: dict[str, dict] = {}
    postcompile_snapshot_roots: set[str] = set()

    def _rollback(msg):
        if precompile_snapshots:
            _restore_snapshots(precompile_snapshots, roots=precompile_snapshot_roots)
        if postcompile_snapshots:
            _restore_snapshots(postcompile_snapshots, roots=postcompile_snapshot_roots)
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
        _write_upgrade_progress(
            vault_root,
            old_version=old_version,
            new_version=new_version,
            dry_run=False,
            stage="copy_brain_core",
            message="Copying brain-core files into the vault",
        )
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

        _snapshot_tree(os.path.join(vault_root, ".brain"), precompile_snapshots, roots=precompile_snapshot_roots)
        _snapshot_tree(os.path.join(vault_root, "_Config"), precompile_snapshots, roots=precompile_snapshot_roots)
        _write_upgrade_progress(
            vault_root,
            old_version=old_version,
            new_version=new_version,
            dry_run=False,
            stage="validate_compile",
            message="Validating the upgraded router/compiler state",
        )
        compile_context = {
            "compile_error": _validate_compile(vault_root),
            "validate_compile": lambda: _validate_compile(vault_root),
            "snapshot_file": lambda path: _snapshot_file(path, precompile_snapshots),
        }
        try:
            precompile_patches, _patch_ledger = _run_migrations(
                vault_root,
                old_version,
                new_version,
                force=force,
                target=_PRECOMPILE_PATCH_TARGET,
                context=compile_context,
                raise_on_error=True,
            )
        except Exception as e:
            return _rollback(f"pre-compile patch failed: {e}")
        if precompile_patches:
            result["precompile_patch_migrations"] = precompile_patches
        compile_err = compile_context.get("compile_error")
        if compile_err:
            return _rollback(compile_err)

    except Exception as e:
        return _rollback(f"copy failed: {e}")

    compiled_router_path = os.path.join(vault_root, ".brain", "local", "compiled-router.json")
    try:
        with open(compiled_router_path, "r", encoding="utf-8") as f:
            compiled_router = json.load(f)
    except (OSError, json.JSONDecodeError):
        compiled_router = {}

    for art in compiled_router.get("artefacts", []):
        path = art.get("path")
        if not path:
            continue
        _snapshot_tree(
            os.path.join(vault_root, path),
            postcompile_snapshots,
            roots=postcompile_snapshot_roots,
        )

    try:
        _write_upgrade_progress(
            vault_root,
            old_version=old_version,
            new_version=new_version,
            dry_run=False,
            stage="post_compile_migrations",
            message="Running post-compile migrations",
        )
        migrations, ledger = _run_migrations(
            vault_root, old_version, new_version, force=force, raise_on_error=True,
        )
    except Exception as e:
        return _rollback(f"post-compile migration failed: {e}")
    if migrations:
        result["migrations"] = migrations
    if _all_migrations_recorded(vault_root, new_version, ledger=ledger):
        _write_migrated_version_marker(vault_root, new_version)

    shutil.rmtree(backup_dir, ignore_errors=True)

    # --- Post-upgrade definition sync ---
    _write_upgrade_progress(
        vault_root,
        old_version=old_version,
        new_version=new_version,
        dry_run=False,
        stage="post_upgrade_sync",
        message="Running post-upgrade definition sync",
    )
    sync_info = _post_upgrade_sync(vault_root, sync=sync)
    if sync_info is not None:
        result.update(sync_info)

    _write_upgrade_progress(
        vault_root,
        old_version=old_version,
        new_version=new_version,
        dry_run=False,
        stage="semantic_repair",
        message="Reconciling semantic runtime state after upgrade",
    )
    semantic_repair = _maybe_repair_semantic_model(Path(vault_root))
    if semantic_repair is not None:
        result["semantic_repair"] = semantic_repair

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
    deps_group = parser.add_mutually_exclusive_group()
    deps_group.add_argument(
        "--sync-deps", action="store_const", const=True, default=None, dest="sync_deps",
        help="Force central managed runtime sync after upgrade",
    )
    deps_group.add_argument(
        "--no-sync-deps", action="store_const", const=False, dest="sync_deps",
        help="Skip central managed runtime sync after upgrade",
    )
    args = parser.parse_args()

    try:
        vault_root = find_vault_root(args.vault)
    except ValueError as e:
        fatal(str(e))

    source = str(Path(args.source).resolve())
    result = upgrade(str(vault_root), source, force=args.force, dry_run=args.dry_run, sync=args.sync)
    if result["status"] == "ok" and not args.dry_run:
        requirements_changed = REQ_FILE_REL in (
            result.get("files_added", []) + result.get("files_modified", [])
        )
        _write_upgrade_progress(
            str(vault_root),
            old_version=result.get("old_version"),
            new_version=result["new_version"],
            dry_run=False,
            stage="dependency_sync",
            message="Provisioning central managed runtime",
        )
        runtime = _ensure_central_runtime(
            vault_root,
            requirements_changed=requirements_changed,
            sync_deps=args.sync_deps,
        )
        if runtime is not None:
            result["central_runtime"] = runtime
        _write_upgrade_log(str(vault_root), result)

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

    if args.dry_run:
        precompile_preview = result.get("precompile_patch_migrations_preview", [])
        migrations_preview = result.get("migrations_preview", [])
        if precompile_preview or migrations_preview:
            info("")
            if precompile_preview:
                versions = ", ".join(m["version"] for m in precompile_preview)
                info(f"Pre-compile patches that would run: {versions}")
            if migrations_preview:
                versions = ", ".join(m["version"] for m in migrations_preview)
                info(f"Post-compile migrations that would run: {versions}")

        sync_preview = result.get("sync_preview")
        if sync_preview:
            info("")
            info("Definition sync preview (would update if real run):")
            for item in sync_preview.get("updated", []):
                info(f"  ~ {item['type']} / {item['role']} → {item['target']}")
            for item in sync_preview.get("warnings", []):
                info(f"  ? {item['type']} / {item['role']} → {item['target']} (conflict)")

    if not args.dry_run:
        print(file=sys.stderr)

        runtime = result.get("central_runtime")
        if runtime is not None:
            outcome = runtime["outcome"]
            if outcome == RUNTIME_CREATED:
                info(f"Created central runtime at {runtime['venv_dir']}")
            elif outcome == RUNTIME_REUSED:
                info(f"Reused central runtime at {runtime['venv_dir']}")
            elif outcome == RUNTIME_SKIPPED_DISABLED:
                info("Central runtime check skipped (--no-sync-deps).")
            elif outcome == RUNTIME_ERROR:
                info(f"Central runtime setup failed: {runtime['message']}")
                info(f"  Retry: {shlex.quote(sys.executable)} "
                     f"{shlex.quote(str(vault_root / VENV_HELPER_REL))} "
                     f"ensure --vault {shlex.quote(str(vault_root))} --launcher {shlex.quote(sys.executable)}")
            if runtime.get("legacy_vault_venv"):
                info(
                    f"  Legacy vault venv detected at {runtime['legacy_vault_venv']}. "
                    f"To migrate MCP config to the central runtime, run:"
                )
                info(f"    {shlex.quote(sys.executable)} "
                     f"{shlex.quote(str(vault_root / '.brain-core' / 'scripts' / 'init.py'))} "
                     f"--vault {shlex.quote(str(vault_root))} --client all")
            print(file=sys.stderr)

        semantic_repair = result.get("semantic_repair")
        if semantic_repair is not None:
            command = shlex.join(semantic_repair["command"])
            if semantic_repair["outcome"] == "ok":
                info("Semantic model state reconciled after upgrade.")
            else:
                info(f"Semantic model repair after upgrade failed: {semantic_repair['message']}")
                info(f"  {command}")
            print(file=sys.stderr)

        info("Post-upgrade: rebuild the search index.")
        info(f"  {shlex.quote(sys.executable)} {shlex.quote(str(vault_root / '.brain-core' / 'scripts' / 'build_index.py'))}")

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
