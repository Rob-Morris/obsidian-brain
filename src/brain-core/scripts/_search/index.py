"""Lexical retrieval index construction and persistence."""

from __future__ import annotations

from datetime import datetime, timezone
import os
import re
from pathlib import Path

from _common import (
    iter_artefact_paths,
    normalize_artefact_key,
    read_artefact,
    read_version,
    safe_write_json,
    scan_living_types,
    scan_temporal_types,
)
from _common._markdown import collect_headings

from .lexical import tokenise


OUTPUT_PATH = os.path.join(".brain", "local", "retrieval-index.json")
INDEX_VERSION = "1.0.0"
BM25_K1 = 1.5
BM25_B = 0.75
EMBEDDING_BODY_CHARS = 500
EMBEDDING_HEADING_LIMIT = 3

_TYPE_DESCRIPTION_CACHE_KEY = "_type_description_cache"
_section_cache: dict[str, re.Pattern] = {}


def extract_title(filename):
    """Extract title from filename stem.

    In Obsidian, the filename is the canonical title — it's what users
    link to, search for, and see in the file explorer. The H1 heading
    is display text and may omit structural info (e.g. type prefixes).
    """
    return Path(filename).stem


def extract_type_description(vault_root, artefact):
    """Read taxonomy file and extract one-liner + Purpose + When To Use/Trigger."""
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


def parse_doc(vault_root, rel_path, type_hint=None):
    """Parse a single .md file into a document dict for the index."""
    vault_str = str(vault_root)
    abs_path = os.path.join(vault_str, rel_path)
    try:
        fields, body = read_artefact(abs_path)
    except (OSError, UnicodeDecodeError):
        return None

    title = extract_title(rel_path)

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
    """Return a shallow-copy index with underscore-prefixed doc fields stripped."""
    if not isinstance(index, dict) or "documents" not in index:
        return index
    stripped = dict(index)
    stripped["documents"] = [
        {k: v for k, v in doc.items() if not k.startswith("_")}
        for doc in index["documents"]
    ]
    return stripped


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
    index["meta"]["built_at"] = datetime.now(timezone.utc).astimezone().isoformat()


def _apply_doc_to_corpus_stats(index, doc, delta):
    """Apply a single doc add/remove to corpus stats."""
    assert delta in (1, -1), f"delta must be +1 or -1, got {delta}"

    stats = index["corpus_stats"]
    df = stats["df"]
    for term in doc["tf"]:
        new_count = df.get(term, 0) + delta
        if new_count < 0:
            raise ValueError(f"corpus df underflow for term {term!r}")
        if new_count == 0:
            df.pop(term, None)
        else:
            df[term] = new_count

    total_docs = stats["total_docs"] + delta
    # Re-sum the surviving documents so incremental updates cannot drift
    # away from the persisted corpus length after add/remove sequences.
    total_length = sum(d["doc_length"] for d in index["documents"])
    avg_dl = total_length / total_docs if total_docs > 0 else 0.0

    stats["total_docs"] = total_docs
    stats["avg_dl"] = round(avg_dl, 1)
    index["meta"]["document_count"] = total_docs
    index["meta"]["avg_doc_length"] = round(avg_dl, 1)


def build_index(vault_root):
    """Build the BM25 retrieval index for the vault."""
    version = read_version(vault_root)
    all_types = scan_living_types(vault_root) + scan_temporal_types(vault_root)

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
    """Persist the lexical retrieval index JSON to disk."""
    output_path = os.path.join(str(vault_root), OUTPUT_PATH)
    safe_write_json(output_path, _strip_build_local_fields(index), bounds=str(vault_root))


def index_update(index, vault_root, rel_path, type_hint=None):
    """Upsert a document in the index. Mutates index in-place."""
    doc = parse_doc(vault_root, rel_path, type_hint=type_hint)
    if doc is None:
        return None

    for i, existing in enumerate(index["documents"]):
        if existing["path"] == rel_path:
            _apply_doc_to_corpus_stats(index, existing, -1)
            index["documents"][i] = doc
            _apply_doc_to_corpus_stats(index, doc, +1)
            return doc

    index["documents"].append(doc)
    _apply_doc_to_corpus_stats(index, doc, +1)
    return doc
