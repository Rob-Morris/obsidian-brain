"""Semantic sidecar build, persistence, and refresh mechanics."""

from __future__ import annotations

from datetime import datetime, timezone
import os
import hashlib
import json
from typing import Any, Mapping, Sequence

from _common import read_artefact, safe_write_json, safe_write_via
from _common._workspace import workspace_discovery_metadata
from _lifecycle.document_parts import EmbeddingParts, embedding_parts_from_body
from _lifecycle.retrieval_errors import (
    RetrievalPersistenceError,
    SemanticRuntimeUnavailableError,
    UnreadableRetrievalSourceError,
)
import _semantic.model as semantic_model
import _semantic.runtime as semantic_runtime
from _taxonomy_descriptions import extract_type_description


# Resolved on first use rather than at import time: semantic provisioning
# installs NumPy into the running interpreter's environment after this module
# has already been imported, and the first asset refresh follows in the same
# process.
np = None


def _require_numpy():
    """Return NumPy or raise the typed semantic-runtime unavailability error."""
    global np
    if np is None:
        try:
            import numpy
        except ImportError as exc:
            raise SemanticRuntimeUnavailableError(
                "semantic runtime dependencies are unavailable: numpy is not installed",
                operation="building semantic embeddings",
            ) from exc
        np = numpy
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
    for doc in documents:
        embedding_parts = _load_embedding_parts(
            vault_root,
            doc["path"],
            embedding_parts_by_path,
        )
        if embedding_parts is None:
            continue
        type_desc = type_desc_by_frontmatter.get(doc["type"], doc["type"])
        parts = [doc["title"], doc["type"], type_desc]
        if embedding_parts.headings:
            parts.append(" ".join(embedding_parts.headings))
        if embedding_parts.body_head:
            parts.append(embedding_parts.body_head)
        doc_texts.append("\n".join(part for part in parts if part))
        doc_meta.append(
            {
                "index": len(doc_meta),
                "path": doc["path"],
                "type": doc["type"],
                "title": doc["title"],
                "tags": doc.get("tags", []),
                "workspace": doc.get("workspace"),
                "key": doc.get("key"),
                **workspace_discovery_metadata(doc),
                "embedding_text_hash": _text_hash(doc_texts[-1]),
                "embedding_source_hash": doc.get("embedding_source_hash"),
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


def _text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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

    meta = _embedding_metadata(router, manifest, type_texts, type_meta, doc_meta)
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
    except UnicodeDecodeError:
        return None
    except OSError as exc:
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
    # Reuse only vectors whose exact encoder inputs and model identity match.
    manifest = semantic_model.inspect_model_state(vault_root).manifest
    old_types = old_docs = old_meta = None
    try:
        old_types, old_docs, old_meta = semantic_runtime.load_embeddings_state(vault_root)
    except semantic_runtime.SemanticEmbeddingsLoadError:
        pass
    if (manifest is None or old_meta is None or old_types is None or old_docs is None
            or old_meta.get("model") != manifest.model_name
            or old_meta.get("model_revision") != manifest.revision
            or old_meta.get("dim") != semantic_runtime.EMBEDDING_DIM):
        return build_embeddings(vault_root, router, documents,
                                embedding_parts_by_path=embedding_parts_by_path)
    type_texts, type_meta, descriptions = _build_type_corpus(vault_root, router)
    doc_texts, doc_meta = _build_doc_corpus(
        vault_root, documents, type_desc_by_frontmatter=descriptions,
        embedding_parts_by_path=embedding_parts_by_path,
    )
    numpy = _require_numpy()
    type_hashes = [_text_hash(text) for text in type_texts]
    old_hashes = old_meta.get("type_text_hashes", [])
    reusable_types = {digest: old_types[i] for i, digest in enumerate(old_hashes)
                      if i < len(old_types)}
    reusable_docs = {entry.get("embedding_text_hash"): old_docs[i]
                     for i, entry in enumerate(old_meta.get("documents", []))
                     if i < len(old_docs) and entry.get("embedding_text_hash")}
    model = None

    def vectors(texts, hashes, reusable):
        nonlocal model
        missing = list(dict.fromkeys(digest for digest in hashes if digest not in reusable))
        if missing:
            if model is None:
                model, _ = semantic_model.load_local_model_with_manifest(vault_root)
            by_hash = dict(zip(hashes, texts))
            encoded = _encode_or_empty(model, [by_hash[digest] for digest in missing])
            reusable.update(zip(missing, encoded))
        return numpy.stack([reusable[digest] for digest in hashes]) if hashes else numpy.zeros((0, semantic_runtime.EMBEDDING_DIM))

    type_embeddings = vectors(type_texts, type_hashes, reusable_types)
    doc_embeddings = vectors(doc_texts, [entry["embedding_text_hash"] for entry in doc_meta], reusable_docs)
    meta = _embedding_metadata(router, manifest, type_texts, type_meta, doc_meta)
    _persist_embeddings_outputs(vault_root, type_embeddings, doc_embeddings, meta)
    return type_embeddings, doc_embeddings, meta



def _embedding_metadata(router, manifest, type_texts, type_meta, doc_meta):
    """Use one persisted metadata schema for full builds and vector reuse."""
    return {
        "metadata_version": semantic_runtime.EMBEDDINGS_METADATA_VERSION,
        "type_resources_hash": _type_resources_hash(router),
        "type_text_hashes": [_text_hash(text) for text in type_texts],
        "model": manifest.model_name,
        "model_revision": manifest.revision,
        semantic_runtime.ROUTER_SOURCE_HASH_KEY: semantic_runtime.router_source_hash(router),
        "dim": semantic_runtime.EMBEDDING_DIM,
        "built_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "types": type_meta,
        "documents": doc_meta,
    }


def _type_resources_hash(router):
    artefacts = router.get("artefacts", [])
    sources = router.get("meta", {}).get("sources", {})
    taxonomy_sources = {
        artefact["taxonomy_file"]: sources.get(artefact["taxonomy_file"])
        for artefact in artefacts if artefact.get("taxonomy_file")
    }
    return _text_hash(json.dumps([artefacts, taxonomy_sources], sort_keys=True))


def refresh_document_metadata(vault_root, router, documents):
    """Refresh filters after an in-place write without loading the encoder or arrays.

    Source fingerprints and type-resource identity prove that every vector's
    encoder inputs are unchanged. Otherwise the caller invalidates the sidecars.
    """
    meta_path = os.path.join(str(vault_root), semantic_runtime.EMBEDDINGS_META_REL)
    try:
        with open(meta_path, encoding="utf-8") as handle:
            meta = json.load(handle)
    except (OSError, ValueError):
        return False
    if (not isinstance(meta, dict)
            or meta.get("metadata_version") != semantic_runtime.EMBEDDINGS_METADATA_VERSION
            or meta.get("type_resources_hash") != _type_resources_hash(router)):
        return False
    old_documents = meta.get("documents")
    if not isinstance(old_documents, list):
        return False
    by_path = {doc["path"]: doc for doc in documents}
    if len(by_path) != len(old_documents):
        return False
    updated = []
    for entry in old_documents:
        if not isinstance(entry, dict):
            return False
        doc = by_path.get(entry.get("path"))
        if (doc is None or not doc.get("embedding_source_hash")
                or entry.get("embedding_source_hash") != doc["embedding_source_hash"]):
            return False
        updated.append({**entry, "tags": doc.get("tags", []),
                        "status": doc.get("status"), "workspace": doc.get("workspace"),
                        "key": doc.get("key"), **workspace_discovery_metadata(doc)})
    meta["documents"] = updated
    meta[semantic_runtime.ROUTER_SOURCE_HASH_KEY] = semantic_runtime.router_source_hash(router)
    safe_write_json(meta_path, meta, bounds=str(vault_root))
    return True
