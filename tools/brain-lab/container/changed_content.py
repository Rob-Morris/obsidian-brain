#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path, PurePosixPath


PER_FILE_LIMIT = 64 * 1024
TOTAL_LIMIT = 512 * 1024


def _safe_path(root: Path, value: str) -> Path:
    relative = PurePosixPath(value)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError(f"unsafe changed-content path: {value!r}")
    path = root.joinpath(*relative.parts)
    resolved_parent = path.parent.resolve(strict=False)
    resolved_parent.relative_to(root)
    return path


def collect(root: Path, paths: list[str]) -> dict:
    root = root.resolve()
    retained = 0
    entries = []
    for value in sorted(set(paths)):
        entry = {"path": value, "text": None, "reason": None}
        try:
            path = _safe_path(root, value)
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode):
                entry["reason"] = "not-regular-file"
            elif metadata.st_size > PER_FILE_LIMIT:
                entry["reason"] = "per-file-limit"
            elif retained + metadata.st_size > TOTAL_LIMIT:
                entry["reason"] = "total-limit"
            else:
                content = path.read_bytes()
                if len(content) != metadata.st_size:
                    entry["reason"] = "changed-during-read"
                elif b"\x00" in content:
                    entry["reason"] = "binary"
                else:
                    try:
                        entry["text"] = content.decode("utf-8")
                    except UnicodeDecodeError:
                        entry["reason"] = "non-utf8"
                    else:
                        entry["sha256"] = hashlib.sha256(content).hexdigest()
                        retained += len(content)
        except (FileNotFoundError, OSError, ValueError) as exc:
            entry["reason"] = f"unavailable:{type(exc).__name__}"
        entries.append(entry)
    return {
        "schema": "brain-lab.changed-content/1",
        "per_file_limit": PER_FILE_LIMIT,
        "total_limit": TOTAL_LIMIT,
        "retained_bytes": retained,
        "entries": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    values = json.load(sys.stdin)
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise ValueError("changed-content stdin must be a JSON array of paths")
    print(json.dumps(collect(args.root, values), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
