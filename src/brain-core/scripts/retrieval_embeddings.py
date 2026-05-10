#!/usr/bin/env python3
"""Shared semantic config/runtime facade for script callers."""

from _semantic.config import (  # noqa: F401
    SemanticConfigLoadError,
    embeddings_enabled,
    load_config_best_effort,
    semantic_engine_installed,
    semantic_processing_enabled,
    semantic_retrieval_enabled,
    set_semantic_engine_installed,
    set_semantic_flags,
    set_semantic_retrieval_enabled,
)
from _semantic.runtime import (  # noqa: F401
    DOC_EMBEDDINGS_REL,
    EMBEDDINGS_META_REL,
    TYPE_EMBEDDINGS_REL,
    clear_embeddings_outputs,
    clear_query_encoder,
    encode_query,
    embeddings_sidecars_present,
    get_query_encoder,
    load_doc_embeddings,
    load_embeddings_state,
    rank_against,
    semantic_engine_available,
)
