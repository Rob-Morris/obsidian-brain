#!/usr/bin/env python3
"""
build_index.py — Brain-core BM25 retrieval index builder

Walks all .md files in living + temporal type folders, extracts frontmatter
and body text, computes BM25 corpus stats and per-doc term frequencies,
and writes .brain/local/retrieval-index.json. When semantic retrieval or
semantic processing is enabled and a compiled router is available, it also
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

import _semantic.runtime as _semantic
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
from _common._markdown import collect_headings

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
INDEX_VERSION = "1.0.0"
BM25_K1 = 1.5
BM25_B = 0.75


# ---------------------------------------------------------------------------
# Type description extraction (for classification + embeddings)
# ---------------------------------------------------------------------------

_TYPE_DESCRIPTION_CACHE_KEY = "_type_description_cache"


def extract_type_description(vault_root, artefact):
    """Read taxonomy file and extract one-liner + Purpose + When To Use/Trigger.

    Returns a combined description string suitable for embedding or BM25
    classification. Returns empty string if taxonomy file missing.

    Result is memoised on the artefact dict; a fresh router compile produces
    fresh artefact dicts, so invalidation happens automatically.
    """
    cached = artefact.get(_TYPE_DESCRIPTION_CACHE_KEY)
    if cached is not None:
        return cached

    taxonomy_file = artefact.get("taxonomy_file")
    if not taxonomy_file:
        artefact[_TYPE_DESCRIPTION_CACHE_KEY] = ""
        return ""

    abs_path = os.path.join(str(vault_root), taxonomy_file)
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        artefact[_TYPE_DESCRIPTION_CACHE_KEY] = ""
        return ""

    parts = []

    h1_match = re.search(r"^# .+\n\n(.+)", content, re.MULTILINE)
    if h1_match:
        parts.append(h1_match.group(1).strip())

    for section_name in ("Purpose", "When To Use", "Trigger"):
        body = _extract_section(content, section_name)
        if body:
            parts.append(body)

    description = "\n\n".join(parts)
    artefact[_TYPE_DESCRIPTION_CACHE_KEY] = description
    return description


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


def _extract_heading_titles(body, limit=3):
    """Extract a few markdown heading titles for embedding context."""
    titles = []
    for _start, _level, text, _raw in collect_headings(body):
        if not text:
            continue
        titles.append(text)
        if len(titles) >= limit:
            break
    return titles


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

    Returns (type_embeddings, doc_embeddings, meta) tuple on success, or None
    if embedding deps unavailable. Arrays are returned alongside meta so that
    callers (notably the MCP server's _ensure_embeddings_fresh) can use them
    directly in memory without re-reading the persisted .npy files.
    """
    if not _HAS_EMBEDDINGS:
        return None

    vault_str = str(vault_root)
    artefacts = [a for a in router.get("artefacts", []) if a.get("configured")]

    # Extract type descriptions
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
        type_meta.append({
            "index": i,
            "key": artefact["key"],
            "type": artefact["type"],
            "description": desc[:200],
        })

    doc_texts = []
    doc_meta = []
    for i, doc in enumerate(documents):
        # parse_doc retains body slice/headings on the doc dict; only re-read
        # when documents came from a source that didn't (e.g. disk-loaded index
        # whose build-local fields were stripped before persistence).
        body_head = doc.get("_body_head")
        headings = doc.get("_headings")
        if body_head is None or headings is None:
            abs_path = os.path.join(vault_str, doc["path"])
            try:
                _, body = read_artefact(abs_path)
            except (OSError, UnicodeDecodeError):
                body = ""
            body_head = body[:EMBEDDING_BODY_CHARS]
            headings = _extract_heading_titles(body, limit=EMBEDDING_HEADING_LIMIT)
        type_desc = type_desc_by_frontmatter.get(doc["type"], doc["type"])
        parts = [
            doc["title"],
            doc["type"],
            type_desc,
        ]
        if headings:
            parts.append(" ".join(headings))
        if body_head:
            parts.append(body_head)
        doc_texts.append("\n".join(part for part in parts if part))
        doc_meta.append({
            "index": i,
            "path": doc["path"],
            "type": doc["type"],
            "title": doc["title"],
            "tags": doc.get("tags", []),
            "status": doc.get("status"),
        })

    # Encode
    model = SentenceTransformer(_semantic.EMBEDDING_MODEL)

    type_embeddings = model.encode(type_texts, normalize_embeddings=True) if type_texts else np.zeros((0, _semantic.EMBEDDING_DIM))
    doc_embeddings = model.encode(doc_texts, normalize_embeddings=True) if doc_texts else np.zeros((0, _semantic.EMBEDDING_DIM))

    # .brain/local/ may not exist yet (directory changed from _Config/ in v0.16.0)
    os.makedirs(os.path.join(vault_str, os.path.dirname(_semantic.TYPE_EMBEDDINGS_REL)), exist_ok=True)
    _safe_save_npy(os.path.join(vault_str, _semantic.TYPE_EMBEDDINGS_REL), type_embeddings, bounds=vault_str)
    _safe_save_npy(os.path.join(vault_str, _semantic.DOC_EMBEDDINGS_REL), doc_embeddings, bounds=vault_str)

    meta = {
        "model": _semantic.EMBEDDING_MODEL,
        "dim": _semantic.EMBEDDING_DIM,
        "built_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "types": type_meta,
        "documents": doc_meta,
    }
    meta_path = os.path.join(vault_str, _semantic.EMBEDDINGS_META_REL)
    safe_write_json(meta_path, meta, bounds=vault_str)

    return (type_embeddings, doc_embeddings, meta)


