"""Shared body/heading extraction for retrieval embedding text."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from _common._markdown import collect_headings


EMBEDDING_BODY_CHARS = 500
EMBEDDING_HEADING_LIMIT = 3


@dataclass(frozen=True)
class EmbeddingParts:
    """The body slice and headings used to build one document embedding."""

    body_head: str
    headings: tuple[str, ...]


def extract_heading_titles(body: str, *, limit: int = EMBEDDING_HEADING_LIMIT) -> tuple[str, ...]:
    """Extract a few markdown heading titles for embedding context."""
    titles: list[str] = []
    for _start, _level, text, _raw in collect_headings(body):
        if not text:
            continue
        titles.append(text)
        if len(titles) >= limit:
            break
    return tuple(titles)


def embedding_parts_from_body(body: str) -> EmbeddingParts:
    """Return the body slice and heading titles used for document embeddings."""
    return EmbeddingParts(
        body_head=body[:EMBEDDING_BODY_CHARS],
        headings=extract_heading_titles(body, limit=EMBEDDING_HEADING_LIMIT),
    )


def embedding_source_hash(title: str, doc_type: str, parts: EmbeddingParts) -> str:
    """Fingerprint the shared source inputs used by lexical and semantic builders."""
    return hashlib.sha256(json.dumps(
        [title, doc_type, parts.headings, parts.body_head]
    ).encode("utf-8")).hexdigest()


def effective_document_type(fields, type_hint=None):
    """Use explicit source type, otherwise the current discovered folder type."""
    return fields.get("type", type_hint or "")


def current_document_type_hints(vault_root):
    """Observe folder types once for one semantic corpus/metadata invocation."""
    from _common import scan_living_types, scan_temporal_types
    types = scan_living_types(vault_root) + scan_temporal_types(vault_root)
    return sorted(((Path(item["path"]).parts, item["type"]) for item in types),
                  key=lambda item: len(item[0]), reverse=True)


def document_type_hint(rel_path, hints):
    """Match a discovered folder using path components rather than text prefixes."""
    parts = Path(rel_path).parts
    return next((value for prefix, value in hints if parts[:len(prefix)] == prefix), None)


def read_embedding_document(vault_root, rel_path, *, type_hint=None):
    """Read admitted source inputs without trusting supplied retrieval metadata."""
    from _common import read_artefact
    from _common._source_access import resolve_public_source
    from _common._workspace import workspace_discovery_metadata
    from .retrieval_errors import UnreadableRetrievalSourceError

    try:
        source = resolve_public_source(vault_root, rel_path)
        fields, body = read_artefact(source)
    except UnicodeDecodeError:
        return None
    except OSError as exc:
        raise UnreadableRetrievalSourceError(
            rel_path, "building semantic embeddings", exc,
        ) from exc
    parts = embedding_parts_from_body(body)
    title = Path(rel_path).stem
    doc_type = effective_document_type(fields, type_hint)
    doc = {
        "path": rel_path, "title": title, "type": doc_type,
        "tags": fields.get("tags", []), "status": fields.get("status"),
        "workspace": fields.get("workspace"), "key": fields.get("key"),
        **workspace_discovery_metadata({**fields, "type": doc_type}),
        "embedding_source_hash": embedding_source_hash(title, doc_type, parts),
    }
    return doc, parts
