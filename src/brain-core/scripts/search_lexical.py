#!/usr/bin/env python3
"""
search_lexical.py — Portable lexical retrieval search.

Direct lexical-only CLI wrapper over `_search.lexical_query`.
"""

from __future__ import annotations

import argparse
import json
import sys

from _common import find_vault_root
from _search.filters import SearchFilters
import _search.lexical_query as lexical_query
import _search.mode as search_mode


def parse_args(argv):
    """Parse CLI arguments for lexical-only search."""
    parser = argparse.ArgumentParser(prog=argv[0])
    parser.add_argument("query", nargs="?")
    parser.add_argument("--type", dest="type_filter")
    parser.add_argument("--tag", dest="tag_filter")
    parser.add_argument("--status", dest="status_filter")
    parser.add_argument("--top-k", dest="top_k", type=int)
    parser.add_argument("--json", dest="json_mode", action="store_true")
    args = parser.parse_args(argv[1:])
    return (
        args.query,
        args.type_filter,
        args.tag_filter,
        args.status_filter,
        args.top_k,
        args.json_mode,
    )


def main():
    query, type_filter, tag_filter, status_filter, top_k, json_mode = parse_args(sys.argv)
    if not query:
        print(
            "Usage: search_lexical.py \"query\" [--type TYPE] [--tag TAG] "
            "[--status STATUS] [--top-k N] [--json]",
            file=sys.stderr,
        )
        sys.exit(1)

    vault_root = find_vault_root()
    filters = SearchFilters(type=type_filter, tag=tag_filter, status=status_filter)
    top_k = search_mode.DEFAULT_TOP_K if top_k is None else top_k

    try:
        index = lexical_query.load_index(vault_root)
    except lexical_query.IndexNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    results = lexical_query.search(index, query, vault_root, filters=filters, top_k=top_k)

    if json_mode:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return

    if not results:
        print("No results found.", file=sys.stderr)
        sys.exit(0)

    for i, result in enumerate(results, 1):
        print(f"\n{i}. [{result['score']:.4f}] {result['title']}")
        print(f"   {result['path']} ({result['type']})")
        if result["snippet"]:
            print(f"   {result['snippet']}")


if __name__ == "__main__":
    main()
