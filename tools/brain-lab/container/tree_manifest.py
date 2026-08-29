#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path


PORTABLE_EXCLUDED_PREFIXES = (".brain/local/", ".codex/", ".claude/")
PORTABLE_EXCLUDED_FILES = {".mcp.json"}
CORE_IGNORED_PARTS = {".DS_Store", "__pycache__"}
CORE_IGNORED_SUFFIXES = {".pyc", ".pyo"}
CORE_IGNORED_FILES = {"scripts/upgrade.py"}
PREPARED_EXCLUDED_PREFIXES = (
    ".cache/",
    ".local/state/brain/command-outcomes/",
    "work/",
    "vault/.brain/local/",
)
RUN_EXCLUDED_PREFIXES = (
    ".cache/",
    ".local/state/brain/command-outcomes/",
    "work/",
)
PREPARED_EXCLUDED_FILES = {
    ".config/brain/brains.json.lock",
    ".config/brain/vaults.lock",
}
GENERATED_COLOURS = "vault/.obsidian/snippets/brain-folder-colours.css"


def _file_hash(path: Path, relative: str) -> str:
    if relative == GENERATED_COLOURS:
        content = path.read_text(encoding="utf-8")
        normalised = re.sub(
            r"(?m)^   Generated: .*$",
            "   Generated: <normalised>",
            content,
        )
        return hashlib.sha256(normalised.encode("utf-8")).hexdigest()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _excluded(scope: str, relative: str) -> bool:
    path = Path(relative)
    if scope == "prepared":
        return (
            relative in PREPARED_EXCLUDED_FILES
            or relative.startswith(PREPARED_EXCLUDED_PREFIXES)
            or "__pycache__" in path.parts
            or path.suffix in CORE_IGNORED_SUFFIXES
        )
    if scope == "run":
        return (
            relative in PREPARED_EXCLUDED_FILES
            or relative.startswith(RUN_EXCLUDED_PREFIXES)
            or "__pycache__" in path.parts
            or path.suffix in CORE_IGNORED_SUFFIXES
        )
    if scope == "portable":
        return relative in PORTABLE_EXCLUDED_FILES or relative.startswith(
            PORTABLE_EXCLUDED_PREFIXES
        )
    if scope == "core":
        return (
            relative in CORE_IGNORED_FILES
            or any(part in CORE_IGNORED_PARTS for part in path.parts)
            or path.suffix in CORE_IGNORED_SUFFIXES
        )
    return False


def _walk_leaf_paths(root: Path, scope: str):
    for directory, names, filenames in os.walk(root, followlinks=False):
        current = Path(directory)
        retained_names = []
        for name in sorted(names):
            path = current / name
            relative = path.relative_to(root).as_posix()
            if _excluded(scope, relative):
                continue
            if path.is_symlink():
                yield path
            else:
                retained_names.append(name)
        names[:] = retained_names
        for name in sorted(filenames):
            yield current / name


def manifest(root: Path, scope: str) -> dict:
    root = root.resolve()
    selected_root = root / ".brain-core" if scope == "core" else root
    entries = []
    for path in _walk_leaf_paths(selected_root, scope):
        relative = path.relative_to(selected_root).as_posix()
        metadata = path.lstat()
        if _excluded(scope, relative):
            continue
        entry = {
            "path": relative,
            "mode": stat.S_IMODE(metadata.st_mode),
            "size": metadata.st_size,
            "sha256": None,
            "link_target": None,
        }
        if stat.S_ISLNK(metadata.st_mode):
            entry["kind"] = "symlink"
            entry["link_target"] = os.readlink(path)
        elif stat.S_ISREG(metadata.st_mode):
            entry["kind"] = "file"
            entry["sha256"] = _file_hash(path, relative)
            if relative == GENERATED_COLOURS:
                entry["normalisation"] = "generated-timestamp"
        else:
            entry["kind"] = "special"
        entries.append(entry)
    entries.sort(key=lambda entry: entry["path"])
    encoded = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    return {
        "scope": scope,
        "tree_sha256": hashlib.sha256(encoded).hexdigest(),
        "total_bytes": sum(entry["size"] for entry in entries),
        "entries": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "--scope", choices=("all", "portable", "core", "prepared", "run"), required=True
    )
    parser.add_argument("--gzip", action="store_true")
    args = parser.parse_args()
    encoded = json.dumps(manifest(args.root, args.scope), sort_keys=True).encode("utf-8")
    if args.gzip:
        sys.stdout.buffer.write(gzip.compress(encoded, mtime=0))
    else:
        print(encoded.decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
