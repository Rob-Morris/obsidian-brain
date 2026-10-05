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
import hashlib
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
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _safe_write(path, content):
    """Atomic file write: tmp -> fsync -> os.replace.

    Duplicated from _common/_filesystem.safe_write because upgrade.py
    rewrites _common/ mid-execution and cannot rely on importing it.
    Keep this body structurally aligned with the canonical helper while
    preserving byte writes for rollback snapshots, and mirror relevant
    fixes into any other self-contained bootstrap copies when the shared pattern changes.
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


def _join_argv(argv: list[str]) -> str:
    """Render guidance argv without importing _common during upgrade."""
    if sys.platform == "win32":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BRAIN_CORE_MARKER = os.path.join(".brain-core", "VERSION")
BRAIN_CORE_DIR = ".brain-core"
IGNORE_DIRS = {"__pycache__"}
IGNORE_FILES = {".DS_Store", "upgrade.py"}
REQ_FILE_REL = os.path.join("brain_mcp", "requirements.txt")
AGENT_SKILL_ADAPTER_REL = os.path.join("client-adapters", "shaping", "SKILL.md")
VENV_HELPER_REL = os.path.join(".brain-core", "scripts", "_common", "_venv.py")
CLI_TARGET_LOCATIONS = (
    Path.home() / ".local" / "bin" / "brain",
    Path("/usr/local/bin/brain"),
)
DEPENDENCY_SYNC_TIMEOUT = 300
MCP_REGISTRATION_REPAIR_TIMEOUT = 300
RETRIEVAL_ASSET_REPAIR_TIMEOUT = 1800
RUNTIME_WARMUP_TIMEOUT = 300
SKIP_BOOTSTRAP_ENV = "BRAIN_SKIP_BOOTSTRAP"

# Outcome tags for `_ensure_central_runtime` (named so producer + consumer cannot drift).
RUNTIME_CREATED = "created"
RUNTIME_REUSED = "reused"
RUNTIME_SKIPPED_DISABLED = "skipped_disabled"
RUNTIME_ERROR = "error"


def _runtime_error_message(snapshot: dict, fallback: str) -> str:
    error = snapshot.get("last_error")
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    return fallback


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


_STRICT_VERSION_RE = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)")


def _strict_version(text: str) -> Optional[tuple[int, int, int]]:
    """Parse ``X.Y.Z`` exactly, no leading zeros; anything else is None rather than an unorderable tuple."""
    if _STRICT_VERSION_RE.fullmatch(text) is None:
        return None
    return tuple(int(part) for part in text.split("."))  # type: ignore[return-value]


def _refuse_unreadable_version(
    path: str, text: str, old_version: Optional[str], new_version: str,
) -> Optional[dict]:
    """Refuse a present VERSION that is not strict ``X.Y.Z``; an absent file is a separate case."""
    if _strict_version(text) is not None:
        return None
    return {
        "status": "error",
        "old_version": old_version,
        "new_version": new_version,
        "reason": "version_unreadable",
        "rollback_verified": True,
        "message": (
            f"Upgrade refused — {path} holds {text!r}, not a version of the form X.Y.Z, "
            "so the guard that keeps an older Core off newer content cannot compare it."
        ),
    }


def _managed_approval_followups(old_version: str | None, new_version: str) -> list[dict]:
    """Offer optional setup when an existing Brain gains managed approvals."""
    if old_version is None or not (
        _parse_version(old_version) < (0, 70, 3) <= _parse_version(new_version)
    ):
        return []
    return [{
        "id": "configure_managed_approvals",
        "reason": "managed_approvals_available",
        "message": (
            "Optional: Brain can now manage approvals for normal read/write commands: "
            "Codex MCP, and Claude MCP/CLI on supported hosts. Codex CLI approvals "
            "are currently unsupported. Inspect the proposed policy first (read-only), "
            "then use brain approvals configure with an explicit client, scope "
            "and surfaces if you want to opt in. This notice changes no approvals; "
            "manual rules are not automatically adopted or removed."
        ),
        "command": ["brain", "approvals", "inspect", "--json"],
    }]


def _managed_adapter_copies_outdated(source: str) -> bool:
    from _bootstrap.agent_skills import managed_adapter_copies_outdated

    try:
        with open(os.path.join(source, AGENT_SKILL_ADAPTER_REL), "r", encoding="utf-8") as handle:
            content = handle.read()
    except OSError:
        return False
    return managed_adapter_copies_outdated(Path.home(), content)


def _agent_skill_adapter_followups(vault_root: str, source: str, diff: dict) -> list[dict]:
    """Return post-upgrade guidance when the discovery adapter is new or managed copies lag it.

    The "newly available" case is advisory and read from the copy diff; the
    "updated" case compares managed copies with the source so a resumed or
    re-applied run, whose diff is empty, still reports them.
    """
    if AGENT_SKILL_ADAPTER_REL in diff.get("files_added", []):
        reason = "shaping_adapter_added"
        message = (
            "The Claude/Codex shaping discovery adapter is now available. "
            "Install it after the upgrade so each client loads shaping from the active Brain."
        )
    elif _managed_adapter_copies_outdated(source):
        reason = "shaping_adapter_updated"
        message = (
            "The Claude/Codex shaping discovery adapter changed. "
            "Re-run its configuration after the upgrade to update managed copies."
        )
    else:
        return []

    command = [
        sys.executable,
        os.path.join(vault_root, BRAIN_CORE_DIR, "scripts", "configure.py"),
        "agent-skills",
        "--vault",
        vault_root,
        "--client",
        "all",
    ]
    return [
        {
            "id": "configure_agent_skills",
            "reason": reason,
            "message": message,
            "command": command,
        }
    ]


def _snapshot_file(path: str, snapshots: dict[str, dict]) -> None:
    """Capture the original state of ``path`` once for rollback."""
    if path in snapshots:
        return
    if os.path.exists(path):
        with open(path, "rb") as f:
            snapshots[path] = {"exists": True, "content": f.read()}
    else:
        snapshots[path] = {"exists": False}


def _fsync_files(paths) -> None:
    """Make copied bytes durable before VERSION can witness them."""
    for path in paths:
        with open(path, "rb+") as handle:
            os.fsync(handle.fileno())


def _fsync_directories(paths) -> None:
    """Make directory entries durable; directory fsync is a POSIX facility."""
    if os.name != "posix":
        return
    synced = set()
    for path in paths:
        while not os.path.isdir(path):
            path = os.path.dirname(path)
        if path in synced:
            continue
        synced.add(path)
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _ensure_directory(path: str, changed_dirs: set[str]) -> None:
    """Create ``path`` and note every directory whose entries changed."""
    created = []
    probe = path
    while not os.path.isdir(probe):
        created.append(probe)
        probe = os.path.dirname(probe)
    if not created:
        return
    os.makedirs(path, exist_ok=True)
    changed_dirs.update(created)
    changed_dirs.add(probe)


def _copy_core_except_version(source: str, target: str, diff: dict) -> tuple[list[str], set[str]]:
    """Copy added and modified core files and remove obsolete ones; VERSION is written at the commit.

    Only the diff is touched, which keeps sync services from making conflict
    copies of unchanged files. A failed removal raises, because VERSION will
    claim a complete tree. Returns the copied paths and the directories whose
    entries changed, for the durability pass.
    """
    copied: list[str] = []
    changed_dirs: set[str] = set()
    added = set(diff["files_added"])
    for rel in diff["files_added"] + diff["files_modified"]:
        if rel == "VERSION":
            continue
        src = os.path.join(source, rel)
        dst = os.path.join(target, rel)
        _ensure_directory(os.path.dirname(dst), changed_dirs)
        shutil.copy2(src, dst)
        copied.append(dst)
        if rel in added:
            changed_dirs.add(os.path.dirname(dst))
    for rel in diff["files_removed"]:
        abs_path = os.path.join(target, rel)
        os.remove(abs_path)
        changed_dirs.add(os.path.dirname(abs_path))
        dir_path = os.path.dirname(abs_path)
        try:
            while dir_path != target:
                os.rmdir(dir_path)  # only removes if empty
                dir_path = os.path.dirname(dir_path)
        except OSError:
            pass
    return copied, changed_dirs


def _commit_version(target: str, version_bytes: bytes) -> Optional[str]:
    """Write VERSION last so it witnesses a complete core and migrations.

    The bytes were captured at run start, so the commit cannot pick up a
    source that changed under the run. A failed read, write or replace
    raises; once the replace has landed, a failed directory fsync is returned
    as a message because the commit itself is already in place.
    """
    version_path = os.path.join(target, "VERSION")
    try:
        with open(version_path, "rb") as handle:
            if handle.read() == version_bytes:
                return None
    except OSError:
        pass
    _safe_write(version_path, version_bytes)
    try:
        _fsync_directories((target,))
    except OSError as exc:
        return str(exc)
    return None


def _snapshot_tree(
    root: str,
    snapshots: dict[str, dict],
    *,
    roots: Optional[dict[str, set[str]]] = None,
) -> None:
    """Capture every file currently under ``root`` for rollback."""
    if roots is not None:
        roots[root] = set()
    if not os.path.exists(root):
        return
    for dirpath, _dirnames, filenames in os.walk(root):
        if roots is not None:
            roots[root].add(dirpath)
        for filename in filenames:
            _snapshot_file(os.path.join(dirpath, filename), snapshots)


@dataclass(frozen=True)
class SnapshotRestoreReport:
    """Complete outcome of restoring migration-owned vault state."""

    errors: tuple[str, ...]
    recovery_paths: tuple[str, ...]


def _path_within_root(path: str, root: str) -> bool:
    """Return containment without raising for different Windows drives."""

    try:
        absolute_root = os.path.abspath(root)
        return os.path.commonpath((absolute_root, os.path.abspath(path))) == absolute_root
    except ValueError:
        return False


def _snapshot_recovery_copy(
    recovery_dir: Optional[str],
    path: str,
    content: bytes,
) -> tuple[Optional[str], Optional[str]]:
    if recovery_dir is None:
        return None, None
    digest = hashlib.sha256(os.path.abspath(path).encode("utf-8")).hexdigest()[:16]
    recovery_path = os.path.join(
        recovery_dir,
        f"{digest}-{os.path.basename(path) or 'file'}.original",
    )
    try:
        _safe_write(recovery_path, content)
    except BaseException as exc:
        return None, f"could not preserve original bytes for {path}: {exc}"
    return recovery_path, None


def _restore_snapshots(
    snapshots: dict[str, dict],
    *,
    roots: Optional[dict[str, set[str]]] = None,
    recovery_dir: Optional[str] = None,
) -> SnapshotRestoreReport:
    """Attempt every vault-state restore and retain material for unresolved paths."""
    errors: list[str] = []
    recovery_paths: list[str] = []
    failed_paths: set[str] = set()

    def record_failure(action: str, path: str, exc: BaseException, state=None) -> None:
        if path in failed_paths:
            return
        failed_paths.add(path)
        errors.append(f"{action} {path}: {exc}")
        recovery_paths.append(path)
        if state is not None and state.get("exists"):
            copy, copy_error = _snapshot_recovery_copy(
                recovery_dir,
                path,
                state["content"],
            )
            if copy is not None:
                recovery_paths.append(copy)
            if copy_error is not None:
                errors.append(copy_error)

    if roots:
        known_paths = set(snapshots)
        for root, original_dirs in roots.items():
            if not os.path.exists(root):
                continue
            for dirpath, dirnames, filenames in os.walk(root, topdown=False):
                for filename in filenames:
                    path = os.path.join(dirpath, filename)
                    if path in known_paths:
                        continue
                    try:
                        os.remove(path)
                    except BaseException as exc:
                        if os.path.exists(path):
                            record_failure("remove new file", path, exc)
                for dirname in dirnames:
                    path = os.path.join(dirpath, dirname)
                    if path in original_dirs:
                        continue
                    try:
                        os.rmdir(path)
                    except BaseException as exc:
                        if os.path.exists(path):
                            record_failure("remove new directory", path, exc)
            if root not in original_dirs:
                try:
                    os.rmdir(root)
                except BaseException as exc:
                    if os.path.exists(root):
                        record_failure("remove new directory", root, exc)
    for path, state in snapshots.items():
        if not state.get("exists"):
            try:
                os.remove(path)
            except FileNotFoundError:
                continue
            except BaseException as exc:
                record_failure("remove introduced file", path, exc)
            continue
        try:
            _safe_write(path, state["content"])
        except BaseException as exc:
            try:
                with open(path, "rb") as handle:
                    restored = handle.read() == state["content"]
            except OSError:
                restored = False
            if not restored:
                record_failure("restore original file", path, exc, state)

    for path, state in snapshots.items():
        if path in failed_paths:
            continue
        try:
            restored = (
                Path(path).read_bytes() == state["content"]
                if state.get("exists")
                else not os.path.exists(path)
            )
        except OSError:
            restored = False
        if not restored:
            record_failure(
                "verify restored file",
                path,
                OSError("restored state does not match the captured snapshot"),
                state,
            )
    for root, original_dirs in (roots or {}).items():
        if not os.path.exists(root):
            if original_dirs:
                record_failure(
                    "verify restored directory",
                    root,
                    OSError("original snapshot root is missing after rollback"),
                )
            continue
        expected = {
            path
            for path, state in snapshots.items()
            if state.get("exists") and _path_within_root(path, root)
        }
        for dirpath, _dirnames, filenames in os.walk(root):
            for filename in filenames:
                path = os.path.join(dirpath, filename)
                if path not in expected:
                    record_failure(
                        "verify removed file",
                        path,
                        OSError("introduced file remains after rollback"),
                    )
        actual_dirs = {
            dirpath for dirpath, _dirnames, _filenames in os.walk(root)
        }
        for path in sorted(actual_dirs ^ original_dirs):
            record_failure(
                "verify restored directory",
                path,
                OSError("directory topology differs from the captured snapshot"),
            )
    return SnapshotRestoreReport(
        tuple(errors),
        tuple(dict.fromkeys(recovery_paths)),
    )


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
MIGRATION_FILE_RE = re.compile(_MIGRATION_RE_PATTERN)
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


class MigrationResultError(RuntimeError):
    """Raised when a migration returns a structured fatal result."""

    def __init__(self, result: dict):
        self.result = result
        message = result.get("message") or result.get("error") or "migration returned status=error"
        super().__init__(message)


def _discover_migrations(migrations_dir: str) -> list[tuple[tuple, str]]:
    """Find all migration scripts and return sorted (version_tuple, path) pairs."""
    migrations = []
    if not os.path.isdir(migrations_dir):
        return migrations
    for name in os.listdir(migrations_dir):
        version_str = migration_file_version(name)
        if version_str is not None:
            migrations.append((_parse_version(version_str), os.path.join(migrations_dir, name)))
    return sorted(migrations)


def migration_file_version(filename: str) -> Optional[str]:
    """Return the dotted version a migration file name declares, or None for any other file."""
    match = MIGRATION_FILE_RE.match(filename)
    return match.group(1).replace("_", ".") if match else None


def _require_known_migration_target(target: str) -> None:
    """Reject unknown migration targets early."""
    if target not in _MIGRATION_TARGETS:
        known = ", ".join(sorted(_MIGRATION_TARGETS))
        raise ValueError(f"Unknown migration target {target!r}. Expected one of: {known}")


def _load_migration_module(version_str: str, script_path: str, target: str):
    """Load a migration module from disk with a target-specific module name."""
    module_name = f"migration_{version_str}_{target}"
    spec = importlib.util.spec_from_file_location(
        module_name, script_path,
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.modules.pop(module_name, None)
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
    return _target_handler_names(_load_migration_ast(script_path), script_path)


def _target_handler_names(tree: ast.AST, script_path: str) -> dict[str, str]:
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


def _declared_targets(tree: ast.AST, handlers: dict[str, str]) -> frozenset[str]:
    targets = set(handlers)
    if "migrate" in _top_level_function_names(tree):
        targets.add(_DEFAULT_MIGRATION_TARGET)
    return frozenset(targets)


def declared_migration_targets(text: str, *, filename: str = "<migration>") -> frozenset[str]:
    """Return the ledger targets a migration's source declares: ``migrate`` and ``TARGET_HANDLERS``.

    The one rule behind every ledger key. The upgrade runner, the repository
    contract that keeps released migrations' identity and the lab's coverage
    gate all read it from here, so the key set can never drift between them.
    Pure: text in, no imports of the migration, no filesystem.
    """
    try:
        tree = ast.parse(text, filename=filename)
    except SyntaxError as exc:
        raise MigrationDefinitionError(
            f"{os.path.basename(filename)} has invalid Python syntax for migration discovery: {exc.msg}",
        ) from exc
    return _declared_targets(tree, _target_handler_names(tree, filename))


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
        tree = _load_migration_ast(script_path)
        handlers = _target_handler_names(tree, script_path)
        if target not in _declared_targets(tree, handlers):
            continue
        handler_name = "migrate" if target == _DEFAULT_MIGRATION_TARGET else handlers[target]
        discovered.append((version_tuple, _migration_version_str(version_tuple), script_path, handler_name))
    return discovered


def migration_record_key(version_str: str, target: str) -> str:
    """Encode the ledger key for a migration version and target."""
    if target == _DEFAULT_MIGRATION_TARGET:
        return version_str
    return f"{version_str}{_MIGRATION_RECORD_SEP}{target}"


def migration_record_version(key: str) -> str:
    """Decode the version a ledger key names, whatever its target."""
    return key.split(_MIGRATION_RECORD_SEP, 1)[0]


def _prospective_migration_effects(module, vault_root: str) -> tuple[str, ...]:
    """Resolve a migration's exact extra file effects before its first write."""

    declare = getattr(module, "prospective_effects", None)
    if declare is None:
        return ()
    raw_paths = declare(vault_root)
    if not isinstance(raw_paths, (list, tuple)):
        raise TypeError("migration prospective_effects() must return a list or tuple")
    effects = []
    for raw_path in raw_paths:
        if not isinstance(raw_path, (str, os.PathLike)):
            raise TypeError("migration effect paths must be strings or path-like values")
        path = os.path.abspath(os.fspath(raw_path))
        if os.path.isdir(path) and not os.path.islink(path):
            raise ValueError(
                f"migration effect must name an exact file, not a directory: {path}"
            )
        effects.append(path)
    return tuple(dict.fromkeys(effects))


