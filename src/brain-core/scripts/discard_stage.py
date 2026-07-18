#!/usr/bin/env python3
"""Discard an unused Brain body staging handle."""

import argparse
import json

from _common import (
    find_vault_root,
    MutationLockError,
    public_mutation_error_message,
    vault_mutation_lock,
)
from _staging import discard_staged_body


def main(argv=None):
    parser = argparse.ArgumentParser(description="Discard an unused Brain body handle.")
    parser.add_argument("handle")
    parser.add_argument("--vault")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    vault_root = str(find_vault_root(args.vault))
    try:
        with vault_mutation_lock(vault_root):
            discarded = discard_staged_body(vault_root, args.handle)
    except (MutationLockError, OSError, ValueError) as exc:
        message = public_mutation_error_message(exc)
        parser.error(message)
    result = {"handle": args.handle, "discarded": discarded}
    print(json.dumps(result) if args.json else ("discarded" if discarded else "already absent"))


if __name__ == "__main__":
    main()
