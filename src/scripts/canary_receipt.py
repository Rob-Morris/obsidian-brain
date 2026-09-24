#!/usr/bin/env python3
"""Receipt grammar for required contributor canaries."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys


TASK_ID = re.compile(r"\[[0-9]+[a-z]?\]")
RECEIPT_LINE = re.compile(r"^(?P<id>\[[0-9]+[a-z]?\]) .+: (done([, ] ?.+)?|skip, ?\S.*)$")


class CanaryError(RuntimeError):
    """The receipt does not cover the brief. The brief itself is left in place."""


def task_ids(brief: str) -> list[str]:
    """Return task ids from a line-anchored ``## Tasks`` section, before ``## Log``."""
    lines = brief.splitlines()
    try:
        start = lines.index("## Tasks") + 1
    except ValueError:
        return []
    end = len(lines)
    for index in range(start, len(lines)):
        if lines[index] == "## Log":
            end = index
            break
    return sorted(set(TASK_ID.findall("\n".join(lines[start:end]))))


def receipt_problems(brief: str, receipt: str) -> tuple[str, ...]:
    """Return grammar problems. An empty Tasks section is a problem."""
    expected = task_ids(brief)
    if not expected:
        return ("brief has no task ids under ## Tasks",)
    problems: list[str] = []
    covered = {
        match.group("id")
        for line in receipt.splitlines()
        if (match := RECEIPT_LINE.fullmatch(line.strip()))
    }
    missing = [item for item in expected if item not in covered]
    if missing:
        problems.append("missing " + ", ".join(missing))
    bad = [
        stripped
        for line in receipt.splitlines()
        if (stripped := line.strip())
        and TASK_ID.match(stripped)
        and not RECEIPT_LINE.fullmatch(stripped)
    ]
    if bad:
        problems.append("lines must use 'done' or 'skip, reason'")
    return tuple(problems)


def check_files(brief: Path, receipt: Path) -> None:
    """Require the brief and a covering receipt. Do not delete either file."""
    if not brief.is_file():
        raise CanaryError(f"missing {brief}")
    if not task_ids(brief.read_text(encoding="utf-8")):
        raise CanaryError(f"{brief} has no task ids under ## Tasks")
    if not receipt.is_file():
        raise CanaryError(f"write {receipt.name} from {brief} before continuing")
    problems = receipt_problems(
        brief.read_text(encoding="utf-8"),
        receipt.read_text(encoding="utf-8"),
    )
    if problems:
        raise CanaryError("; ".join(problems))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brief", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.brief.is_file():
        print(f"canary: required brief {args.brief} not found", file=sys.stderr)
        return 2
    if not task_ids(args.brief.read_text(encoding="utf-8")):
        print(f"canary: {args.brief} has no task ids under ## Tasks", file=sys.stderr)
        return 2
    if not args.receipt.is_file():
        print(f"pre-commit: {args.receipt} not found.", file=sys.stderr)
        print(f"Read {args.brief} and write {args.receipt}", file=sys.stderr)
        print("confirming you've followed each task. Format:", file=sys.stderr)
        print("", file=sys.stderr)
        print("  [2] Label: done", file=sys.stderr)
        print("  [4] Label: skip, reason", file=sys.stderr)
        return 2
    problems = receipt_problems(
        args.brief.read_text(encoding="utf-8"),
        args.receipt.read_text(encoding="utf-8"),
    )
    if problems:
        print("pre-commit: " + "; ".join(problems), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
