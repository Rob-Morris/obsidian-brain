#!/usr/bin/env python3
"""Materialise the portable shaping workflow into Brain's core package."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from pathlib import PurePosixPath
import subprocess
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CORE_SCRIPTS = REPOSITORY_ROOT / "src" / "brain-core" / "scripts"
if str(CORE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(CORE_SCRIPTS))

from _portable_path import validate_portable_relative_path  # noqa: E402


DEFAULT_DESTINATION = REPOSITORY_ROOT / "src" / "brain-core" / "skills" / "shaping"
PROVENANCE_FILE = "portable-provenance.json"
SOURCE_TO_DESTINATION = {
    "SKILL.md": "portable.md",
    "references/assess.md": "references/assess.md",
    "references/brainstorm.md": "references/brainstorm.md",
    "references/discover.md": "references/discover.md",
    "references/refine.md": "references/refine.md",
    "references/review.md": "references/review.md",
}
BRAIN_OWNED_DESTINATIONS = frozenset(("SKILL.md", "references/brain.md"))


def _portable_relative_path(value: object, *, field: str) -> str:
    try:
        return validate_portable_relative_path(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be a portable relative path") from exc


def _committed_source_membership(
    repository_root: Path,
    *,
    revision: str,
    skill_path: str,
) -> set[str]:
    raw_paths = _git(
        repository_root,
        "ls-tree",
        "-r",
        "--name-only",
        "-z",
        revision,
        "--",
        skill_path,
    )
    prefix = f"{skill_path}/"
    committed = set()
    for raw_path in raw_paths.split(b"\0"):
        if not raw_path:
            continue
        path = raw_path.decode("utf-8")
        if not path.startswith(prefix):
            raise ValueError(f"unexpected portable shaping source path: {path}")
        committed.add(path[len(prefix) :])
    return committed


def _previous_vendor_destinations(destination_root: Path) -> set[str]:
    provenance_path = destination_root / PROVENANCE_FILE
    if not provenance_path.exists():
        return set()
    try:
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        entries = provenance["materialised_files"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError("existing portable provenance is invalid") from exc
    if not isinstance(entries, list):
        raise ValueError("existing portable provenance is invalid")
    destinations = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("existing portable provenance is invalid")
        relative = _portable_relative_path(
            entry.get("destination"),
            field="portable provenance destination",
        )
        if relative == PROVENANCE_FILE or relative in BRAIN_OWNED_DESTINATIONS:
            raise ValueError("portable provenance claims a Brain-owned destination")
        destinations.add(relative)
    return destinations


def _stale_destination_path(destination_root: Path, relative: str) -> Path:
    path = destination_root / relative
    current = destination_root
    for part in PurePosixPath(relative).parts[:-1]:
        current /= part
        if current.is_symlink():
            raise ValueError("stale portable destination traverses a symlink")
    if path.is_dir() and not path.is_symlink():
        raise ValueError("stale portable destination is not a file")
    return path


def _git(repository_root: Path, *args: str) -> bytes:
    process = subprocess.run(
        ["git", "-C", str(repository_root), *args],
        capture_output=True,
        check=False,
    )
    if process.returncode != 0:
        detail = process.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"git {' '.join(args)} failed: {detail}")
    return process.stdout


def materialise(
    source_root: Path,
    destination_root: Path,
    *,
    expected_repository: str,
    expected_revision: str,
) -> dict[str, object]:
    """Copy the complete portable workflow and record exact source identity."""
    if not expected_revision.strip():
        raise ValueError("expected revision must be non-empty")
    if not expected_repository.strip():
        raise ValueError("expected repository must be non-empty")

    source_root = source_root.resolve()
    repository_root = Path(
        _git(source_root, "rev-parse", "--show-toplevel")
        .decode("utf-8")
        .strip()
    ).resolve()
    try:
        skill_path = source_root.relative_to(repository_root).as_posix()
    except ValueError as exc:
        raise ValueError("source must be inside its Git repository") from exc
    if not skill_path or skill_path == ".":
        raise ValueError("source must identify a skill directory inside the repository")

    revision = _git(repository_root, "rev-parse", "HEAD").decode("ascii").strip()
    if revision != expected_revision:
        raise ValueError(
            "portable shaping source revision mismatch: "
            f"expected {expected_revision}, found {revision}"
        )
    repository = (
        _git(repository_root, "remote", "get-url", "origin")
        .decode("utf-8")
        .strip()
    )
    if repository != expected_repository:
        raise ValueError(
            "portable shaping source repository mismatch: "
            f"expected {expected_repository}, found {repository}"
        )
    dirty = _git(
        repository_root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--",
        skill_path,
    ).decode("utf-8")
    if dirty.strip():
        raise ValueError("portable shaping source has uncommitted changes")

    committed_sources = _committed_source_membership(
        repository_root,
        revision=revision,
        skill_path=skill_path,
    )
    expected_sources = set(SOURCE_TO_DESTINATION)
    if committed_sources != expected_sources:
        unexpected = sorted(committed_sources - expected_sources)
        missing = sorted(expected_sources - committed_sources)
        detail = []
        if unexpected:
            detail.append(f"unmapped source files: {', '.join(unexpected)}")
        if missing:
            detail.append(f"missing source files: {', '.join(missing)}")
        raise ValueError(
            "portable shaping source membership changed; " + "; ".join(detail)
        )

    previous_destinations = _previous_vendor_destinations(destination_root)
    current_destinations = set(SOURCE_TO_DESTINATION.values())
    stale_destinations = previous_destinations - current_destinations
    stale_paths = tuple(
        _stale_destination_path(destination_root, relative)
        for relative in sorted(stale_destinations)
    )

    contents: dict[str, bytes] = {}
    for source_relative in SOURCE_TO_DESTINATION:
        committed_path = f"{skill_path}/{source_relative}"
        contents[source_relative] = _git(
            repository_root,
            "show",
            f"{revision}:{committed_path}",
        )

    files = []
    for source_relative, destination_relative in SOURCE_TO_DESTINATION.items():
        destination = destination_root / destination_relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        content = contents[source_relative]
        destination.write_bytes(content)
        files.append(
            {
                "source": source_relative,
                "destination": destination_relative,
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )

    for stale_path in stale_paths:
        if stale_path.exists() or stale_path.is_symlink():
            stale_path.unlink()

    provenance: dict[str, object] = {
        "schema_version": 1,
        "source": {
            "repository": repository,
            "revision": revision,
            "skill_path": skill_path,
        },
        "materialised_files": files,
    }
    (destination_root / PROVENANCE_FILE).write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return provenance


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Vendor the portable shaping workflow into Brain core."
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument(
        "--repository",
        default="https://github.com/Rob-Morris/agent-skills.git",
    )
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    args = parser.parse_args()

    materialise(
        args.source.resolve(),
        args.destination.resolve(),
        expected_repository=args.repository,
        expected_revision=args.revision,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
