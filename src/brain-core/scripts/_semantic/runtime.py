"""Shared semantic runtime helpers for embeddings-backed search and processing."""

from __future__ import annotations

import importlib.util
import json
import os
from typing import Literal

from _common import load_compiled_router
from _bootstrap.owned_paths import owned_file, UnsafeFilesystemLayoutError
import _semantic.config as semantic_config
import _semantic.model as semantic_model


TYPE_EMBEDDINGS_REL = os.path.join(".brain", "local", "type-embeddings.npy")
DOC_EMBEDDINGS_REL = os.path.join(".brain", "local", "doc-embeddings.npy")
EMBEDDINGS_META_REL = os.path.join(".brain", "local", "embeddings-meta.json")
EMBEDDINGS_METADATA_VERSION = 3
ROUTER_SOURCE_HASH_KEY = "router_source_hash"
EMBEDDING_MODEL = semantic_model.SHIPPED_MODEL_NAME
EMBEDDING_MODEL_REVISION = semantic_model.SHIPPED_MODEL_REVISION
EMBEDDING_DIM = 384
EmbeddingSelection = Literal["types", "documents", "all"]


class SemanticEmbeddingsLoadError(RuntimeError):
    """Raised when persisted semantic embeddings state is present but unreadable."""


class SemanticEmbeddingsLayoutError(SemanticEmbeddingsLoadError):
    """Unsafe fixed semantic storage must be restored, never rebuilt through."""


class RouterMetadataError(RuntimeError):
    """Raised when compiled router metadata is malformed."""


class CompiledRouterMissingError(RouterMetadataError):
    """Raised when embeddings validation needs a compiled router that is missing."""


def semantic_runtime_dependencies_available():
    """Return True when lightweight semantic-search deps appear importable."""
    return all(
        importlib.util.find_spec(module) is not None
        for module in semantic_model.ENCODER_MODULES
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

    Assumes embeddings are L2-normalized (the local encoder always produces
    them with `normalize_embeddings=True`), so `matrix @ query_vec` equals
    cosine similarity per row.
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
    try:
        for relative in (TYPE_EMBEDDINGS_REL, DOC_EMBEDDINGS_REL, EMBEDDINGS_META_REL):
            owned_file(vault_root, relative)
    except OSError:
        return False
    return True


def admit_semantic_outputs(vault_root):
    """Observe the complete fixed semantic destination set before any effects."""
    from _bootstrap.owned_paths import owned_output_paths
    relatives = (EMBEDDINGS_META_REL, TYPE_EMBEDDINGS_REL, DOC_EMBEDDINGS_REL)
    return dict(zip(relatives, owned_output_paths(vault_root, relatives)))


def clear_embeddings_outputs(vault_root):
    """Admit every sidecar before withdrawing readiness or removing any array."""
    from _lifecycle.retrieval_errors import RetrievalPersistenceError
    paths = admit_semantic_outputs(vault_root)
    removed = []
    try:
        for relative, path in paths.items():
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            removed.append(relative)
    except OSError as exc:
        error = RetrievalPersistenceError(relative, "clearing semantic sidecars", exc)
        error.committed_paths = tuple(removed)
        raise error from exc
    return [path for path in (TYPE_EMBEDDINGS_REL, DOC_EMBEDDINGS_REL, EMBEDDINGS_META_REL)
            if path in removed]


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

    try:
        meta_path = owned_file(vault_root, EMBEDDINGS_META_REL)
        with open(meta_path, "r", encoding="utf-8") as handle:
            meta = json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError):
        return (True, True)
    if not isinstance(meta, dict):
        return (True, True)
    outdated = (
        meta.get("metadata_version") != EMBEDDINGS_METADATA_VERSION
        or meta.get("model") != manifest.model_name
        or meta.get("model_revision") != manifest.revision
    )
    return (True, outdated)

def router_source_hash(router):
    """Return the router source-hash fingerprint when present."""
    if not isinstance(router, dict):
        raise RouterMetadataError(
            "compiled router must be a JSON object with a meta.source_hash field"
        )
    meta = router.get("meta")
    if not isinstance(meta, dict):
        raise RouterMetadataError(
            "compiled router metadata must be a JSON object containing source_hash"
        )
    source_hash = meta.get("source_hash")
    if source_hash is None:
        return None
    if not isinstance(source_hash, str):
        raise RouterMetadataError("compiled router meta.source_hash must be a string")
    return source_hash


def embeddings_meta_matches_router(meta, router):
    """Return True when embeddings metadata was built against `router`."""
    current_router_source_hash = router_source_hash(router)
    if current_router_source_hash is None:
        return False
    return (isinstance(meta, dict)
            and meta.get("metadata_version") == EMBEDDINGS_METADATA_VERSION
            and meta.get(ROUTER_SOURCE_HASH_KEY) == current_router_source_hash)


def embeddings_meta_matches_current_router(vault_root, meta, *, loader=None):
    """Return True when embeddings metadata matches the current compiled router."""
    load = loader or load_compiled_router
    router = load(vault_root)
    if not isinstance(router, dict):
        raise RouterMetadataError(
            "compiled router must load as a JSON object with metadata"
        )
    if "error" in router:
        error = router["error"]
        if isinstance(error, str) and error.startswith("Compiled router not found at "):
            raise CompiledRouterMissingError(error)
        raise RouterMetadataError(error)
    return embeddings_meta_matches_router(meta, router)


