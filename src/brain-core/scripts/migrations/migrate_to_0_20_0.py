#!/usr/bin/env python3
"""
migrate_to_0_20_0.py — Move terminal-status artefacts into +Status/ folders.

Two tasks:
  1. Rename Writing/_Published/ → Writing/+Published/ (convention change)
  2. Scan all artefact types with terminal_statuses and move files not already
     in the correct +Status/ folder using rename_and_update_links()
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from _common import load_compiled_router, parse_frontmatter
from rename import rename_and_update_links

VERSION = "0.20.0"


def migrate(vault_root):
    """Move terminal-status artefacts into +Status/ folders.

    Returns dict with status and list of actions taken.
    """
    vault_root = str(vault_root)

    router = load_compiled_router(vault_root)
    if "error" in router:
        return {"status": "skipped", "actions": [router["error"]]}

    actions = []
    moved = 0

    # Rename Writing/_Published/ → Writing/+Published/ (old convention)
    old_published = os.path.join(vault_root, "Writing", "_Published")
    new_published = os.path.join(vault_root, "Writing", "+Published")
    if os.path.isdir(old_published) and not os.path.isdir(new_published):
        os.rename(old_published, new_published)
        actions.append("renamed Writing/_Published/ → Writing/+Published/")

    for art in router.get("artefacts", []):
        fm = art.get("frontmatter") or {}
        terminal = fm.get("terminal_statuses")
        if not terminal:
            continue

        art_path = art.get("path")
        if not art_path:
            continue

        abs_art_dir = os.path.join(vault_root, art_path)
        if not os.path.isdir(abs_art_dir):
            continue

        for dirpath, dirnames, filenames in os.walk(abs_art_dir):
            dirnames[:] = [d for d in dirnames if d != "_Archive"]
            for fname in filenames:
                if not fname.endswith(".md"):
                    continue

                abs_file = os.path.join(dirpath, fname)
                try:
                    with open(abs_file, "r", encoding="utf-8") as f:
                        content = f.read()
                except OSError:
                    continue

                fields, _ = parse_frontmatter(content)
                status = fields.get("status")
                if not status or status not in terminal:
                    continue

                rel_path = os.path.relpath(abs_file, vault_root)
                parent_dir = os.path.dirname(rel_path)
                parent_name = os.path.basename(parent_dir)
                status_folder = f"+{status.capitalize()}"

                if parent_name == status_folder:
                    continue  # already in correct folder

                new_path = os.path.join(parent_dir, status_folder, fname)
                links_updated = rename_and_update_links(vault_root, rel_path, new_path)
                actions.append(f"moved {rel_path} → {new_path} ({links_updated} links updated)")
                moved += 1

    if not actions:
        return {"status": "skipped", "actions": []}

    return {
        "status": "ok",
        "moved": moved,
        "actions": actions,
    }
