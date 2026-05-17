"""Lexical retrieval index construction and persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any

from _common import (
    iter_artefact_paths,
    normalize_artefact_key,
    read_artefact,
    read_version,
    safe_write_json,
    scan_living_types,
    scan_temporal_types,
)

from _lifecycle.document_parts import EmbeddingParts, embedding_parts_from_body
from _lifecycle.retrieval_errors import (
    RetrievalPersistenceError,
    UnreadableRetrievalSourceError,
)
from .lexical import tokenise
from .paths import INDEX_VERSION, OUTPUT_PATH
BM25_K1 = 1.5
BM25_B = 0.75


@dataclass(frozen=True)
class ParsedDocument:
    """One parsed retrieval document plus the embedding parts derived from its body."""

    doc: dict[str, Any]
    embedding_parts: EmbeddingParts


@dataclass(frozen=True)
class IndexBuildResult:
    """A built lexical index plus the cached embedding parts for its documents."""

    index: dict[str, Any]
    embedding_parts_by_path: dict[str, EmbeddingParts]


def extract_title(filename):
    """Extract title from filename stem.

    In Obsidian, the filename is the canonical title — it's what users
    link to, search for, and see in the file explorer. The H1 heading
    is display text and may omit structural info (e.g. type prefixes).
    """
    return Path(filename).stem


def parse_doc(
    vault_root,
    rel_path,
    type_hint=None,
    *,
    missing_ok: bool = False,
) -> ParsedDocument | None:
    """Parse a single .md file into an index document and embedding text parts.

    Incremental updates may treat a vanished path as a deletion sentinel, but a
    full rebuild must fail loudly when a discovered retrieval source cannot be read.
    """
    vault_str = str(vault_root)
    abs_path = os.path.join(vault_str, rel_path)
    try:
        fields, body = read_artefact(abs_path)
    except FileNotFoundError as exc:
        if missing_ok:
            return None
        raise UnreadableRetrievalSourceError(
            rel_path,
            "building lexical retrieval state",
            exc,
        ) from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise UnreadableRetrievalSourceError(
            rel_path,
            "building lexical retrieval state",
            exc,
        ) from exc

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

    return ParsedDocument(
        doc={
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
        },
        embedding_parts=embedding_parts_from_body(body),
    )


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


def _build_index_result(vault_root) -> IndexBuildResult:
    """Build the lexical index plus cached embedding parts for its documents."""
    version = read_version(vault_root)
    all_types = scan_living_types(vault_root) + scan_temporal_types(vault_root)

    documents = []
    embedding_parts_by_path: dict[str, EmbeddingParts] = {}
    for type_info in all_types:
        for rel_path in iter_artefact_paths(vault_root, type_info):
            parsed = parse_doc(
                vault_root,
                rel_path,
                type_hint=type_info["type"],
            )
            documents.append(parsed.doc)
            embedding_parts_by_path[rel_path] = parsed.embedding_parts

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
    return IndexBuildResult(index=index, embedding_parts_by_path=embedding_parts_by_path)


def build_index(vault_root) -> IndexBuildResult:
    """Build the lexical index plus cached embedding parts for its documents."""
    return _build_index_result(vault_root)


def persist_retrieval_index(vault_root, index) -> None:
    """Persist the lexical retrieval index JSON to disk."""
    output_path = os.path.join(str(vault_root), OUTPUT_PATH)
    try:
        safe_write_json(output_path, index, bounds=str(vault_root))
    except (OSError, ValueError) as exc:
        raise RetrievalPersistenceError(
            OUTPUT_PATH,
            "persisting lexical retrieval state",
            exc,
        ) from exc


def index_update(index, vault_root, rel_path, type_hint=None) -> ParsedDocument | None:
    """Upsert a document and return its parsed document plus embedding parts."""
    parsed = parse_doc(
        vault_root,
        rel_path,
        type_hint=type_hint,
        missing_ok=True,
    )
    if parsed is None:
        return None
    doc = parsed.doc

    for i, existing in enumerate(index["documents"]):
        if existing["path"] == rel_path:
            _apply_doc_to_corpus_stats(index, existing, -1)
            index["documents"][i] = doc
            _apply_doc_to_corpus_stats(index, doc, +1)
            return parsed

    index["documents"].append(doc)
    _apply_doc_to_corpus_stats(index, doc, +1)
    return parsed
