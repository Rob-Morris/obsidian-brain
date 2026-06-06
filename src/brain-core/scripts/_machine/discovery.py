"""Machine-level Brain discovery and derived registry sync.

`vault_registry` is the user-curated bootstrap source the shell reads first.
`brains.json` is a Python-owned derived machine registry used after launcher-safe
handoff succeeds. The shell may consult it as a secondary fallback only when no
source Brain can be found from the curated vault registry.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any

from _common import config_home, is_vault_root, safe_write_json
from _common._file_lock import exclusive_file_lock
import vault_registry


MACHINE_REGISTRY_VERSION = 1
DEFAULT_MACHINE_REGISTRY_BLOCK_MESSAGE = "machine-registry state could not be safely interpreted; leaving brains.json untouched"
MACHINE_REGISTRY_BLOCK_MESSAGES = {
    "newer-version": "newer machine-registry schema detected; leaving brains.json untouched",
}


def _canonical_brain_path(path: str | Path) -> Path:
    expanded = os.path.expanduser(str(path))
    if not os.path.isabs(expanded):
        expanded = os.path.abspath(expanded)
    return Path(os.path.realpath(expanded))


def machine_registry_path() -> Path:
    return config_home() / "brain" / "brains.json"


@contextlib.contextmanager
def _locked_machine_registry():
    """Serialise load-modify-save for the derived machine registry."""
    lock_path = Path(str(machine_registry_path()) + ".lock")
    with exclusive_file_lock(lock_path):
        yield


def _empty_machine_registry_state(path: Path) -> dict[str, Any]:
    return {
        "blocked": False,
        "blocked_reason": None,
        "brains": [],
        "exists": path.is_file(),
        "malformed": False,
        "path": str(path),
        "stale_machine_registry_entries": [],
        "version": None,
    }


def _block_machine_registry(
    state: dict[str, Any],
    *,
    reason: str,
    message: str,
) -> dict[str, Any]:
    print(message, file=sys.stderr)
    state["blocked"] = True
    state["blocked_reason"] = reason
    return state


def _parse_machine_registry(data: Any, path: Path) -> dict[str, Any]:
    state = _empty_machine_registry_state(path)
    state["exists"] = True
    if not isinstance(data, dict):
        return _block_machine_registry(
            state,
            reason="invalid-root",
            message=f"_machine.discovery: invalid root object in {path}; refusing to rewrite unknown schema",
        )

    version = data.get("version")
    if version is None:
        return _block_machine_registry(
            state,
            reason="missing-version",
            message=f"_machine.discovery: missing version in {path}; refusing to rewrite unknown schema",
        )
    if not isinstance(version, int):
        return _block_machine_registry(
            state,
            reason="invalid-version",
            message=f"_machine.discovery: invalid version in {path}; refusing to rewrite unknown schema",
        )
    state["version"] = version
    if version > MACHINE_REGISTRY_VERSION:
        return _block_machine_registry(
            state,
            reason="newer-version",
            message=(
                f"_machine.discovery: refusing to rewrite newer machine registry schema "
                f"v{version} at {path}"
            ),
        )
    if version != MACHINE_REGISTRY_VERSION:
        return _block_machine_registry(
            state,
            reason="unsupported-version",
            message=(
                f"_machine.discovery: unsupported machine registry schema v{version} at {path}; "
                "refusing to rewrite"
            ),
        )

    # Keep this parser aligned with the shell-side minimal brains.json scan in
    # cli/brain: both understand only the path/helper-discovery subset needed
    # before Python handoff can take over.
    brains = data.get("brains")
    if not isinstance(brains, list):
        state["malformed"] = True
        return state

    for entry in brains:
        if not isinstance(entry, dict):
            state["malformed"] = True
            continue
        path_value = entry.get("path")
        if not isinstance(path_value, str) or not path_value:
            state["malformed"] = True
            continue
        alias = entry.get("alias")
        if alias is not None and (not isinstance(alias, str) or not alias):
            state["malformed"] = True
            alias = None
        machine_path = _canonical_brain_path(path_value)
        record = {"alias": alias, "path": str(machine_path)}
        if is_vault_root(machine_path):
            state["brains"].append(record)
        else:
            state["stale_machine_registry_entries"].append(record)
    return state


def _load_machine_registry() -> dict[str, Any]:
    path = machine_registry_path()
    state = _empty_machine_registry_state(path)
    if not path.is_file():
        return state

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        return _block_machine_registry(
            state,
            reason="unreadable",
            message=f"_machine.discovery: failed to read {path}: {exc}",
        )

    try:
        data = json.loads(text)
    except ValueError as exc:
        return _block_machine_registry(
            state,
            reason="invalid-json",
            message=f"_machine.discovery: failed to parse {path}: {exc}",
        )

    return _parse_machine_registry(data, path)


def _machine_registry_view(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "blocked": state["blocked"],
        "blocked_reason": state["blocked_reason"],
        "path": state["path"],
        "version": state["version"],
    }


def _brain_entry(path: Path, *, alias: str | None = None, source: str) -> dict[str, Any]:
    return {
        "alias": alias,
        "path": str(path),
        "sources": [source],
        "stale": False,
    }


def _merge_brain_entry(
    discovered: dict[str, dict[str, Any]],
    path: Path,
    *,
    alias: str | None,
    source: str,
) -> None:
    record = discovered.setdefault(
        str(path),
        _brain_entry(
            path,
            alias=alias,
            source=source,
        ),
    )
    if source not in record["sources"]:
        record["sources"].append(source)
    if record["alias"] is None and alias is not None:
        record["alias"] = alias


def _render_machine_registry(brains: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "version": MACHINE_REGISTRY_VERSION,
        "brains": [
            {
                "alias": brain.get("alias"),
                "path": brain["path"],
            }
            for brain in brains
        ],
    }


def _machine_registry_backup_path(path: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return path.with_name(f"{path.name}.bak.{stamp}")


def _backup_machine_registry(path: Path) -> str | None:
    if not path.is_file():
        return None
    backup_path = _machine_registry_backup_path(path)
    shutil.copy2(path, backup_path)
    return str(backup_path)


def sync_machine_registry(brains: list[dict[str, Any]]) -> dict[str, Any]:
    """Synchronise the derived machine registry from discovered Brains."""
    target_state = _render_machine_registry(brains)
    target_brains = target_state["brains"]
    path = machine_registry_path()

    with _locked_machine_registry():
        current = _load_machine_registry()
        if current["blocked"]:
            return {
                "backup_path": None,
                "blocked": True,
                "blocked_reason": current["blocked_reason"],
                "brains_count": len(current["brains"]),
                "changed": False,
                "malformed_rewritten": False,
                "path": str(path),
                "stale_machine_registry_entries": current["stale_machine_registry_entries"],
                "version": current["version"],
            }

        malformed_current = current["malformed"]
        stale_entries_present = bool(current["stale_machine_registry_entries"])
        registry_differs = current["brains"] != target_brains
        missing_file_with_brains = not current["exists"] and bool(target_brains)
        changed = bool(
            malformed_current
            or stale_entries_present
            or registry_differs
            or missing_file_with_brains
        )

        backup_path = None
        if changed:
            # brains.json is derived cache state. Keep a backup only for
            # malformed-v1 recovery; routine stale-entry pruning and ordinary
            # reconciliation should stay cheap and automatic.
            if current["exists"] and malformed_current:
                backup_path = _backup_machine_registry(path)
            safe_write_json(path, target_state)

    return {
        "backup_path": backup_path,
        "blocked": False,
        "blocked_reason": None,
        "brains_count": len(target_brains),
        "changed": changed,
        "malformed_rewritten": malformed_current,
        "path": str(path),
        "stale_machine_registry_entries": current["stale_machine_registry_entries"],
        "version": MACHINE_REGISTRY_VERSION,
    }


def discover_brains(
    *,
    current_vault: str | Path | None = None,
) -> dict[str, Any]:
    """Discover candidate Brains from the machine registry and seed roots."""
    discovered: dict[str, dict[str, Any]] = {}
    machine_registry = _load_machine_registry()

    for entry in machine_registry["brains"]:
        machine_path = _canonical_brain_path(entry["path"])
        _merge_brain_entry(
            discovered,
            machine_path,
            alias=entry.get("alias"),
            source="machine_registry",
        )

    if current_vault is not None:
        current_path = _canonical_brain_path(current_vault)
        if is_vault_root(current_path):
            _merge_brain_entry(
                discovered,
                current_path,
                alias=None,
                source="current",
            )

    stale_registry_entries: list[dict[str, str]] = []
    for entry in vault_registry.list_entries():
        if entry.get("kind") != vault_registry.TYPE_LOCAL:
            continue
        registry_path = _canonical_brain_path(entry["value"])
        if entry["stale"] or not is_vault_root(registry_path):
            stale_registry_entries.append(
                {"alias": entry["alias"], "path": str(registry_path)}
            )
            continue

        _merge_brain_entry(
            discovered,
            registry_path,
            alias=entry["alias"],
            source="vault_registry",
        )

    brains = sorted(
        discovered.values(),
        key=lambda item: ((item["alias"] or ""), item["path"]),
    )
    stale_registry_entries.sort(key=lambda item: (item["alias"], item["path"]))
    machine_registry["stale_machine_registry_entries"].sort(
        key=lambda item: ((item["alias"] or ""), item["path"])
    )
    return {
        "brains": brains,
        "machine_registry_view": _machine_registry_view(machine_registry),
        "stale_machine_registry_entries": machine_registry["stale_machine_registry_entries"],
        "stale_registry_entries": stale_registry_entries,
    }
