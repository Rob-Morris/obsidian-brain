#!/usr/bin/env python3
"""
migrate_to_0_21_3.py — Backfill status: active on existing documentation artefacts.

Documentation now has a lifecycle (new → shaping → ready → active → deprecated).
Existing docs without a status field are assumed to be active and authoritative.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from _common import parse_frontmatter, serialize_frontmatter, safe_write

VERSION = "0.21.3"


def migrate(vault_root):
    """Add status: active to documentation artefacts missing a status field.

    Returns dict with status and list of actions taken.
    """
    vault_root = str(vault_root)
    docs_dir = os.path.join(vault_root, "Documentation")

    if not os.path.isdir(docs_dir):
        return {"status": "skipped", "actions": []}

    actions = []
    updated = 0

    for dirpath, dirnames, filenames in os.walk(docs_dir):
        dirnames[:] = [d for d in dirnames if d != "_Archive"]
        for fname in filenames:
            if not fname.endswith(".md"):
                continue

            abs_path = os.path.join(dirpath, fname)
            try:
                with open(abs_path, "r", encoding="utf-8") as f:
                    content = f.read()
            except OSError:
                continue

            fields, body = parse_frontmatter(content)
            if fields.get("type") != "living/documentation":
                continue

            if "status" in fields:
                continue  # already has a status, don't overwrite

            fields["status"] = "active"
            new_content = serialize_frontmatter(fields, body=body)
            safe_write(abs_path, new_content, bounds=vault_root)
            rel = os.path.relpath(abs_path, vault_root)
            actions.append(f"added status: active to {rel}")
            updated += 1

    if not actions:
        return {"status": "skipped", "actions": []}

    return {
        "status": "ok",
        "updated": updated,
        "actions": actions,
    }