def load_embeddings_state(
    vault_root,
    *,
    selection: EmbeddingSelection = "all",
):
    """Load selected embedding arrays and their shared metadata from disk.

    Returns `(type_embeddings, doc_embeddings, meta)`. Meta is the shared
    row→entry mapping; without it the npy arrays can't be interpreted, so
    type/doc default to `None` whenever metadata is absent. An unselected or
    missing array is represented by `None`. Returns
    `(None, None, None)` when numpy is unavailable. Raises
    `SemanticEmbeddingsLoadError` when persisted metadata or arrays are
    present but unreadable.
    """
    if selection not in {"types", "documents", "all"}:
        raise ValueError(f"unsupported embedding selection: {selection}")
    try:
        import numpy as np
    except ModuleNotFoundError as exc:
        if exc.name == "numpy":
            return (None, None, None)
        raise SemanticEmbeddingsLoadError(
            f"semantic NumPy dependency failed to import: {exc}"
        ) from exc
    except ImportError as exc:
        raise SemanticEmbeddingsLoadError(
            f"semantic NumPy dependency failed to import: {exc}"
        ) from exc

    vault_root = str(vault_root)
    try:
        meta_path = owned_file(vault_root, EMBEDDINGS_META_REL)
    except FileNotFoundError:
        return (None, None, None)
    except UnsafeFilesystemLayoutError as exc:
        raise SemanticEmbeddingsLayoutError(str(exc)) from exc
    except OSError as exc:
        raise SemanticEmbeddingsLoadError(str(exc)) from exc

    meta = None
    if meta_path is not None:
        try:
            with open(meta_path, "rb") as f:
                observed = os.fstat(f.fileno())
                metadata_bytes = f.read()
            loaded = json.loads(metadata_bytes)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise SemanticEmbeddingsLoadError(
                f"semantic embeddings metadata is unreadable at {meta_path}: {exc}"
            ) from exc
        if not isinstance(loaded, dict):
            raise SemanticEmbeddingsLoadError(
                f"semantic embeddings metadata at {meta_path} is not a JSON object"
            )
        meta = loaded
        try:
            if not embeddings_meta_matches_current_router(vault_root, meta):
                return (None, None, None)
        except RouterMetadataError:
            return (None, None, None)

    def load_array(relative):
        try:
            path = owned_file(vault_root, relative)
            return np.load(path)
        except FileNotFoundError:
            return None
        except UnsafeFilesystemLayoutError as exc:
            raise SemanticEmbeddingsLayoutError(str(exc)) from exc
        except (OSError, ValueError) as exc:
            kind = "type" if relative == TYPE_EMBEDDINGS_REL else "document"
            raise SemanticEmbeddingsLoadError(
                f"semantic {kind} embeddings are unreadable at {relative}; "
                "run retrieval.rebuild-semantic."
            ) from exc

    type_embeddings = (load_array(TYPE_EMBEDDINGS_REL)
                       if selection in {"types", "all"} and meta is not None else None)
    doc_embeddings = (load_array(DOC_EMBEDDINGS_REL)
                      if selection in {"documents", "all"} and meta is not None else None)

    if meta is not None:
        try:
            meta_path = owned_file(vault_root, EMBEDDINGS_META_REL)
            with open(meta_path, "rb") as handle:
                after = os.fstat(handle.fileno())
                unchanged = handle.read() == metadata_bytes
        except UnsafeFilesystemLayoutError as exc:
            raise SemanticEmbeddingsLayoutError(str(exc)) from exc
        except OSError:
            return (None, None, None)
        identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
        if not unchanged or identity(observed) != identity(after):
            return (None, None, None)
        validate_embedding_arrays(meta, type_embeddings, doc_embeddings)
    return (type_embeddings, doc_embeddings, meta)


def validate_embedding_arrays(meta, type_embeddings=None, doc_embeddings=None):
    """Refuse incoherent disk or supplied vectors before they influence a query."""
    if (not isinstance(meta, dict)
            or meta.get("metadata_version") != EMBEDDINGS_METADATA_VERSION
            or meta.get("model") != EMBEDDING_MODEL
            or meta.get("model_revision") != EMBEDDING_MODEL_REVISION
            or meta.get("dim") != EMBEDDING_DIM):
        raise SemanticEmbeddingsLoadError("Semantic metadata is outdated; run retrieval.rebuild-semantic.")
    for matrix, key in ((type_embeddings, "types"), (doc_embeddings, "documents")):
        if matrix is None:
            continue
        entries = meta.get(key)
        if (not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries)
                or getattr(matrix, "ndim", None) != 2
                or matrix.shape != (len(entries), EMBEDDING_DIM)):
            raise SemanticEmbeddingsLoadError("Semantic array and metadata rows do not match; run retrieval.rebuild-semantic.")


def load_doc_embeddings(vault_root):
    """Load persisted document embeddings + metadata, or return (None, None).

    Convenience wrapper over `load_embeddings_state` for callers that don't
    need type embeddings. Returns `(None, None)` if either is missing.
    """
    _type, doc, meta = load_embeddings_state(
        vault_root,
        selection="documents",
    )
    if doc is None or meta is None:
        return (None, None)
    return (doc, meta)