# ---------------------------------------------------------------------------
# Per-file document parsing
# ---------------------------------------------------------------------------

EMBEDDING_BODY_CHARS = 500
EMBEDDING_HEADING_LIMIT = 3


def parse_doc(vault_root, rel_path, type_hint=None):
    """Parse a single .md file into a document dict for the index.

    Args:
        vault_root: Absolute path to the vault root.
        rel_path: Path relative to vault root.
        type_hint: Fallback type string if frontmatter has no type field.

    Returns:
        Document dict with path, title, type, tags, status, modified,
        doc_length, tf, title_tf. Returns None if the file cannot be read.

        Also includes build-local fields `_body_head` (first
        EMBEDDING_BODY_CHARS chars of body) and `_headings`
        (up to EMBEDDING_HEADING_LIMIT heading titles) so that build_embeddings
        does not need to re-read the file. These fields are stripped before
        persistence; see _strip_build_local_fields.
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
        "_body_head": body[:EMBEDDING_BODY_CHARS],
        "_headings": _extract_heading_titles(body, limit=EMBEDDING_HEADING_LIMIT),
    }


def _strip_build_local_fields(index):
    """Return a shallow-copy index with build-local fields (underscore-prefixed
    keys, e.g. `_body_head`, `_headings`) stripped from documents. Body slices
    and heading lists live in memory only — they must not land in
    retrieval-index.json.
    """
    if not isinstance(index, dict) or "documents" not in index:
        return index
    stripped = dict(index)
    stripped["documents"] = [
        {k: v for k, v in doc.items() if not k.startswith("_")}
        for doc in index["documents"]
    ]
    return stripped


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
    """Persist the BM25 retrieval index JSON to disk.

    Build-local fields on documents (e.g. retained body slices, heading
    lists) are stripped first — they live in memory only.
    """
    output_path = os.path.join(str(vault_root), OUTPUT_PATH)
    safe_write_json(output_path, _strip_build_local_fields(index), bounds=str(vault_root))


def refresh_embeddings_outputs(
    vault_root,
    router,
    documents,
    *,
    enable_embeddings=None,
    config=None,
):
    """Refresh embeddings sidecars, clearing stale files when unavailable.

    Returns (type_embeddings, doc_embeddings, meta) tuple on success, or None
    when embeddings are disabled / unavailable / failed to build.
    """
    if enable_embeddings is None:
        enable_embeddings = (
            _semantic.embeddings_enabled(vault_root, config=config)
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
    """Persist index JSON and refresh embeddings when enabled and available.

    Returns (type_embeddings, doc_embeddings, meta) tuple on success, or None
    when embeddings are disabled / unavailable.
    """
    persist_retrieval_index(vault_root, index)
    if enable_embeddings is None:
        enable_embeddings = (
            _semantic.embeddings_enabled(vault_root, config=config)
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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    vault_root = find_vault_root()
    try:
        cfg = _semantic.load_config_best_effort(vault_root)
    except _semantic.SemanticConfigLoadError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    index = build_index(vault_root)

    json_output = json.dumps(index, indent=2, ensure_ascii=False)

    if "--json" in sys.argv:
        print(json_output)
    else:
        embeddings_result = persist_retrieval_outputs(vault_root, index, config=cfg)

        doc_count = index["meta"]["document_count"]
        term_count = len(index["corpus_stats"]["df"])
        embeddings_note = ", embeddings refreshed" if embeddings_result is not None else ""
        print(
            f"Built retrieval index: {doc_count} documents, "
            f"{term_count} unique terms{embeddings_note} → {OUTPUT_PATH}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
