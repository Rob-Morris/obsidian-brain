#!/usr/bin/env python3
"""Explicit lifecycle-field commands for Brain artefacts."""

from __future__ import annotations

import argparse
import json

import edit
from _common import (
    find_vault_root,
    MutationLockError,
    public_mutation_error_message,
    PartialApplyError,
    vault_mutation_lock,
)
from _lifecycle.derived_cache_state import load_fresh_compiled_router


def _build_parser():
    parser = argparse.ArgumentParser(
        description="Change lifecycle-owned artefact metadata safely."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    parent = sub.add_parser("reparent", help="Change an artefact's authoritative parent.")
    parent.add_argument("path")
    parent.add_argument("--parent", help="Canonical parent reference; omit with --clear.")
    parent.add_argument("--clear", action="store_true")

    status = sub.add_parser("set-status", help="Change lifecycle status.")
    status.add_argument("path")
    status.add_argument("status")

    key = sub.add_parser("set-key", help="Change a living artefact key.")
    key.add_argument("path")
    key.add_argument("key")

    naming = sub.add_parser("set-naming-field", help="Change a naming-driving field.")
    naming.add_argument("path")
    naming.add_argument("field")
    naming.add_argument("value")

    for command in (parent, status, key, naming):
        command.add_argument("--vault")
        command.add_argument("--json", action="store_true")
    return parser


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "reparent":
        if bool(args.parent) == bool(args.clear):
            parser.error("reparent requires exactly one of --parent or --clear")
        field, value = "parent", None if args.clear else args.parent
    elif args.command == "set-status":
        field, value = "status", args.status
    elif args.command == "set-key":
        field, value = "key", args.key
    else:
        field, value = args.field, args.value

    vault_root = str(find_vault_root(args.vault))
    router = load_fresh_compiled_router(vault_root)
    if "error" in router:
        parser.error(router["error"])
    try:
        with vault_mutation_lock(vault_root):
            result = edit.update_lifecycle_field(
                vault_root, router, args.path, field, value
            )
    except (MutationLockError, PartialApplyError, ValueError, FileNotFoundError) as exc:
        message = public_mutation_error_message(exc)
        parser.error(message)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(
            f"Updated {field}: {result['old_value']!r} -> {result['new_value']!r}; "
            f"{result['resolved_path']} -> {result['path']}"
        )


if __name__ == "__main__":
    main()
