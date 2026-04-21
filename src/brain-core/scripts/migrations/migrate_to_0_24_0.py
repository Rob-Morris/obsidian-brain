#!/usr/bin/env python3
"""
migrate_to_0_24_0.py — Update bootstrap text in CLAUDE.md/AGENTS.md and router.

Replaces old bootstrap variants with the new brain_session + index pattern.
Removes the stale 'Always read [[.brain-core/index]].' directive from router.md.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from _common import safe_write

VERSION = "0.24.0"

# Old bootstrap variants to replace
OLD_VARIANTS = [
    'If brain MCP tools are available, call brain_read(resource="router") at session start.',
    "Always read [[_Config/router]] before working in this vault.",
    "This is Brain, a self-extending Obsidian vault. Always read [[_Config/router]] before working in this vault.",
    "This is Brain, an Obsidian vault.\n\nRead [[_Config/router|Router]] before working in this vault.",
]

NEW_BOOTSTRAP = "ALWAYS DO FIRST: Call brain_session. Read [[.brain-core/index]]"

STALE_ROUTER_LINE = "Always read [[.brain-core/index]]."


def migrate(vault_root):
    """Update bootstrap text and router directives.

    Returns dict with status and list of actions taken.
    """
    vault_root = str(vault_root)
    actions = []
    seen_paths = set()

    # --- Update CLAUDE.md / AGENTS.md (plus legacy Agents.md) ---
    for filename in ("CLAUDE.md", "AGENTS.md", "Agents.md"):
        filepath = os.path.join(vault_root, filename)
        if not os.path.isfile(filepath):
            continue

        # Resolve symlinks — edit the target, not the link
        real_path = os.path.realpath(filepath)
        if real_path in seen_paths:
            continue  # already handled (e.g. CLAUDE.md → AGENTS.md symlink)
        seen_paths.add(real_path)

        try:
            with open(real_path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError:
            continue

        if NEW_BOOTSTRAP in content:
            continue  # already migrated

        original = content
        for variant in OLD_VARIANTS:
            if variant in content:
                content = content.replace(variant, NEW_BOOTSTRAP)
                break

        if content != original:
            safe_write(real_path, content, bounds=vault_root)
            rel = os.path.relpath(real_path, vault_root)
            actions.append(f"updated bootstrap in {rel}")

    # --- Remove stale router directive ---
    router_path = os.path.join(vault_root, "_Config", "router.md")
    if os.path.isfile(router_path):
        try:
            with open(router_path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError:
            content = None

        if content and STALE_ROUTER_LINE in content:
            # Remove the line and any trailing blank line
            content = content.replace(STALE_ROUTER_LINE + "\n\n", "")
            content = content.replace(STALE_ROUTER_LINE + "\n", "")
            content = content.replace(STALE_ROUTER_LINE, "")
            safe_write(router_path, content, bounds=vault_root)
            actions.append("removed 'Always read [[.brain-core/index]].' from router.md")

    if not actions:
        return {"status": "skipped", "actions": []}

    return {
        "status": "ok",
        "actions": actions,
    }
