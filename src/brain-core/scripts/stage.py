#!/usr/bin/env python3
"""Create a Brain-owned body staging handle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from _common import (
    find_vault_root,
    MutationLockError,
    public_mutation_error_message,
    vault_mutation_lock,
)
from _staging import stage_body


def main(argv=None):
    parser = argparse.ArgumentParser(description="Stage body content for create/edit.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--body")
    group.add_argument("--body-file")
    parser.add_argument("--vault")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    content = args.body
    if args.body_file:
        try:
            content = Path(args.body_file).read_text(encoding="utf-8")
        except OSError as exc:
            parser.error(f"cannot read --body-file: {exc}")
    vault_root = str(find_vault_root(args.vault))
    try:
        with vault_mutation_lock(vault_root):
            result = stage_body(vault_root, content)
    except (MutationLockError, OSError, ValueError) as exc:
        message = public_mutation_error_message(exc)
        parser.error(message)
    print(json.dumps(result) if args.json else result["handle"])


if __name__ == "__main__":
    main()
