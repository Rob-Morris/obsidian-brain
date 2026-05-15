"""Lexical retrieval execution and persisted index loading."""

from __future__ import annotations

import json
import math
import os

from .filters import SearchFilters
from .lexical import tokenise
from .mode import DEFAULT_TOP_K
from .paths import OUTPUT_PATH
from .snippet import attach_snippets


TITLE_BOOST = 3.0


class IndexNotFoundError(FileNotFoundError):
    """Raised when the persisted lexical retrieval index is missing."""


def load_index(vault_root):
    """Load the pre-built retrieval index."""
    index_path = os.path.join(str(vault_root), OUTPUT_PATH)
    if not os.path.isfile(index_path):
        raise IndexNotFoundError(
            f"retrieval index not found at {OUTPUT_PATH}. Run build_index.py first."
        )

    with open(index_path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def search(
    index,
    query,
    vault_root,
    *,
    filters: SearchFilters = SearchFilters(),
    top_k=DEFAULT_TOP_K,
    attach_snippets_to_results=True,
):
    """Score documents against one query using BM25."""
    query_tokens = tokenise(query)
    if not query_tokens:
        return []

    corpus = index["corpus_stats"]
    params = index["bm25_params"]
    k1 = params["k1"]
    b = params["b"]
    total_docs = corpus["total_docs"]
    avg_dl = corpus["avg_dl"]
    df = corpus["df"]

    results = []
    for doc in index["documents"]:
        if not filters.matches(doc):
            continue

        score = 0.0
        dl = doc["doc_length"]
        tf = doc["tf"]
        title_tf = doc.get("title_tf", {})

        for term in query_tokens:
            term_df = df.get(term, 0)
            if term_df == 0:
                continue

            idf = math.log((total_docs - term_df + 0.5) / (term_df + 0.5) + 1)
            term_tf_val = tf.get(term, 0)
            if term_tf_val > 0 and avg_dl > 0:
                tf_norm = (term_tf_val * (k1 + 1)) / (
                    term_tf_val + k1 * (1 - b + b * dl / avg_dl)
                )
                score += idf * tf_norm

            if title_tf.get(term, 0) > 0:
                score += idf * TITLE_BOOST

        if score > 0:
            results.append(
                {
                    "path": doc["path"],
                    "title": doc["title"],
                    "type": doc["type"],
                    "status": doc.get("status"),
                    "score": round(score, 4),
                }
            )

    results.sort(key=lambda result: result["score"], reverse=True)
    top = results[:top_k]
    if attach_snippets_to_results:
        attach_snippets(top, vault_root, query_tokens)
    return top
