#!/usr/bin/env python3
"""
migrate_to_0_25_0.py — Canonicalise installed bootstrap text.

Rewrites historical bootstrap variants in CLAUDE.md / AGENTS.md to the
current contract text that points agents at `brain_session` first and the
bootloader `index.md` only for the no-MCP path.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from _common import safe_write

VERSION = "0.25.0"

OLD_VARIANTS = [
    'If brain MCP tools are available, call brain_read(resource="router") at session start.',
    "Always read [[_Config/router]] before working in this vault.",
    "This is Brain, a self-extending Obsidian vault. Always read [[_Config/router]] before working in this vault.",
    "This is Brain, an Obsidian vault.\n\nRead [[_Config/router|Router]] before working in this vault.",
    "ALWAYS DO FIRST: Call brain_session. Read [[.brain-core/index]]",
    "ALWAYS DO FIRST: Call MCP `brain_session`, else read `.brain-core/index.md` if it exists",
]

NEW_BOOTSTRAP = "ALWAYS DO FIRST: Call MCP `brain_session`, else read `.brain-core/index.md` if it exists."

STALE_ROUTER_LINE = "Always read [[.brain-core/index]]."


def migrate(vault_root):
    """Update bootstrap text and remove stale router directives when present."""
    vault_root = str(vault_root)
    actions = []
    seen_paths = set()

    for filename in ("CLAUDE.md", "AGENTS.md", "Agents.md"):
        filepath = os.path.join(vault_root, filename)
        if not os.path.isfile(filepath):
            continue

        real_path = os.path.realpath(filepath)
        if real_path in seen_paths:
            continue
        seen_paths.add(real_path)

        try:
            with open(real_path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError:
            continue

        if NEW_BOOTSTRAP in content:
            continue

        original = content
        for variant in OLD_VARIANTS:
            if variant in content:
                content = content.replace(variant, NEW_BOOTSTRAP)
                break

        if content != original:
            safe_write(real_path, content, bounds=vault_root)
            rel = os.path.relpath(real_path, vault_root)
            actions.append(f"updated bootstrap in {rel}")

    router_path = os.path.join(vault_root, "_Config", "router.md")
    if os.path.isfile(router_path):
        try:
            with open(router_path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError:
            content = None

        if content and STALE_ROUTER_LINE in content:
            content = content.replace(STALE_ROUTER_LINE + "\n\n", "")
            content = content.replace(STALE_ROUTER_LINE + "\n", "")
            content = content.replace(STALE_ROUTER_LINE, "")
            safe_write(router_path, content, bounds=vault_root)
            actions.append("removed 'Always read [[.brain-core/index]].' from router.md")

    if not actions:
        return {"status": "skipped", "actions": []}

    return {"status": "ok", "actions": actions}
