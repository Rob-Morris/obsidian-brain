"""Semantic retrieval execution and sidecar validation."""

from __future__ import annotations

import _semantic.model as semantic_model
import _semantic.runtime as semantic_runtime

from .filters import SearchFilters
from .mode import DEFAULT_TOP_K, SearchModeUnavailableError
from .snippet import attach_snippets
from .lexical import tokenise


class EmbeddingsSidecarsUnavailableError(SearchModeUnavailableError):
    """Raised when semantic sidecars are absent or stale for the current router."""


def encode_query_or_unavailable(vault_root, query, *, query_encoder=None):
    """Encode one query string or raise the canonical unavailable error."""
    try:
        return semantic_runtime.encode_query(
            vault_root,
            query,
            query_encoder=query_encoder,
        )
    except ImportError as exc:
        raise SearchModeUnavailableError(
            f"semantic retrieval is unavailable: {exc}"
        ) from exc
    except semantic_model.SemanticModelError as exc:
        raise SearchModeUnavailableError(
            f"semantic retrieval is unavailable: {exc}"
        ) from exc


def load_doc_embeddings_or_unavailable(vault_root, *, loader=None):
    """Load semantic sidecars or raise the canonical unavailable error."""
    load = loader or semantic_runtime.load_doc_embeddings
    try:
        doc_embeddings, meta = load(vault_root)
    except semantic_runtime.SemanticEmbeddingsLoadError as exc:
        raise SearchModeUnavailableError(
            f"semantic retrieval is unavailable: {exc}"
        ) from exc
    if doc_embeddings is None or meta is None:
        raise EmbeddingsSidecarsUnavailableError(
            "semantic retrieval is unavailable: embeddings sidecars are missing"
        )
    try:
        if not semantic_runtime.embeddings_meta_matches_current_router(vault_root, meta):
            raise EmbeddingsSidecarsUnavailableError(
                "semantic retrieval is unavailable: semantic embeddings were "
                "built for a different compiled router"
            )
    except semantic_runtime.CompiledRouterMissingError as exc:
        raise EmbeddingsSidecarsUnavailableError(
            f"semantic retrieval is unavailable: {exc}"
        ) from exc
    except semantic_runtime.RouterMetadataError as exc:
        raise SearchModeUnavailableError(
            f"semantic retrieval is unavailable: compiled router metadata is invalid: {exc}"
        ) from exc
    return (doc_embeddings, meta)


def search_semantic(
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
    """Rank documents by cosine similarity against persisted document vectors."""
    query_tokens = tokenise(query)
    if not query_tokens:
        return []

    if doc_embeddings is None or embeddings_meta is None:
        doc_embeddings, embeddings_meta = load_doc_embeddings_or_unavailable(vault_root)

    query_vec = encode_query_or_unavailable(
        vault_root,
        query,
        query_encoder=query_encoder,
    )
    ranked = semantic_runtime.rank_against(
        query_vec,
        doc_embeddings,
        embeddings_meta.get("documents", []),
        filter_fn=filters.matches,
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
    if attach_snippets_to_results:
        attach_snippets(top, vault_root, query_tokens)
    return top
