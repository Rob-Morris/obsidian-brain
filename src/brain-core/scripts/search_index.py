#!/usr/bin/env python3
"""
search_index.py — Brain-core retrieval search

Loads the pre-built retrieval index and supports lexical BM25 search plus
optional semantic and hybrid retrieval over persisted embedding sidecars.
Supports filtering by type, tag, status, and top-k limit.

Usage:
    python3 search_index.py "query text"
    python3 search_index.py "query" --type living/design --top-k 5
    python3 search_index.py "query" --tag brain-core
    python3 search_index.py "query" --status shaping
    python3 search_index.py "query" --mode hybrid
    python3 search_index.py "query" --json
"""

import json
import math
import os
import re
import sys

import _semantic.runtime as _semantic
from _common import FM_RE, LEXICAL_ANCHOR_RE, find_vault_root, tokenise

# Backwards-compatible alias for tests and older local call sites.
_retrieval_embeddings = _semantic

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INDEX_PATH = os.path.join(".brain", "local", "retrieval-index.json")
DEFAULT_TOP_K = 10
SNIPPET_LENGTH = 200
TITLE_BOOST = 3.0
RRF_K = 60
RRF_LEXICAL_WEIGHT = 1.0
RRF_SEMANTIC_WEIGHT = 1.0
RRF_CANDIDATE_MULTIPLIER = 3
SEMANTIC_CHAMPION_MARGIN = 0.05
SEMANTIC_CHAMPION_BONUS = 0.002
SEMANTIC_RESCUE_MIN_QUERY_TOKENS = 5
SEMANTIC_RESCUE_MAX_LEXICAL_TITLE_OVERLAP = 1
SEMANTIC_RESCUE_TOP_OVERLAP_WINDOW = 3
SEMANTIC_RESCUE_BONUS = 0.015
SEARCH_MODES = {"lexical", "semantic", "hybrid"}


class SearchModeUnavailableError(ValueError):
    """Raised when the caller requests a retrieval mode that is unavailable."""


def default_search_mode(
    vault_root,
    *,
    config=None,
    doc_embeddings=None,
    embeddings_meta=None,
):
    """Return the best-effort default search mode."""
    if (
        _semantic.semantic_retrieval_enabled(vault_root, config=config)
        and _semantic.semantic_engine_available(
            vault_root,
            config=config,
            doc_embeddings=doc_embeddings,
            embeddings_meta=embeddings_meta,
            skip_sidecar_check=True,
        )
    ):
        return "hybrid"
    return "lexical"


def resolve_search_mode(
    vault_root,
    mode,
    *,
    config=None,
    doc_embeddings=None,
    embeddings_meta=None,
):
    """Validate and resolve a requested search mode.

    Returns the resolved mode string. Raises SearchModeUnavailableError when
    the requested mode is unknown or its prerequisites (feature flag, runtime
    deps, embedding sidecars) are not satisfied.
    """
    if mode is not None and mode not in SEARCH_MODES:
        valid = ", ".join(sorted(SEARCH_MODES))
        raise SearchModeUnavailableError(
            f"unknown search mode '{mode}'. Valid modes: {valid}"
        )

    resolved = mode if mode is not None else default_search_mode(
        vault_root,
        config=config,
        doc_embeddings=doc_embeddings,
        embeddings_meta=embeddings_meta,
    )

    if resolved in {"semantic", "hybrid"}:
        if not _semantic.semantic_retrieval_enabled(
            vault_root, config=config
        ):
            raise SearchModeUnavailableError(
                "semantic retrieval is disabled; enable "
                "defaults.flags.semantic_retrieval or use mode='lexical'"
            )
        if not _semantic.semantic_engine_available(
            vault_root,
            config=config,
            doc_embeddings=doc_embeddings,
            embeddings_meta=embeddings_meta,
            skip_sidecar_check=True,
        ):
            raise SearchModeUnavailableError(
                "semantic retrieval is unavailable: semantic runtime is not "
                "installed or dependencies are unavailable"
            )

    return resolved


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