def _select_pending_migrations(
    all_migrations: list,
    old_version: Optional[str],
    new_version: str,
    ledger: dict,
    *,
    target: str,
) -> list:
    """Filter discovered migrations to those eligible to run.

    Single source of truth for the rule that determines which migrations a
    real run would invoke; `_run_migrations` and `_pending_migrations_summary`
    both call this so dry-run previews can never drift from real execution.
    The window is ``installed VERSION < version <= target`` minus recorded
    keys; nothing re-runs a recorded migration.

    Returns the same 4-tuple shape as `_discover_target_migrations`
    ((version_tuple, version_str, script_path, handler)) so callers can take
    what they need.
    """
    new_tuple = _parse_version(new_version)
    old_tuple = _parse_version(old_version) if old_version else (0,)
    pending = []
    for entry in all_migrations:
        version_tuple, version_str, _sp, _h = entry
        if version_tuple <= old_tuple or version_tuple > new_tuple:
            continue
        if migration_record_key(version_str, target) in ledger["migrations"]:
            continue
        pending.append(entry)
    return pending


def _run_migrations(
    vault_root: str,
    old_version: Optional[str],
    new_version: str,
    *,
    target: str = _DEFAULT_MIGRATION_TARGET,
    context: Optional[dict] = None,
    prepare_effects=None,
) -> tuple[list[dict], dict]:
    """Run pending migrations between old_version and new_version.

    Discovers migration scripts in .brain-core/scripts/migrations/ and runs
    those whose version is > old_version and <= new_version and whose ledger
    key is not yet recorded. A recorded migration never re-runs; a correction
    ships as a new migration.

    Each `ok` or `skipped` result is recorded in `.brain/local/migrations.json`
    straight after its migration. Any other result, or a raising migration,
    raises so the caller rolls back. Historical migrations up to old_version
    are backfilled into the ledger the first time a vault with pre-ledger
    history is upgraded.

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
        all_migrations, old_version, new_version, ledger, target=target,
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
                effects = _prospective_migration_effects(mod, vault_root)
                if prepare_effects is not None and effects:
                    prepare_effects(effects)
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
                result["version"] = version_str
                result["target"] = target
                if result.get("status") not in {"ok", "skipped"}:
                    if (
                        target == _PRECOMPILE_PATCH_TARGET
                        and context is not None
                        and context.get("compile_error")
                        and not (result.get("message") or result.get("error"))
                    ):
                        result["message"] = context["compile_error"]
                    raise MigrationResultError(result)
            results.append(result)
            ledger = _record_migration_result(
                vault_root, ledger, version_str, script_path, result, target=target,
            )
        except MigrationResultError:
            raise
        except Exception as e:
            raise RuntimeError(
                f"Migration {os.path.basename(script_path)} target {target!r} failed: {e}",
            ) from e
    return results, ledger

def _pending_migrations_summary(
    vault_root: str,
    old_version: Optional[str],
    new_version: str,
    *,
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
    pre-ledger history correctly without seeding.

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
        all_migrations, old_version, new_version, ledger, target=target,
    )
    return [{"version": vs, "target": target} for _vt, vs, _sp, _h in pending]


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
        record_key = migration_record_key(version_str, target)
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
    """Record an `ok` or `skipped` migration in the local ledger straight after it ran."""
    status = result.get("status")
    if status not in {"ok", "skipped"}:
        raise RuntimeError(
            f"migration {version_str} target {target!r} returned {status!r}; "
            "only ok or skipped results are recorded"
        )

    ledger["migrations"][migration_record_key(version_str, target)] = {
        "version": version_str,
        "target": target,
        "status": status,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "recorded_from": "runner",
        "script": os.path.basename(script_path),
    }
    _write_migration_ledger(vault_root, ledger)
    return ledger


def _recorded_content_versions(ledger: dict) -> dict[str, tuple[int, int, int]]:
    """Return each strictly parsed version the ledger records, keyed by its text."""
    versions = {}
    for key in ledger["migrations"]:
        version_str = migration_record_version(key)
        parsed = _strict_version(version_str)
        if parsed is not None:
            versions[version_str] = parsed
    return versions


def _refuse_older_source(
    vault_root: str, old_version: Optional[str], new_version: str,
) -> Optional[dict]:
    """Refuse a source older than the recorded content, with or without force.

    The content version is the higher of the installed ``VERSION`` and the
    highest ledger record. Migrations only run forward, so an older Core over
    content they shaped is undefined. The remedy never names ``brain upgrade``:
    its upgrader is the installed distribution's, which may be the source
    being refused.
    """
    source = _strict_version(new_version)
    if source is None:
        return None
    recorded = _recorded_content_versions(_load_migration_ledger(vault_root))
    candidates = dict(recorded)
    installed = _strict_version(old_version) if old_version else None
    if installed is not None:
        candidates[old_version] = installed
    if not candidates:
        return None
    content_str, content = max(candidates.items(), key=lambda item: item[1])
    if source >= content:
        return None
    above = sorted(
        (text for text, parsed in recorded.items() if parsed > source),
        key=lambda text: recorded[text],
    )
    recorded_note = (
        f" (the migration ledger records {', '.join(above)} above this source)"
        if above else ""
    )
    return {
        "status": "error",
        "old_version": old_version,
        "new_version": new_version,
        "reason": "content_ahead",
        "rollback_verified": True,
        "message": (
            f"Upgrade refused — this source is {new_version} but the Brain's content "
            f"is at {content_str}{recorded_note}. Migrations only run forward, so "
            "Brain never applies an older Core. Upgrade from a source at or above "
            f"{content_str}: run install.sh from a current clone, or upgrade.py "
            "--source <path> with that source."
        ),
    }


# ---------------------------------------------------------------------------
# Backup / restore — keeps .brain-core/ recoverable during upgrades
# ---------------------------------------------------------------------------

_COMPILE_TIMEOUT = 60  # seconds (longer than server's 30s startup timeout
# because upgrade runs interactively and compile is
# the validation gate — worth waiting longer)


def _walk_exact_tree(root: str) -> set[str]:
    """Return every file under ``root`` for exact rollback accounting."""
    paths = set()
    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in filenames:
            paths.add(os.path.relpath(os.path.join(dirpath, filename), root))
    return paths


def _backup_brain_core(target: str) -> str:
    """Copy the exact .brain-core/ tree to a temp directory outside the vault.

    Returns the backup directory path. The caller is responsible for
    cleanup (success) or restore (failure).
    """
    backup_dir = tempfile.mkdtemp(prefix="brain-core-backup-")
    shutil.copytree(target, os.path.join(backup_dir, BRAIN_CORE_DIR))
    return backup_dir


def _restore_brain_core(backup_dir: str, target: str) -> None:
    """Restore .brain-core/ from a backup, file-by-file to be iCloud-safe."""
    backup_src = os.path.join(backup_dir, BRAIN_CORE_DIR)

    # Remove files that weren't in the backup (i.e. newly added by upgrade)
    backup_files = _walk_exact_tree(backup_src)
    current_files = _walk_exact_tree(target)
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

    # Bytecode generation and other temporary work can leave empty directories
    # that were not part of the old core. Remove only directories now proven
    # empty; directories containing restored files are retained naturally.
    for dirpath, dirnames, _filenames in os.walk(target, topdown=False):
        for dirname in dirnames:
            try:
                os.rmdir(os.path.join(dirpath, dirname))
            except OSError:
                pass


def _tree_fingerprint(root: str) -> str:
    """Hash the exact file set and bytes used by upgrade rollback."""
    digest = hashlib.sha256()
    for rel in sorted(_walk_exact_tree(root)):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        with open(os.path.join(root, rel), "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _snapshots_verified(
    snapshots: dict[str, dict],
    *,
    roots: Optional[dict[str, set[str]]] = None,
) -> bool:
    for path, state in snapshots.items():
        if state.get("exists"):
            try:
                with open(path, "rb") as handle:
                    if handle.read() != state["content"]:
                        return False
            except OSError:
                return False
        elif os.path.exists(path):
            return False
    for root, original_dirs in (roots or {}).items():
        expected = {
            path
            for path, state in snapshots.items()
            if state.get("exists") and _path_within_root(path, root)
        }
        actual = set()
        if os.path.exists(root):
            for dirpath, _dirnames, filenames in os.walk(root):
                actual.update(os.path.join(dirpath, name) for name in filenames)
        if actual != expected:
            return False
        actual_dirs = {
            dirpath for dirpath, _dirnames, _filenames in os.walk(root)
        }
        if actual_dirs != original_dirs:
            return False
    return True


def _write_upgrade_log(vault_root: str, result: dict) -> None:
    """Write upgrade result to .brain/local/last-upgrade.json for diagnostics."""
    log_path = os.path.join(vault_root, _LAST_UPGRADE_FILE)
    entry = {**result, "timestamp": datetime.now(timezone.utc).isoformat()}
    try:
        _safe_write(log_path, json.dumps(entry, indent=2) + "\n")
    except OSError:
        pass  # best-effort — don't let log failure mask the real error


STAGE_BACKUP_BRAIN_CORE = "backup_brain_core"
STAGE_COPY_BRAIN_CORE = "copy_brain_core"
STAGE_VALIDATE_COMPILE = "validate_compile"
STAGE_POST_COMPILE_MIGRATIONS = "post_compile_migrations"
STAGE_VERSION_COMMIT = "version_commit"
STAGE_POST_UPGRADE_SYNC = "post_upgrade_sync"
STAGE_ROUTER_COMPILE = "router_compile"
STAGE_DEPENDENCY_SYNC = "dependency_sync"
STAGE_MCP_REGISTRATION_REPAIR = "mcp_registration_repair"
STAGE_MACHINE_RESOLUTION_RUNTIME = "machine_resolution_runtime"
STAGE_RETRIEVAL_ASSET_REPAIR = "retrieval_asset_repair"
STAGE_RUNTIME_READINESS = "runtime_readiness"
STAGE_RUNTIME_TIDINESS = "runtime_tidiness"
# Every stage this upgrader logs, in order. The pre-commit set is closed: a
# ``running`` log at any other stage, including one an older Core wrote, with
# ``new_version == VERSION`` means the Core committed and post-commit work was lost.
_STAGES = (
    STAGE_BACKUP_BRAIN_CORE,
    STAGE_COPY_BRAIN_CORE,
    STAGE_VALIDATE_COMPILE,
    STAGE_POST_COMPILE_MIGRATIONS,
    STAGE_VERSION_COMMIT,
    STAGE_POST_UPGRADE_SYNC,
    STAGE_ROUTER_COMPILE,
    STAGE_DEPENDENCY_SYNC,
    STAGE_MCP_REGISTRATION_REPAIR,
    STAGE_MACHINE_RESOLUTION_RUNTIME,
    STAGE_RETRIEVAL_ASSET_REPAIR,
    STAGE_RUNTIME_READINESS,
    STAGE_RUNTIME_TIDINESS,
)
_PRE_COMMIT_STAGES = (
    STAGE_BACKUP_BRAIN_CORE,
    STAGE_COPY_BRAIN_CORE,
    STAGE_VALIDATE_COMPILE,
    STAGE_POST_COMPILE_MIGRATIONS,
)
_POST_COMMIT_STAGES = tuple(stage for stage in _STAGES if stage not in _PRE_COMMIT_STAGES)
_POST_COMMIT_REPAIRS = "runtime.refresh-router, brain runtime repair or mcp.repair"
_SAME_VERSION_RE_APPLY = (
    "re-apply the same version (upgrade.py --force, or brain upgrade with \"force\": true)"
)


def _interrupted_upgrade_warning(vault_root: str, installed_version: Optional[str]) -> Optional[dict]:
    """Warn about a ``running`` upgrade log; it is never an input to selection."""
    try:
        with open(os.path.join(vault_root, _LAST_UPGRADE_FILE), "r", encoding="utf-8") as handle:
            entry = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(entry, dict) or entry.get("status") != "running":
        return None
    stage = entry.get("stage")
    old = entry.get("old_version")
    new = entry.get("new_version")
    message = (
        f"A previous upgrade {old or '(none)'} → {new} was interrupted during "
        f"{stage or 'an unknown stage'}."
    )
    if new != installed_version:
        message += (
            " This upgrade resumes it: recorded migrations are skipped and the "
            "interrupted one restarts."
        )
    elif stage not in _PRE_COMMIT_STAGES:
        message += (
            f" Its Core is committed; {_SAME_VERSION_RE_APPLY} or run the named "
            f"repair ({_POST_COMMIT_REPAIRS}) to finish the post-commit stages."
        )
    elif old == new:
        message += f" It was a same-version re-apply; {_SAME_VERSION_RE_APPLY} to finish it."
    else:
        message += (
            " Its VERSION was written before its migrations finished, so some "
            "migrations may be missing; a forced re-apply will not run them."
        )
    return {
        "stage": "upgrade_start",
        "code": "interrupted_previous_upgrade",
        "message": message,
        "interrupted_stage": stage,
        "interrupted_old_version": old,
        "interrupted_new_version": new,
    }


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


def _format_sync_updated(item: dict) -> str:
    """Render one sync ``updated`` entry, naming a convention folder change.

    Mirrors ``sync_definitions.format_sync_updated``; duplicated because this
    script stays self-contained (it replaces the scripts it would import).
    """
    detail = (
        f" (Naming folder {item['previous']} → {item['folder']})"
        if item.get("previous") else ""
    )
    return f"~ {item['type']} / {item['role']} → {item['target']}{detail}"


def _format_sync_warning(item: dict) -> str:
    """Render one sync ``warnings`` entry with its reason or action (see above)."""
    reason = item.get("reason") or item.get("action") or "conflict"
    return f"? {item['type']} / {item['role']} → {item['target']} ({reason})"


def _format_sync_error(item: dict) -> str:
    """Render one sync ``errors`` entry (see above)."""
    return f"! {item['type']} / {item['role']}: {item['error']}"


def _validate_compile(vault_root: str) -> Optional[str]:
    """Run compile_router.py against the vault as a validation step.

    Returns None on success, or an error message on failure.
    """
    script = os.path.join(vault_root, BRAIN_CORE_DIR, "scripts", "compile_router.py")
    if not os.path.isfile(script):
        return f"compile_router.py not found at {script}"

    try:
        env = os.environ.copy()
        # Internal upgrade validation is already running from a known-good
        # Python environment; do not recurse into managed-runtime bootstrap.
        env[SKIP_BOOTSTRAP_ENV] = "1"
        proc = subprocess.run(
            [sys.executable, script],
            cwd=vault_root,
            capture_output=True,
            text=True,
            timeout=_COMPILE_TIMEOUT,
            env=env,
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
        if not (sync_result["updated"] or sync_result["warnings"] or sync_result["errors"]):
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
) -> dict:
    """Ensure the central Brain managed runtime exists for this vault's requirements.

    Idempotent: when a venv already exists at the resolved central path
    (`~/.brain/venvs/<py-tag>-<req-hash>/`), this is a no-op. When the hash
    has changed or no central venv exists yet, a new one is created and pip
    installs the requirements. Errors are informational and never fail the
    upgrade.

    Auto mode (`sync_deps=None`) ensures the runtime every run through the
    sentinel-checked reuse, so a resumed or re-applied upgrade with an empty
    copy diff still provisions it; `requirements_changed` is reported, not a
    trigger. `sync_deps=True` adds full conformance checking of an existing
    runtime. `sync_deps=False` opts out entirely. The resolution uses the
    freshly-installed `_venv.py` helper from the vault's
    `.brain-core/scripts/_common/`, so the path rule is single-sourced.
    """
    if sync_deps is False:
        return {"requirements_changed": requirements_changed, "outcome": RUNTIME_SKIPPED_DISABLED}

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
    # Delegate to the orchestrator so upgrade picks up any existing
    # compatible-minor runtime (Brew-churn etc) instead of always creating
    # a new exact-tag venv. The orchestrator's vocabulary is richer than
    # ensure_central_venv's `created` flag — translate to upgrade.py's
    # legacy RUNTIME_CREATED / RUNTIME_REUSED outcomes for the human and
    # JSON output, treating "synced in place" as a reuse from upgrade's
    # perspective (the venv dir wasn't created, only pip ran).
    try:
        provision = venv_mod.resolve_or_provision_central_venv(
            vault_root,
            launcher=Path(sys.executable),
            required_modules=(),
            install_requirements=True,
            full_conformance=sync_deps is True,
            dry_run=False,
            timeout=DEPENDENCY_SYNC_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return {**summary, "outcome": RUNTIME_ERROR, "message": f"timed out after {DEPENDENCY_SYNC_TIMEOUT}s"}
    except OSError as e:
        return {**summary, "outcome": RUNTIME_ERROR, "message": f"{type(e).__name__}: {e}"}

    if provision["outcome"] == venv_mod.RUNTIME_ERROR:
        return {**summary, "outcome": RUNTIME_ERROR, "message": provision.get("message", "unknown provision error")}

    legacy = venv_mod.legacy_vault_venv_dir(vault_root)
    if legacy.is_dir():
        summary["legacy_vault_venv"] = str(legacy)

    # RUNTIME_REUSED + RUNTIME_SYNCED both mean "no new venv directory
    # was created" — collapse to the existing RUNTIME_REUSED outcome
    # so the upgrade output stays stable across the v0.38.7 + v0.39.0
    # lookup-softening / convergence work.
    upgrade_outcome = (
        RUNTIME_CREATED if provision["outcome"] == venv_mod.RUNTIME_CREATED else RUNTIME_REUSED
    )
    summary.update({
        "outcome": upgrade_outcome,
        "venv_dir": provision["venv_dir"],
        "python": provision["python"],
        "python_tag": provision["python_tag"],
        "hash": provision["hash"],
    })
    return summary


def _ensure_machine_resolution_runtime(vault_root: Path) -> dict:
    """Deploy the machine-level resolver runtime from the freshly-installed core."""
    scripts_dir = vault_root / ".brain-core" / "scripts"
    module_path = scripts_dir / "_machine" / "resolution_runtime.py"
    if not module_path.is_file():
        return {
            "outcome": "error",
            "message": f"resolution runtime helper missing at {module_path}",
        }

    try:
        # Load the freshly copied provisioner from the target vault; a sys.path
        # import could pick up the upgrader's older source checkout instead.
        spec = importlib.util.spec_from_file_location("_brain_resolution_runtime", str(module_path))
        if spec is None or spec.loader is None:
            return {
                "outcome": "error",
                "message": f"could not load resolution runtime helper spec at {module_path}",
            }
        runtime_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runtime_mod)
        result = runtime_mod.ensure_resolution_runtime(scripts_dir)
    except OSError as e:
        return {
            "outcome": "error",
            "message": f"could not provision machine resolution runtime: {e}",
        }

    outcome = "updated" if result.get("status") == "changed" else result.get("status", "unknown")
    return {
        "outcome": outcome,
        "path": result.get("path"),
        "entry": result.get("entry"),
        "version": result.get("version"),
        "changed_files": result.get("changed_files", []),
        "message": result.get("message"),
    }


def _await_runtime_readiness(readiness, vault_root: Path, timeout_seconds: float) -> dict:
    """Await readiness through an already-loaded canonical readiness module."""

    request_outcome, snapshot = readiness.ensure_runtime_warmup(
        vault_root,
        retry_failed=True,
    )
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while snapshot.get("state") == "warming":
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {
                "outcome": "error",
                "request_outcome": request_outcome,
                "runtime_status": snapshot,
                "message": (
                    "Runtime warm-up did not become ready before the "
                    f"{timeout_seconds:g}s upgrade-completion timeout."
                ),
            }
        retry_ms = snapshot.get("retry_after_ms")
        delay = retry_ms / 1000 if isinstance(retry_ms, int) else 0.25
        time.sleep(min(max(delay, 0.01), remaining))
        snapshot = readiness.read_runtime_status(vault_root)

    if snapshot.get("state") != "ready":
        return {
            "outcome": "error",
            "request_outcome": request_outcome,
            "runtime_status": snapshot,
            "message": _runtime_error_message(
                snapshot,
                "Runtime warm-up finished without a ready state.",
            ),
        }
    return {
        "outcome": "ok",
        "request_outcome": request_outcome,
        "runtime_status": snapshot,
        "message": "Runtime warm-up completed and the selected Brain is ready.",
    }


def _complete_runtime_readiness(
    vault_root: Path,
    *,
    timeout_seconds: float = RUNTIME_WARMUP_TIMEOUT,
) -> dict:
    """Start or join canonical warm-up and await a closed readiness state."""

    module_path = vault_root / ".brain-core" / "scripts" / "_bootstrap" / "readiness.py"
    if not module_path.is_file():
        return {
            "outcome": "error",
            "message": f"runtime readiness helper missing at {module_path}",
        }
    try:
        with _MigrationImportContext(str(module_path)):
            from _bootstrap import readiness
            return _await_runtime_readiness(readiness, vault_root, timeout_seconds)
    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "outcome": "error",
            "message": f"Runtime readiness could not be completed: {exc}",
        }


def _deferred_runtime_readiness() -> dict:
    """Describe the warm-up intentionally deferred by ``--no-sync-deps``."""
    return {
        "outcome": "deferred",
        "command": ["brain", "runtime", "warmup"],
        "message": (
            "Runtime warm-up was deferred because dependency synchronisation "
            "was disabled."
        ),
    }


def _inspect_runtime_orphans(vault_root: Path) -> dict:
    """Inspect shared runtimes and return canonical launcher follow-up guidance."""

    module_path = vault_root / ".brain-core" / "scripts" / "_machine" / "maintenance.py"
    if not module_path.is_file():
        return {
            "outcome": "error",
            "message": f"runtime maintenance helper missing at {module_path}",
        }
    try:
        with _MigrationImportContext(str(module_path)):
            from _machine import maintenance

            summary = maintenance.collect_machine_summary(
                current_vault=str(vault_root),
                launcher_python=sys.executable,
            )
    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "outcome": "error",
            "message": f"Shared-runtime tidiness could not be inspected: {exc}",
        }

    return _runtime_orphan_guidance(summary)


def _runtime_orphan_guidance(summary: dict) -> dict:
    """Project a machine summary into bounded cleanup guidance."""

    count = summary.get("counts", {}).get("orphan_candidates")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        return {
            "outcome": "error",
            "message": "Shared-runtime inspection returned an invalid orphan count.",
        }
    if count == 0:
        return {
            "outcome": "ok",
            "orphan_candidates": 0,
            "message": "No orphaned shared runtimes need follow-up.",
        }
    return {
        "outcome": "follow_up",
        "orphan_candidates": count,
        "dry_run_command": ["brain", "runtime", "remove-orphans", "--dry-run"],
        "remove_command": ["brain", "runtime", "remove-orphans"],
        "message": f"{count} orphaned shared runtime(s) are safe cleanup candidates.",
    }


def _prepare_cli_cutover(
    vault_root: Path,
    source: Path,
    *,
    acknowledge_global_cli_cutover: bool,
    excluded_stale_brain_ids: tuple[str, ...],
) -> dict | None:
    """Build the complete-registry plan for an installed global CLI."""

    targets = [path for path in CLI_TARGET_LOCATIONS if path.is_file()]
    if not targets:
        return None
    if len(targets) != 1:
        raise ValueError(
            "multiple global Brain CLI installations exist; retain exactly one "
            "before the coordinated cutover"
        )
    target = targets[0]
    repo_root = source.resolve().parent.parent
    cli_root = repo_root / "cli"
    if not (cli_root / "_distribution.py").is_file():
        raise ValueError("breaking Brain upgrade requires the complete Brain CLI distribution")
    if str(cli_root) not in sys.path:
        sys.path.insert(0, str(cli_root))
    from _distribution import source_versions
    from _launcher.cutover import preflight

    catalogue = json.loads((source / "command-catalogue.json").read_text(encoding="utf-8"))
    epoch = catalogue.get("interface_epoch")
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 1:
        raise ValueError("source command catalogue has an invalid interface epoch")
    protocol_text = (source / "brain_mcp" / "_interface_protocol.py").read_text(
        encoding="utf-8"
    )
    protocol_match = re.search(
        r"^PROXY_PROTOCOL = ([0-9]+)$", protocol_text, re.MULTILINE
    )
    if protocol_match is None:
        raise ValueError("source proxy protocol declaration is invalid")
    source_version = _read_version(str(source))
    if source_version is None:
        raise ValueError("source Brain Core version is missing")
    new_cli_version = source_versions(repo_root).cli_version
    cli_text = target.read_text(encoding="utf-8")
    version_match = re.search(
        r'^BRAIN_CLI_VERSION="([0-9]+\.[0-9]+\.[0-9]+)"$',
        cli_text,
        re.MULTILINE,
    )
    if version_match is None:
        raise ValueError(
            f"installed Brain CLI version is unclassifiable: {target}"
        )
    old_cli_version = version_match.group(1)
    report = preflight(
        selected_vault=vault_root,
        source_brain_core_version=source_version,
        old_cli_version=old_cli_version,
        new_cli_version=new_cli_version,
        interface_epoch=epoch,
        proxy_protocol=int(protocol_match.group(1)),
        acknowledge_global_cli_cutover=acknowledge_global_cli_cutover,
        excluded_stale_brain_ids=excluded_stale_brain_ids,
    )
    return {
        "target": target,
        "repo_root": repo_root,
        "source_version": source_version,
        "cli_version": new_cli_version,
        "preflight": report,
    }


def _commit_cli_cutover(plan: dict) -> dict:
    from _distribution import distribution_cutover_commit, install_distribution

    installed = install_distribution(
        plan["repo_root"],
        plan["target"],
        cli_version=plan["cli_version"],
        expected_brain_core_version=plan["source_version"],
    )
    return distribution_cutover_commit(installed)


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


def _post_upgrade_retrieval_scope(vault_root: Path) -> str:
    """Return the repair scope that should reconcile retrieval assets after upgrade."""
    return "semantic" if _semantic_intent_active(vault_root) else "lexical"


def _load_json_dict_from_output(text: str) -> Optional[dict]:
    """Best-effort parse for JSON output that may have noisy prefix lines.

    Some runtime/model dependencies emit progress or load-report text around a
    `--json` payload. Prefer the full string, then salvage a trailing JSON
    object when a preceding line-oriented prefix is present.
    """
    stripped = text.strip()
    if not stripped:
        return None

    candidates = [stripped]
    marker = stripped.rfind("\n{")
    if marker != -1:
        candidates.append(stripped[marker + 1 :])

    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            loaded = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            return loaded
    return None


def _repair_failure_message(
    *,
    returncode: int,
    payload: Optional[dict],
    stdout: str,
    stderr: str,
) -> str:
    """Return the operator-facing summary for a failed repair subprocess."""
    if isinstance(payload, dict):
        payload_message = payload.get("message")
        if isinstance(payload_message, str) and payload_message.strip():
            return payload_message.strip()
    if stderr:
        return stderr
    if stdout:
        return stdout
    return f"repair.py exited {returncode}"


def _run_repair_scope_after_upgrade(
    vault_root: Path,
    scope: str,
    *,
    timeout: float,
) -> dict:
    """Run one canonical vault repair scope and preserve its typed evidence."""
    repair_script = vault_root / ".brain-core" / "scripts" / "repair.py"
    command = [
        sys.executable,
        str(repair_script),
        scope,
        "--vault",
        str(vault_root),
        "--json",
    ]
    summary = {"scope": scope, "command": command}
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {**summary, "outcome": "error", "message": f"timed out after {timeout:g}s"}
    except OSError as exc:
        return {**summary, "outcome": "error", "message": str(exc)}

    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    payload = _load_json_dict_from_output(stdout)
    if proc.returncode != 0:
        return {
            **summary,
            "outcome": "error",
            "message": _repair_failure_message(
                returncode=proc.returncode,
                payload=payload,
                stdout=stdout,
                stderr=stderr,
            ),
            "returncode": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "result": payload,
        }
    return {**summary, "outcome": "ok", "result": payload}


def _has_current_vault_mcp_registration(vault_root: Path) -> bool:
    """Return whether this vault records any MCP registration state."""
    from _bootstrap.mcp_registration import user_ledger_path
    local_state_paths = (
        vault_root / ".mcp.json",
        vault_root / ".codex" / "config.toml",
        vault_root / ".grok" / "config.toml",
        vault_root / ".brain" / "local" / "init-state.json",
        user_ledger_path(Path.home()),
        Path.home() / ".claude.json",
        Path.home() / ".codex/config.toml",
        Path.home() / ".grok/config.toml",
    )
    return any(path.exists() for path in local_state_paths)


def _repair_mcp_registration_after_upgrade(vault_root: Path) -> dict:
    """Verify migration then reconcile all registered projections through one owner."""
    from _bootstrap.mcp_transport import delegate_machine_command, InitTransportError

    effects = []
    try:
        migrated = delegate_machine_command("migrate", {}, vault_root=vault_root)
        effects.extend(migrated.get("committed_effects", []))
        repaired = delegate_machine_command("repair", {"breadth": "brain"}, vault_root=vault_root)
        effects.extend(repaired.get("committed_effects", []))
    except (InitTransportError, OSError, ValueError) as exc:
        effects.extend(getattr(exc, "committed_effects", []))
        return {"scope": "mcp", "outcome": "partial" if effects else "error", "message": str(exc), "committed_effects": effects}
    return {"scope": "mcp", "outcome": "ok", "committed_effects": effects,
            "message": "Canonical MCP registrations reconciled; client-host reconnect and a normal MCP call remain separate verification."}


def _deferred_mcp_registration_after_upgrade(vault_root: Path) -> dict:
    """Describe MCP reconciliation intentionally deferred with dependencies."""
    if not _has_current_vault_mcp_registration(vault_root):
        return {
            "scope": "mcp",
            "command": [],
            "outcome": "noop",
            "message": "No existing current-vault MCP registrations need reconciliation.",
        }
    command = [
        "brain", "mcp", "repair",
        "--vault",
        str(vault_root),
        "--request-json", '{"breadth":"brain"}',
        "--json",
    ]
    return {
        "scope": "mcp",
        "command": command,
        "outcome": "deferred",
        "message": (
            "MCP registration repair was deferred because dependency "
            "synchronisation was disabled. Run `brain mcp migrate --json` first, "
            "then run the Brain-wide repair command."
        ),
    }


def _repair_retrieval_assets_after_upgrade(vault_root: Path) -> dict:
    """Reconcile the vault's supported retrieval assets after upgrade.

    Lexical-only vaults route through `repair.py lexical`. Vaults with
    semantic intent route through `repair.py semantic`, which already owns
    the broader router + lexical index + embeddings-sidecar refresh path.
    """
    try:
        scope = _post_upgrade_retrieval_scope(vault_root)
    except (OSError, ValueError) as exc:
        return {
            "scope": "retrieval-assets",
            "command": [],
            "outcome": "error",
            "message": str(exc),
        }
    return _run_repair_scope_after_upgrade(
        vault_root,
        scope,
        timeout=RETRIEVAL_ASSET_REPAIR_TIMEOUT,
    )


def _deferred_retrieval_assets_after_upgrade(vault_root: Path) -> dict:
    """Describe retrieval reconciliation deferred with dependency work."""
    try:
        scope = _post_upgrade_retrieval_scope(vault_root)
    except (OSError, ValueError) as exc:
        return {
            "scope": "retrieval-assets",
            "command": [],
            "outcome": "error",
            "message": str(exc),
        }
    command = [
        sys.executable,
        str(vault_root / ".brain-core" / "scripts" / "repair.py"),
        scope,
        "--vault",
        str(vault_root),
        "--json",
    ]
    return {
        "scope": scope,
        "command": command,
        "outcome": "deferred",
        "message": (
            "Retrieval asset repair was deferred because dependency "
            "synchronisation was disabled."
        ),
    }


# ---------------------------------------------------------------------------
# Core upgrade function
# ---------------------------------------------------------------------------

def upgrade(
    vault_root: str,
    source: str,
    *,
    force: bool = False,
    dry_run: bool = False,
    sync: Optional[bool] = None,
    sync_deps: Optional[bool] = None,
    commit_callback=None,
) -> dict:
    """Upgrade .brain-core/ in a vault from a source directory.

    Flow: backup → copy (every core file except VERSION) → compile (validate)
    → migrate → reconcile skills → cutover → write VERSION → sync definitions
    → compile the router. ``VERSION`` is the commit witness: until it is
    written every failure restores the backup, so an interrupted run resumes
    as an ordinary upgrade whose recorded migrations are skipped. On
    non-dry-run success, the upgrader also orchestrates central-runtime
    provisioning and post-upgrade retrieval-asset repair via `repair.py
    lexical` or `repair.py semantic`.

    Args:
        vault_root: Path to the vault root.
        source: Path to the source brain-core directory.
        force: Re-apply a source whose version and core already match the
            installed Core. Migrations never re-run, and a source older than
            the recorded content is refused regardless.
        dry_run: Report changes without modifying files.
        sync: Override artefact_sync preference (True=force, False=skip,
            None=follow preference).
        sync_deps: Override post-upgrade central-runtime provisioning
            (True=full conformance, False=skip, None=ensure every run).
        commit_callback: Optional local cutover callback executed after core,
            compile and migrations validate but before VERSION is written.
            Raising requests a checked core rollback.

    Returns:
        Dict with status, version info, and file change lists.
    """
    # Validate source
    if not os.path.isdir(source):
        return {"status": "error", "message": f"Source directory not found: {source}"}

    source_version_path = os.path.join(source, "VERSION")
    try:
        with open(source_version_path, "rb") as handle:
            version_bytes = handle.read()
    except OSError:
        return {"status": "error", "message": f"No VERSION file in source: {source}"}
    new_version = version_bytes.decode("utf-8", errors="replace").strip()

    target = os.path.join(vault_root, BRAIN_CORE_DIR)
    old_version = _read_version(target)

    warnings = []
    interrupted = _interrupted_upgrade_warning(vault_root, old_version)
    if interrupted is not None:
        warnings.append(interrupted)

    refusal = _refuse_unreadable_version(source_version_path, new_version, old_version, new_version)
    if refusal is None and old_version is not None:
        refusal = _refuse_unreadable_version(
            os.path.join(target, "VERSION"), old_version, old_version, new_version,
        )
    if refusal is None:
        refusal = _refuse_older_source(vault_root, old_version, new_version)
    if refusal is not None:
        if warnings:
            refusal["warnings"] = warnings
        return refusal

    # Diff
    diff = _diff_trees(source, target)

    core_matches = not (diff["files_added"] or diff["files_modified"] or diff["files_removed"])
    if old_version == new_version and not force:
        if core_matches:
            skipped = {
                "status": "skipped",
                "old_version": old_version,
                "new_version": new_version,
                "message": f"Already at {new_version}. Use --force to re-apply.",
            }
            if warnings:
                skipped["warnings"] = warnings
            return skipped
        warnings.append({
            "stage": "version_guard",
            "code": "core_mismatch",
            "message": (
                f"The installed Brain Core differs from this {new_version} source, "
                "so this upgrade replaces it: any local edits under .brain-core/ "
                "are overwritten. Keep customisations outside .brain-core/."
            ),
        })

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
    if warnings:
        result["warnings"] = warnings
    followups = _agent_skill_adapter_followups(vault_root, source, diff)
    followups.extend(_managed_approval_followups(old_version, new_version))
    if followups:
        result["followups"] = followups

    if dry_run:
        result["message"] = f"Dry run: {old_version or '(none)'} → {new_version}"

        try:
            from _skill_library import preview_core_override_reconciliation

            collapse = preview_core_override_reconciliation(
                vault_root,
                core_root=source,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            result.setdefault("warnings", []).append({
                "stage": "skill_reconciliation_preview",
                "code": "skill_reconciliation_preview_unavailable",
                "message": str(exc),
            })
        else:
            if collapse:
                result["skill_reconciliation_preview"] = [
                    {"name": name, "action": "collapse_to_core"}
                    for name in collapse
                ]

        # Preview migrations and definition sync that would run after the
        # version copy. Without this, dry-run reports only file-copy changes
        # and silently hides the migration + sync side effects (Bug B). The
        # source migrations dir is used so the preview reflects the
        # about-to-be-installed scripts, not whichever older set is still in
        # the vault.
        source_migrations_dir = os.path.join(source, "scripts", "migrations")
        precompile_preview = _pending_migrations_summary(
            vault_root, old_version, new_version,
            target=_PRECOMPILE_PATCH_TARGET,
            migrations_dir=source_migrations_dir,
        )
        if precompile_preview:
            result["precompile_patch_migrations_preview"] = precompile_preview

        migrations_preview = _pending_migrations_summary(
            vault_root, old_version, new_version,
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

    def progress(stage: str, message: str) -> None:
        _write_upgrade_progress(
            vault_root,
            old_version=old_version,
            new_version=new_version,
            dry_run=False,
            stage=stage,
            message=message,
        )

    progress(STAGE_BACKUP_BRAIN_CORE, f"Preparing upgrade {old_version or '(none)'} → {new_version}")

    # Preserve old security defaults before replacement; the existing pre-compile
    # migration context carries only inert evidence, never old executable code.
    # The template layer persists across runs because the copy replaces it
    # before an interrupted run can resume.
    from _command_interface.authorisation_migration import (
        capture_legacy_authorisation,
        load_or_persist_template_capture,
    )
    try:
        template_layer = load_or_persist_template_capture(vault_root, old_version)
        authorisation_before_upgrade = capture_legacy_authorisation(
            vault_root, old_version, template_layer,
        )
    except (OSError, ValueError, TypeError) as exc:
        return {
            "status": "error",
            "old_version": old_version,
            "new_version": new_version,
            "message": f"Upgrade refused — could not capture existing authorisation settings: {exc}",
            "rollback_verified": True,
        }

    # --- Backup .brain-core/ before modifying anything ---
    old_core_fingerprint = _tree_fingerprint(target)
    backup_dir = _backup_brain_core(target)
    backup_core = os.path.join(backup_dir, BRAIN_CORE_DIR)
    if _tree_fingerprint(backup_core) != old_core_fingerprint:
        shutil.rmtree(backup_dir, ignore_errors=True)
        return {
            "status": "error",
            "old_version": old_version,
            "new_version": new_version,
            "message": "Upgrade refused — Brain Core backup verification failed.",
            "rollback_verified": True,
        }

    precompile_snapshots: dict[str, dict] = {}
    precompile_snapshot_roots: dict[str, set[str]] = {}
    postcompile_snapshots: dict[str, dict] = {}
    postcompile_snapshot_roots: dict[str, set[str]] = {}
    ledger_path = os.path.join(vault_root, _MIGRATION_LEDGER_FILE)
    recovery_dir = os.path.join(backup_dir, "vault-state-recovery")

    def _snapshot_declared_effects(paths, snapshots):
        for path in paths:
            _snapshot_file(path, snapshots)

    def _rollback(msg, *, migration_result: Optional[dict] = None):
        restore_errors = []
        recovery_paths = []
        snapshots_verified = True
        # The ledger is restored before any content: a migration records
        # itself after its effects land, so unwinding must unrecord before it
        # undoes. An interrupted rollback then leaves the ledger at or behind
        # the content, and restartable migrations heal that on resume.
        if ledger_path in precompile_snapshots:
            report = _restore_snapshots(
                {ledger_path: precompile_snapshots[ledger_path]},
                recovery_dir=recovery_dir,
            )
            restore_errors.extend(f"migration ledger: {error}" for error in report.errors)
            recovery_paths.extend(report.recovery_paths)
        # Unwind stages in reverse order. Their scopes can overlap: the later
        # snapshot may contain an earlier migration's intermediate bytes.
        for snapshots, roots, label in (
            (postcompile_snapshots, postcompile_snapshot_roots, "post-compile state"),
            (precompile_snapshots, precompile_snapshot_roots, "pre-compile state"),
        ):
            if not snapshots:
                continue
            report = _restore_snapshots(
                snapshots,
                roots=roots,
                recovery_dir=recovery_dir,
            )
            restore_errors.extend(f"{label}: {error}" for error in report.errors)
            recovery_paths.extend(report.recovery_paths)
            # Verify each stage before its predecessor intentionally restores
            # older bytes over overlapping paths. Both snapshots cannot be
            # expected to match the final filesystem simultaneously.
            stage_verified = _snapshots_verified(snapshots, roots=roots)
            snapshots_verified = stage_verified and snapshots_verified
        try:
            _restore_brain_core(backup_dir, target)
        except BaseException as exc:
            restore_errors.append(f"Brain Core: {exc}")
        try:
            core_verified = _tree_fingerprint(target) == old_core_fingerprint
        except OSError as exc:
            restore_errors.append(f"Brain Core verification: {exc}")
            core_verified = False
        rollback_verified = not restore_errors and core_verified and snapshots_verified
        if rollback_verified:
            shutil.rmtree(backup_dir, ignore_errors=True)
        unresolved_paths = sorted(
            set(
                recovery_paths
                + ([] if rollback_verified else [backup_dir])
            )
        )
        err_result = {
            "status": "error",
            "old_version": old_version,
            "new_version": new_version,
            "message": (
                f"Upgrade rolled back — {msg}"
                if rollback_verified
                else f"Upgrade rollback is incomplete or unverified — {msg}"
            ),
            "rollback_verified": rollback_verified,
            "recovery_paths": unresolved_paths,
            "rollback": {
                "brain_core": "restored" if core_verified else "unverified",
                "vault_state": "restored" if snapshots_verified else "unverified",
                "recovery_backup": None if rollback_verified else backup_dir,
                "errors": restore_errors,
                "recovery_paths": unresolved_paths,
            },
        }
        if migration_result is not None:
            err_result["migration_result"] = migration_result
        _write_upgrade_log(vault_root, err_result)
        return err_result

    try:
        progress(STAGE_COPY_BRAIN_CORE, "Copying brain-core files into the vault")
        try:
            copied, changed_dirs = _copy_core_except_version(source, target, diff)
        except (OSError, shutil.Error) as e:
            return _rollback(f"copy failed: {e}")
        try:
            _fsync_files(copied)
            _fsync_directories(changed_dirs)
        except OSError as e:
            return _rollback(f"could not make copied files durable: {e}")

        _snapshot_tree(os.path.join(vault_root, ".brain"), precompile_snapshots, roots=precompile_snapshot_roots)
        _snapshot_tree(os.path.join(vault_root, "_Config"), precompile_snapshots, roots=precompile_snapshot_roots)
        # Snapshot the ledger even when absent, so rollback can unrecord first.
        _snapshot_file(ledger_path, precompile_snapshots)
        progress(STAGE_VALIDATE_COMPILE, "Validating the upgraded router/compiler state")
        compile_context = {
            "authorisation_before_upgrade": authorisation_before_upgrade,
            "compile_error": _validate_compile(vault_root),
            "validate_compile": lambda: _validate_compile(vault_root),
            "snapshot_file": lambda path: _snapshot_file(path, precompile_snapshots),
        }
        try:
            precompile_patches, _patch_ledger = _run_migrations(
                vault_root,
                old_version,
                new_version,
                target=_PRECOMPILE_PATCH_TARGET,
                context=compile_context,
                prepare_effects=lambda paths: _snapshot_declared_effects(
                    paths, precompile_snapshots
                ),
            )
        except MigrationResultError as e:
            return _rollback(
                f"pre-compile patch failed: {e}",
                migration_result=e.result,
            )
        except RuntimeError as e:
            return _rollback(f"pre-compile patch failed: {e}")
        if precompile_patches:
            result["precompile_patch_migrations"] = precompile_patches
        compile_err = compile_context.get("compile_error")
        if compile_err:
            return _rollback(compile_err)

    except (OSError, shutil.Error, RuntimeError) as e:
        return _rollback(f"copy failed: {e}")
    except BaseException as exc:
        rollback = _rollback(f"copy interrupted: {exc}")
        exc.add_note(rollback["message"])
        raise

    compiled_router_path = os.path.join(vault_root, ".brain", "local", "compiled-router.json")
    try:
        with open(compiled_router_path, "r", encoding="utf-8") as f:
            compiled_router = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        compiled_router = {}
        result.setdefault("warnings", []).append({
            "stage": "post_compile_snapshot_seed",
            "code": "post_compile_snapshot_seed_unavailable",
            "message": f"Could not read compiled router for post-compile rollback snapshots: {e}",
        })

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
        progress(STAGE_POST_COMPILE_MIGRATIONS, "Running post-compile migrations")
        migrations, ledger = _run_migrations(
            vault_root,
            old_version,
            new_version,
            prepare_effects=lambda paths: _snapshot_declared_effects(
                paths, postcompile_snapshots
            ),
        )
    except MigrationResultError as e:
        return _rollback(
            f"post-compile migration failed: {e}",
            migration_result=e.result,
        )
    except RuntimeError as e:
        return _rollback(f"post-compile migration failed: {e}")
    except BaseException as exc:
        rollback = _rollback(f"post-compile migration interrupted: {exc}")
        exc.add_note(rollback["message"])
        raise
    if migrations:
        result["migrations"] = migrations

    try:
        from _skill_library import (
            preview_core_override_reconciliation,
            reconcile_core_overrides,
        )

        for skill_name in preview_core_override_reconciliation(vault_root):
            _snapshot_tree(
                os.path.join(vault_root, "_Config", "Skills", skill_name),
                postcompile_snapshots,
                roots=postcompile_snapshot_roots,
            )

        reconciled_skills = reconcile_core_overrides(vault_root)
        if reconciled_skills:
            result["skill_reconciliation"] = [
                {
                    "name": item.name,
                    "action": item.action,
                    "archived_path": item.archived_path,
                    "detail": item.detail,
                }
                for item in reconciled_skills
            ]
            compile_error = _validate_compile(vault_root)
            if compile_error is not None:
                return _rollback(
                    "skill override reconciliation could not refresh the router: "
                    f"{compile_error}"
                )
    except (OSError, ValueError, RuntimeError) as exc:
        return _rollback(f"skill override reconciliation failed: {exc}")
    except BaseException as exc:
        rollback = _rollback(f"skill override reconciliation interrupted: {exc}")
        exc.add_note(rollback["message"])
        raise

    if commit_callback is not None:
        try:
            result["cutover_commit"] = commit_callback(result)
        except Exception as exc:
            rolled_back = _rollback(f"coordinated cutover commit failed: {exc}")
            external_recovery_paths = tuple(
                str(path)
                for path in getattr(exc, "recovery_paths", ())
                if isinstance(path, (str, os.PathLike))
            )
            rolled_back["cutover_commit"] = {
                "status": "error",
                "message": str(exc),
                "external_rollback_verified": getattr(
                    exc, "rollback_verified", None
                ),
                "recovery_paths": list(external_recovery_paths),
            }
            rolled_back["recovery_paths"] = sorted(
                set((*rolled_back.get("recovery_paths", ()), *external_recovery_paths))
            )
            return rolled_back
        except BaseException as exc:
            rollback = _rollback(f"coordinated cutover commit interrupted: {exc}")
            exc.add_note(rollback["message"])
            if rollback.get("recovery_paths"):
                exc.add_note(
                    "Recovery paths: " + ", ".join(rollback["recovery_paths"])
                )
            raise

    return _commit_and_reconcile(
        vault_root, target, version_bytes, result, backup_dir, progress,
        sync=sync, sync_deps=sync_deps,
    )


def _commit_and_reconcile(
    vault_root: str,
    target: str,
    version_bytes: bytes,
    result: dict,
    backup_dir: str,
    progress,
    *,
    sync: Optional[bool],
    sync_deps: Optional[bool],
) -> dict:
    """Write VERSION, then run the post-commit stages; nothing here rolls back."""
    from _command_interface.authorisation_migration import discard_template_capture

    old_version = result["old_version"]
    new_version = result["new_version"]

    progress(STAGE_VERSION_COMMIT, "Committing the upgraded Brain Core version")
    try:
        not_durable = _commit_version(target, version_bytes)
    except OSError as exc:
        # The cutover has committed, so rollback no longer applies; the next
        # run (old < new) has nothing left to migrate and finishes the commit.
        result["version_commit"] = {
            "outcome": "error",
            "message": f"could not write {BRAIN_CORE_MARKER}: {exc}",
        }
        result["status"] = "partial"
        result["message"] = (
            f"Upgrade {old_version or '(none)'} → {new_version} applied, but its "
            "VERSION could not be written; rerun the same upgrade."
        )
        shutil.rmtree(backup_dir, ignore_errors=True)
        _write_upgrade_log(vault_root, result)
        return result
    if not_durable is not None:
        result.setdefault("warnings", []).append({
            "stage": STAGE_VERSION_COMMIT,
            "code": "version_commit_not_durable",
            "message": (
                f"{BRAIN_CORE_MARKER} is written but its directory could not be "
                f"fsynced ({not_durable}); a crash before the next sync could lose "
                "the commit, and rerunning the upgrade would then resume it."
            ),
        })
    discard_template_capture(vault_root)
    shutil.rmtree(backup_dir, ignore_errors=True)

    progress(STAGE_POST_UPGRADE_SYNC, "Running post-upgrade definition sync")
    sync_info = _post_upgrade_sync(vault_root, sync=sync)
    if sync_info is not None:
        result.update(sync_info)

    # One compile after the commit: the router stamps and tracks VERSION, so
    # every compile inside the transaction window is stale once it is written.
    progress(STAGE_ROUTER_COMPILE, "Compiling the router for the committed Brain Core")
    compile_error = _validate_compile(vault_root)
    if compile_error is None:
        result["router_compile"] = {"outcome": "ok"}
    else:
        result["router_compile"] = {
            "outcome": "error",
            "message": f"Router recompilation failed after the commit: {compile_error}",
        }
        result["status"] = "partial"

    from _common._venv import RUNTIME_EXPORT_NAMES

    requirements_changed = bool(
        {os.path.join("brain_mcp", name) for name in RUNTIME_EXPORT_NAMES}
        & set(result.get("files_added", []) + result.get("files_modified", []) + result.get("files_removed", []))
    )
    progress(STAGE_DEPENDENCY_SYNC, "Provisioning central managed runtime")
    result["central_runtime"] = _ensure_central_runtime(
        Path(vault_root),
        requirements_changed=requirements_changed,
        sync_deps=sync_deps,
    )

    dependency_provisioning_allowed = sync_deps is not False

    progress(STAGE_MCP_REGISTRATION_REPAIR, "Reconciling existing current-vault MCP registrations")
    if dependency_provisioning_allowed:
        result["mcp_registration_repair"] = _repair_mcp_registration_after_upgrade(
            Path(vault_root)
        )
    else:
        result["mcp_registration_repair"] = _deferred_mcp_registration_after_upgrade(
            Path(vault_root)
        )

    progress(STAGE_MACHINE_RESOLUTION_RUNTIME, "Provisioning machine-level resolution runtime")
    result["machine_resolution_runtime"] = _ensure_machine_resolution_runtime(Path(vault_root))

    progress(STAGE_RETRIEVAL_ASSET_REPAIR, "Reconciling retrieval asset state after upgrade")
    if dependency_provisioning_allowed:
        result["retrieval_asset_repair"] = _repair_retrieval_assets_after_upgrade(
            Path(vault_root)
        )
    else:
        result["retrieval_asset_repair"] = _deferred_retrieval_assets_after_upgrade(
            Path(vault_root)
        )

    progress(STAGE_RUNTIME_READINESS, "Completing selected-Brain runtime warm-up")
    if dependency_provisioning_allowed:
        result["runtime_readiness"] = _complete_runtime_readiness(Path(vault_root))
    else:
        result["runtime_readiness"] = _deferred_runtime_readiness()

    progress(STAGE_RUNTIME_TIDINESS, "Inspecting shared-runtime cleanup candidates")
    result["runtime_orphans"] = _inspect_runtime_orphans(Path(vault_root))

    result["message"] = f"Upgraded {old_version or '(none)'} → {new_version}"
    if result["router_compile"]["outcome"] == "error":
        result["message"] += "; router recompilation requires recovery"
    if result["mcp_registration_repair"].get("outcome") in {"error", "partial", "unknown"}:
        result["status"] = "partial"
        result["message"] += "; MCP registration reconciliation requires recovery"
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
        help=(
            "Re-apply this version over a matching installed core; migrations "
            "never re-run and an older source is still refused"
        ),
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
    parser.add_argument(
        "--acknowledge-global-cli-cutover",
        action="store_true",
        help="Acknowledge every classified pre-cutover Brain named by preflight.",
    )
    parser.add_argument(
        "--exclude-stale-brain",
        action="append",
        default=[],
        metavar="BRAIN_ID",
        help="Explicitly exclude one stale registry entry from global CLI cutover scope.",
    )
    parser.add_argument(
        "--unattended",
        action="store_true",
        help="Refuse any confirmation prompt; all required acknowledgements must be explicit.",
    )
    args = parser.parse_args()

    try:
        vault_root = find_vault_root(args.vault)
    except ValueError as e:
        fatal(str(e))

    source = str(Path(args.source).resolve())
    from _bootstrap import machine_cli
    if machine_cli.approvals_present():
        try:
            receipt = machine_cli.invoke("brain.upgrade", {
                "force": args.force,
                "definition_sync": "auto" if args.sync is None else "enable" if args.sync else "disable",
                "dependency_sync": "auto" if args.sync_deps is None else "enable" if args.sync_deps else "disable",
                "acknowledge_global_cli_cutover": args.acknowledge_global_cli_cutover,
                "excluded_stale_brain_ids": sorted(set(args.exclude_stale_brain)),
            }, source_root=Path(source).parent.parent, vault=Path(vault_root), dry_run=args.dry_run)
        except (OSError, ValueError, RuntimeError) as exc:
            fatal(str(exc))
        print(json.dumps(receipt, indent=2))
        raise SystemExit(0 if receipt["status"] == "ok" else 1)
    try:
        cutover = _prepare_cli_cutover(
            Path(vault_root),
            Path(source),
            acknowledge_global_cli_cutover=True,
            excluded_stale_brain_ids=tuple(args.exclude_stale_brain),
        )
    except (OSError, ValueError) as exc:
        fatal(f"CLI cutover preflight failed: {exc}")
    if cutover is not None:
        affected = cutover["preflight"].affected_brain_ids
        if affected and not args.acknowledge_global_cli_cutover:
            print(
                "The global CLI 2 replacement leaves these registered Brains on "
                "launcher-only recovery until each is upgraded:",
                file=sys.stderr,
            )
            for brain_id in affected:
                print(f"  - {brain_id}", file=sys.stderr)
            if args.unattended or not sys.stdin.isatty():
                fatal(
                    "rerun with --acknowledge-global-cli-cutover after reviewing "
                    "the affected Brain IDs"
                )
            response = input("Proceed with this exact global CLI cutover? [y/N]: ")
            if response.casefold() != "y":
                fatal("global CLI cutover was not acknowledged")
    result = upgrade(
        str(vault_root),
        source,
        force=args.force,
        dry_run=args.dry_run,
        sync=args.sync,
        sync_deps=args.sync_deps,
        commit_callback=(
            None
            if args.dry_run or cutover is None
            else lambda _result: _commit_cli_cutover(cutover)
        ),
    )
    if cutover is not None:
        result["cutover_preflight"] = asdict(cutover["preflight"])
    if result["status"] == "ok" and not args.dry_run:
        _write_upgrade_log(str(vault_root), result)

    if args.json_output:
        print(json.dumps(result, indent=2))
        if result["status"] in {"partial", "error"}:
            sys.exit(1)
        return

    # Human-readable output
    if result["status"] == "error":
        recovery_paths = result.get("recovery_paths", ())
        if recovery_paths:
            info("Recovery paths:")
            for path in recovery_paths:
                info(f"  - {path}")
        fatal(result["message"])

    if result["status"] == "skipped":
        info(result["message"])
        for warning in result.get("warnings", []):
            info(f"Warning: {warning['message']}")
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

    cutover_commit = result.get("cutover_commit")
    cleanup_recovery_paths = (
        cutover_commit.get("cleanup_recovery_paths", [])
        if isinstance(cutover_commit, dict)
        else []
    )
    if cleanup_recovery_paths:
        info("")
        info(
            "The Brain/CLI cutover committed, but old CLI backup material "
            "still requires cleanup:"
        )
        for path in cleanup_recovery_paths:
            info(f"  - {path}")

    followups = result.get("followups", [])
    if followups:
        info("")
        heading = "Follow-up after upgrade:" if args.dry_run else "Recommended follow-up:"
        info(heading)
        for followup in followups:
            info(f"  {followup['message']}")
            info(f"  Run: {_join_argv(followup['command'])}")

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
                info(f"  {_format_sync_updated(item)}")
            for item in sync_preview.get("warnings", []):
                info(f"  {_format_sync_warning(item)}")
            for item in sync_preview.get("errors", []):
                info(f"  {_format_sync_error(item)}")

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
                command = _join_argv([
                    sys.executable,
                    str(vault_root / VENV_HELPER_REL),
                    "ensure",
                    "--vault",
                    str(vault_root),
                    "--launcher",
                    sys.executable,
                ])
                info(f"  Retry: {command}")
            if runtime.get("legacy_vault_venv"):
                info(
                    f"  Legacy vault venv detected at {runtime['legacy_vault_venv']}. "
                    f"To migrate MCP config to the central runtime, run:"
                )
                info(
                    "    "
                    + _join_argv([
                        sys.executable,
                        str(vault_root / ".brain-core" / "scripts" / "configure.py"),
                        "mcp",
                        "--vault",
                        str(vault_root),
                        "--workspace",
                        str(vault_root),
                        "--client",
                        "all",
                    ])
                )
            print(file=sys.stderr)

        cli_refresh = result.get("cli_refresh")
        if cli_refresh is not None:
            outcome = cli_refresh["outcome"]
            if outcome == "refreshed":
                for target in cli_refresh.get("refreshed", []):
                    info(f"Refreshed brain CLI: {target}")
            elif outcome == "error":
                for err in cli_refresh.get("errors", []):
                    info(f"Could not refresh brain CLI at {err['target']}: {err['message']}")
            print(file=sys.stderr)

        mcp_registration_repair = result.get("mcp_registration_repair")
        if isinstance(mcp_registration_repair, dict) and mcp_registration_repair.get("outcome") in {"error", "partial", "unknown"}:
            info(f"MCP registration reconciliation failed: {mcp_registration_repair['message']}")
            info("  Inspect: brain mcp migrate --dry-run --json")
            info("  After resolving conflicts: brain mcp migrate --json")
            info(f"  Then: brain mcp repair --vault {shlex.quote(str(vault_root))} --request-json '{{\"breadth\":\"brain\"}}' --json")
        if (
            isinstance(mcp_registration_repair, dict)
            and mcp_registration_repair.get("outcome") == "deferred"
        ):
            info(mcp_registration_repair["message"])
            info(f"  Run: {_join_argv(mcp_registration_repair['command'])}")
            print(file=sys.stderr)

        retrieval_asset_repair = result.get("retrieval_asset_repair")
        if retrieval_asset_repair is not None:
            command = _join_argv(retrieval_asset_repair["command"])
            scope = retrieval_asset_repair["scope"]
            if retrieval_asset_repair["outcome"] == "ok":
                if scope == "semantic":
                    info("Semantic retrieval asset state reconciled after upgrade.")
                else:
                    info("Lexical retrieval state reconciled after upgrade.")
            elif retrieval_asset_repair["outcome"] == "deferred":
                info(retrieval_asset_repair["message"])
                if command:
                    info(f"  Run: {command}")
            else:
                if scope == "semantic":
                    info(f"Semantic retrieval asset repair after upgrade failed: {retrieval_asset_repair['message']}")
                elif scope == "lexical":
                    info(f"Lexical retrieval repair after upgrade failed: {retrieval_asset_repair['message']}")
                else:
                    info(f"Retrieval asset repair after upgrade failed: {retrieval_asset_repair['message']}")
                if command:
                    info(f"  Retry: {command}")
            print(file=sys.stderr)

        runtime_readiness = result.get("runtime_readiness")
        if runtime_readiness is not None:
            if runtime_readiness.get("outcome") == "ok":
                info("Selected Brain runtime warm-up completed; session.start is ready.")
            elif runtime_readiness.get("outcome") == "deferred":
                info(runtime_readiness["message"])
                info(f"  Run: {_join_argv(runtime_readiness['command'])}")
            else:
                info(
                    "Selected Brain runtime warm-up is incomplete: "
                    f"{runtime_readiness.get('message', 'unknown readiness error')}"
                )
                info("  Retry: brain runtime warmup")
                info("  Inspect: brain runtime status")
            print(file=sys.stderr)

        runtime_orphans = result.get("runtime_orphans")
        if runtime_orphans is not None:
            if runtime_orphans.get("outcome") == "follow_up":
                info(runtime_orphans["message"])
                info(f"  Preview: {_join_argv(runtime_orphans['dry_run_command'])}")
                info(f"  Remove: {_join_argv(runtime_orphans['remove_command'])}")
            elif runtime_orphans.get("outcome") == "error":
                info(f"Shared-runtime tidiness is unknown: {runtime_orphans['message']}")
                info("  Inspect: brain runtime remove-orphans --dry-run")
            print(file=sys.stderr)

        for warning in result.get("warnings", []):
            message = warning.get("message")
            if message:
                info(f"Warning: {message}")
        if result.get("warnings"):
            print(file=sys.stderr)

        version_commit = result.get("version_commit")
        if version_commit is not None and version_commit["outcome"] == "error":
            info(f"The upgrade applied but its VERSION was not committed: {version_commit['message']}")
            info("  Rerun the same upgrade to finish.")
            print(file=sys.stderr)

        router_compile = result.get("router_compile")
        if router_compile is not None and router_compile["outcome"] == "error":
            info(router_compile["message"])
            info("  Repair: brain runtime refresh-router --json")
            print(file=sys.stderr)

        # Sync results
        if "sync_error" in result:
            info(f"Definition sync failed: {result['sync_error']}")
            info("Run sync_definitions.py manually after investigating.")
        elif "sync_result" in result:
            sr = result["sync_result"]
            if sr.get("updated"):
                info("Definition sync:")
                for item in sr["updated"]:
                    info(f"  {_format_sync_updated(item)}")
            if sr.get("warnings"):
                info("Conflicts (local changes differ from upstream — manual review needed):")
                for item in sr["warnings"]:
                    info(f"  {_format_sync_warning(item)}")
            if sr.get("errors"):
                info("Definition sync errors (definitions left unchanged — investigate):")
                for item in sr["errors"]:
                    info(f"  {_format_sync_error(item)}")


    if result["status"] == "partial":
        sys.exit(1)


if __name__ == "__main__":
    main()
