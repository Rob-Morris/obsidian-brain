#!/usr/bin/env python3
"""
build_lexical_index.py — Portable lexical retrieval index builder.

Direct lexical-only CLI wrapper over `_search.index`.
"""

from __future__ import annotations

import json
import sys

from _common import find_vault_root
from _lifecycle.retrieval_errors import (
    RetrievalPersistenceError,
    UnreadableRetrievalSourceError,
)
import _search.index as search_index
import _search.paths as search_paths


def main():
    vault_root = find_vault_root()

    try:
        build_result = search_index.build_index(vault_root)
        index = build_result.index

        if "--json" in sys.argv:
            print(json.dumps(index, indent=2, ensure_ascii=False))
            return

        search_index.persist_retrieval_index(vault_root, index)
    except (UnreadableRetrievalSourceError, RetrievalPersistenceError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    doc_count = index["meta"]["document_count"]
    term_count = len(index["corpus_stats"]["df"])
    print(
        f"Built lexical retrieval index: {doc_count} documents, "
        f"{term_count} unique terms → {search_paths.OUTPUT_PATH}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
