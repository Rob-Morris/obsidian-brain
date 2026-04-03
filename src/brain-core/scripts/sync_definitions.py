#!/usr/bin/env python3
"""
sync_definitions.py — Artefact library definition sync.

Compares artefact library source files (.brain-core/artefact-library/)
against installed vault definitions (_Config/) using three-way hash
comparison, and updates safely.

Chained after upgrade: upgrade.py → sync_definitions.py → compile_router.py.

Returns warnings for conflicts/collisions — callers decide how to act
(agents can mediate interactively, deterministic pipelines can use force).

Usage:
  python3 sync_definitions.py [--vault /path] [--dry-run] [--force] [--types t1,t2] [--json]
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from _common import find_vault_root, read_version
from compile_router import hash_file


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BRAIN_CORE_DIR = ".brain-core"
LIBRARY_DIR = os.path.join(BRAIN_CORE_DIR, "artefact-library")
TRACKING_PATH = os.path.join(".brain", "tracking.json")
PREFERENCES_PATH = os.path.join(".brain", "preferences.json")
CLASSIFICATIONS = ("living", "temporal")

def _tracking_seed() -> dict:
    return {"schema_version": 1, "installed": {}}


# ---------------------------------------------------------------------------
# Manifest parser (hand-rolled — no PyYAML dependency)
# ---------------------------------------------------------------------------

def parse_manifest(path: str) -> Optional[dict]:
    """Parse a manifest.yaml file into a dict.

    Returns None if the file doesn't exist or is malformed.
    Handles the fixed manifest schema only:
      files:
        <role>:
          source: <filename>
          target: <vault-relative-path>
      folders:
        - <path>
      router_trigger: "<text>"
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return None

    result: dict = {"files": {}, "folders": []}
    current_section = None  # "files" | "folders" | None
    current_role = None

    for raw_line in lines:
        line = raw_line.rstrip("\n")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Top-level keys
        if not line[0].isspace():
            if stripped == "files:":
                current_section = "files"
                current_role = None
            elif stripped == "folders:":
                current_section = "folders"
                current_role = None
            elif stripped.startswith("router_trigger:"):
                current_section = None
                current_role = None
                value = stripped[len("router_trigger:"):].strip().strip('"')
                result["router_trigger"] = value
            else:
                current_section = None
                current_role = None
            continue

        # Inside files section
        if current_section == "files":
            # Role line (2-space indent): "  taxonomy:"
            if line.startswith("  ") and not line.startswith("    "):
                role = stripped.rstrip(":")
                current_role = role
                result["files"][role] = {}
            # Property line (4-space indent): "    source: taxonomy.md"
            elif line.startswith("    ") and current_role:
                if ":" in stripped:
                    key, _, value = stripped.partition(":")
                    result["files"][current_role][key.strip()] = value.strip()

        # Inside folders section
        elif current_section == "folders":
            if stripped.startswith("- "):
                result["folders"].append(stripped[2:].strip())

    # Validate: must have at least one file entry
    if not result["files"]:
        return None

    return result


# ---------------------------------------------------------------------------
# Library discovery
# ---------------------------------------------------------------------------

def discover_library_types(vault_root: str) -> list[dict]:
    """Walk the artefact library and return type info for each manifest found."""
    library_root = os.path.join(vault_root, LIBRARY_DIR)
    types = []

    for classification in CLASSIFICATIONS:
        class_dir = os.path.join(library_root, classification)
        if not os.path.isdir(class_dir):
            continue
        for name in sorted(os.listdir(class_dir)):
            type_dir = os.path.join(class_dir, name)
            if not os.path.isdir(type_dir):
                continue
            manifest_path = os.path.join(type_dir, "manifest.yaml")
            manifest = parse_manifest(manifest_path)
            if manifest is None:
                continue
            types.append({
                "type_key": f"{classification}/{name}",
                "classification": classification,
                "name": name,
                "manifest": manifest,
                "library_dir": type_dir,
            })

    return types


# ---------------------------------------------------------------------------
# Tracking and preferences I/O
# ---------------------------------------------------------------------------

def load_tracking(vault_root: str) -> dict:
    """Read .brain/tracking.json, returning seed structure if missing."""
    path = os.path.join(vault_root, TRACKING_PATH)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "installed" not in data:
            return _tracking_seed()
        return data
    except (OSError, json.JSONDecodeError):
        return _tracking_seed()


