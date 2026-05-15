#!/usr/bin/env python3
"""
search_index.py — Brain-core retrieval search

CLI entry wrapper over the `_search` domain owners.

Internal Python callers should prefer `_search.query` directly. Live runtime
retrieval code has converged onto that owner; this module is retained as a
stable script surface, not as the canonical internal import boundary.
"""

from __future__ import annotations

import json
import sys

import _search.query as _query
from _common import find_vault_root
from _search._lazy import LazyModuleProxy

_semantic_config = LazyModuleProxy("_semantic.config")

SearchModeUnavailableError = _query.SearchModeUnavailableError
IndexNotFoundError = _query.IndexNotFoundError

DEFAULT_TOP_K = _query.DEFAULT_TOP_K
SNIPPET_LENGTH = _query.SNIPPET_LENGTH
TITLE_BOOST = _query.TITLE_BOOST
RRF_K = _query.RRF_K
RRF_LEXICAL_WEIGHT = _query.RRF_LEXICAL_WEIGHT
RRF_SEMANTIC_WEIGHT = _query.RRF_SEMANTIC_WEIGHT
RRF_CANDIDATE_MULTIPLIER = _query.RRF_CANDIDATE_MULTIPLIER
SEMANTIC_CHAMPION_MARGIN = _query.SEMANTIC_CHAMPION_MARGIN
SEMANTIC_CHAMPION_BONUS = _query.SEMANTIC_CHAMPION_BONUS
LEXICAL_TITLE_CHAMPION_BONUS = _query.LEXICAL_TITLE_CHAMPION_BONUS
LEXICAL_TITLE_CHAMPION_MIN_MARGIN = _query.LEXICAL_TITLE_CHAMPION_MIN_MARGIN
LEXICAL_TITLE_CHAMPION_MIN_TOKENS = _query.LEXICAL_TITLE_CHAMPION_MIN_TOKENS
BRAIN_PRODUCT_NAMESPACE_PREFIX_TOKENS = _query.BRAIN_PRODUCT_NAMESPACE_PREFIX_TOKENS
SEMANTIC_RESCUE_MIN_QUERY_TOKENS = _query.SEMANTIC_RESCUE_MIN_QUERY_TOKENS
SEMANTIC_RESCUE_MAX_LEXICAL_TITLE_OVERLAP = _query.SEMANTIC_RESCUE_MAX_LEXICAL_TITLE_OVERLAP
SEMANTIC_RESCUE_TOP_OVERLAP_WINDOW = _query.SEMANTIC_RESCUE_TOP_OVERLAP_WINDOW
SEMANTIC_RESCUE_BONUS = _query.SEMANTIC_RESCUE_BONUS
SEARCH_MODES = _query.SEARCH_MODES
INDEX_PATH = _query.OUTPUT_PATH
SEARCHABLE_RESOURCES = _query.SEARCHABLE_RESOURCES

default_search_mode = _query.default_search_mode
resolve_search_mode = _query.resolve_search_mode
load_index = _query.load_index
load_doc_embeddings_or_unavailable = _query.load_doc_embeddings_or_unavailable
extract_snippet = _query.extract_snippet
search = _query.search
dispatch_search = _query.dispatch_search
search_semantic = _query.search_semantic
search_hybrid = _query.search_hybrid
search_resource = _query.search_resource


def parse_args(argv):
    """Parse CLI arguments."""
    query = None
    type_filter = None
    tag_filter = None
    status_filter = None
    top_k = DEFAULT_TOP_K
    json_mode = False
    mode = None

    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == "--type" and i + 1 < len(argv):
            type_filter = argv[i + 1]
            i += 2
        elif arg == "--tag" and i + 1 < len(argv):
            tag_filter = argv[i + 1]
            i += 2
        elif arg == "--status" and i + 1 < len(argv):
            status_filter = argv[i + 1]
            i += 2
        elif arg == "--top-k" and i + 1 < len(argv):
            top_k = int(argv[i + 1])
            i += 2
        elif arg == "--mode" and i + 1 < len(argv):
            mode = argv[i + 1]
            i += 2
        elif arg == "--json":
            json_mode = True
            i += 1
        elif not arg.startswith("--") and query is None:
            query = arg
            i += 1
        else:
            i += 1

    return query, type_filter, tag_filter, status_filter, top_k, json_mode, mode


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
        index = load_index(vault_root)
    except IndexNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    try:
        resolved_mode = resolve_search_mode(vault_root, mode, config=cfg)
        if resolved_mode == "lexical":
            results = search(index, query, vault_root, type_filter, tag_filter, status_filter, top_k)
        elif resolved_mode == "semantic":
            results = search_semantic(
                query,
                vault_root,
                type_filter=type_filter,
                tag_filter=tag_filter,
                status_filter=status_filter,
                top_k=top_k,
            )
        else:
            results = search_hybrid(
                index,
                query,
                vault_root,
                type_filter=type_filter,
                tag_filter=tag_filter,
                status_filter=status_filter,
                top_k=top_k,
            )
    except SearchModeUnavailableError as exc:
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