def _encode_query(query, *, query_encoder=None):
    """Encode a query string for semantic retrieval."""
    try:
        return _semantic.encode_query(
            query,
            query_encoder=query_encoder,
        )
    except ImportError as exc:
        raise SearchModeUnavailableError(
            "semantic retrieval dependencies are unavailable"
        ) from exc


def _entry_matches_filters(entry, type_filter, tag_filter, status_filter):
    """Apply the standard artefact filters to an index or embedding entry."""
    if type_filter and entry.get("type") != type_filter:
        return False
    if tag_filter and tag_filter not in entry.get("tags", []):
        return False
    if status_filter and entry.get("status") != status_filter:
        return False
    return True


# ---------------------------------------------------------------------------
# Snippet extraction
# ---------------------------------------------------------------------------

def extract_snippet(vault_root, rel_path, query_tokens, length=SNIPPET_LENGTH,
                    *, body=None):
    """Extract a ~length char snippet centred on the first query term match.

    If *body* is provided, use it directly (already frontmatter-stripped).
    Otherwise read and strip the file at *rel_path*.
    """
    if body is None:
        abs_path = os.path.join(str(vault_root), rel_path)
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                text = f.read()
        except (OSError, UnicodeDecodeError):
            return ""

        # Strip frontmatter
        fm_match = FM_RE.match(text)
        body = text[fm_match.end():] if fm_match else text

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
           status_filter=None, top_k=DEFAULT_TOP_K, *, attach_snippets=True):
    """Score documents against query using BM25. Returns ranked results.

    When `attach_snippets` is False, results are returned without snippets
    (caller is responsible for attaching them later).
    """
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
        if not _entry_matches_filters(doc, type_filter, tag_filter, status_filter):
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
            results.append({
                "path": doc["path"],
                "title": doc["title"],
                "type": doc["type"],
                "status": doc.get("status"),
                "score": round(score, 4),
            })

    # Sort by score descending
    results.sort(key=lambda r: r["score"], reverse=True)
    top = results[:top_k]
    if attach_snippets:
        _attach_snippets(top, vault_root, query_tokens)
    return top


def _attach_snippets(results, vault_root, query_tokens):
    """Attach a snippet to each result in-place. Hot-path helper used after
    top-k truncation so disk reads scale with returned results, not candidates.
    """
    for result in results:
        result["snippet"] = extract_snippet(vault_root, result["path"], query_tokens)


def search_semantic(
    query,
    vault_root,
    *,
    type_filter=None,
    tag_filter=None,
    status_filter=None,
    top_k=DEFAULT_TOP_K,
    doc_embeddings=None,
    embeddings_meta=None,
    query_encoder=None,
    attach_snippets=True,
):
    """Rank documents by cosine similarity against persisted document vectors.

    When `attach_snippets` is False, results are returned without snippets
    (caller is responsible for attaching them later).
    """
    query_tokens = tokenise(query)
    if not query_tokens:
        return []

    if doc_embeddings is None or embeddings_meta is None:
        doc_embeddings, embeddings_meta = _retrieval_embeddings.load_doc_embeddings(
            vault_root
        )
    if doc_embeddings is None or embeddings_meta is None:
        raise SearchModeUnavailableError(
            "semantic retrieval is unavailable: embeddings sidecars are missing"
        )

    if query_encoder is None:
        query_vec = _encode_query(query)
    else:
        query_vec = _encode_query(query, query_encoder=query_encoder)
    ranked = _semantic.rank_against(
        query_vec,
        doc_embeddings,
        embeddings_meta.get("documents", []),
        filter_fn=lambda entry: _entry_matches_filters(
            entry, type_filter, tag_filter, status_filter
        ),
        top_k=top_k,
    )
    top = [
        {
            "path": entry["path"],
            "title": entry["title"],
            "type": entry["type"],
            "status": entry.get("status"),
            "score": round(entry["score"], 4),
        }
        for entry in ranked
    ]
    if attach_snippets:
        _attach_snippets(top, vault_root, query_tokens)
    return top


def _semantic_champion_bonus_applies(semantic_results):
    return (
        len(semantic_results) >= 2
        and semantic_results[0]["score"] - semantic_results[1]["score"] >= SEMANTIC_CHAMPION_MARGIN
    )


