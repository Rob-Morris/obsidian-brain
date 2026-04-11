"""BM25 tokenisation."""

import re

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenise(text):
    """Lowercase, split on non-alphanumeric, strip tokens < 2 chars."""
    return [t for t in _TOKEN_RE.findall(text.lower()) if len(t) >= 2]
