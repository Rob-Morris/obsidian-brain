"""Shared semantic runtime helpers for embeddings-backed search and processing."""

from __future__ import annotations

import importlib.util
import json
import os

import _semantic.config as semantic_config
import _semantic.model as semantic_model


TYPE_EMBEDDINGS_REL = os.path.join(".brain", "local", "type-embeddings.npy")
DOC_EMBEDDINGS_REL = os.path.join(".brain", "local", "doc-embeddings.npy")
EMBEDDINGS_META_REL = os.path.join(".brain", "local", "embeddings-meta.json")
EMBEDDING_MODEL = semantic_model.SHIPPED_MODEL_NAME
EMBEDDING_MODEL_REVISION = semantic_model.SHIPPED_MODEL_REVISION
EMBEDDING_DIM = 384


class SemanticEmbeddingsLoadError(RuntimeError):
    """Raised when persisted semantic embeddings state is present but unreadable."""


def semantic_runtime_dependencies_available():
    """Return True when lightweight semantic-search deps appear importable."""
    return (
        importlib.util.find_spec("numpy") is not None
        and importlib.util.find_spec("sentence_transformers") is not None
    )


def clear_query_encoder():
    semantic_model.clear_query_encoder()


def load_query_encoder(vault_root):
    return semantic_model.load_local_model(vault_root)


def get_query_encoder(vault_root):
    return semantic_model.get_query_encoder(vault_root)


def encode_query(vault_root, query, *, query_encoder=None):
    """Encode a single query string with an optional preloaded encoder."""
    encoder = query_encoder if query_encoder is not None else get_query_encoder(vault_root)
    return encoder.encode([query], normalize_embeddings=True)[0]


def rank_against(query_vec, matrix, meta_entries, *, filter_fn=None, top_k=None):
    """Rank `meta_entries` by cosine similarity of `query_vec` against `matrix` rows.

    Assumes embeddings are L2-normalized (as SentenceTransformer produces with
    `normalize_embeddings=True`), so `matrix @ query_vec` equals cosine
    similarity per row.
    """
    assert matrix.shape[0] == len(meta_entries), (
        "embedding matrix row count must match metadata entry count"
    )
    similarities = matrix @ query_vec
    results = []
    for idx, score in enumerate(similarities):
        entry = meta_entries[idx]
        if filter_fn is not None and not filter_fn(entry):
            continue
        results.append({**entry, "score": float(score)})
    results.sort(key=lambda item: item["score"], reverse=True)
    if top_k is not None:
        results = results[:top_k]
    return results


def embeddings_sidecars_present(vault_root):
    """Return True when the persisted semantic-engine sidecars exist on disk."""
    vault_root = str(vault_root)
    return (
        os.path.isfile(os.path.join(vault_root, TYPE_EMBEDDINGS_REL))
        and os.path.isfile(os.path.join(vault_root, DOC_EMBEDDINGS_REL))
        and os.path.isfile(os.path.join(vault_root, EMBEDDINGS_META_REL))
    )


def clear_embeddings_outputs(vault_root):
    """Remove persisted embeddings sidecars (npy + meta json) if they exist."""
    vault_str = str(vault_root)
    removed = []
    for rel_path in (TYPE_EMBEDDINGS_REL, DOC_EMBEDDINGS_REL, EMBEDDINGS_META_REL):
        abs_path = os.path.join(vault_str, rel_path)
        try:
            os.remove(abs_path)
            removed.append(rel_path)
        except FileNotFoundError:
            continue
    return removed


def semantic_engine_available(
    vault_root,
    *,
    config=None,
    doc_embeddings=None,
    embeddings_meta=None,
    skip_sidecar_check=False,
):
    """Return True when the semantic runtime is provisioned and usable."""
    if not semantic_config.semantic_engine_installed(vault_root, config=config):
        return False
    if not semantic_runtime_dependencies_available():
        return False
    model_state = semantic_model.inspect_model_state(vault_root)
    if not model_state.healthy:
        return False
    if skip_sidecar_check:
        return True
    if doc_embeddings is not None and embeddings_meta is not None:
        return True
    return embeddings_sidecars_present(vault_root)


def embeddings_sidecars_match_manifest(vault_root, manifest):
    """Return `(present, outdated)` for sidecars relative to a model manifest."""
    present = embeddings_sidecars_present(vault_root)
    if not present or manifest is None:
        return (present, False)

    meta_path = os.path.join(str(vault_root), EMBEDDINGS_META_REL)
    try:
        with open(meta_path, "r", encoding="utf-8") as handle:
            meta = json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError):
        return (True, True)
    if not isinstance(meta, dict):
        return (True, True)
    outdated = (
        meta.get("model") != manifest.model_name
        or meta.get("model_revision") != manifest.revision
    )
    return (True, outdated)


def load_embeddings_state(vault_root):
    """Load type+doc embeddings and shared meta from disk.

    Returns `(type_embeddings, doc_embeddings, meta)`. Meta is the shared
    row→entry mapping; without it the npy arrays can't be interpreted, so
    type/doc default to `None` whenever metadata is absent. Either npy can
    independently be `None` if its file is missing. Returns
    `(None, None, None)` when numpy is unavailable. Raises
    `SemanticEmbeddingsLoadError` when persisted metadata or arrays are
    present but unreadable.
    """
    try:
        import numpy as np
    except ImportError:
        return (None, None, None)

    vault_root = str(vault_root)
    type_path = os.path.join(vault_root, TYPE_EMBEDDINGS_REL)
    doc_path = os.path.join(vault_root, DOC_EMBEDDINGS_REL)
    meta_path = os.path.join(vault_root, EMBEDDINGS_META_REL)

    meta = None
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise SemanticEmbeddingsLoadError(
                f"semantic embeddings metadata is unreadable at {meta_path}: {exc}"
            ) from exc
        if not isinstance(loaded, dict):
            raise SemanticEmbeddingsLoadError(
                f"semantic embeddings metadata at {meta_path} is not a JSON object"
            )
        meta = loaded

    type_embeddings = None
    if meta is not None and os.path.isfile(type_path):
        try:
            type_embeddings = np.load(type_path)
        except (OSError, ValueError) as exc:
            raise SemanticEmbeddingsLoadError(
                f"semantic type embeddings are unreadable at {type_path}: {exc}"
            ) from exc

    doc_embeddings = None
    if meta is not None and os.path.isfile(doc_path):
        try:
            doc_embeddings = np.load(doc_path)
        except (OSError, ValueError) as exc:
            raise SemanticEmbeddingsLoadError(
                f"semantic document embeddings are unreadable at {doc_path}: {exc}"
            ) from exc

    return (type_embeddings, doc_embeddings, meta)


def load_doc_embeddings(vault_root):
    """Load persisted document embeddings + metadata, or return (None, None).

    Convenience wrapper over `load_embeddings_state` for callers that don't
    need type embeddings. Returns `(None, None)` if either is missing.
    """
    _type, doc, meta = load_embeddings_state(vault_root)
    if doc is None or meta is None:
        return (None, None)
    return (doc, meta)