def _semantic_rescue_applies(query_tokens, lexical_results, semantic_results):
    """Return whether hybrid should apply the stronger semantic rescue bonus."""
    query_token_set = set(query_tokens)
    if len(query_token_set) < SEMANTIC_RESCUE_MIN_QUERY_TOKENS:
        return False
    if not lexical_results or not semantic_results:
        return False

    lexical_title_tokens = set(tokenise(lexical_results[0]["title"]))
    title_overlap = len(query_token_set & lexical_title_tokens)
    if title_overlap > SEMANTIC_RESCUE_MAX_LEXICAL_TITLE_OVERLAP:
        return False

    lexical_top = {
        result["path"] for result in lexical_results[:SEMANTIC_RESCUE_TOP_OVERLAP_WINDOW]
    }
    semantic_top = {
        result["path"] for result in semantic_results[:SEMANTIC_RESCUE_TOP_OVERLAP_WINDOW]
    }
    return not (lexical_top & semantic_top)


def _fuse_rrf(lexical_results, semantic_results, *, top_k, query_tokens=()):
    """Fuse two ranked result lists with Reciprocal Rank Fusion.

    Applies two post-RRF score nudges before truncating to `top_k`: a small
    tie-break bonus when the semantic leg has a clearly dominant top-1, and a
    larger rescue bonus when the lexical and semantic legs are disjoint and
    the lexical top is weakly title-grounded against the query.
    """
    fused = {}
    for weight, results in (
        (RRF_LEXICAL_WEIGHT, lexical_results),
        (RRF_SEMANTIC_WEIGHT, semantic_results),
    ):
        for rank, result in enumerate(results, start=1):
            path = result["path"]
            entry = fused.setdefault(
                path,
                {
                    "path": result["path"],
                    "title": result["title"],
                    "type": result["type"],
                    "status": result.get("status"),
                    "snippet": result.get("snippet", ""),
                    "score": 0.0,
                },
            )
            entry["score"] += weight / (RRF_K + rank)
    if _semantic_champion_bonus_applies(semantic_results):
        champion = fused.get(semantic_results[0]["path"])
        if champion is not None:
            champion["score"] += SEMANTIC_CHAMPION_BONUS
    if _semantic_rescue_applies(query_tokens, lexical_results, semantic_results):
        rescue = fused.get(semantic_results[0]["path"])
        if rescue is not None:
            rescue["score"] += SEMANTIC_RESCUE_BONUS
    ranked = sorted(fused.values(), key=lambda result: result["score"], reverse=True)
    for result in ranked:
        result["score"] = round(result["score"], 6)
    return ranked[:top_k]


def search_hybrid(
    index,
    query,
    vault_root,
    *,
    type_filter=None,
    tag_filter=None,
    status_filter=None,
    top_k=DEFAULT_TOP_K,
    doc_embeddings=None,
    embeddings_meta=None,
    query_encoder=None,
):
    """Fuse lexical BM25 and semantic retrieval with RRF.

    Snippets are attached once after RRF truncation so disk reads scale with
    `top_k`, not `top_k * RRF_CANDIDATE_MULTIPLIER * 2` (lexical + semantic
    candidate sets).

    Exact-anchor queries (version strings, ticket codes) keep the lexical
    champion outright so hybrid does not dilute obvious exact-match wins.
    """
    if LEXICAL_ANCHOR_RE.search(query):
        return search(
            index,
            query,
            vault_root,
            type_filter=type_filter,
            tag_filter=tag_filter,
            status_filter=status_filter,
            top_k=top_k,
        )
    query_tokens = tokenise(query)
    candidate_k = top_k * RRF_CANDIDATE_MULTIPLIER
    lexical_results = search(
        index,
        query,
        vault_root,
        type_filter=type_filter,
        tag_filter=tag_filter,
        status_filter=status_filter,
        top_k=candidate_k,
        attach_snippets=False,
    )
    semantic_results = search_semantic(
        query,
        vault_root,
        type_filter=type_filter,
        tag_filter=tag_filter,
        status_filter=status_filter,
        top_k=candidate_k,
        doc_embeddings=doc_embeddings,
        embeddings_meta=embeddings_meta,
        query_encoder=query_encoder,
        attach_snippets=False,
    )
    fused = _fuse_rrf(
        lexical_results,
        semantic_results,
        top_k=top_k,
        query_tokens=query_tokens,
    )
    _attach_snippets(fused, vault_root, query_tokens)
    return fused


