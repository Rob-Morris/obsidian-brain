"""Shared body/heading extraction for semantic embedding text."""

from __future__ import annotations

from dataclasses import dataclass

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
