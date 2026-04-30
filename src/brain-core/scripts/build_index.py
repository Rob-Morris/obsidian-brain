#!/usr/bin/env python3
"""
build_index.py — Brain-core BM25 retrieval index builder

Walks all .md files in living + temporal type folders, extracts frontmatter
and body text, computes BM25 corpus stats and per-doc term frequencies,
and writes .brain/local/retrieval-index.json.

Usage:
    python3 build_index.py           # write .brain/local/retrieval-index.json
    python3 build_index.py --json    # output JSON to stdout
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from _common import (
    find_vault_root,
    iter_artefact_paths,
    normalize_artefact_key,
    read_artefact,
    read_version,
    safe_write_json,
    scan_living_types,
    scan_temporal_types,
    tokenise,
)


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
        persist_retrieval_index(vault_root, index)

        doc_count = index["meta"]["document_count"]
        term_count = len(index["corpus_stats"]["df"])
        print(
            f"Built retrieval index: {doc_count} documents, "
            f"{term_count} unique terms → {OUTPUT_PATH}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