# ---------------------------------------------------------------------------
# Resource-scoped search (non-artefact)
# ---------------------------------------------------------------------------

# Map resource → (router_key, doc_field, extra_fields).
# doc_field is the key to the backing file (None for inline-only resources).
# extra_fields are additional item keys whose values are included in search text.
_SEARCH_RESOURCE_MAP = {
    "skill":   ("skills",   "skill_doc",  ()),
    "style":   ("styles",   "style_doc",  ()),
    "memory":  ("memories", "memory_doc", ("triggers",)),
    "trigger": ("triggers", None,         ("condition", "target", "detail")),
    "plugin":  ("plugins",  "skill_doc",  ()),
}

SEARCHABLE_RESOURCES = {"artefact"} | set(_SEARCH_RESOURCE_MAP)


def _read_file_body(vault_root, rel_path):
    """Read a file and return the body (frontmatter stripped)."""
    abs_path = os.path.join(str(vault_root), rel_path)
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return ""
    fm_match = FM_RE.match(text)
    return text[fm_match.end():] if fm_match else text


def search_resource(router, vault_root, resource, query, top_k=DEFAULT_TOP_K):
    """Search a non-artefact resource by text matching on name + file content.

    Returns results in the same shape as BM25 search (path, title, type, score,
    snippet) so the MCP server can format them uniformly.

    Raises ValueError if the resource is not searchable.
    """
    if resource == "artefact":
        raise ValueError("Use search() for artefact search, not search_resource().")
    if resource not in _SEARCH_RESOURCE_MAP:
        _searchable = sorted(SEARCHABLE_RESOURCES)
        raise ValueError(
            f"Resource '{resource}' is not searchable. "
            f"Searchable resources: {', '.join(_searchable)}"
        )

    router_key, doc_field, extra_fields = _SEARCH_RESOURCE_MAP[resource]
    items = router.get(router_key, [])
    query_lower = query.lower()
    query_tokens = tokenise(query)

    results = []
    for item in items:
        name = item.get("name", "")
        # Build searchable text: name + extra inline fields
        parts = [name]
        for field in extra_fields:
            val = item.get(field)
            if isinstance(val, list):
                parts.extend(val)
            elif val:
                parts.append(val)
        searchable = " ".join(parts)

        # Load file content if available
        file_body = ""
        if doc_field and item.get(doc_field):
            file_body = _read_file_body(vault_root, item[doc_field])
            searchable += " " + file_body

        score = searchable.lower().count(query_lower)
        if score <= 0:
            continue

        # Single-pass snippet: pass pre-read body to avoid re-reading file
        snippet = extract_snippet(
            vault_root, item.get(doc_field) or "", query_tokens,
            body=file_body or searchable,
        )

        results.append({
            "path": item.get(doc_field, "") if doc_field else "",
            "title": name,
            "type": resource,
            "status": None,
            "score": round(score, 4),
            "snippet": snippet,
        })

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:top_k]


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

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
        cfg = _semantic.load_config_best_effort(vault_root)
    except _semantic.SemanticConfigLoadError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    index = load_index(vault_root)
    try:
        resolved_mode = resolve_search_mode(vault_root, mode, config=cfg)
        if resolved_mode == "lexical":
            results = search(
                index, query, vault_root,
                type_filter, tag_filter, status_filter, top_k,
            )
        elif resolved_mode == "semantic":
            results = search_semantic(
                query, vault_root,
                type_filter=type_filter,
                tag_filter=tag_filter,
                status_filter=status_filter,
                top_k=top_k,
            )
        else:
            results = search_hybrid(
                index, query, vault_root,
                type_filter=type_filter,
                tag_filter=tag_filter,
                status_filter=status_filter,
                top_k=top_k,
            )
    except SearchModeUnavailableError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

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
