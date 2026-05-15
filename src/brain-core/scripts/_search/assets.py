"""Shared retrieval asset refresh orchestration."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path

from _common import load_compiled_router, safe_write_via, safe_write_json

from ._lazy import LazyModuleProxy
from .index import (
    EMBEDDING_BODY_CHARS,
    EMBEDDING_HEADING_LIMIT,
    _extract_heading_titles,
    build_index,
    extract_type_description,
    persist_retrieval_index,
)

try:
    import numpy as np

    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False


_semantic_config = LazyModuleProxy("_semantic.config")
_semantic_model = LazyModuleProxy("_semantic.model")
_semantic = LazyModuleProxy("_semantic.runtime")


def _safe_save_npy(path, array, *, bounds=None):
    """Persist a NumPy array atomically via the shared write primitive."""
    return safe_write_via(
        path,
        lambda handle: np.save(handle, array),
        bounds=bounds,
    )


def build_embeddings(vault_root, router, documents):
    """Compute embeddings for type descriptions and documents."""
    if not _HAS_NUMPY:
        return None

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
        body_head = doc.get("_body_head")
        headings = doc.get("_headings")
        if body_head is None or headings is None:
            abs_path = os.path.join(vault_str, doc["path"])
            try:
                from _common import read_artefact

                _, body = read_artefact(abs_path)
            except (OSError, UnicodeDecodeError):
                body = ""
            body_head = body[:EMBEDDING_BODY_CHARS]
            headings = _extract_heading_titles(body, limit=EMBEDDING_HEADING_LIMIT)
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
        else np.zeros((0, _semantic.EMBEDDING_DIM))
    )
    doc_embeddings = (
        model.encode(doc_texts, normalize_embeddings=True)
        if doc_texts
        else np.zeros((0, _semantic.EMBEDDING_DIM))
    )

    os.makedirs(
        os.path.join(vault_str, os.path.dirname(_semantic.TYPE_EMBEDDINGS_REL)),
        exist_ok=True,
    )
    _safe_save_npy(
        os.path.join(vault_str, _semantic.TYPE_EMBEDDINGS_REL),
        type_embeddings,
        bounds=vault_str,
    )
    _safe_save_npy(
        os.path.join(vault_str, _semantic.DOC_EMBEDDINGS_REL),
        doc_embeddings,
        bounds=vault_str,
    )

    router_hash = _semantic.router_source_hash(router)
    meta = {
        "model": manifest.model_name,
        "model_revision": manifest.revision,
        _semantic.ROUTER_SOURCE_HASH_KEY: router_hash,
        "dim": _semantic.EMBEDDING_DIM,
        "built_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "types": type_meta,
        "documents": doc_meta,
    }
    meta_path = os.path.join(vault_str, _semantic.EMBEDDINGS_META_REL)
    safe_write_json(meta_path, meta, bounds=vault_str)

    return (type_embeddings, doc_embeddings, meta)


def refresh_embeddings_outputs(
    vault_root,
    router,
    documents,
    *,
    enable_embeddings=None,
    config=None,
):
    """Refresh embeddings sidecars, clearing stale files when unavailable."""
    if enable_embeddings is None:
        enable_embeddings = (
            _semantic_config.embeddings_enabled(vault_root, config=config)
            and _semantic.semantic_engine_available(
                vault_root,
                config=config,
                skip_sidecar_check=True,
            )
        )
    if not enable_embeddings or router is None:
        _semantic.clear_embeddings_outputs(vault_root)
        return None

    result = build_embeddings(vault_root, router, documents)
    if result is None:
        _semantic.clear_embeddings_outputs(vault_root)
        return None
    return result


def persist_retrieval_outputs(
    vault_root,
    index,
    *,
    router=None,
    enable_embeddings=None,
    config=None,
):
    """Persist index JSON and refresh embeddings when enabled and available."""
    persist_retrieval_index(vault_root, index)
    if enable_embeddings is None:
        enable_embeddings = (
            _semantic_config.embeddings_enabled(vault_root, config=config)
            and _semantic.semantic_engine_available(
                vault_root,
                config=config,
                skip_sidecar_check=True,
            )
        )
    if not enable_embeddings:
        _semantic.clear_embeddings_outputs(vault_root)
        return None
    if router is None:
        router = load_compiled_router(vault_root)
        if isinstance(router, dict) and router.get("error"):
            router = None
    return refresh_embeddings_outputs(
        vault_root,
        router,
        index["documents"],
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
    sidecars when embeddings are unavailable or disabled. Semantic provisioning
    and semantic repair pass ``force_embeddings=True`` because their contract is
    stronger: sidecars must be rebuilt, not merely reconciled.
    """
    import compile_router

    vault_root = Path(vault_root)
    notes = []
    compiled = compile_router.compile(str(vault_root))
    compile_router.persist_compiled_router(str(vault_root), compiled)
    compile_router.refresh_session_markdown(str(vault_root), compiled)
    notes.append("Compiled router refreshed.")

    index = build_index(str(vault_root))
    notes.append("Lexical retrieval assets refreshed.")
    embeddings_result = persist_retrieval_outputs(
        str(vault_root),
        index,
        router=compiled,
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
