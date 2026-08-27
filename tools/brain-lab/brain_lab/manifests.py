from __future__ import annotations

import gzip
import hashlib
import json
import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence

from .model import canonical_json


CORE_IGNORES = {".DS_Store", "__pycache__"}
CORE_SUFFIX_IGNORES = {".pyc", ".pyo"}
CORE_FILE_IGNORES = {"scripts/upgrade.py"}
PORTABLE_EXCLUDED_PREFIXES = (
    ".brain/local/",
    ".codex/",
    ".claude/",
)
PORTABLE_EXCLUDED_FILES = {".mcp.json"}


def read_gzip_json(path: Path) -> dict:
    try:
        value = json.loads(gzip.decompress(path.read_bytes()))
    except (OSError, EOFError, gzip.BadGzipFile, json.JSONDecodeError) as exc:
        raise ValueError(f"compressed JSON evidence is invalid: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"compressed JSON evidence root is not an object: {path}")
    return value


@dataclass(frozen=True)
class ManifestEntry:
    path: str
    kind: str
    mode: int
    size: int
    sha256: str | None = None
    link_target: str | None = None


@dataclass(frozen=True)
class TreeManifest:
    entries: tuple[ManifestEntry, ...]
    tree_sha256: str
    total_bytes: int

    def to_dict(self) -> dict:
        return {
            "entries": [asdict(entry) for entry in self.entries],
            "tree_sha256": self.tree_sha256,
            "total_bytes": self.total_bytes,
        }


def _relative_path(root: Path, path: Path) -> str:
    relative = path.relative_to(root).as_posix()
    if relative in {"", "."} or PurePosixPath(relative).is_absolute() or ".." in PurePosixPath(relative).parts:
        raise ValueError(f"unsafe capture path: {path}")
    return relative


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_symlink(root: Path, path: Path, target: str) -> None:
    if os.path.isabs(target):
        raise ValueError(f"absolute symlink is not allowed: {_relative_path(root, path)} -> {target}")
    resolved = (path.parent / target).resolve(strict=False)
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(
            f"out-of-root symlink is not allowed: {_relative_path(root, path)} -> {target}"
        ) from exc


def _walk_leaf_paths(
    root: Path,
    *,
    excluded_prefixes: Sequence[str] = (),
    excluded_parts: frozenset[str] = frozenset(),
) -> list[Path]:
    paths: list[Path] = []
    for directory, names, filenames in os.walk(root, followlinks=False):
        current = Path(directory)
        retained_names = []
        for name in sorted(names):
            path = current / name
            relative = path.relative_to(root).as_posix()
            excluded = (
                excluded_parts.intersection(PurePosixPath(relative).parts)
                or any(
                    relative == prefix.rstrip("/") or relative.startswith(prefix)
                    for prefix in excluded_prefixes
                )
            )
            if excluded:
                continue
            if path.is_symlink():
                paths.append(path)
            else:
                retained_names.append(name)
        names[:] = retained_names
        paths.extend(current / name for name in sorted(filenames))
    return paths


def manifest_tree(root: Path, relative_paths: Iterable[str] | None = None) -> TreeManifest:
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"capture root is not a directory: {root}")
    if relative_paths is None:
        candidates = _walk_leaf_paths(root)
    else:
        candidates = [root / relative for relative in relative_paths]

    entries: list[ManifestEntry] = []
    seen: set[str] = set()
    for path in sorted(candidates, key=lambda item: item.as_posix()):
        relative = _relative_path(root, path)
        if relative in seen:
            continue
        seen.add(relative)
        try:
            metadata = path.lstat()
        except FileNotFoundError as exc:
            raise ValueError(f"capture path disappeared: {relative}") from exc
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISLNK(metadata.st_mode):
            target = os.readlink(path)
            _validate_symlink(root, path, target)
            entries.append(ManifestEntry(relative, "symlink", mode, len(target.encode()), link_target=target))
        elif stat.S_ISREG(metadata.st_mode):
            entries.append(ManifestEntry(relative, "file", mode, metadata.st_size, sha256=_hash_file(path)))
        elif stat.S_ISDIR(metadata.st_mode):
            continue
        else:
            raise ValueError(f"special files are not allowed in captures: {relative}")

    canonical = [asdict(entry) for entry in entries]
    tree_sha256 = hashlib.sha256(canonical_json(canonical).encode("utf-8")).hexdigest()
    return TreeManifest(tuple(entries), tree_sha256, sum(entry.size for entry in entries))


def select_manifest(manifest: TreeManifest, prefix: str) -> TreeManifest:
    prefix = prefix.rstrip("/") + "/"
    selected = []
    for entry in manifest.entries:
        if not entry.path.startswith(prefix):
            continue
        payload = asdict(entry)
        payload["path"] = entry.path[len(prefix) :]
        selected.append(ManifestEntry(**payload))
    canonical = [asdict(entry) for entry in selected]
    digest = hashlib.sha256(canonical_json(canonical).encode("utf-8")).hexdigest()
    return TreeManifest(tuple(selected), digest, sum(entry.size for entry in selected))


def normalised_core_tree(core: Path) -> TreeManifest:
    if not core.is_dir():
        raise ValueError(f"Brain Core directory is missing: {core}")
    paths = []
    for path in _walk_leaf_paths(core, excluded_parts=frozenset(CORE_IGNORES)):
        relative = path.relative_to(core)
        if relative.as_posix() in CORE_FILE_IGNORES:
            continue
        if any(part in CORE_IGNORES for part in relative.parts):
            continue
        if path.suffix in CORE_SUFFIX_IGNORES:
            continue
        paths.append(relative.as_posix())
    return manifest_tree(core, paths)


def normalised_core_manifest(root: Path) -> TreeManifest:
    return normalised_core_tree(root / ".brain-core")


def portable_manifest(root: Path) -> TreeManifest:
    paths = []
    for path in _walk_leaf_paths(root, excluded_prefixes=PORTABLE_EXCLUDED_PREFIXES):
        relative = path.relative_to(root).as_posix()
        if relative in PORTABLE_EXCLUDED_FILES:
            continue
        if any(relative.startswith(prefix) for prefix in PORTABLE_EXCLUDED_PREFIXES):
            continue
        paths.append(relative)
    return manifest_tree(root, paths)


def manifest_from_json(value: dict) -> TreeManifest:
    entries = tuple(ManifestEntry(**item) for item in value["entries"])
    return TreeManifest(entries, value["tree_sha256"], value["total_bytes"])
