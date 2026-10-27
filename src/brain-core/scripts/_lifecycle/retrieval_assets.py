"""Lifecycle orchestration for combined retrieval asset refresh flows."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from _common import load_compiled_router
from _lifecycle.retrieval_errors import (
    CompiledRouterUnavailableError,
    RetrievalPersistenceError,
    SemanticRuntimeUnavailableError,
)
import _semantic.assets as semantic_assets
import _semantic.config as semantic_config
import _semantic.runtime as semantic_runtime
import _semantic.model as semantic_model
import _search.index as search_index
import _search.paths as search_paths
import compile_router
import session


class RetrievalRefreshIncomplete(RetrievalPersistenceError):
    """A refresh failed after publishing some independent retrieval outputs."""

    def __init__(self, cause, committed_paths):
        self.committed_paths = tuple(committed_paths)
        super().__init__(".brain/local", "refreshing retrieval assets", cause)


def _asset_signatures(root, paths=None):
    if paths is None:
        paths = (compile_router.OUTPUT_PATH, search_paths.OUTPUT_PATH,
                 semantic_runtime.EMBEDDINGS_META_REL, semantic_runtime.TYPE_EMBEDDINGS_REL,
                 semantic_runtime.DOC_EMBEDDINGS_REL)
    result = {}
    for path in paths:
        try:
            info = (Path(root) / path).stat()
            result[path] = (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size)
        except FileNotFoundError:
            result[path] = None
    return result


def drop_path_keyed_retrieval_caches(vault_root) -> list[str]:
    """Remove the derived caches that key on artefact paths; return what was removed.

    The lexical index and the embeddings sidecars record each document's path
    and judge their own freshness by mtime, which ``os.rename`` preserves — so
    after a mass move they would report themselves fresh while pointing at
    paths that no longer exist. Removing them makes the next lexical/semantic
    repair see ``missing`` and rebuild.
    """
    from _bootstrap.owned_paths import owned_output_paths
    relatives = (search_paths.OUTPUT_PATH, semantic_runtime.TYPE_EMBEDDINGS_REL,
                 semantic_runtime.DOC_EMBEDDINGS_REL, semantic_runtime.EMBEDDINGS_META_REL)
    paths = dict(zip(relatives, owned_output_paths(vault_root, relatives)))
    removed = []
    try:
        for relative in (semantic_runtime.EMBEDDINGS_META_REL, *relatives[:-1]):
            try:
                paths[relative].unlink()
            except FileNotFoundError:
                continue
            removed.append(relative)
    except OSError as exc:
        raise RetrievalRefreshIncomplete(exc, removed) from exc
    return [relative for relative in relatives if relative in removed]


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
    force_embeddings: bool = False,
    config=None,
):
    """Persist lexical state and refresh semantic sidecars when enabled."""
    from _bootstrap.owned_paths import owned_output_paths
    relatives = (search_paths.OUTPUT_PATH, semantic_runtime.EMBEDDINGS_META_REL,
                 semantic_runtime.TYPE_EMBEDDINGS_REL, semantic_runtime.DOC_EMBEDDINGS_REL)
    owned_output_paths(vault_root, relatives)
    published = []
    try:
        search_index.persist_retrieval_index(vault_root, index)
        published.append(search_paths.OUTPUT_PATH)
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
        return semantic_assets.refresh_embeddings_outputs(vault_root, router, index["documents"])
    except (OSError, ValueError, RetrievalPersistenceError, CompiledRouterUnavailableError,
            SemanticRuntimeUnavailableError, semantic_model.SemanticModelError,
            semantic_runtime.SemanticEmbeddingsLoadError) as exc:
        committed = (*published, *getattr(exc, "committed_paths", ()))
        if committed:
            raise RetrievalRefreshIncomplete(exc, dict.fromkeys(committed)) from exc
        raise


def refresh_embeddings_for_loaded_state(
    vault_root,
    router: Mapping[str, Any],
    documents: Sequence[Mapping[str, Any]],
    *,
    config=None,
):
    """Refresh semantic sidecars for already-loaded router/index state.

    The MCP runtime uses this to preserve the retrieval-asset ownership seam
    after router/index state is already resident in memory.
    """
    if not embeddings_should_refresh(vault_root, config=config):
        semantic_runtime.clear_embeddings_outputs(vault_root)
        return None
    return semantic_assets.refresh_embeddings_outputs(
        vault_root,
        router,
        documents,
    )


def refresh_retrieval_assets(
    vault_root: str | Path,
    *,
    force_embeddings: bool = False,
) -> list[str]:
    """Refresh independently published assets, retaining known partial effects."""
    from _common._source_access import SourceAccessError
    from _bootstrap.owned_paths import owned_output_paths
    relatives = (compile_router.OUTPUT_PATH, compile_router.compile_colours.OUTPUT_REL,
                 compile_router.compile_colours.GRAPH_JSON_REL, session.SESSION_MARKDOWN_REL,
                 search_paths.OUTPUT_PATH, semantic_runtime.EMBEDDINGS_META_REL,
                 semantic_runtime.TYPE_EMBEDDINGS_REL, semantic_runtime.DOC_EMBEDDINGS_REL)
    owned_output_paths(vault_root, relatives)
    published = []
    try:
        return _refresh_retrieval_assets(vault_root, force_embeddings=force_embeddings,
                                         published=published)
    except (OSError, ValueError, RetrievalPersistenceError,
            CompiledRouterUnavailableError, SemanticRuntimeUnavailableError,
            semantic_model.SemanticModelError, semantic_runtime.SemanticEmbeddingsLoadError) as exc:
        committed = (*published, *getattr(exc, "committed_paths", ()))
        if committed:
            raise RetrievalRefreshIncomplete(exc, dict.fromkeys(committed)) from exc
        if isinstance(exc, SourceAccessError):
            raise CompiledRouterUnavailableError(str(exc), operation="refreshing retrieval assets") from exc
        raise


def _refresh_retrieval_assets(vault_root, *, force_embeddings=False, published):
    """Build and publish in dependency order under the caller's mutation lock."""
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
    router_outputs = _persist_step(
        compile_router.OUTPUT_PATH,
        "persisting compiled router",
        lambda: compile_router.persist_compiled_router(str(vault_root), compiled),
    )
    published.extend(router_outputs or (compile_router.OUTPUT_PATH,))
    notes.append("Compiled router refreshed.")
    try:
        if compile_router.refresh_session_markdown(str(vault_root), compiled):
            published.append(session.SESSION_MARKDOWN_REL)
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
        error = RetrievalPersistenceError(rel_path, operation, exc)
        error.committed_paths = getattr(exc, "committed_paths", ())
        raise error from exc
