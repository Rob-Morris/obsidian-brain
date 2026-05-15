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
import _search.index as _index
from _common import find_vault_root
from _search._lazy import LazyModuleProxy

_semantic_config = LazyModuleProxy("_semantic.config")


def main():
    vault_root = find_vault_root()
    try:
        cfg = _semantic_config.load_config_checked(vault_root)
    except _semantic_config.SemanticConfigLoadError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    index = _index.build_index(vault_root)
    json_output = json.dumps(index, indent=2, ensure_ascii=False)

    if "--json" in sys.argv:
        print(json_output)
        return

    embeddings_result = _assets.persist_retrieval_outputs(vault_root, index, config=cfg)

    doc_count = index["meta"]["document_count"]
    term_count = len(index["corpus_stats"]["df"])
    embeddings_note = ", embeddings refreshed" if embeddings_result is not None else ""
    print(
        f"Built retrieval index: {doc_count} documents, "
        f"{term_count} unique terms{embeddings_note} → {_index.OUTPUT_PATH}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
