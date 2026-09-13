#!/usr/bin/env python3
"""
migrate_to_0_50_0.py - Recursive living owner-folder projection migration.

This migration aligns pre-recursive living artefact folders with the recursive
owner-chain runtime. It is intentionally narrow:

1. Build a living artefact index from canonical ``key:`` and ``parent:`` fields.
2. Backfill a missing ``parent:`` only when the immediate containing owner
   folder uniquely resolves to one living owner and does not conflict with an
   existing canonical parent.
3. Recompute every living artefact path through ``resolve_folder`` and move the
   resulting flat-to-nested relocation set through ``move_and_update_links``.

Dry-run enumerates invalid parent chains without mutating the vault. Apply mode
aborts before any write if duplicate keys, invalid chains, or move collisions
are found. Rollback for applied migrations is provided by the upgrade runner's
snapshot context; this script does not manage its own backup.

The filename/version assume recursive projection ships in the next pre-1.0
minor release. Rob must confirm the final release version before the versioned
commit; do not bump ``VERSION`` here independently.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, replace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from _common import (
    ParentChainError,
    PartialApplyError,
    STATUS_FOLDER_PREFIX,
    apply_terminal_status_folder,
    artefact_type_prefix,
    canonical_living_artefact_key,
    finalize_living_artefact_index,
    find_vault_root,
    is_archived_path,
    is_valid_key,
    iter_artefact_paths,
    iter_vault_md_files,
    living_artefact_index_entry,
    load_compiled_router,
    make_artefact_key,
    normalize_artefact_key,
    parse_frontmatter,
    safe_write,
    serialize_frontmatter,
    resolve_folder,
)
from rename import move_and_update_links, preflight_move_set


VERSION = "0.50.0"


@dataclass(frozen=True)
class LivingRecord:
    artefact_key: str
    rel_path: str
    artefact: dict
    fields: dict
    body: str


@dataclass(frozen=True)
class MigrationPlan:
    status: str
    version: str
    vault_root: str
    router: dict
    records: tuple[LivingRecord, ...]
    planned_router: dict
    parent_updates: tuple[dict, ...]
    moves: tuple[dict, ...]
    invalid_chains: tuple[dict, ...]
    collisions: tuple[dict, ...]
    cyclic_moves: tuple[dict, ...]
    conflicts: tuple[dict, ...]
    duplicate_keys: tuple[dict, ...]
    read_errors: tuple[dict, ...]
    keyless_living: tuple[dict, ...]
    error: str | None = None

    def blockers(self):
        return [
            name
            for name in (
                "duplicate_keys",
                "read_errors",
                "keyless_living",
                "invalid_chains",
                "collisions",
                "cyclic_moves",
            )
            if getattr(self, name)
        ]

    def to_result(self, *, dry_run):
        result = {
            "status": self.status,
            "version": self.version,
            "dry_run": dry_run,
            "parent_updates": list(self.parent_updates),
            "moves": list(self.moves),
            "invalid_chains": list(self.invalid_chains),
            "collisions": list(self.collisions),
            "cyclic_moves": list(self.cyclic_moves),
            "conflicts": list(self.conflicts),
            "duplicate_keys": list(self.duplicate_keys),
            "read_errors": list(self.read_errors),
            "keyless_living": list(self.keyless_living),
        }
        if self.error:
            result["error"] = self.error
        return result


def _with_status_stripped(folder):
    """Return (ownership_folder, status_segment_or_none)."""
    tail = os.path.basename(folder)
    if tail.startswith(STATUS_FOLDER_PREFIX):
        return os.path.dirname(folder), tail
    return folder, None


def _insert_parent_field(fields, parent_key):
    """Return fields with ``parent`` placed after ``key`` when possible."""
    ordered = {}
    inserted = False
    for key, value in fields.items():
        if key == "parent":
            continue
        ordered[key] = value
        if key == "key":
            ordered["parent"] = parent_key
            inserted = True
    if not inserted:
        ordered["parent"] = parent_key
    return ordered


def _read_living_records(vault_root, router):
    records = []
    duplicate_keys = []
    read_errors = []
    keyless_living = []
    seen = {}

    for artefact in router.get("artefacts", []):
        if artefact.get("classification") != "living":
            continue
        for rel_path in sorted(iter_artefact_paths(vault_root, artefact, include_status_folders=True)):
            if is_archived_path(rel_path):
                continue
            abs_path = os.path.join(vault_root, rel_path)
            try:
                with open(abs_path, "r", encoding="utf-8") as handle:
                    content = handle.read()
            except (OSError, UnicodeDecodeError) as exc:
                read_errors.append({
                    "path": rel_path,
                    "error": str(exc),
                })
                continue
            fields, body = parse_frontmatter(content)
            if fields.get("type") != artefact.get("frontmatter_type"):
                continue
            artefact_key = canonical_living_artefact_key(artefact, fields)
            if not artefact_key:
                keyless_living.append({
                    "path": rel_path,
                    "type": fields.get("type"),
                    "reason": "missing or invalid canonical key",
                })
                continue
            if artefact_key in seen:
                duplicate_keys.append({
                    "key": artefact_key,
                    "paths": [seen[artefact_key], rel_path],
                })
                continue
            seen[artefact_key] = rel_path
            records.append(LivingRecord(
                artefact_key=artefact_key,
                rel_path=rel_path,
                artefact=artefact,
                fields=fields,
                body=body,
            ))

    return records, duplicate_keys, read_errors, keyless_living


def _whole_vault_read_errors(vault_root):
    """Return markdown files that cannot be read before migration writes."""
    errors = []
    for dirpath, fname in iter_vault_md_files(vault_root):
        fpath = os.path.join(dirpath, fname)
        rel_path = os.path.relpath(fpath, vault_root)
        try:
            with open(fpath, "r", encoding="utf-8") as handle:
                handle.read()
        except (OSError, UnicodeDecodeError) as exc:
            errors.append({
                "path": rel_path,
                "error": str(exc),
            })
    return errors


def _merge_read_errors(*groups):
    merged = {}
    for group in groups:
        for item in group:
            merged.setdefault(item["path"], item)
    return list(merged.values())


def _router_with_records(router, records):
    entries = {
        record.artefact_key: living_artefact_index_entry(
            record.artefact,
            record.rel_path,
            record.fields,
        )
        for record in records
    }
    updated = dict(router)
    updated["artefact_index"] = finalize_living_artefact_index(entries)
    return updated


def _parent_from_owner_segment(record, segment, index):
    """Resolve the immediate owner-folder segment for *record*, if unique."""
    if not segment:
        return None
    candidate = normalize_artefact_key(segment)
    if not candidate and is_valid_key(segment):
        candidate = make_artefact_key(artefact_type_prefix(record.artefact), segment)
    if not candidate or candidate == record.artefact_key:
        return None
    return candidate if candidate in index else None


def _infer_missing_parent(record, index):
    current_parent = normalize_artefact_key(record.fields.get("parent"))
    current_folder, _status_segment = _with_status_stripped(os.path.dirname(record.rel_path))
    base = record.artefact["path"]
    if current_folder in {"", base}:
        return None, None
    rel_to_base = os.path.relpath(current_folder, base)
    if rel_to_base in {".", ""} or rel_to_base.startswith(".."):
        return None, None
    segment = os.path.basename(current_folder)
    inferred = _parent_from_owner_segment(record, segment, index)
    if not inferred:
        return None, None
    if current_parent and current_parent != inferred:
        return None, {
            "path": record.rel_path,
            "existing_parent": current_parent,
            "folder_parent": inferred,
            "reason": "existing parent conflicts with immediate owner folder",
        }
    if current_parent == inferred:
        return None, None
    return inferred, None


def _backfill_missing_parents(records, index):
    updated = []
    parent_updates = []
    conflicts = []

    for record in records:
        inferred, conflict = _infer_missing_parent(record, index)
        if conflict:
            conflicts.append(conflict)
            updated.append(record)
            continue
        if not inferred:
            updated.append(record)
            continue
        fields = _insert_parent_field(record.fields, inferred)
        updated_record = replace(record, fields=fields)
        updated.append(updated_record)
        parent_updates.append({
            "path": record.rel_path,
            "parent": inferred,
            "source": "immediate_owner_folder",
        })

    return updated, parent_updates, conflicts


def _expected_path(record, router):
    parent = normalize_artefact_key(record.fields.get("parent"))
    base_folder = resolve_folder(
        record.artefact,
        parent=parent,
        fields=record.fields,
        router=router,
    )
    _current_owner_folder, status_segment = _with_status_stripped(os.path.dirname(record.rel_path))
    if status_segment:
        folder = os.path.join(base_folder, status_segment)
    else:
        folder = apply_terminal_status_folder(base_folder, record.artefact, record.fields)
    return os.path.join(folder, os.path.basename(record.rel_path))


def _invalid_parent_chains(records, router):
    invalid = []
    for record in records:
        try:
            _expected_path(record, router)
        except ParentChainError as exc:
            invalid.append({
                "path": record.rel_path,
                "key": record.artefact_key,
                "parent": normalize_artefact_key(record.fields.get("parent")),
                "error": str(exc),
            })
    return invalid


def _plan_moves(records, router):
    moves = []
    for record in records:
        dest = _expected_path(record, router)
        if dest != record.rel_path:
            moves.append({"source": record.rel_path, "dest": dest})
    return moves


def _move_preflight_blockers(vault_root, moves):
    def collision(message):
        dest = None
        if message.startswith("Destination file already exists: "):
            dest = message.split(": ", 1)[1]
        elif message.startswith("Duplicate move destination: "):
            dest = message.split(": ", 1)[1]
        elif message.startswith("Destination parent is not a directory: ") and " for " in message:
            dest = message.rsplit(" for ", 1)[1]
        return [{"source": None, "dest": dest, "reason": message}]

    try:
        preflight_move_set(vault_root, moves)
    except FileExistsError as exc:
        return collision(str(exc)), []
    except NotADirectoryError as exc:
        return collision(str(exc)), []
    except ValueError as exc:
        message = str(exc)
        if "Cyclic move set" in message:
            return [], [{"error": message}]
        if (
            "Duplicate move destination" in message
            or "Duplicate move source" in message
            or "Move source cannot be a symlink" in message
            or "Move destination cannot be a symlink" in message
        ):
            return collision(message), []
        raise
    return [], []


def _write_parent_updates(vault_root, records, parent_updates):
    by_path = {record.rel_path: record for record in records}
    written = []
    for update in parent_updates:
        rel_path = update["path"]
        record = by_path[rel_path]
        abs_path = os.path.join(vault_root, rel_path)
        try:
            safe_write(
                abs_path,
                serialize_frontmatter(record.fields, record.body),
                bounds=vault_root,
            )
        except OSError as exc:
            raise RuntimeError(
                "recursive placement migration partially applied during parent "
                f"backfill; written {written}, failed at {rel_path}"
            ) from exc
        written.append(rel_path)
    return written


def build_migration_plan(vault_root, router=None):
    """Return a frozen migration plan; never writes."""
    vault_root = os.path.abspath(str(vault_root))
    router = router or load_compiled_router(vault_root)
    if "error" in router:
        return MigrationPlan(
            status="error",
            version=VERSION,
            vault_root=vault_root,
            router=router,
            records=(),
            planned_router=router,
            parent_updates=(),
            moves=(),
            invalid_chains=(),
            collisions=(),
            cyclic_moves=(),
            conflicts=(),
            duplicate_keys=(),
            read_errors=(),
            keyless_living=(),
            error=router["error"],
        )

    whole_vault_read_errors = _whole_vault_read_errors(vault_root)
    records, duplicate_keys, living_read_errors, keyless_living = _read_living_records(vault_root, router)
    read_errors = _merge_read_errors(whole_vault_read_errors, living_read_errors)
    base_router = _router_with_records(router, records)
    index = dict(base_router["artefact_index"])
    records, parent_updates, conflicts = _backfill_missing_parents(records, index)
    planned_router = _router_with_records(router, records)
    invalid_chains = _invalid_parent_chains(records, planned_router)
    read_blockers = duplicate_keys or read_errors or keyless_living or invalid_chains
    moves = [] if read_blockers else _plan_moves(records, planned_router)
    collisions, cyclic_moves = _move_preflight_blockers(vault_root, moves) if moves else ([], [])
    status = "blocked" if read_blockers or collisions or cyclic_moves else "ok"
    if status == "ok" and not parent_updates and not moves and not conflicts:
        status = "skipped"

    return MigrationPlan(
        status=status,
        version=VERSION,
        vault_root=vault_root,
        router=router,
        records=tuple(records),
        planned_router=planned_router,
        parent_updates=tuple(parent_updates),
        moves=tuple(moves),
        invalid_chains=tuple(invalid_chains),
        collisions=tuple(collisions),
        cyclic_moves=tuple(cyclic_moves),
        conflicts=tuple(conflicts),
        duplicate_keys=tuple(duplicate_keys),
        read_errors=tuple(read_errors),
        keyless_living=tuple(keyless_living),
    )


def plan_vault(vault_root, router=None):
    """Plan the recursive owner-folder migration without writing."""
    return build_migration_plan(vault_root, router=router).to_result(dry_run=True)


def migrate_vault(vault_root, *, apply=False, router=None):
    """Run the migration, applying only when requested and preflight-clean."""
    plan = build_migration_plan(vault_root, router=router)
    if not apply:
        return plan.to_result(dry_run=True)

    blockers = plan.blockers()
    if plan.error or blockers:
        result = plan.to_result(dry_run=False)
        if blockers:
            result["error"] = f"Cannot apply migration with blockers: {', '.join(blockers)}"
        return result

    written = _write_parent_updates(plan.vault_root, plan.records, plan.parent_updates)
    move_result = {"moves": [], "applied": [], "links_updated": 0}
    if plan.moves:
        try:
            move_result = move_and_update_links(
                plan.vault_root,
                list(plan.moves),
                prune_router=plan.router,
            )
        except PartialApplyError as exc:
            result = {
                **plan.to_result(dry_run=False),
                "status": "error",
                "written_parent_updates": written,
                "move_result": {
                    "status": "error",
                    "moves": list(plan.moves),
                    "applied": [],
                    "links_updated": 0,
                    "error": str(exc),
                },
            }
            result["error"] = (
                "recursive placement migration partially applied during moves; "
                f"written_parent_updates {written}; move failure: {exc}"
            )
            return result

    return {
        **plan.to_result(dry_run=False),
        "status": "ok" if plan.parent_updates or plan.moves else "skipped",
        "written_parent_updates": written,
        "move_result": move_result,
    }


def migrate(vault_root: str) -> dict:
    """Upgrade-runner entry point."""
    result = migrate_vault(vault_root, apply=True)
    if result.get("status") == "blocked":
        message = result.get("error") or "Cannot apply migration with blockers"
        return {
            **result,
            "status": "error",
            "message": message,
        }
    if result.get("status") == "error":
        return {
            **result,
            "message": result.get("message") or result.get("error") or "migration returned status=error",
        }
    return result


def _print_human(result):
    if result.get("error"):
        print(f"Error: {result['error']}", file=sys.stderr)
    print(f"Recursive owner-folder migration ({VERSION}): {result['status']}")
    print(f"  parent updates: {len(result.get('parent_updates', []))}")
    print(f"  moves: {len(result.get('moves', []))}")
    print(f"  conflicts: {len(result.get('conflicts', []))}")
    print(f"  read errors: {len(result.get('read_errors', []))}")
    print(f"  keyless living: {len(result.get('keyless_living', []))}")
    print(f"  duplicate keys: {len(result.get('duplicate_keys', []))}")
    print(f"  invalid chains: {len(result.get('invalid_chains', []))}")
    print(f"  collisions: {len(result.get('collisions', []))}")
    print(f"  cyclic moves: {len(result.get('cyclic_moves', []))}")
    for item in result.get("read_errors", []):
        print(f"  unreadable: {item['path']} ({item['error']})")
    for item in result.get("keyless_living", []):
        print(f"  keyless: {item['path']} ({item['reason']})")
    for item in result.get("invalid_chains", []):
        print(f"  invalid: {item['path']} ({item['error']})")
    for item in result.get("duplicate_keys", []):
        paths = ", ".join(item.get("paths", []))
        print(f"  duplicate key: {item.get('key')} ({paths})")
    for item in result.get("conflicts", []):
        print(
            "  conflict: "
            f"{item.get('path')} existing={item.get('existing_parent')} "
            f"folder={item.get('folder_parent')} ({item.get('reason')})"
        )
    for item in result.get("collisions", []):
        source = item.get("source") or "unknown source"
        dest = item.get("dest") or "unknown destination"
        print(f"  collision: {source} -> {dest} ({item.get('reason')})")
    for item in result.get("cyclic_moves", []):
        print(f"  cyclic move set: {item['error']}")
    for move in result.get("moves", []):
        prefix = "[DRY RUN] " if result.get("dry_run") else ""
        print(f"  {prefix}{move['source']} -> {move['dest']}")
    return 1 if result.get("status") in {"blocked", "error"} else 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Migrate living artefacts to recursive owner-folder projection."
    )
    parser.add_argument("--vault", help="Path to vault root (default: auto-detect)")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args(argv)

    vault_root = str(find_vault_root(args.vault))
    result = migrate_vault(vault_root, apply=not args.dry_run)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 1 if result.get("status") in {"blocked", "error"} else 0
    return _print_human(result)


if __name__ == "__main__":
    raise SystemExit(main())
