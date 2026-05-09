"""BM25 tokenisation and exact-anchor query detection."""

import re

_TOKEN_RE = re.compile(r"[a-z0-9]+")

LEXICAL_ANCHOR_RE = re.compile(
    r"\b(?:v\d+(?:\.\d+){1,3}(?:[-+][a-z0-9._-]+)?|[A-Z]{2,}-\d{1,4}|[A-Z]{2,}\d{2,})\b"
)


def tokenise(text):
    """Lowercase, split on non-alphanumeric, strip tokens < 2 chars."""
    return [t for t in _TOKEN_RE.findall(text.lower()) if len(t) >= 2]
