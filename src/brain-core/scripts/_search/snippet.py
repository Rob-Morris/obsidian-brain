"""Snippet extraction for retrieval results."""

from __future__ import annotations

import os
import re

from _common import FM_RE


SNIPPET_LENGTH = 200


def extract_snippet(vault_root, rel_path, query_tokens, length=SNIPPET_LENGTH, *, body=None):
    """Extract a snippet centred on the first query-term match."""
    if body is None:
        abs_path = os.path.join(str(vault_root), rel_path)
        try:
            with open(abs_path, "r", encoding="utf-8") as handle:
                text = handle.read()
        except (OSError, UnicodeDecodeError):
            return ""

        fm_match = FM_RE.match(text)
        body = text[fm_match.end():] if fm_match else text

    body = re.sub(r"\s+", " ", body).strip()
    if not body:
        return ""

    body_lower = body.lower()
    best_pos = None
    for token in query_tokens:
        pos = body_lower.find(token)
        if pos >= 0 and (best_pos is None or pos < best_pos):
            best_pos = pos

    if best_pos is None:
        snippet = body[:length]
        trimmed_from_start = False
        trimmed_from_end = len(snippet) < len(body)
    else:
        half = length // 2
        start = max(0, best_pos - half)
        end = min(len(body), start + length)

        if start > 0:
            space = body.rfind(" ", 0, start)
            if space >= 0 and (start - space) < 30:
                start = space + 1
        if end < len(body):
            space = body.find(" ", end)
            if space >= 0 and (space - end) < 30:
                end = space

        trimmed_from_start = start > 0
        trimmed_from_end = end < len(body)
        snippet = body[start:end]

    if trimmed_from_start:
        snippet = "…" + snippet
    if trimmed_from_end:
        snippet = snippet + "…"
    return snippet


def attach_snippets(results, vault_root, query_tokens):
    """Attach snippets to result dicts in place."""
    for result in results:
        result["snippet"] = extract_snippet(vault_root, result["path"], query_tokens)
