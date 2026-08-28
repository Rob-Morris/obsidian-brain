#!/usr/bin/env python3
"""Backfill keys newly required for living artefacts in status folders.

Brain Core 0.59.1 extended ``living_key_fields`` validation into ``+Status``
folders after the original v0.31 key migration had already been recorded.
This migration repairs only that compatibility seam.  It does not repair
unrelated keyless artefacts outside status folders or alter any existing key.

Rollback is owned by the upgrade runner's post-compile artefact snapshots.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from _common import (
    STATUS_FOLDER_PREFIX,
    artefact_type_prefix,
    derive_distinctive_slug,
    is_valid_key,
    iter_artefact_paths,
    load_compiled_router,
    read_artefact,
    safe_write,
    serialize_frontmatter,
)


VERSION = "0.62.4"


def _is_status_path(artefact: dict, rel_path: str) -> bool:
    relative = os.path.relpath(rel_path, artefact["path"])
    return any(
        part.startswith(STATUS_FOLDER_PREFIX)
        for part in relative.split(os.sep)[:-1]
    )


def _insert_key(fields: dict, key: str) -> dict:
    """Return ``fields`` with ``key`` placed after tags or type."""

    anchor = "tags" if "tags" in fields else "type"
    result = {}
    inserted = False
    for name, value in fields.items():
        if name == "key":
            continue
        result[name] = value
        if name == anchor:
            result["key"] = key
            inserted = True
    if not inserted:
        result["key"] = key
    return result


def _records_for_type(vault_root: str, artefact: dict) -> tuple[list[dict], list[dict]]:
    records = []
    errors = []
    for rel_path in sorted(
        iter_artefact_paths(vault_root, artefact, include_status_folders=True)
    ):
        abs_path = os.path.join(vault_root, rel_path)
        try:
            fields, body = read_artefact(abs_path)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            errors.append({"path": rel_path, "error": str(exc)})
            continue
        if fields.get("type") != artefact.get("frontmatter_type"):
            continue
        records.append(
            {
                "path": rel_path,
                "fields": fields,
                "body": body,
                "in_status_folder": _is_status_path(artefact, rel_path),
            }
        )
    return records, errors


def plan(vault_root: str, *, router: dict | None = None) -> dict:
    """Plan the bounded backfill without writing any vault file."""

    vault_root = os.path.abspath(vault_root)
    router = router if router is not None else load_compiled_router(vault_root)
    if "error" in router:
        return {"status": "error", "error": router["error"], "updates": []}

    updates = []
    read_errors = []
    for artefact in router.get("artefacts", []):
        if artefact.get("classification") != "living":
            continue
        records, errors = _records_for_type(vault_root, artefact)
        read_errors.extend(errors)
        taken = {
            record["fields"]["key"]
            for record in records
            if is_valid_key(record["fields"].get("key"))
        }
        type_prefix = artefact_type_prefix(artefact)
        for record in records:
            if not record["in_status_folder"]:
                continue
            if is_valid_key(record["fields"].get("key")):
                continue
            title = record["fields"].get("title")
            if not isinstance(title, str) or not title.strip():
                title = os.path.splitext(os.path.basename(record["path"]))[0]
            key = derive_distinctive_slug(title.strip(), taken)
            taken.add(key)
            updates.append(
                {
                    "path": record["path"],
                    "type_prefix": type_prefix,
                    "key": key,
                    "fields": _insert_key(record["fields"], key),
                    "body": record["body"],
                }
            )

    if read_errors:
        return {
            "status": "error",
            "error": "Could not account for every living artefact before key backfill.",
            "read_errors": read_errors,
            "updates": [],
        }
    return {"status": "ok", "updates": updates}


def migrate(vault_root: str) -> dict:
    migration_plan = plan(vault_root)
    if migration_plan["status"] != "ok":
        return migration_plan
    updates = migration_plan["updates"]
    if not updates:
        return {"status": "skipped", "updated": []}

    for update in updates:
        safe_write(
            os.path.join(vault_root, update["path"]),
            serialize_frontmatter(update["fields"], update["body"]),
            bounds=vault_root,
        )
    return {
        "status": "ok",
        "updated": [
            {
                "path": update["path"],
                "type_prefix": update["type_prefix"],
                "key": update["key"],
            }
            for update in updates
        ],
    }
