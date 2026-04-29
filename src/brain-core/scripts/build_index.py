#!/usr/bin/env python3
"""
build_index.py — Brain-core BM25 retrieval index builder

Walks all .md files in living + temporal type folders, extracts frontmatter
and body text, computes BM25 corpus stats and per-doc term frequencies,
and writes .brain/local/retrieval-index.json. When the experimental
brain_process feature is enabled and a compiled router is available, it also
refreshes the optional embeddings sidecar files.

Usage:
    python3 build_index.py           # write index JSON (+ embeddings when enabled)
    python3 build_index.py --json    # output JSON to stdout
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from _common import (
    find_vault_root,
    iter_artefact_paths,
    load_compiled_router,
    normalize_artefact_key,
    read_artefact,
    read_version,
    safe_write_via,
    safe_write_json,
    scan_living_types,
    scan_temporal_types,
    tokenise,
)

# Optional embedding dependencies — graceful degradation when missing
try:
    import numpy as np
    from sentence_transformers import SentenceTransformer
    _HAS_EMBEDDINGS = True
except ImportError:
    _HAS_EMBEDDINGS = False


def extract_title(body, filename):
    """Extract title from filename stem.

    In Obsidian, the filename is the canonical title — it's what users
    link to, search for, and see in the file explorer. The H1 heading
    is display text and may omit structural info (e.g. type prefixes).
    """
    return Path(filename).stem


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OUTPUT_PATH = os.path.join(".brain", "local", "retrieval-index.json")
TYPE_EMBEDDINGS_REL = os.path.join(".brain", "local", "type-embeddings.npy")
DOC_EMBEDDINGS_REL = os.path.join(".brain", "local", "doc-embeddings.npy")
EMBEDDINGS_META_REL = os.path.join(".brain", "local", "embeddings-meta.json")
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384
INDEX_VERSION = "1.0.0"
BM25_K1 = 1.5
BM25_B = 0.75
PROCESS_FEATURE_FLAG = "brain_process"


def embeddings_enabled(vault_root, *, config=None):
    """Return True when experimental process embeddings are enabled.

    Embeddings are an implementation detail of the experimental
    brain_process surface, so they follow the same feature flag. This helper
    is best-effort so BM25 builds remain usable without config-loading deps.
    """
    if config is None:
        try:
            import config as config_mod
        except ImportError:
            return False
        try:
            config = config_mod.load_config(str(vault_root))
        except Exception:
            return False

    if not process_enabled(vault_root, config=config):
        return False
    return True


def process_enabled(vault_root, *, config=None):
    """Return True when the experimental process feature is enabled."""
    if config is None:
        try:
            import config as config_mod
        except ImportError:
            return False
        try:
            config = config_mod.load_config(str(vault_root))
        except Exception:
            return False

    if not isinstance(config, dict):
        return False
    defaults = config.get("defaults", {})
    if not isinstance(defaults, dict):
        return False
    flags = defaults.get("flags", {})
    if not isinstance(flags, dict):
        return False
    return bool(flags.get(PROCESS_FEATURE_FLAG, False))


def clear_embeddings_outputs(vault_root):
    """Remove persisted embeddings sidecars if they exist."""
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


# ---------------------------------------------------------------------------
# Type description extraction (for classification + embeddings)
# ---------------------------------------------------------------------------

def extract_type_description(vault_root, artefact):
    """Read taxonomy file and extract one-liner + Purpose + When To Use/Trigger.

    Returns a combined description string suitable for embedding or BM25
    classification. Returns empty string if taxonomy file missing.
    """
    taxonomy_file = artefact.get("taxonomy_file")
    if not taxonomy_file:
        return ""

    abs_path = os.path.join(str(vault_root), taxonomy_file)
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return ""

    parts = []

    # One-liner: first paragraph after H1 (single line, no DOTALL)
    h1_match = re.search(r"^# .+\n\n(.+)", content, re.MULTILINE)
    if h1_match:
        parts.append(h1_match.group(1).strip())

    # Extract named sections
    for section_name in ("Purpose", "When To Use", "Trigger"):
        body = _extract_section(content, section_name)
        if body:
            parts.append(body)

    return "\n\n".join(parts)


_section_cache: dict[str, re.Pattern] = {}


def _extract_section(content, heading):
    """Extract the body of a ## heading section, stopping at the next ## or EOF."""
    pattern = _section_cache.get(heading)
    if pattern is None:
        pattern = re.compile(
            rf"^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)",
            re.MULTILINE | re.DOTALL,
        )
        _section_cache[heading] = pattern
    match = pattern.search(content)
    if match:
        return match.group(1).strip()
    return ""


