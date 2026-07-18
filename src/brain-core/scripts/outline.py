#!/usr/bin/env python3
"""Return the structural targets accepted by Brain edit operations."""

from __future__ import annotations

import argparse
import json

from _common import find_vault_root, outline_structural_nodes, parse_frontmatter
from _lifecycle.derived_cache_state import load_fresh_compiled_router
import read as read_mod


def outline_artefact(vault_root, router, path):
    content = read_mod.read_artefact(router, vault_root, path)
    if isinstance(content, dict) and "error" in content:
        raise ValueError(content["error"])
    _fields, body = parse_frontmatter(content)
    return {
        "path": path,
        "targets": outline_structural_nodes(body),
    }


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
    except ValueError as exc:
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
