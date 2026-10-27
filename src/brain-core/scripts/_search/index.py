"""Lexical retrieval index construction and persistence."""

from __future__ import annotations

from _common._source_access import SourceAccessError, resolve_public_source

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any

from _common import (
    iter_artefact_paths,
    parse_scalar_index_date,
    normalize_artefact_key,
    read_artefact,
    read_version,
    safe_write_json,
    scan_living_types,
    scan_temporal_types,
)

from _lifecycle.document_parts import EmbeddingParts, embedding_parts_from_body, embedding_source_hash, effective_document_type
from _lifecycle.retrieval_errors import (
    RetrievalPersistenceError,
    UnreadableRetrievalSourceError,
)
from _common._workspace import workspace_discovery_metadata
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
    """One complete lexical index built from admitted sources."""

    index: dict[str, Any]


def _valid_frontmatter_date(value, fallback=None):
    """Keep only valid scalar date text in the persisted retrieval index."""
    if parse_scalar_index_date(value) is None:
        return fallback
    return value


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

    Incremental updates may treat a vanished path as a deletion sentinel.
    Undecodable text is skipped; other read failures retain their named error.
    """
    vault_str = str(vault_root)
    abs_path = os.path.join(vault_str, rel_path)
    try:
        abs_path = resolve_public_source(vault_root, rel_path)
        fields, body = read_artefact(abs_path)
    except FileNotFoundError as exc:
        if missing_ok:
            return None
        raise UnreadableRetrievalSourceError(
            rel_path,
            "building lexical retrieval state",
            exc,
        ) from exc
    except UnicodeDecodeError:
        return None
    except OSError as exc:
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

    doc_type = effective_document_type(fields, type_hint)
    title_tokens = tokenise(title) + tokenise(doc_type)
    title_tf = {}
    for token in title_tokens:
        title_tf[token] = title_tf.get(token, 0) + 1

    embedding_parts = embedding_parts_from_body(body)
    source_hash = embedding_source_hash(title, doc_type, embedding_parts)
    return ParsedDocument(
        doc={
            "path": rel_path,
            "title": title,
            "type": doc_type,
            "tags": fields.get("tags", []),
            "workspace": fields.get("workspace"),
            **workspace_discovery_metadata({**fields, "type": doc_type}),
            "key": fields.get("key"),
            "parent": normalize_artefact_key(fields.get("parent")),
            "status": fields.get("status"),
            "created": _valid_frontmatter_date(fields.get("created")),
            "modified": _valid_frontmatter_date(fields.get("modified"), modified),
            "embedding_source_hash": source_hash,
            "doc_length": len(tokens),
            "tf": tf,
            "title_tf": title_tf,
        },
        embedding_parts=embedding_parts,
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
    """Build a complete lexical index from admitted sources."""
    version = read_version(vault_root)
    all_types = scan_living_types(vault_root) + scan_temporal_types(vault_root)

    documents = []
    skipped_paths = []
    for type_info in all_types:
        for rel_path in iter_artefact_paths(vault_root, type_info):
            try:
                parsed = parse_doc(
                    vault_root,
                    rel_path,
                    type_hint=type_info["type"],
                )
            except SourceAccessError:
                skipped_paths.append(rel_path)
                continue
            if parsed is None:
                skipped_paths.append(rel_path)
                continue
            documents.append(parsed.doc)

    index = {
        "meta": {
            "brain_core_version": version,
            "index_version": INDEX_VERSION,
            "built_at": "",
            "document_count": 0,
            "skipped_paths": sorted(skipped_paths),
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
    return IndexBuildResult(index=index)


def build_index(vault_root) -> IndexBuildResult:
    """Build a complete lexical index from admitted sources."""
    return _build_index_result(vault_root)


def persist_retrieval_index(vault_root, index) -> None:
    """Persist the lexical retrieval index JSON to disk."""
    from _bootstrap.owned_paths import owned_output_paths
    output_path, = owned_output_paths(vault_root, (OUTPUT_PATH,))
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
    if index.get("meta", {}).get("index_version") != INDEX_VERSION:
        raise ValueError("retrieval index schema is outdated; run retrieval.refresh-lexical")
    parsed = parse_doc(
        vault_root,
        rel_path,
        type_hint=type_hint,
        missing_ok=True,
    )
    if parsed is None:
        return None
    skipped = set(index["meta"].get("skipped_paths", ()))
    skipped.discard(rel_path)
    index["meta"]["skipped_paths"] = sorted(skipped)
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
