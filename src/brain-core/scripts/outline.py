#!/usr/bin/env python3
"""Return the structural targets accepted by Brain edit operations."""

from __future__ import annotations

import argparse
import json

from _common import find_vault_root
from _lifecycle.derived_cache_state import load_fresh_compiled_router
from _portable.artefact_outline import outline_artefact as _portable_outline_artefact


def outline_artefact(vault_root, router, path):
    return _portable_outline_artefact(vault_root, router, path)


def _build_parser():
    parser = argparse.ArgumentParser(
        description="List editable headings and callouts in a Brain artefact."
    )
    parser.add_argument("path", help="Artefact key, relative path, or resolvable name.")
    parser.add_argument("--vault")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    vault_root = str(find_vault_root(args.vault))
    router = load_fresh_compiled_router(vault_root)
    if "error" in router:
        parser.error(router["error"])
    try:
        result = outline_artefact(vault_root, router, args.path)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    print(f"{len(result['targets'])} editable target(s) in {args.path}")
    for item in result["targets"]:
        indent = "  " * len(item["within"])
        occurrence = (
            f" (occurrence {item['occurrence']})" if item["occurrence"] > 1 else ""
        )
        print(f"{item['line']:>5}  {indent}{item['target']}{occurrence}")


if __name__ == "__main__":
    main()
