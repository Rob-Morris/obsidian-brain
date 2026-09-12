#!/usr/bin/env python3
"""Remove the legacy creation-date prefix from living Note filenames."""

from __future__ import annotations

import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from _common import parse_frontmatter
from _common._wikilinks import iter_vault_md_files
from rename import move_and_update_links, preflight_move_set


VERSION = "0.63.0"
_LEGACY_NOTE_NAME = re.compile(r"^\d{8} - (?P<title>.+\.md)$")


def _planned_moves(vault_root: str) -> list[dict[str, str]]:
    notes_root = os.path.join(vault_root, "Notes")
    if not os.path.isdir(notes_root):
        return []

    moves: list[dict[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(notes_root):
        dirnames[:] = [name for name in dirnames if not name.startswith((".", "_"))]
        for filename in sorted(filenames):
            match = _LEGACY_NOTE_NAME.fullmatch(filename)
            if match is None:
                continue
            absolute = os.path.join(dirpath, filename)
            if os.path.islink(absolute):
                raise ValueError(
                    "Legacy Note migration refuses symlink source: "
                    + os.path.relpath(absolute, vault_root)
                )
            with open(absolute, "r", encoding="utf-8") as handle:
                fields, _body = parse_frontmatter(handle.read())
            if fields.get("type") != "living/note":
                continue
            destination = os.path.join(dirpath, match.group("title"))
            moves.append({
                "source": os.path.relpath(absolute, vault_root),
                "dest": os.path.relpath(destination, vault_root),
            })
    return moves


def _title_identity(path: str) -> str:
    return unicodedata.normalize("NFC", os.path.basename(path)).casefold()


def _preflight_titles(vault_root: str, moves: list[dict[str, str]]) -> None:
    preflight_move_set(vault_root, moves)
    counts: dict[str, int] = {}
    for _directory, filename in iter_vault_md_files(vault_root):
        title = _title_identity(filename)
        counts[title] = counts.get(title, 0) + 1
    for move in moves:
        source = _title_identity(move["source"])
        destination = _title_identity(move["dest"])
        counts[source] = counts.get(source, 0) - 1
        counts[destination] = counts.get(destination, 0) + 1
    for move in moves:
        if counts[_title_identity(move["dest"])] > 1:
            raise ValueError(
                f"Migration would create an ambiguous Note title: {move['dest']}. "
                "Give the colliding documents distinct titles before upgrading."
            )


def prospective_effects(vault_root: str) -> list[str]:
    """Declare moved files and the complete shared wikilink rewrite surface."""
    moves = _planned_moves(vault_root)
    if not moves:
        return []
    _preflight_titles(vault_root, moves)
    paths = {
        os.path.join(directory, filename)
        for directory, filename in iter_vault_md_files(vault_root)
    }
    paths.update(os.path.join(vault_root, move["dest"]) for move in moves)
    return sorted(os.path.abspath(path) for path in paths)


def migrate(vault_root: str) -> dict[str, object]:
    """Rename legacy Notes atomically enough to fail before collision writes."""

    vault_root = str(vault_root)
    moves = _planned_moves(vault_root)
    if not moves:
        return {"status": "skipped", "moves": [], "links_updated": 0}

    _preflight_titles(vault_root, moves)
    result = move_and_update_links(vault_root, moves)
    return {
        "status": "ok",
        "moves": result["moves"],
        "links_updated": result["links_updated"],
    }
