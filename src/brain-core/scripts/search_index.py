#!/usr/bin/env python3
"""
search_index.py — Brain-core retrieval search

Thin CLI wrapper over `_search`.

Import `_search.query` directly from Python; do not import this module.
"""

from __future__ import annotations

import argparse
import json
import sys

import _search.query as _query
from _common import find_vault_root
from _search._lazy import LazyModuleProxy

_semantic_config = LazyModuleProxy("_semantic.config")


def parse_args(argv):
    """Parse CLI arguments for `search_index.py`."""
    parser = argparse.ArgumentParser(prog=argv[0])
    parser.add_argument("query", nargs="?")
    parser.add_argument("--type", dest="type_filter")
    parser.add_argument("--tag", dest="tag_filter")
    parser.add_argument("--status", dest="status_filter")
    parser.add_argument("--top-k", dest="top_k", type=int, default=_query.DEFAULT_TOP_K)
    parser.add_argument("--mode")
    parser.add_argument("--json", dest="json_mode", action="store_true")
    args = parser.parse_args(argv[1:])
    return (
        args.query,
        args.type_filter,
        args.tag_filter,
        args.status_filter,
        args.top_k,
        args.json_mode,
        args.mode,
    )


def main():
    query, type_filter, tag_filter, status_filter, top_k, json_mode, mode = parse_args(sys.argv)

    if not query:
        print(
            "Usage: search_index.py \"query\" [--type TYPE] [--tag TAG] "
            "[--status STATUS] [--top-k N] [--mode lexical|semantic|hybrid] [--json]",
            file=sys.stderr,
        )
        sys.exit(1)

    vault_root = find_vault_root()
    try:
        cfg = _semantic_config.load_config_checked(vault_root)
    except _semantic_config.SemanticConfigLoadError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    try:
        index = _query.load_index(vault_root)
    except _query.IndexNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    try:
        resolved_mode = _query.resolve_search_mode(vault_root, mode, config=cfg)
        if resolved_mode == "lexical":
            results = _query.search(
                index,
                query,
                vault_root,
                type_filter,
                tag_filter,
                status_filter,
                top_k,
            )
        elif resolved_mode == "semantic":
            results = _query.search_semantic(
                query,
                vault_root,
                type_filter=type_filter,
                tag_filter=tag_filter,
                status_filter=status_filter,
                top_k=top_k,
            )
        else:
            results = _query.search_hybrid(
                index,
                query,
                vault_root,
                type_filter=type_filter,
                tag_filter=tag_filter,
                status_filter=status_filter,
                top_k=top_k,
            )
    except _query.SearchModeUnavailableError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if json_mode:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return

    if not results:
        print("No results found.", file=sys.stderr)
        sys.exit(0)

    for i, r in enumerate(results, 1):
        print(f"\n{i}. [{r['score']:.4f}] {r['title']}")
        print(f"   {r['path']} ({r['type']})")
        if r["snippet"]:
            print(f"   {r['snippet']}")


if __name__ == "__main__":
    main()
