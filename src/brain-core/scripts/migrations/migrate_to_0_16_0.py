#!/usr/bin/env python3
"""
migrate_to_0_16_0.py — Move dotfiles from _Config/ to .brain/.

Restructures vault config storage:
  _Config/ → markdown only (prose you edit)
  .brain/ → structured data (data the system manages)
  .brain/local/ → machine-specific generated caches (gitignored)
"""

import json
import os
import shutil
import tempfile

def _safe_write(path, content):
    """Atomic write: tmp → fsync → os.replace."""
    target = os.path.realpath(path)
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=os.path.basename(target) + ".",
        suffix=".tmp",
        dir=os.path.dirname(target) or ".",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
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


VERSION = "0.16.0"

# Files to move: (old_path_parts, new_path_parts)
_MOVES = [
    # Generated caches → .brain/local/
    (("_Config", ".compiled-router.json"), (".brain", "local", "compiled-router.json")),
    (("_Config", ".retrieval-index.json"), (".brain", "local", "retrieval-index.json")),
    (("_Config", ".type-embeddings.npy"), (".brain", "local", "type-embeddings.npy")),
    (("_Config", ".doc-embeddings.npy"), (".brain", "local", "doc-embeddings.npy")),
    (("_Config", ".embeddings-meta.json"), (".brain", "local", "embeddings-meta.json")),
    # Machine-local workspace registry → .brain/local/
    ((".brain", "workspaces.json"), (".brain", "local", "workspaces.json")),
]

# Seed files to create if missing: (path_parts, default_content)
_SEEDS = [
    ((".brain", "preferences.json"), "{}"),
    ((".brain", "tracking.json"), json.dumps({"schema_version": 1, "installed": {}}, indent=2)),
]


def migrate(vault_root):
    """Move dotfiles from _Config/ to .brain/ and .brain/local/.

    Idempotent — safe to run multiple times. Skips moves where source
    doesn't exist or destination already exists.

    Returns dict with status and list of actions taken.
    """
    vault_root = str(vault_root)
    actions = []

    # Ensure .brain/local/ exists
    local_dir = os.path.join(vault_root, ".brain", "local")
    os.makedirs(local_dir, exist_ok=True)

    # Move files
    for old_parts, new_parts in _MOVES:
        old_path = os.path.join(vault_root, *old_parts)
        new_path = os.path.join(vault_root, *new_parts)

        if not os.path.exists(old_path):
            continue
        if os.path.exists(new_path):
            continue

        os.makedirs(os.path.dirname(new_path), exist_ok=True)
        shutil.move(old_path, new_path)
        actions.append(f"moved {os.path.join(*old_parts)} → {os.path.join(*new_parts)}")

    # Create seed files
    for path_parts, default_content in _SEEDS:
        path = os.path.join(vault_root, *path_parts)
        if os.path.exists(path):
            continue
        _safe_write(path, default_content + "\n")
        actions.append(f"created {os.path.join(*path_parts)}")

    if not actions:
        return {"status": "skipped", "actions": []}

    return {"status": "ok", "actions": actions}
