#!/usr/bin/env python3
"""
search_index.py — Brain-core retrieval search

Thin CLI wrapper over `_search`.

Import the canonical `_search` submodules directly from Python; do not import
this module.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from _bootstrap.runtime import (
    handoff_current_script_to_managed_runtime,
    required_modules_for_scope,
)
from _common import find_vault_root
from _repair_common import build_repair_command


SearchFilters = None
lexical_query = None
search_mode = None
semantic_config = None


def _load_runtime_modules() -> None:
    """Load retrieval modules only after wrapper handoff is settled."""
    global SearchFilters, lexical_query, search_mode, semantic_config
    if SearchFilters is not None:
        return
    from _search.filters import SearchFilters as _SearchFilters
    import _search.lexical_query as _lexical_query
    import _search.mode as _search_mode
    import _semantic.config as _semantic_config

    SearchFilters = _SearchFilters
    lexical_query = _lexical_query
    search_mode = _search_mode
    semantic_config = _semantic_config


if __name__ != "__main__":
    _load_runtime_modules()


def parse_args(argv):
    """Parse CLI arguments for `search_index.py`."""
    parser = argparse.ArgumentParser(prog=argv[0])
    parser.add_argument("query", nargs="?")
    parser.add_argument("--type", dest="type_filter")
    parser.add_argument("--tag", dest="tag_filter")
    parser.add_argument("--status", dest="status_filter")
    parser.add_argument("--top-k", dest="top_k", type=int)
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
        handoff_current_script_to_managed_runtime(
            vault_root,
            dependency_owner="search_index.py",
            required_modules=required_modules_for_scope("runtime"),
            script_path=os.path.abspath(__file__),
            forwarded_args=sys.argv[1:],
        )
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print(build_repair_command(vault_root, "runtime"), file=sys.stderr)
        sys.exit(1)

    _load_runtime_modules()
    filters = SearchFilters(
        type=type_filter,
        tag=tag_filter,
        status=status_filter,
    )
    top_k = search_mode.DEFAULT_TOP_K if top_k is None else top_k
    try:
        cfg = semantic_config.load_config_checked(vault_root)
    except semantic_config.SemanticConfigLoadError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    try:
        index = lexical_query.load_index(vault_root)
    except lexical_query.IndexNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    try:
        resolved_mode = search_mode.resolve_search_mode(vault_root, mode, config=cfg)
        results = search_mode.dispatch_search(
            index,
            query,
            vault_root,
            resolved_mode,
            filters=filters,
            top_k=top_k,
        )
    except search_mode.SearchModeUnavailableError as exc:
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
