"""Shared semantic runtime helpers for embeddings-backed search and processing."""

from __future__ import annotations

import importlib.util
import json
import os
import threading

from _common import safe_write_via


TYPE_EMBEDDINGS_REL = os.path.join(".brain", "local", "type-embeddings.npy")
DOC_EMBEDDINGS_REL = os.path.join(".brain", "local", "doc-embeddings.npy")
EMBEDDINGS_META_REL = os.path.join(".brain", "local", "embeddings-meta.json")
LOCAL_CONFIG_REL = os.path.join(".brain", "local", "config.yaml")
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384

SEMANTIC_PROCESSING_FLAG = "semantic_processing"
SEMANTIC_RETRIEVAL_FLAG = "semantic_retrieval"
SEMANTIC_ENGINE_INSTALLED_FLAG = "semantic_engine_installed"

_QUERY_ENCODER = None
_QUERY_ENCODER_LOCK = threading.Lock()


class SemanticConfigLoadError(RuntimeError):
    """Raised when semantic config probing hits a real config load failure."""


def load_config_best_effort(vault_root, config=None):
    """Best-effort config loader used by feature-flag helpers."""
    if config is not None:
        return config
    try:
        import config as config_mod
    except ImportError:
        return None
    try:
        return config_mod.load_config(str(vault_root))
    except Exception as exc:
        raise SemanticConfigLoadError(f"failed to load config: {exc}") from exc


def _read_nested_flag(vault_root, section, flag_name, *, config=None, compat_names=()):
    """Return a boolean from defaults.<section>.<flag_name>."""
    config = load_config_best_effort(vault_root, config=config)
    if not isinstance(config, dict):
        return False
    flags = config.get("defaults", {}).get(section, {})
    if not isinstance(flags, dict):
        return False
    if bool(flags.get(flag_name, False)):
        return True
    return any(bool(flags.get(name, False)) for name in compat_names)


def semantic_processing_enabled(vault_root, *, config=None):
    """Return True when embedding-backed processing is enabled."""
    return _read_nested_flag(vault_root, "flags", SEMANTIC_PROCESSING_FLAG, config=config)


def semantic_retrieval_enabled(vault_root, *, config=None):
    """Return True when semantic retrieval is enabled for search."""
    return _read_nested_flag(vault_root, "flags", SEMANTIC_RETRIEVAL_FLAG, config=config)


def embeddings_enabled(vault_root, *, config=None):
    """Return True when any embedding-backed feature is enabled."""
    return (
        semantic_processing_enabled(vault_root, config=config)
        or semantic_retrieval_enabled(vault_root, config=config)
    )


def semantic_engine_installed(vault_root, *, config=None):
    """Return True when the local environment was provisioned for semantic work."""
    return _read_nested_flag(
        vault_root,
        "local_runtime",
        SEMANTIC_ENGINE_INSTALLED_FLAG,
        config=config,
    )


def semantic_runtime_dependencies_available():
    """Return True when lightweight semantic-search deps appear importable."""
    return (
        importlib.util.find_spec("numpy") is not None
        and importlib.util.find_spec("sentence_transformers") is not None
    )


def clear_query_encoder():
    global _QUERY_ENCODER
    with _QUERY_ENCODER_LOCK:
        _QUERY_ENCODER = None


def load_query_encoder():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(EMBEDDING_MODEL)


def get_query_encoder():
    global _QUERY_ENCODER
    if _QUERY_ENCODER is not None:
        return _QUERY_ENCODER
    with _QUERY_ENCODER_LOCK:
        if _QUERY_ENCODER is None:
            _QUERY_ENCODER = load_query_encoder()
        return _QUERY_ENCODER


def encode_query(query, *, query_encoder=None):
    """Encode a single query string with an optional preloaded encoder."""
    encoder = query_encoder if query_encoder is not None else get_query_encoder()
    return encoder.encode([query], normalize_embeddings=True)[0]


def rank_against(query_vec, matrix, meta_entries, *, filter_fn=None, top_k=None):
    """Rank `meta_entries` by cosine similarity of `query_vec` against `matrix` rows.

    Assumes embeddings are L2-normalized (as SentenceTransformer produces with
    `normalize_embeddings=True`), so `matrix @ query_vec` equals cosine
    similarity per row.
    """
    similarities = matrix @ query_vec
    results = []
    for idx, score in enumerate(similarities):
        if idx >= len(meta_entries):
            continue
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
    if not semantic_engine_installed(vault_root, config=config):
        return False
    if not semantic_runtime_dependencies_available():
        return False
    if skip_sidecar_check:
        return True
    if doc_embeddings is not None and embeddings_meta is not None:
        return True
    return embeddings_sidecars_present(vault_root)


def load_embeddings_state(vault_root):
    """Load type+doc embeddings and shared meta from disk.

    Returns `(type_embeddings, doc_embeddings, meta)`. Meta is the shared
    row→entry mapping; without it the npy arrays can't be interpreted, so
    type/doc default to `None` whenever meta is missing or unparseable.
    Either npy can independently be `None` if its file is missing or fails
    to load. Returns `(None, None, None)` when numpy is unavailable.
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
            if isinstance(loaded, dict):
                meta = loaded
        except (OSError, ValueError, json.JSONDecodeError):
            meta = None

    type_embeddings = None
    if meta is not None and os.path.isfile(type_path):
        try:
            type_embeddings = np.load(type_path)
        except (OSError, ValueError):
            type_embeddings = None

    doc_embeddings = None
    if meta is not None and os.path.isfile(doc_path):
        try:
            doc_embeddings = np.load(doc_path)
        except (OSError, ValueError):
            doc_embeddings = None

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


def set_semantic_engine_installed(vault_root, installed=True):
    """Write the local semantic-engine provisioning marker if this is a vault."""
    import yaml

    vault_root = str(vault_root)
    if not os.path.isdir(os.path.join(vault_root, ".brain")):
        return False

    config_path = os.path.join(vault_root, LOCAL_CONFIG_REL)
    os.makedirs(os.path.dirname(config_path), exist_ok=True)

    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except FileNotFoundError:
        data = {}
    if not isinstance(data, dict):
        data = {}

    defaults = data.get("defaults")
    if not isinstance(defaults, dict):
        defaults = {}
        data["defaults"] = defaults
    local_runtime = defaults.get("local_runtime")
    if not isinstance(local_runtime, dict):
        local_runtime = {}
        defaults["local_runtime"] = local_runtime
    local_runtime[SEMANTIC_ENGINE_INSTALLED_FLAG] = bool(installed)

    safe_write_via(
        config_path,
        lambda handle: yaml.safe_dump(data, handle, sort_keys=False),
        mode="w",
    )
    return True
