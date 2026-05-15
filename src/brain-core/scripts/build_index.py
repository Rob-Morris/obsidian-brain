#!/usr/bin/env python3
"""
build_index.py — Brain-core BM25 retrieval index builder

CLI entry wrapper over the `_search` domain owners.

Internal Python callers should prefer `_search.index` / `_search.assets`
directly. Live runtime retrieval code has converged onto those owners; this
module is retained as a stable script surface, not as the canonical internal
import boundary.
"""

from __future__ import annotations

import json
import sys

import _search.assets as _assets
import _search.index as _index
from _common import find_vault_root
from _search._lazy import LazyModuleProxy

_semantic_config = LazyModuleProxy("_semantic.config")

extract_title = _index.extract_title
extract_type_description = _index.extract_type_description
parse_doc = _index.parse_doc
build_index = _index.build_index
persist_retrieval_index = _index.persist_retrieval_index
index_update = _index.index_update

OUTPUT_PATH = _index.OUTPUT_PATH
INDEX_VERSION = _index.INDEX_VERSION
BM25_K1 = _index.BM25_K1
BM25_B = _index.BM25_B
EMBEDDING_BODY_CHARS = _index.EMBEDDING_BODY_CHARS
EMBEDDING_HEADING_LIMIT = _index.EMBEDDING_HEADING_LIMIT
build_embeddings = _assets.build_embeddings
refresh_embeddings_outputs = _assets.refresh_embeddings_outputs
persist_retrieval_outputs = _assets.persist_retrieval_outputs


def main():
    vault_root = find_vault_root()
    try:
        cfg = _semantic_config.load_config_checked(vault_root)
    except _semantic_config.SemanticConfigLoadError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    index = build_index(vault_root)
    json_output = json.dumps(index, indent=2, ensure_ascii=False)

    if "--json" in sys.argv:
        print(json_output)
        return

    embeddings_result = persist_retrieval_outputs(vault_root, index, config=cfg)

    doc_count = index["meta"]["document_count"]
    term_count = len(index["corpus_stats"]["df"])
    embeddings_note = ", embeddings refreshed" if embeddings_result is not None else ""
    print(
        f"Built retrieval index: {doc_count} documents, "
        f"{term_count} unique terms{embeddings_note} → {OUTPUT_PATH}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
