#!/usr/bin/env python3
"""Flatten temporal date folders: move every file out of ``yyyy-mm/`` under ``_Temporal``.

v0.67.0 files temporal artefacts flat under their type root or owner chain.
This migration moves each file that still sits beneath a ``yyyy-mm``
directory under ``_Temporal/`` up past that segment, rewriting wikilinks
vault-wide through the shared move engine, and leaves ``_Archive`` untouched:
archived layout is a historical snapshot that ``artefact.unarchive`` re-files
by current conventions. Definition files are never edited here — managed
taxonomies update through post-upgrade definition sync and unmanaged custom
types through sync's convention pass.

``prospective_effects`` and ``migrate`` are independent runner entry points
and each plan the move set from a fresh walk; the second walk is accepted
because the migration runs once per vault.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import compile_router
from _common._wikilinks import iter_vault_md_files
from _lifecycle.retrieval_assets import drop_path_keyed_retrieval_caches
from rename import move_and_update_links, preflight_move_set


VERSION = "0.67.0"
TEMPORAL_DIR = "_Temporal"
ARCHIVE_DIR = "_Archive"
_MONTH_DIR = re.compile(r"^\d{4}-\d{2}$")


def _excluded_dirname(name: str) -> bool:
    """Directories neither walk descends: any ``_Archive`` segment and hidden dirs."""
    return name == ARCHIVE_DIR or name.startswith(".")


def _walk_temporal(vault_root: str):
    """Yield ``(dirpath, filenames)`` under ``_Temporal`` with excluded dirs pruned."""
    temporal_root = os.path.join(vault_root, TEMPORAL_DIR)
    if not os.path.isdir(temporal_root):
        return
    for dirpath, dirnames, filenames in os.walk(temporal_root):
        dirnames[:] = sorted(name for name in dirnames if not _excluded_dirname(name))
        yield dirpath, filenames


def _planned_moves(vault_root: str) -> list[dict[str, str]]:
    """Plan one move per file under a month directory beneath ``_Temporal``.

    Symlinked sources are refused because the move engine will not follow them.
    """
    moves: list[dict[str, str]] = []
    for dirpath, filenames in _walk_temporal(vault_root):
        rel_dir = os.path.relpath(dirpath, vault_root)
        parts = rel_dir.split(os.sep)
        kept = [part for part in parts if not _MONTH_DIR.fullmatch(part)]
        if len(kept) == len(parts):
            continue
        for filename in sorted(filenames):
            if filename.startswith("."):
                continue
            absolute = os.path.join(dirpath, filename)
            if os.path.islink(absolute):
                raise ValueError(
                    "Temporal flattening refuses symlink source: "
                    + os.path.relpath(absolute, vault_root)
                )
            moves.append({
                "source": os.path.join(rel_dir, filename),
                "dest": os.path.join(*kept, filename),
            })
    return moves


def _remaining_month_dirs(vault_root: str) -> list[str]:
    """Return vault-relative ``yyyy-mm`` directories the walk still finds under ``_Temporal``."""
    return sorted(
        os.path.relpath(dirpath, vault_root)
        for dirpath, _filenames in _walk_temporal(vault_root)
        if _MONTH_DIR.fullmatch(os.path.basename(dirpath))
    )


def prospective_effects(vault_root: str) -> list[str]:
    """Declare moved files and the complete shared wikilink rewrite surface.

    Derived retrieval caches are deliberately not declared: rollback restores
    the vault's markdown and the caches rebuild from it.
    """
    moves = _planned_moves(vault_root)
    if not moves:
        return []
    preflight_move_set(vault_root, moves)
    paths = {
        os.path.join(directory, filename)
        for directory, filename in iter_vault_md_files(vault_root)
    }
    paths.update(os.path.join(vault_root, move["dest"]) for move in moves)
    return sorted(os.path.abspath(path) for path in paths)


def migrate(vault_root: str) -> dict[str, object]:
    """Move every temporal file out of its month folder in one preflighted move set."""
    vault_root = str(vault_root)
    moves = _planned_moves(vault_root)
    if not moves:
        return {"status": "skipped", "moves": [], "links_updated": 0}

    # Fail on a bad move set before the cache invalidation below touches anything;
    # the move engine preflights again, which is cheap.
    preflight_move_set(vault_root, moves)
    router = compile_router.compile(vault_root)
    # Invalidate first: if this ran after the moves and failed, a re-run would
    # find nothing to move and leave a falsely fresh index behind.
    caches_removed = drop_path_keyed_retrieval_caches(vault_root)
    result = move_and_update_links(vault_root, moves, prune_router=router)

    outcome: dict[str, object] = {
        "status": "ok",
        "moves": result["moves"],
        "links_updated": result["links_updated"],
        "retrieval_caches_removed": caches_removed,
    }
    remaining = _remaining_month_dirs(vault_root)
    if remaining:
        outcome["warnings"] = [
            "Month folders remain because they still hold non-artefact content "
            "(for example .DS_Store); run `repair.py empty_folders` to review and "
            "remove them: " + ", ".join(remaining)
        ]
    return outcome
