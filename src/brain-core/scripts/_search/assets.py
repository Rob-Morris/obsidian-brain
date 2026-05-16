"""Shared retrieval asset refresh orchestration."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path

from _common import load_compiled_router, read_artefact, safe_write_via, safe_write_json
from _taxonomy_descriptions import extract_type_description

from .document_parts import embedding_parts_from_body
from .errors import CompiledRouterUnavailableError, UnreadableRetrievalSourceError
from .index import build_index, persist_retrieval_index

try:
    import numpy as np

    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False


semantic_config = None
semantic_model = None
semantic_runtime = None


def _semantic_modules():
    """Import semantic helpers lazily so `_search` stays acyclic at module load time."""
    global semantic_config, semantic_model, semantic_runtime
    if semantic_config is None:
        import _semantic.config as semantic_config_mod

        semantic_config = semantic_config_mod
    if semantic_model is None:
        import _semantic.model as semantic_model_mod

        semantic_model = semantic_model_mod
    if semantic_runtime is None:
        import _semantic.runtime as semantic_runtime_mod

        semantic_runtime = semantic_runtime_mod
    return semantic_config, semantic_model, semantic_runtime


def _safe_save_npy(path, array, *, bounds=None):
    """Persist a NumPy array atomically via the shared write primitive."""
    return safe_write_via(
        path,
        lambda handle: np.save(handle, array),
        bounds=bounds,
    )


def _load_embedding_parts(vault_root, rel_path, embedding_parts_by_path):
    """Return embedding text fragments for one path, using cached parts when available."""
    if embedding_parts_by_path is not None:
        cached = embedding_parts_by_path.get(rel_path)
        if cached is not None:
            return cached

    abs_path = os.path.join(str(vault_root), rel_path)
    try:
        _, body = read_artefact(abs_path)
    except (OSError, UnicodeDecodeError) as exc:
        raise UnreadableRetrievalSourceError(
            rel_path,
            "building semantic embeddings",
            exc,
        ) from exc
    return embedding_parts_from_body(body)


def build_embeddings(vault_root, router, documents, *, embedding_parts_by_path=None):
    """Compute embeddings for type descriptions and documents.

    Returns `None` when NumPy is unavailable so callers can clear stale
    sidecars instead of leaving them to drift.
    """
    if not _HAS_NUMPY:
        return None
    _, _semantic_model, _semantic_runtime = _semantic_modules()

    vault_str = str(vault_root)
    artefacts = [a for a in router.get("artefacts", []) if a.get("configured")]

    type_texts = []
    type_meta = []
    type_desc_by_frontmatter = {}
    for i, artefact in enumerate(artefacts):
        desc = extract_type_description(vault_root, artefact)
        if not desc:
            desc = artefact.get("type", artefact.get("key", ""))
        frontmatter_type = artefact.get("frontmatter_type", artefact.get("type", ""))
        type_desc_by_frontmatter[frontmatter_type] = desc
        type_texts.append(desc)
        type_meta.append(
            {
                "index": i,
                "key": artefact["key"],
                "type": artefact["type"],
                "description": desc[:200],
            }
        )

    doc_texts = []
    doc_meta = []
    for i, doc in enumerate(documents):
        embedding_parts = _load_embedding_parts(
            vault_root,
            doc["path"],
            embedding_parts_by_path,
        )
        body_head = embedding_parts.body_head
        headings = embedding_parts.headings
        type_desc = type_desc_by_frontmatter.get(doc["type"], doc["type"])
        parts = [doc["title"], doc["type"], type_desc]
        if headings:
            parts.append(" ".join(headings))
        if body_head:
            parts.append(body_head)
        doc_texts.append("\n".join(part for part in parts if part))
        doc_meta.append(
            {
                "index": i,
                "path": doc["path"],
                "type": doc["type"],
                "title": doc["title"],
                "tags": doc.get("tags", []),
                "status": doc.get("status"),
            }
        )

    model, manifest = _semantic_model.load_local_model_with_manifest(vault_root)

    type_embeddings = (
        model.encode(type_texts, normalize_embeddings=True)
        if type_texts
        else np.zeros((0, _semantic_runtime.EMBEDDING_DIM))
    )
    doc_embeddings = (
        model.encode(doc_texts, normalize_embeddings=True)
        if doc_texts
        else np.zeros((0, _semantic_runtime.EMBEDDING_DIM))
    )

    os.makedirs(
        os.path.join(vault_str, os.path.dirname(_semantic_runtime.TYPE_EMBEDDINGS_REL)),
        exist_ok=True,
    )
    _safe_save_npy(
        os.path.join(vault_str, _semantic_runtime.TYPE_EMBEDDINGS_REL),
        type_embeddings,
        bounds=vault_str,
    )
    _safe_save_npy(
        os.path.join(vault_str, _semantic_runtime.DOC_EMBEDDINGS_REL),
        doc_embeddings,
        bounds=vault_str,
    )

    router_hash = _semantic_runtime.router_source_hash(router)
    meta = {
        "model": manifest.model_name,
        "model_revision": manifest.revision,
        _semantic_runtime.ROUTER_SOURCE_HASH_KEY: router_hash,
        "dim": _semantic_runtime.EMBEDDING_DIM,
        "built_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "types": type_meta,
        "documents": doc_meta,
    }
    meta_path = os.path.join(vault_str, _semantic_runtime.EMBEDDINGS_META_REL)
    safe_write_json(meta_path, meta, bounds=vault_str)

    return (type_embeddings, doc_embeddings, meta)


def refresh_embeddings_outputs(
    vault_root,
    router,
    documents,
    *,
    embedding_parts_by_path=None,
    enable_embeddings=None,
    config=None,
):
    """Refresh embeddings sidecars, clearing stale files when unavailable."""
    _semantic_config, _, _semantic_runtime = _semantic_modules()
    if enable_embeddings is None:
        enable_embeddings = (
            _semantic_config.embeddings_enabled(vault_root, config=config)
            and _semantic_runtime.semantic_engine_available(
                vault_root,
                config=config,
                skip_sidecar_check=True,
            )
        )
    if not enable_embeddings or router is None:
        _semantic_runtime.clear_embeddings_outputs(vault_root)
        return None

    result = build_embeddings(
        vault_root,
        router,
        documents,
        embedding_parts_by_path=embedding_parts_by_path,
    )
    if result is None:
        _semantic_runtime.clear_embeddings_outputs(vault_root)
        return None
    return result


def persist_retrieval_outputs(
    vault_root,
    index,
    *,
    router=None,
    embedding_parts_by_path=None,
    enable_embeddings=None,
    config=None,
):
    """Persist index JSON and refresh embeddings when enabled and available."""
    _semantic_config, _, _semantic_runtime = _semantic_modules()
    persist_retrieval_index(vault_root, index)
    if enable_embeddings is None:
        enable_embeddings = (
            _semantic_config.embeddings_enabled(vault_root, config=config)
            and _semantic_runtime.semantic_engine_available(
                vault_root,
                config=config,
                skip_sidecar_check=True,
            )
        )
    if not enable_embeddings:
        _semantic_runtime.clear_embeddings_outputs(vault_root)
        return None
    if router is None:
        router = load_compiled_router(vault_root)
        if isinstance(router, dict) and router.get("error"):
            raise CompiledRouterUnavailableError(
                f"compiled router is unavailable: {router['error']}",
                operation="building semantic embeddings",
            )
    return refresh_embeddings_outputs(
        vault_root,
        router,
        index["documents"],
        embedding_parts_by_path=embedding_parts_by_path,
        enable_embeddings=True,
        config=config,
    )


def refresh_search_assets(
    vault_root: str | Path,
    *,
    force_embeddings: bool = False,
) -> list[str]:
    """Refresh router, lexical retrieval assets, and semantic sidecars when requested.

    Generic search refresh honours the configured semantic flags and clears stale
    sidecars when embeddings are unavailable or disabled, including when the
    semantic runtime cannot currently build embeddings (for example NumPy is
    unavailable). Semantic provisioning and semantic repair pass
    ``force_embeddings=True`` because their contract is stronger: sidecars must
    be rebuilt, not merely reconciled.
    """
    import compile_router

    vault_root = Path(vault_root)
    notes = []
    compiled = compile_router.compile(str(vault_root))
    compile_router.persist_compiled_router(str(vault_root), compiled)
    compile_router.refresh_session_markdown(str(vault_root), compiled)
    notes.append("Compiled router refreshed.")

    build_result = build_index(str(vault_root))
    index = build_result.index
    notes.append("Lexical retrieval assets refreshed.")
    embeddings_result = persist_retrieval_outputs(
        str(vault_root),
        index,
        router=compiled,
        embedding_parts_by_path=build_result.embedding_parts_by_path,
        enable_embeddings=True if force_embeddings else None,
    )
    if force_embeddings and embeddings_result is None:
        raise ValueError(
            "semantic embeddings sidecars could not be rebuilt after semantic provisioning"
        )
    if embeddings_result is None:
        notes.append("Semantic sidecars cleared (unavailable or disabled).")
    else:
        notes.append("Semantic sidecars refreshed.")
    return notes
