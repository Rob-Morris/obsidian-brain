"""Semantic sidecar build, persistence, and refresh mechanics."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Any, Mapping, Sequence

from _common import read_artefact, safe_write_json, safe_write_via
from _lifecycle.document_parts import EmbeddingParts, embedding_parts_from_body
from _lifecycle.retrieval_errors import (
    RetrievalPersistenceError,
    SemanticRuntimeUnavailableError,
    UnreadableRetrievalSourceError,
)
import _semantic.model as semantic_model
import _semantic.runtime as semantic_runtime
from _taxonomy_descriptions import extract_type_description


try:
    import numpy as np

    _HAS_NUMPY = True
except ImportError:
    np = None
    _HAS_NUMPY = False


def _require_numpy():
    """Return NumPy or raise the typed semantic-runtime unavailability error."""
    if not _HAS_NUMPY:
        raise SemanticRuntimeUnavailableError(
            "semantic runtime dependencies are unavailable: numpy is not installed",
            operation="building semantic embeddings",
        )
    return np


def _safe_save_npy(vault_root, rel_path, array):
    """Persist a NumPy array atomically via the shared write primitive."""
    numpy = _require_numpy()
    vault_str = str(vault_root)
    abs_path = os.path.join(vault_str, rel_path)
    try:
        return safe_write_via(
            abs_path,
            lambda handle: numpy.save(handle, array),
            bounds=vault_str,
        )
    except (OSError, ValueError) as exc:
        raise RetrievalPersistenceError(
            rel_path,
            "writing semantic embeddings sidecar",
            exc,
        ) from exc


def _build_type_corpus(vault_root, router: Mapping[str, Any]):
    """Return type texts, metadata, and frontmatter-type lookup for embeddings."""
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
    return type_texts, type_meta, type_desc_by_frontmatter


def _build_doc_corpus(
    vault_root,
    documents: Sequence[Mapping[str, Any]],
    *,
    type_desc_by_frontmatter: Mapping[str, str],
    embedding_parts_by_path: Mapping[str, EmbeddingParts] | None,
):
    """Return document texts and metadata for semantic sidecar generation."""
    doc_texts = []
    doc_meta = []
    for i, doc in enumerate(documents):
        embedding_parts = _load_embedding_parts(
            vault_root,
            doc["path"],
            embedding_parts_by_path,
        )
        type_desc = type_desc_by_frontmatter.get(doc["type"], doc["type"])
        parts = [doc["title"], doc["type"], type_desc]
        if embedding_parts.headings:
            parts.append(" ".join(embedding_parts.headings))
        if embedding_parts.body_head:
            parts.append(embedding_parts.body_head)
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
    return doc_texts, doc_meta


def _persist_embeddings_outputs(vault_root, type_embeddings, doc_embeddings, meta):
    """Persist semantic sidecar arrays and metadata with typed write failures."""
    vault_str = str(vault_root)
    try:
        os.makedirs(
            os.path.join(vault_str, os.path.dirname(semantic_runtime.TYPE_EMBEDDINGS_REL)),
            exist_ok=True,
        )
    except (OSError, ValueError) as exc:
        raise RetrievalPersistenceError(
            os.path.dirname(semantic_runtime.TYPE_EMBEDDINGS_REL),
            "preparing semantic embeddings sidecar directory",
            exc,
        ) from exc

    _safe_save_npy(vault_root, semantic_runtime.TYPE_EMBEDDINGS_REL, type_embeddings)
    _safe_save_npy(vault_root, semantic_runtime.DOC_EMBEDDINGS_REL, doc_embeddings)

    meta_path = os.path.join(vault_str, semantic_runtime.EMBEDDINGS_META_REL)
    try:
        safe_write_json(meta_path, meta, bounds=vault_str)
    except (OSError, ValueError) as exc:
        raise RetrievalPersistenceError(
            semantic_runtime.EMBEDDINGS_META_REL,
            "writing semantic embeddings metadata",
            exc,
        ) from exc


def _encode_or_empty(model, texts):
    """Return embeddings for `texts`, or an empty array with the canonical width."""
    numpy = _require_numpy()
    if texts:
        return model.encode(texts, normalize_embeddings=True)
    return numpy.zeros((0, semantic_runtime.EMBEDDING_DIM))


def build_embeddings(
    vault_root,
    router: Mapping[str, Any],
    documents: Sequence[Mapping[str, Any]],
    *,
    embedding_parts_by_path: Mapping[str, EmbeddingParts] | None = None,
):
    """Compute embeddings for type descriptions and documents."""
    _require_numpy()

    type_texts, type_meta, type_desc_by_frontmatter = _build_type_corpus(vault_root, router)
    doc_texts, doc_meta = _build_doc_corpus(
        vault_root,
        documents,
        type_desc_by_frontmatter=type_desc_by_frontmatter,
        embedding_parts_by_path=embedding_parts_by_path,
    )

    model, manifest = semantic_model.load_local_model_with_manifest(vault_root)

    type_embeddings = _encode_or_empty(model, type_texts)
    doc_embeddings = _encode_or_empty(model, doc_texts)

    router_hash = semantic_runtime.router_source_hash(router)
    meta = {
        "model": manifest.model_name,
        "model_revision": manifest.revision,
        semantic_runtime.ROUTER_SOURCE_HASH_KEY: router_hash,
        "dim": semantic_runtime.EMBEDDING_DIM,
        "built_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "types": type_meta,
        "documents": doc_meta,
    }
    _persist_embeddings_outputs(vault_root, type_embeddings, doc_embeddings, meta)

    return (type_embeddings, doc_embeddings, meta)


def _load_embedding_parts(
    vault_root,
    rel_path,
    embedding_parts_by_path: Mapping[str, EmbeddingParts] | None,
):
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


def refresh_embeddings_outputs(
    vault_root,
    router: Mapping[str, Any],
    documents: Sequence[Mapping[str, Any]],
    *,
    embedding_parts_by_path: Mapping[str, EmbeddingParts] | None = None,
):
    """Refresh semantic sidecars for the current router and lexical document set."""
    return build_embeddings(
        vault_root,
        router,
        documents,
        embedding_parts_by_path=embedding_parts_by_path,
    )
