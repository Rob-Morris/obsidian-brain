#!/usr/bin/env python3
"""
search_index.py — Brain-core BM25 retrieval search

Loads the pre-built retrieval index and scores a query using BM25.
Supports filtering by type, tag, status, and top-k limit.

Usage:
    python3 search_index.py "query text"
    python3 search_index.py "query" --type living/design --top-k 5
    python3 search_index.py "query" --tag brain-core
    python3 search_index.py "query" --status shaping
    python3 search_index.py "query" --json
"""

import json
import math
import os
import re
import sys

from _common import _FM_RE, find_vault_root, tokenise

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INDEX_PATH = os.path.join(".brain", "local", "retrieval-index.json")
DEFAULT_TOP_K = 10
SNIPPET_LENGTH = 200
TITLE_BOOST = 3.0


# ---------------------------------------------------------------------------
# Index loading
# ---------------------------------------------------------------------------

def load_index(vault_root):
    """Load the pre-built retrieval index."""
    index_path = os.path.join(str(vault_root), INDEX_PATH)
    if not os.path.isfile(index_path):
        print(
            f"Error: retrieval index not found at {INDEX_PATH}. "
            f"Run build_index.py first.",
            file=sys.stderr,
        )
        sys.exit(1)

    with open(index_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Snippet extraction
# ---------------------------------------------------------------------------

def extract_snippet(vault_root, rel_path, query_tokens, length=SNIPPET_LENGTH):
    """Extract a ~length char snippet centred on the first query term match."""
    abs_path = os.path.join(str(vault_root), rel_path)
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return ""

    # Strip frontmatter
    fm_match = _FM_RE.match(text)
    if fm_match:
        body = text[fm_match.end():]
    else:
        body = text

    # Clean up whitespace
    body = re.sub(r"\s+", " ", body).strip()

    if not body:
        return ""

    # Find first occurrence of any query token
    body_lower = body.lower()
    best_pos = None
    for token in query_tokens:
        pos = body_lower.find(token)
        if pos >= 0 and (best_pos is None or pos < best_pos):
            best_pos = pos

    if best_pos is None:
        # No match — return start of body
        snippet = body[:length]
    else:
        # Centre window around match
        half = length // 2
        start = max(0, best_pos - half)
        end = min(len(body), start + length)

        # Expand to nearest word boundary
        if start > 0:
            space = body.rfind(" ", 0, start)
            if space >= 0 and (start - space) < 30:
                start = space + 1
        if end < len(body):
            space = body.find(" ", end)
            if space >= 0 and (space - end) < 30:
                end = space

        snippet = body[start:end]

    # Add ellipsis indicators
    if not body.startswith(snippet):
        snippet = "…" + snippet
    if not body.endswith(snippet.lstrip("…")):
        snippet = snippet + "…"

    return snippet


# ---------------------------------------------------------------------------
# BM25 search
# ---------------------------------------------------------------------------

def search(index, query, vault_root, type_filter=None, tag_filter=None,
           status_filter=None, top_k=DEFAULT_TOP_K):
    """Score documents against query using BM25. Returns ranked results."""
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
        # Apply filters
        if type_filter and doc["type"] != type_filter:
            continue
        if tag_filter and tag_filter not in doc.get("tags", []):
            continue
        if status_filter and doc.get("status") != status_filter:
            continue

        # BM25 score with title boosting
        score = 0.0
        dl = doc["doc_length"]
        tf = doc["tf"]
        title_tf = doc.get("title_tf", {})

        for term in query_tokens:
            term_df = df.get(term, 0)
            if term_df == 0:
                continue

            # IDF: log((N - df + 0.5) / (df + 0.5) + 1)
            idf = math.log((total_docs - term_df + 0.5) / (term_df + 0.5) + 1)

            # Body TF component
            term_tf_val = tf.get(term, 0)
            if term_tf_val > 0 and avg_dl > 0:
                tf_norm = (term_tf_val * (k1 + 1)) / (term_tf_val + k1 * (1 - b + b * dl / avg_dl))
                score += idf * tf_norm

            # Title boost: flat IDF * boost when term appears in title
            if title_tf.get(term, 0) > 0:
                score += idf * TITLE_BOOST

        if score > 0:
            snippet = extract_snippet(vault_root, doc["path"], query_tokens)
            results.append({
                "path": doc["path"],
                "title": doc["title"],
                "type": doc["type"],
                "status": doc.get("status"),
                "score": round(score, 4),
                "snippet": snippet,
            })

    # Sort by score descending
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:top_k]


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------

def parse_args(argv):
    """Parse CLI arguments. Returns (query, type_filter, tag_filter, status_filter, top_k, json_mode)."""
    query = None
    type_filter = None
    tag_filter = None
    status_filter = None
    top_k = DEFAULT_TOP_K
    json_mode = False

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
        elif arg == "--json":
            json_mode = True
            i += 1
        elif not arg.startswith("--") and query is None:
            query = arg
            i += 1
        else:
            i += 1

    return query, type_filter, tag_filter, status_filter, top_k, json_mode


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    query, type_filter, tag_filter, status_filter, top_k, json_mode = parse_args(sys.argv)

    if not query:
        print("Usage: search_index.py \"query\" [--type TYPE] [--tag TAG] [--status STATUS] [--top-k N] [--json]", file=sys.stderr)
        sys.exit(1)

    vault_root = find_vault_root()
    index = load_index(vault_root)
    results = search(index, query, vault_root, type_filter, tag_filter, status_filter, top_k)

    if json_mode:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
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
