"""Search mode policy and dispatch."""

from __future__ import annotations

from .filters import SearchFilters

DEFAULT_TOP_K = 10
SEARCH_MODES = {"lexical", "semantic", "hybrid"}


class SearchModeUnavailableError(ValueError):
    """Raised when the caller requests a retrieval mode that is unavailable."""


def _semantic_modules():
    """Import semantic helpers lazily so lexical-only imports stay dependency-light."""
    import _semantic.config as semantic_config
    import _semantic.runtime as semantic_runtime

    return semantic_config, semantic_runtime


def _reject_unknown_mode(mode, *, exc_type):
    valid = ", ".join(sorted(SEARCH_MODES))
    raise exc_type(f"unknown search mode '{mode}'. Valid modes: {valid}")


def default_search_mode(
    vault_root,
    *,
    config=None,
    doc_embeddings=None,
    embeddings_meta=None,
):
    """Return the best-effort default search mode."""
    semantic_config, semantic_runtime = _semantic_modules()

    if (
        semantic_config.semantic_retrieval_enabled(vault_root, config=config)
        and semantic_runtime.semantic_engine_available(
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
    """Validate and resolve one requested search mode."""
    if mode is not None and mode not in SEARCH_MODES:
        _reject_unknown_mode(mode, exc_type=SearchModeUnavailableError)

    resolved = (
        mode
        if mode is not None
        else default_search_mode(
            vault_root,
            config=config,
            doc_embeddings=doc_embeddings,
            embeddings_meta=embeddings_meta,
        )
    )

    if resolved in {"semantic", "hybrid"}:
        semantic_config, semantic_runtime = _semantic_modules()

        if not semantic_config.semantic_retrieval_enabled(vault_root, config=config):
            raise SearchModeUnavailableError(
                "semantic retrieval is disabled; enable "
                "defaults.flags.semantic_retrieval or use mode='lexical'"
            )
        if not semantic_runtime.semantic_engine_available(
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


def mode_available(
    vault_root,
    mode,
    *,
    config=None,
    doc_embeddings=None,
    embeddings_meta=None,
):
    """Return `(available, error_message)` for one retrieval mode."""
    try:
        resolve_search_mode(
            vault_root,
            mode,
            config=config,
            doc_embeddings=doc_embeddings,
            embeddings_meta=embeddings_meta,
        )
    except SearchModeUnavailableError as exc:
        return (False, str(exc))
    return (True, None)


def dispatch_search(
    index,
    query,
    vault_root,
    mode,
    *,
    filters: SearchFilters = SearchFilters(),
    top_k=DEFAULT_TOP_K,
    doc_embeddings=None,
    embeddings_meta=None,
    query_encoder=None,
    attach_snippets=True,
):
    """Dispatch one query through the selected retrieval mode."""
    if mode == "lexical":
        from .lexical_query import search

        return search(
            index,
            query,
            vault_root,
            filters=filters,
            top_k=top_k,
            attach_snippets_to_results=attach_snippets,
        )
    if mode == "semantic":
        from .semantic_query import search_semantic

        return search_semantic(
            query,
            vault_root,
            filters=filters,
            top_k=top_k,
            doc_embeddings=doc_embeddings,
            embeddings_meta=embeddings_meta,
            query_encoder=query_encoder,
            attach_snippets_to_results=attach_snippets,
        )
    if mode == "hybrid":
        from .hybrid_query import search_hybrid

        return search_hybrid(
            index,
            query,
            vault_root,
            filters=filters,
            top_k=top_k,
            doc_embeddings=doc_embeddings,
            embeddings_meta=embeddings_meta,
            query_encoder=query_encoder,
            attach_snippets_to_results=attach_snippets,
        )
    _reject_unknown_mode(mode, exc_type=ValueError)