# ---------------------------------------------------------------------------
# Embedding building (optional)
# ---------------------------------------------------------------------------

def _safe_save_npy(path, array, *, bounds=None):
    """Persist a NumPy array atomically via the shared write primitive."""
    return safe_write_via(
        path,
        lambda handle: np.save(handle, array),
        bounds=bounds,
    )


def build_embeddings(vault_root, router, documents):
    """Compute embeddings for type descriptions and documents.

    Returns meta dict on success, None if embedding deps unavailable.
    """
    if not _HAS_EMBEDDINGS:
        return None

    vault_str = str(vault_root)
    artefacts = [a for a in router.get("artefacts", []) if a.get("configured")]

    # Extract type descriptions
    type_texts = []
    type_meta = []
    for i, artefact in enumerate(artefacts):
        desc = extract_type_description(vault_root, artefact)
        if not desc:
            desc = artefact.get("type", artefact.get("key", ""))
        type_texts.append(desc)
        type_meta.append({
            "index": i,
            "key": artefact["key"],
            "type": artefact["type"],
            "description": desc[:200],
        })

    # Build document texts (title + body)
    doc_texts = []
    doc_meta = []
    for i, doc in enumerate(documents):
        abs_path = os.path.join(vault_str, doc["path"])
        try:
            _, body = read_artefact(abs_path)
        except (OSError, UnicodeDecodeError):
            body = ""
        doc_texts.append(f"{doc['title']} {body[:500]}")
        doc_meta.append({
            "index": i,
            "path": doc["path"],
            "type": doc["type"],
            "title": doc["title"],
        })

    # Encode
    model = SentenceTransformer(EMBEDDING_MODEL)

    type_embeddings = model.encode(type_texts, normalize_embeddings=True) if type_texts else np.zeros((0, EMBEDDING_DIM))
    doc_embeddings = model.encode(doc_texts, normalize_embeddings=True) if doc_texts else np.zeros((0, EMBEDDING_DIM))

    # .brain/local/ may not exist yet (directory changed from _Config/ in v0.16.0)
    os.makedirs(os.path.join(vault_str, os.path.dirname(TYPE_EMBEDDINGS_REL)), exist_ok=True)
    _safe_save_npy(os.path.join(vault_str, TYPE_EMBEDDINGS_REL), type_embeddings, bounds=vault_str)
    _safe_save_npy(os.path.join(vault_str, DOC_EMBEDDINGS_REL), doc_embeddings, bounds=vault_str)

    meta = {
        "model": EMBEDDING_MODEL,
        "dim": EMBEDDING_DIM,
        "built_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "types": type_meta,
        "documents": doc_meta,
    }
    meta_path = os.path.join(vault_str, EMBEDDINGS_META_REL)
    safe_write_json(meta_path, meta, bounds=vault_str)

    return meta


# ---------------------------------------------------------------------------
# Per-file document parsing
# ---------------------------------------------------------------------------

def parse_doc(vault_root, rel_path, type_hint=None):
    """Parse a single .md file into a document dict for the index.

    Args:
        vault_root: Absolute path to the vault root.
        rel_path: Path relative to vault root.
        type_hint: Fallback type string if frontmatter has no type field.

    Returns:
        Document dict with path, title, type, tags, status, modified,
        doc_length, tf, title_tf. Returns None if the file cannot be read.
    """
    vault_str = str(vault_root)
    abs_path = os.path.join(vault_str, rel_path)
    try:
        fields, body = read_artefact(abs_path)
    except (OSError, UnicodeDecodeError):
        return None

    title = extract_title(body, rel_path)

    try:
        mtime = os.path.getmtime(abs_path)
        modified = datetime.fromtimestamp(mtime, tz=timezone.utc).astimezone().isoformat()
    except OSError:
        modified = None

    tokens = tokenise(body)
    tf = {}
    for token in tokens:
        tf[token] = tf.get(token, 0) + 1

    doc_type = fields.get("type", type_hint or "")
    title_tokens = tokenise(title) + tokenise(doc_type)
    title_tf = {}
    for token in title_tokens:
        title_tf[token] = title_tf.get(token, 0) + 1

    return {
        "path": rel_path,
        "title": title,
        "type": doc_type,
        "tags": fields.get("tags", []),
        "key": fields.get("key"),
        "parent": normalize_artefact_key(fields.get("parent")),
        "status": fields.get("status"),
        "modified": modified,
        "doc_length": len(tokens),
        "tf": tf,
        "title_tf": title_tf,
    }


