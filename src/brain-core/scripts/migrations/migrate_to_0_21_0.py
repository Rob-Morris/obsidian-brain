#!/usr/bin/env python3
"""
migrate_to_0_21_0.py — Move per-type _Archive/ contents to top-level _Archive/.

Migrates from scattered archives (Ideas/_Archive/, Designs/Brain/_Archive/) to
a single top-level _Archive/ preserving type/project structure inside:

    Ideas/_Archive/20260101-old.md         → _Archive/Ideas/20260101-old.md
    Designs/Brain/_Archive/20260317-old.md → _Archive/Designs/Brain/20260317-old.md

Uses rename_and_update_links() to update wikilinks vault-wide.
Idempotent: skips files that already exist at the destination.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from _common import is_system_dir
from rename import rename_and_update_links

VERSION = "0.21.0"


def migrate(vault_root):
    """Move per-type _Archive/ contents to top-level _Archive/.

    Scans the vault for _Archive/ directories inside type/project folders
    and moves their contents to the top-level _Archive/.

    Returns dict with status and list of actions taken.
    """
    vault_root = str(vault_root)

    actions = []
    moved = 0

    # Walk the vault looking for _Archive/ directories (not the top-level one)
    for dirpath, dirnames, _filenames in os.walk(vault_root):
        # Don't descend into system dirs or top-level _Archive
        rel_dir = os.path.relpath(dirpath, vault_root)
        if rel_dir == ".":
            dirnames[:] = [d for d in dirnames if not is_system_dir(d)]
            continue

        # Check if this directory contains an _Archive/ subfolder
        if "_Archive" not in dirnames:
            # Don't descend into _Archive dirs found elsewhere
            dirnames[:] = [d for d in dirnames if d != "_Archive"]
            continue

        # Found a per-type _Archive/ dir (e.g. Ideas/_Archive/ or Designs/Brain/_Archive/)
        archive_dir = os.path.join(dirpath, "_Archive")
        # rel_dir is the type/project path (e.g. "Ideas" or "Designs/Brain")

        # Move each file from this archive to _Archive/{type_path}/
        for sub_dirpath, _sub_dirnames, sub_filenames in os.walk(archive_dir):
            for fname in sub_filenames:
                if not fname.endswith(".md"):
                    continue

                abs_source = os.path.join(sub_dirpath, fname)
                rel_source = os.path.relpath(abs_source, vault_root)

                # Compute destination: _Archive/{type_path}/{filename}
                rel_from_archive = os.path.relpath(abs_source, archive_dir)
                dest = os.path.join("_Archive", rel_dir, rel_from_archive)

                # Idempotent: skip if already at destination
                abs_dest = os.path.join(vault_root, dest)
                if os.path.isfile(abs_dest):
                    actions.append(f"skipped {rel_source} (already at {dest})")
                    continue

                links_updated = rename_and_update_links(
                    vault_root, rel_source, dest
                )
                actions.append(
                    f"moved {rel_source} → {dest} "
                    f"({links_updated} links updated)"
                )
                moved += 1

        # Remove empty per-type _Archive/ directory
        _remove_empty_dirs(archive_dir)

        # Don't descend into the _Archive/ we just processed
        dirnames[:] = [d for d in dirnames if d != "_Archive"]

    if not actions:
        return {"status": "skipped", "actions": []}

    return {
        "status": "ok",
        "moved": moved,
        "actions": actions,
    }


def _remove_empty_dirs(path):
    """Remove directory and any empty parents."""
    for dirpath, dirnames, filenames in os.walk(path, topdown=False):
        if not dirnames and not filenames:
            try:
                os.rmdir(dirpath)
            except OSError:
                pass
