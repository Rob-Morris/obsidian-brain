#!/usr/bin/env python3
"""Update Brain-authored bootstrap instructions to the portable MCP spelling."""

from pathlib import Path

from _bootstrap.mcp_state import CLAUDE_LOCAL_MD_FILE, migrate_bootstrap_text
from _common import BOOTSTRAP_VARIANTS, find_root_bootstrap_file, safe_write

VERSION = "0.64.0"


def _bootstrap_paths(vault_root):
    root = Path(vault_root).resolve()
    paths = set()
    candidates = [find_root_bootstrap_file(root, name) for name in BOOTSTRAP_VARIANTS]
    local = root / CLAUDE_LOCAL_MD_FILE
    if local.exists() or local.is_symlink():
        candidates.append(local)
    for candidate in candidates:
        if candidate is None:
            continue
        path = Path(candidate).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Bootstrap file resolves outside the Brain vault")
        paths.add(path)
    return sorted(paths)


def prospective_effects(vault_root):
    """Declare the complete bootstrap rewrite surface for upgrade rollback."""
    return [str(path) for path in _bootstrap_paths(vault_root)]


def migrate(vault_root):
    """Preserve custom instructions and canonical dotted permission identifiers."""
    updates = []
    for path in _bootstrap_paths(vault_root):
        content = path.read_text(encoding="utf-8")
        updated = migrate_bootstrap_text(content)
        if updated != content:
            updates.append((path, updated))
    for path, updated in updates:
        safe_write(path, updated, bounds=vault_root)
    return {
        "status": "ok" if updates else "skipped",
        "bootstraps": [
            str(path.relative_to(Path(vault_root).resolve())) for path, _ in updates
        ],
    }