# ---------------------------------------------------------------------------
# Corpus stats helpers
# ---------------------------------------------------------------------------

def _recompute_corpus_stats(index):
    """Recompute corpus_stats and meta from the documents list in-place."""
    documents = index["documents"]
    total_docs = len(documents)
    total_length = sum(d["doc_length"] for d in documents)
    avg_dl = total_length / total_docs if total_docs > 0 else 0.0

    df = {}
    for doc in documents:
        for term in doc["tf"]:
            df[term] = df.get(term, 0) + 1

    index["corpus_stats"]["total_docs"] = total_docs
    index["corpus_stats"]["avg_dl"] = round(avg_dl, 1)
    index["corpus_stats"]["df"] = df
    index["meta"]["document_count"] = total_docs
    index["meta"]["avg_doc_length"] = round(avg_dl, 1)
    # NOTE: This unconditionally advances built_at to now(). Callers doing
    # incremental updates should save and restore built_at if they don't want
    # to move the staleness threshold forward.
    index["meta"]["built_at"] = datetime.now(timezone.utc).astimezone().isoformat()


# ---------------------------------------------------------------------------
# Index building
# ---------------------------------------------------------------------------

def build_index(vault_root):
    """Build the BM25 retrieval index for the vault."""
    vault_str = str(vault_root)
    version = read_version(vault_root)

    # Discover type folders
    all_types = scan_living_types(vault_root) + scan_temporal_types(vault_root)

    # Collect all .md files with their type info
    documents = []
    for type_info in all_types:
        for rel_path in iter_artefact_paths(vault_root, type_info):
            doc = parse_doc(vault_root, rel_path, type_hint=type_info["type"])
            if doc is not None:
                documents.append(doc)

    index = {
        "meta": {
            "brain_core_version": version,
            "index_version": INDEX_VERSION,
            "built_at": "",
            "document_count": 0,
            "avg_doc_length": 0.0,
        },
        "bm25_params": {
            "k1": BM25_K1,
            "b": BM25_B,
        },
        "corpus_stats": {
            "total_docs": 0,
            "avg_dl": 0.0,
            "df": {},
        },
        "documents": documents,
    }
    _recompute_corpus_stats(index)

    return index


def persist_retrieval_index(vault_root, index):
    """Persist the BM25 retrieval index JSON to disk."""
    output_path = os.path.join(str(vault_root), OUTPUT_PATH)
    safe_write_json(output_path, index, bounds=str(vault_root))


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
        enable_embeddings = embeddings_enabled(vault_root, config=config)
    if not enable_embeddings or router is None:
        clear_embeddings_outputs(vault_root)
        return None

    meta = build_embeddings(vault_root, router, documents)
    if meta is None:
        clear_embeddings_outputs(vault_root)
        return None
    return meta


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
        enable_embeddings = embeddings_enabled(vault_root, config=config)
    if not enable_embeddings:
        clear_embeddings_outputs(vault_root)
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


# ---------------------------------------------------------------------------
# Incremental index updates
# ---------------------------------------------------------------------------

def index_update(index, vault_root, rel_path, type_hint=None, recompute=True):
    """Upsert a document in the index. Mutates index in-place.

    Re-parses the file at rel_path, replaces the existing entry if found,
    or appends it. Recomputes corpus stats unless recompute=False (useful
    when batching multiple updates — call _recompute_corpus_stats once after).

    Returns the parsed doc dict, or None if the file cannot be read.
    """
    doc = parse_doc(vault_root, rel_path, type_hint=type_hint)
    if doc is None:
        return None

    # Find and replace existing entry
    for i, existing in enumerate(index["documents"]):
        if existing["path"] == rel_path:
            index["documents"][i] = doc
            if recompute:
                _recompute_corpus_stats(index)
            return doc

    # Not found — append
    index["documents"].append(doc)
    if recompute:
        _recompute_corpus_stats(index)
    return doc


# Keep backward-compatible alias
index_add = index_update


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    vault_root = find_vault_root()
    index = build_index(vault_root)

    json_output = json.dumps(index, indent=2, ensure_ascii=False)

    if "--json" in sys.argv:
        print(json_output)
    else:
        embeddings_meta = persist_retrieval_outputs(vault_root, index)

        doc_count = index["meta"]["document_count"]
        term_count = len(index["corpus_stats"]["df"])
        embeddings_note = ", embeddings refreshed" if embeddings_meta is not None else ""
        print(
            f"Built retrieval index: {doc_count} documents, "
            f"{term_count} unique terms{embeddings_note} → {OUTPUT_PATH}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
