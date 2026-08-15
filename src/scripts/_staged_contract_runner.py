#!/usr/bin/env python3
"""Run repository contracts from an exact materialisation of the Git index."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile


CHECKER_PATH = Path("src/scripts/check_repository_contracts.py")


def run_staged_checker(repo_root: Path, python_executable: str) -> int:
    """Materialise the index and execute its checker with its own imports."""
    root = repo_root.resolve()
    with tempfile.TemporaryDirectory(prefix="brain-staged-contracts-") as temp_dir:
        snapshot = Path(temp_dir) / "index"
        snapshot.mkdir()
        subprocess.run(
            [
                "git",
                "checkout-index",
                "--all",
                f"--prefix={snapshot}{os.sep}",
            ],
            cwd=root,
            check=True,
        )
        checker = snapshot / CHECKER_PATH
        if not checker.is_file():
            print(
                f"pre-commit: staged snapshot is missing {CHECKER_PATH}",
                file=sys.stderr,
            )
            return 1
        completed = subprocess.run(
            [
                python_executable,
                str(checker),
                "--staged",
                "--materialized-index-of",
                str(root),
            ],
            cwd=snapshot,
        )
        return completed.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo_root", type=Path)
    parser.add_argument("python_executable")
    args = parser.parse_args(argv)
    return run_staged_checker(args.repo_root, args.python_executable)


if __name__ == "__main__":
    raise SystemExit(main())
