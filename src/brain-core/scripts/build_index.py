#!/usr/bin/env python3
"""
build_index.py — Brain-core BM25 retrieval index builder

Thin CLI wrapper over `_search`.

Import `_search.index` / `_search.assets` directly from Python; do not import
this module.
"""

from __future__ import annotations

import json
import sys

import _search.assets as _assets
from _search.errors import (
    CompiledRouterUnavailableError,
    RetrievalPersistenceError,
    UnreadableRetrievalSourceError,
)
import _search.index as _index
import _search.paths as _paths
import _semantic.config as semantic_config
from _common import find_vault_root


def main():
    vault_root = find_vault_root()
    try:
        cfg = semantic_config.load_config_checked(vault_root)
    except semantic_config.SemanticConfigLoadError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    try:
        build_result = _index.build_index(vault_root)
        index = build_result.index
        json_output = json.dumps(index, indent=2, ensure_ascii=False)

        if "--json" in sys.argv:
            print(json_output)
            return

        embeddings_result = _assets.persist_retrieval_outputs(
            vault_root,
            index,
            config=cfg,
            embedding_parts_by_path=build_result.embedding_parts_by_path,
        )
    except (
        UnreadableRetrievalSourceError,
        CompiledRouterUnavailableError,
        RetrievalPersistenceError,
    ) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    doc_count = index["meta"]["document_count"]
    term_count = len(index["corpus_stats"]["df"])
    embeddings_note = ", embeddings refreshed" if embeddings_result is not None else ""
    print(
        f"Built retrieval index: {doc_count} documents, "
        f"{term_count} unique terms{embeddings_note} → {_paths.OUTPUT_PATH}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
