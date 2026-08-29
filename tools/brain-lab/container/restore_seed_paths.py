#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
from pathlib import Path, PurePosixPath


def _relative_path(value: str) -> Path:
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or not parsed.parts or any(part in {"", ".", ".."} for part in parsed.parts):
        raise ValueError(f"restore path must be a contained relative path: {value!r}")
    return Path(*parsed.parts)


def _require_contained(root: Path, path: Path) -> None:
    try:
        path.resolve(strict=False).relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"restore path escapes its root: {path}") from exc


def _remove_destination(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_dir() and not path.is_symlink():
        raise ValueError(f"refusing to replace a directory during path restore: {path}")
    path.unlink()


def restore_path(seed: Path, vault: Path, value: str) -> dict[str, str]:
    relative = _relative_path(value)
    source = seed / relative
    destination = vault / relative
    _require_contained(seed, source.parent)
    _require_contained(vault, destination.parent)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _require_contained(vault, destination.parent)

    if not source.exists() and not source.is_symlink():
        _remove_destination(destination)
        return {"path": relative.as_posix(), "action": "removed-generated"}

    metadata = source.lstat()
    _remove_destination(destination)
    if stat.S_ISLNK(metadata.st_mode):
        target = os.readlink(source)
        if os.path.isabs(target):
            raise ValueError(f"absolute seed symlink is not restorable: {relative} -> {target}")
        _require_contained(seed, source.parent / target)
        destination.symlink_to(target)
        action = "restored-symlink"
    elif stat.S_ISREG(metadata.st_mode):
        shutil.copy2(source, destination, follow_symlinks=False)
        action = "restored-file"
    else:
        raise ValueError(f"seed restore supports only regular files and symlinks: {relative}")
    return {"path": relative.as_posix(), "action": action}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", required=True, type=Path)
    parser.add_argument("--vault", required=True, type=Path)
    parser.add_argument("--path", required=True, action="append", dest="paths")
    args = parser.parse_args()
    seed = args.seed.resolve()
    vault = args.vault.resolve()
    if not seed.is_dir() or not vault.is_dir():
        raise ValueError("seed and vault roots must be directories")
    restored = [restore_path(seed, vault, value) for value in args.paths]
    print(json.dumps({"restored": restored}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
