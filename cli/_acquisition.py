"""Resolve a published release once and retain its exact source identity."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess

OFFICIAL_ORIGIN = "https://github.com/rob-morris/obsidian-brain.git"
RECEIPT = ".brain-source-receipt.json"


@dataclass(frozen=True, slots=True)
class AcquiredSource:
    source_root: Path
    receipt: dict[str, str]


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
        timeout=120, check=False,
    )
    if result.returncode:
        raise ValueError(f"Source acquisition failed ({args[0]}): {result.stderr.strip()}")
    return result.stdout.strip()


def acquire(version: str, destination: Path, *, repository: str = OFFICIAL_ORIGIN) -> AcquiredSource:
    """Acquire latest or an exact version into a new, caller-owned directory."""
    if version != "latest" and not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", version):
        raise ValueError("Release version must be latest or an exact X.Y.Z")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Source acquisition destination must be empty")
    destination.mkdir(parents=True, exist_ok=True)
    _git(destination, "init", "--quiet")
    _git(destination, "remote", "add", "origin", repository)
    ref = "refs/heads/main" if version == "latest" else f"refs/tags/v{version}"
    _git(destination, "fetch", "--quiet", "--depth=1", "origin", f"{ref}:{ref}" if version != "latest" else ref)
    commit = _git(destination, "rev-parse", "FETCH_HEAD^{commit}")
    _git(destination, "checkout", "--quiet", "--detach", commit)
    return AcquiredSource(destination.resolve(), certify_checkout(destination, version))


def certify_checkout(destination: Path, version: str) -> dict[str, str]:
    """Certify a newly cloned snapshot and persist its acquisition identity."""
    commit = _git(destination, "rev-parse", "HEAD^{commit}")
    actual = (destination / "src/brain-core/VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", actual):
        raise ValueError("Selected source has an invalid Core VERSION")
    if version != "latest" and actual != version:
        raise ValueError("Release tag conflicts with the selected source VERSION")
    tag = f"refs/tags/v{actual}"
    if version == "latest":
        _git(destination, "fetch", "--quiet", "--depth=1", "origin", tag)
        tagged_commit = _git(destination, "rev-parse", "FETCH_HEAD^{commit}")
    else:
        tagged_commit = _git(destination, "rev-parse", tag + "^{commit}")
    if tagged_commit != commit:
        raise ValueError("Published source and its release tag identify different commits")
    receipt = {"schema": "brain.source-acquisition/1", "origin": _git(destination, "remote", "get-url", "origin"),
               "ref": tag, "commit": commit, "tree": _git(destination, "rev-parse", "HEAD^{tree}"),
               "version": actual}
    (destination / RECEIPT).write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def verify_acquired(root: Path) -> dict[str, str]:
    """Validate a retained acquisition receipt without resolving a moving ref again."""
    receipt = json.loads((root / RECEIPT).read_text(encoding="utf-8"))
    if receipt.get("schema") != "brain.source-acquisition/1":
        raise ValueError("Source acquisition receipt schema is unsupported")
    if _git(root, "rev-parse", "HEAD^{commit}") != receipt.get("commit") or _git(root, "rev-parse", "HEAD^{tree}") != receipt.get("tree"):
        raise ValueError("Source acquisition no longer matches its receipt")
    if _git(root, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("Acquired release source has tracked modifications")
    from _distribution import SOURCE_ENTRIES
    tracked = set(_git(root, "ls-files", "-z").split("\0"))
    for entry in SOURCE_ENTRIES:
        base = root / entry
        candidates = [base, *base.rglob("*")] if base.is_dir() and not base.is_symlink() else [base]
        for path in candidates:
            relative = path.relative_to(root)
            if any(part == "__pycache__" or part.endswith((".pyc", ".pyo", ".DS_Store")) for part in relative.parts):
                continue
            if (path.is_file() or path.is_symlink()) and relative.as_posix() not in tracked:
                raise ValueError(f"Acquired source has an extra deployable file: {relative}")
    if (root / "src/brain-core/VERSION").read_text().strip() != receipt.get("version"):
        raise ValueError("Source VERSION no longer matches its receipt")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("destination", type=Path)
    parser.add_argument("--certify-checkout", action="store_true")
    parser.add_argument("--verify-retained", action="store_true")
    args = parser.parse_args()
    try:
        result = (verify_acquired(args.destination) if args.verify_retained else
                  certify_checkout(args.destination, args.version) if args.certify_checkout
                  else acquire(args.version, args.destination).receipt)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"{exc}\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
