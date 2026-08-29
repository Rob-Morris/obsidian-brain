#!/usr/bin/env python3
"""Materialise the portable shaping workflow into Brain's core package."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
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
