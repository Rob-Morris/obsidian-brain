"""Hybrid retrieval fusion and related ranking heuristics."""

from __future__ import annotations

from .filters import SearchFilters
from .lexical import LEXICAL_ANCHOR_RE, tokenise
from .lexical_query import search
from .mode import DEFAULT_TOP_K
from .semantic_query import search_semantic
from .snippet import attach_snippets


RRF_K = 60
RRF_LEXICAL_WEIGHT = 1.0
RRF_SEMANTIC_WEIGHT = 1.0
RRF_CANDIDATE_MULTIPLIER = 3
SEMANTIC_CHAMPION_MARGIN = 0.05
SEMANTIC_CHAMPION_BONUS = 0.002
# Preserve strong exact-title lexical wins when the top lexical hit clearly
# outperforms the next lexical candidate and the query already contains that
# title phrase.
LEXICAL_TITLE_CHAMPION_BONUS = 0.002
LEXICAL_TITLE_CHAMPION_MIN_MARGIN = 1.2
LEXICAL_TITLE_CHAMPION_MIN_TOKENS = 2
# Strip the Brain product namespace before title-overlap heuristics so
# "Brain X" titles do not win merely for sharing the product prefix.
BRAIN_PRODUCT_NAMESPACE_PREFIX_TOKENS = frozenset({"brain"})
SEMANTIC_RESCUE_MIN_QUERY_TOKENS = 5
SEMANTIC_RESCUE_MAX_LEXICAL_TITLE_OVERLAP = 1
SEMANTIC_RESCUE_TOP_OVERLAP_WINDOW = 3
# Give clearly better semantic leaders a small lift when lexical and semantic
# disagree on long, low-overlap queries.
SEMANTIC_RESCUE_BONUS = 0.015


def _semantic_champion_bonus_applies(semantic_results):
    return (
        len(semantic_results) >= 2
        and semantic_results[0]["score"] - semantic_results[1]["score"]
        >= SEMANTIC_CHAMPION_MARGIN
    )


def _core_title_tokens(title):
    """Return title tokens with the known Brain product namespace stripped."""
    tokens = tokenise(title)
    while tokens and tokens[0] in BRAIN_PRODUCT_NAMESPACE_PREFIX_TOKENS:
        tokens = tokens[1:]
    return tokens


def _lexical_title_champion_bonus_applies(query_tokens, lexical_results, semantic_results):
    """Return whether hybrid should preserve a strong lexical title champion."""
    if not query_tokens or not lexical_results or not semantic_results:
        return False
    if lexical_results[0]["path"] == semantic_results[0]["path"]:
        return False

    title_tokens = _core_title_tokens(lexical_results[0]["title"])
    if len(title_tokens) < LEXICAL_TITLE_CHAMPION_MIN_TOKENS:
        return False

    query_text = " ".join(query_tokens)
    title_phrase = " ".join(title_tokens)
    if title_phrase not in query_text:
        return False

    if len(lexical_results) < 2:
        return True
    return (
        lexical_results[0]["score"]
        >= lexical_results[1]["score"] * LEXICAL_TITLE_CHAMPION_MIN_MARGIN
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
    """Fuse two ranked result lists with Reciprocal Rank Fusion."""
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
    if _lexical_title_champion_bonus_applies(query_tokens, lexical_results, semantic_results):
        champion = fused.get(lexical_results[0]["path"])
        if champion is not None:
            champion["score"] += LEXICAL_TITLE_CHAMPION_BONUS
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
    filters: SearchFilters = SearchFilters(),
    top_k=DEFAULT_TOP_K,
    doc_embeddings=None,
    embeddings_meta=None,
    query_encoder=None,
    attach_snippets_to_results=True,
):
    """Fuse lexical BM25 and semantic retrieval with RRF."""
    if LEXICAL_ANCHOR_RE.search(query):
        return search(
            index,
            query,
            vault_root,
            filters=filters,
            top_k=top_k,
            attach_snippets_to_results=attach_snippets_to_results,
        )
    query_tokens = tokenise(query)
    candidate_k = top_k * RRF_CANDIDATE_MULTIPLIER
    lexical_results = search(
        index,
        query,
        vault_root,
        filters=filters,
        top_k=candidate_k,
        attach_snippets_to_results=False,
    )
    semantic_results = search_semantic(
        query,
        vault_root,
        filters=filters,
        top_k=candidate_k,
        doc_embeddings=doc_embeddings,
        embeddings_meta=embeddings_meta,
        query_encoder=query_encoder,
        attach_snippets_to_results=False,
    )
    fused = _fuse_rrf(
        lexical_results,
        semantic_results,
        top_k=top_k,
        query_tokens=query_tokens,
    )
    if attach_snippets_to_results:
        attach_snippets(fused, vault_root, query_tokens)
    return fused