def save_tracking(vault_root: str, tracking: dict) -> None:
    """Write .brain/tracking.json."""
    path = os.path.join(vault_root, TRACKING_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(tracking, f, indent=2)
        f.write("\n")


def load_preferences(vault_root: str) -> dict:
    """Read .brain/preferences.json, returning {} if missing."""
    path = os.path.join(vault_root, PREFERENCES_PATH)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


# ---------------------------------------------------------------------------
# Three-way comparison
# ---------------------------------------------------------------------------

def compute_file_status(
    upstream_path: str,
    installed_entry: Optional[dict],
    vault_path: str,
) -> dict:
    """Compare upstream, installed, and local file states.

    Returns dict with keys: action, upstream_hash, installed_from_hash,
    local_hash, target (vault-relative).
    """
    upstream_hash = hash_file(upstream_path)
    target_exists = os.path.isfile(vault_path)
    local_hash = hash_file(vault_path) if target_exists else None
    installed_from_hash = installed_entry.get("source_hash") if installed_entry else None

    # No tracking entry — bootstrap or collision
    if installed_from_hash is None:
        if not target_exists:
            action = "new"
        elif local_hash == upstream_hash:
            action = "baseline"  # silent bootstrap — hashes match
        else:
            action = "collision"
    else:
        upstream_changed = upstream_hash != installed_from_hash
        local_changed = local_hash != installed_from_hash if local_hash else True

        if not upstream_changed:
            action = "skip"
        elif not local_changed:
            action = "update"
        else:
            action = "conflict"

    return {
        "action": action,
        "upstream_hash": upstream_hash,
        "installed_from_hash": installed_from_hash,
        "local_hash": local_hash,
    }


# ---------------------------------------------------------------------------
# Core sync
# ---------------------------------------------------------------------------

def load_exclude_set(prefs: dict) -> set:
    """Read artefact_sync_exclude list from preferences as a set."""
    raw = prefs.get("artefact_sync_exclude", [])
    return set(raw) if isinstance(raw, list) else set()


def _make_tracking_entry(upstream_hash: str, target: str) -> dict:
    """Build a tracking file entry."""
    return {"source_hash": upstream_hash, "target": target}


def _is_type_installed(vault_root: str, type_files_tracking: dict, manifest: dict) -> bool:
    """Check whether a type is installed (has tracking entries or target files).

    A type is considered installed if either:
    - It has tracking entries from a prior sync, OR
    - At least one of its target files already exists in the vault
      (e.g. manually placed before tracking was introduced).
    """
    if type_files_tracking:
        return True
    return any(
        os.path.isfile(os.path.join(vault_root, fi["target"]))
        for fi in manifest["files"].values()
    )


def sync_definitions(
    vault_root: str,
    *,
    dry_run: bool = False,
    force: bool = False,
    types: Optional[list[str]] = None,
) -> dict:
    """Sync artefact library definitions to vault _Config/ files.

    Returns a structured result with updated, skipped, and warnings lists.
    Warnings indicate conflicts/collisions that need caller attention.
    Use force=True to overwrite despite warnings.
    """
    prefs = load_preferences(vault_root)
    preference = prefs.get("artefact_sync", "auto")
    brain_core_version = read_version(vault_root) or "unknown"

    if preference == "skip":
        return {
            "status": "skipped",
            "brain_core_version": brain_core_version,
            "preference": "skip",
            "updated": [],
            "skipped": [],
            "warnings": [],
            "errors": [],
            "dry_run": dry_run,
            "message": "Sync disabled (artefact_sync: skip).",
        }
    tracking = load_tracking(vault_root)
    library_types = discover_library_types(vault_root)
    exclude_set = load_exclude_set(prefs)

    if types is not None:
        type_set = set(types)
        library_types = [t for t in library_types if t["type_key"] in type_set]

    updated = []
    skipped = []
    warnings = []
    errors = []
    now = datetime.now(timezone.utc).isoformat()

    for type_info in library_types:
        type_key = type_info["type_key"]
        manifest = type_info["manifest"]
        lib_dir = type_info["library_dir"]

        type_tracking = tracking["installed"].get(type_key, {})
        type_files_tracking = type_tracking.get("files", {})

        if not _is_type_installed(vault_root, type_files_tracking, manifest):
            continue

        new_type_files = {}
        type_changed = False

        for role, file_info in manifest["files"].items():
            source_path = os.path.join(lib_dir, file_info["source"])
            target_rel = file_info["target"]
            vault_path = os.path.join(vault_root, target_rel)

            if not os.path.isfile(source_path):
                errors.append({
                    "type": type_key,
                    "role": role,
                    "error": f"Library source missing: {file_info['source']}",
                })
                continue

            installed_entry = type_files_tracking.get(role)

            # Check exclusion list
            exclude_key = f"{type_key}/{role}"
            if exclude_key in exclude_set:
                skipped.append({
                    "type": type_key,
                    "role": role,
                    "target": target_rel,
                    "reason": "excluded",
                })
                if installed_entry:
                    new_type_files[role] = dict(installed_entry)
                continue

            status = compute_file_status(source_path, installed_entry, vault_path)

            if status["action"] == "skip":
                skipped.append({
                    "type": type_key,
                    "role": role,
                    "target": target_rel,
                    "reason": "in_sync" if status["local_hash"] == status["upstream_hash"]
                    else "user_customised",
                })
                new_type_files[role] = installed_entry if installed_entry else _make_tracking_entry(
                    status["upstream_hash"], target_rel,
                )
                continue

            if status["action"] == "baseline":
                new_type_files[role] = _make_tracking_entry(
                    status["upstream_hash"], target_rel,
                )
                type_changed = True
                skipped.append({
                    "type": type_key,
                    "role": role,
                    "target": target_rel,
                    "reason": "baseline_established",
                })
                continue

            # Apply update: auto-update safe changes, or force all changes
            should_apply = (
                (status["action"] == "update" and preference == "auto")
                or force
            )
            if should_apply:
                if not dry_run:
                    os.makedirs(os.path.dirname(vault_path), exist_ok=True)
                    shutil.copy2(source_path, vault_path)
                new_type_files[role] = _make_tracking_entry(
                    status["upstream_hash"], target_rel,
                )
                type_changed = True
                updated.append({
                    "type": type_key,
                    "role": role,
                    "target": target_rel,
                    "action": status["action"],
                })
                continue

            warnings.append({
                "type": type_key,
                "role": role,
                "target": target_rel,
                "action": status["action"],
                "upstream_hash": status["upstream_hash"],
                "local_hash": status["local_hash"],
            })
            if installed_entry:
                new_type_files[role] = dict(installed_entry)

        # Update tracking — only rewrite entry when something changed
        if new_type_files:
            if type_changed or type_key not in tracking["installed"]:
                tracking["installed"][type_key] = {
                    "brain_core_version": brain_core_version,
                    "installed_at": now,
                    "files": new_type_files,
                }
            else:
                tracking["installed"][type_key]["files"] = new_type_files

        for folder in manifest.get("folders", []):
            folder_path = os.path.join(vault_root, folder)
            if not dry_run:
                os.makedirs(folder_path, exist_ok=True)

    if not dry_run:
        save_tracking(vault_root, tracking)

    status_val = "warnings" if warnings else "ok"
    parts = []
    if updated:
        parts.append(f"{len(updated)} updated")
    if skipped:
        parts.append(f"{len(skipped)} skipped")
    if warnings:
        parts.append(f"{len(warnings)} warning{'s' if len(warnings) != 1 else ''}")
    if errors:
        parts.append(f"{len(errors)} error{'s' if len(errors) != 1 else ''}")
    message = ", ".join(parts) if parts else "Nothing to do."

    return {
        "status": status_val,
        "brain_core_version": brain_core_version,
        "preference": preference,
        "updated": updated,
        "skipped": skipped,
        "warnings": warnings,
        "errors": errors,
        "dry_run": dry_run,
        "message": message,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync artefact library definitions to vault.",
        epilog=(
            "Examples:\n"
            "  python3 sync_definitions.py --json\n"
            "      Sync all types, JSON output\n\n"
            "  python3 sync_definitions.py --dry-run\n"
            "      Preview what would change\n\n"
            "  python3 sync_definitions.py --force\n"
            "      Overwrite despite conflicts\n\n"
            "  python3 sync_definitions.py --types temporal/cookies,living/wiki\n"
            "      Sync specific types only\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--vault", help="Path to vault root (default: auto-detect)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without modifying")
    parser.add_argument("--force", action="store_true", help="Overwrite despite conflicts")
    parser.add_argument("--types", help="Comma-separated type keys to sync")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    try:
        vault_root = str(find_vault_root(args.vault))
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    type_list = args.types.split(",") if args.types else None
    result = sync_definitions(
        vault_root, dry_run=args.dry_run, force=args.force, types=type_list,
    )

    if args.json_output:
        print(json.dumps(result, indent=2))
    else:
        _print_human(result)


def _print_human(result: dict) -> None:
    """Print sync result in human-readable format."""
    if result["status"] == "skipped":
        print(f"  {result['message']}", file=sys.stderr)
        return

    print(f"  {result['message']}", file=sys.stderr)

    if result.get("updated"):
        print(f"  Updated ({len(result['updated'])}):", file=sys.stderr)
        for item in result["updated"]:
            print(f"    ~ {item['type']} / {item['role']} → {item['target']}", file=sys.stderr)

    if result.get("warnings"):
        print(f"  Warnings ({len(result['warnings'])}):", file=sys.stderr)
        for item in result["warnings"]:
            print(
                f"    ? {item['type']} / {item['role']} ({item['action']}) → {item['target']}",
                file=sys.stderr,
            )

    if result.get("errors"):
        print(f"  Errors ({len(result['errors'])}):", file=sys.stderr)
        for item in result["errors"]:
            print(f"    ! {item['type']} / {item['role']}: {item['error']}", file=sys.stderr)

    if result["dry_run"]:
        print("\n  (dry run — no files modified)", file=sys.stderr)


if __name__ == "__main__":
    main()
