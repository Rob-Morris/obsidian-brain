"""Lifecycle orchestration for combined retrieval asset refresh flows."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from _common import load_compiled_router, read_artefact
from _lifecycle.document_parts import EmbeddingParts, embedding_parts_from_body
from _lifecycle.retrieval_errors import (
    CompiledRouterUnavailableError,
    RetrievalPersistenceError,
    SemanticRuntimeUnavailableError,
    UnreadableRetrievalSourceError,
)
import _semantic.assets as semantic_assets
import _semantic.config as semantic_config
import _semantic.runtime as semantic_runtime
import _search.index as search_index
import compile_router
import session


def embeddings_should_refresh(vault_root, *, config=None) -> bool:
    """Return whether embeddings sidecars should be refreshed for generic flows."""
    return (
        semantic_config.embeddings_enabled(vault_root, config=config)
        and semantic_runtime.semantic_engine_available(
            vault_root,
            config=config,
            skip_sidecar_check=True,
        )
    )


def persist_retrieval_outputs(
    vault_root,
    index: Mapping[str, Any],
    *,
    router: Mapping[str, Any] | None = None,
    embedding_parts_by_path: Mapping[str, EmbeddingParts] | None = None,
    force_embeddings: bool = False,
    config=None,
):
    """Persist lexical state and refresh semantic sidecars when enabled."""
    search_index.persist_retrieval_index(vault_root, index)
    if not force_embeddings and not embeddings_should_refresh(vault_root, config=config):
        semantic_runtime.clear_embeddings_outputs(vault_root)
        return None
    if router is None:
        router = load_compiled_router(vault_root)
        if router.get("error"):
            raise CompiledRouterUnavailableError(
                f"compiled router is unavailable: {router['error']}",
                operation="building semantic embeddings",
            )
    return semantic_assets.refresh_embeddings_outputs(
        vault_root,
        router,
        index["documents"],
        embedding_parts_by_path=embedding_parts_by_path,
    )


def refresh_embeddings_for_loaded_state(
    vault_root,
    router: Mapping[str, Any],
    documents: Sequence[Mapping[str, Any]],
    *,
    embedding_parts_by_path: Mapping[str, EmbeddingParts] | None = None,
    config=None,
):
    """Refresh semantic sidecars for already-loaded router/index state.

    The MCP runtime uses this to preserve the retrieval-asset ownership seam
    after router/index state is already resident in memory.
    """
    if not embeddings_should_refresh(vault_root, config=config):
        semantic_runtime.clear_embeddings_outputs(vault_root)
        return None
    if embedding_parts_by_path is None:
        embedding_parts_by_path = _materialise_embedding_parts_by_path(vault_root, documents)
    return semantic_assets.refresh_embeddings_outputs(
        vault_root,
        router,
        documents,
        embedding_parts_by_path=embedding_parts_by_path,
    )


def refresh_retrieval_assets(
    vault_root: str | Path,
    *,
    force_embeddings: bool = False,
) -> list[str]:
    """Refresh router, lexical retrieval assets, and semantic sidecars when requested."""
    vault_root = Path(vault_root)
    notes = []
    # Wrap only boundary-shape failures here. If compile/persist/session starts
    # raising structural errors like KeyError/TypeError, that is a bug we want
    # to crash loudly rather than mislabel as a persistence issue.
    try:
        compiled = compile_router.compile(str(vault_root))
    except (OSError, UnicodeDecodeError) as exc:
        raise CompiledRouterUnavailableError(
            f"compiled router refresh failed: {exc}",
            operation="refreshing retrieval assets",
        ) from exc
    _persist_step(
        compile_router.OUTPUT_PATH,
        "persisting compiled router",
        lambda: compile_router.persist_compiled_router(str(vault_root), compiled),
    )
    notes.append("Compiled router refreshed.")
    try:
        compile_router.refresh_session_markdown(str(vault_root), compiled)
    except (OSError, ValueError) as exc:
        notes.append(
            f"Warning: failed to refresh {session.SESSION_MARKDOWN_REL}: {exc}"
        )

    build_result = search_index.build_index(str(vault_root))
    index = build_result.index
    notes.append("Lexical retrieval assets refreshed.")
    embeddings_result = persist_retrieval_outputs(
        str(vault_root),
        index,
        router=compiled,
        embedding_parts_by_path=build_result.embedding_parts_by_path,
        force_embeddings=force_embeddings,
    )
    if embeddings_result is None:
        notes.append("Semantic sidecars cleared (unavailable or disabled).")
    else:
        notes.append("Semantic sidecars refreshed.")
    return notes


def _persist_step(rel_path, operation, fn):
    """Run one persistence step and wrap write-boundary failures consistently."""
    try:
        return fn()
    except (OSError, ValueError) as exc:
        raise RetrievalPersistenceError(rel_path, operation, exc) from exc


def _materialise_embedding_parts_by_path(
    vault_root,
    documents: Sequence[Mapping[str, Any]],
) -> dict[str, EmbeddingParts]:
    """Build embedding parts for loaded documents when no cache was threaded through."""
    parts_by_path = {}
    for doc in documents:
        rel_path = doc["path"]
        abs_path = Path(vault_root) / rel_path
        try:
            _, body = read_artefact(str(abs_path))
        except (OSError, UnicodeDecodeError) as exc:
            raise UnreadableRetrievalSourceError(
                rel_path,
                "building semantic embeddings",
                exc,
            ) from exc
        parts_by_path[rel_path] = embedding_parts_from_body(body)
    return parts_by_path
