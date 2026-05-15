"""Non-artefact text search across router-backed resources."""

from __future__ import annotations

import os

from _common import FM_RE

from .lexical import tokenise
from .mode import DEFAULT_TOP_K
from .snippet import extract_snippet


SEARCH_RESOURCE_MAP = {
    "skill": ("skills", "skill_doc", ()),
    "style": ("styles", "style_doc", ()),
    "memory": ("memories", "memory_doc", ("triggers",)),
    "trigger": ("triggers", None, ("condition", "target", "detail")),
    "plugin": ("plugins", "skill_doc", ()),
}

SEARCHABLE_RESOURCES = {"artefact"} | set(SEARCH_RESOURCE_MAP)


def _read_file_body(vault_root, rel_path):
    """Read a file and return the body with frontmatter stripped."""
    abs_path = os.path.join(str(vault_root), rel_path)
    try:
        with open(abs_path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError):
        return ""
    fm_match = FM_RE.match(text)
    return text[fm_match.end():] if fm_match else text


def search_resource(router, vault_root, resource, query, top_k=DEFAULT_TOP_K):
    """Search a non-artefact resource by text matching on name plus file content."""
    if resource == "artefact":
        raise ValueError("Use lexical_query.search() for artefact search, not search_resource().")
    if resource not in SEARCH_RESOURCE_MAP:
        searchable = sorted(SEARCHABLE_RESOURCES)
        raise ValueError(
            f"Resource '{resource}' is not searchable. "
            f"Searchable resources: {', '.join(searchable)}"
        )

    router_key, doc_field, extra_fields = SEARCH_RESOURCE_MAP[resource]
    items = router.get(router_key, [])
    query_lower = query.lower()
    query_tokens = tokenise(query)

    results = []
    for item in items:
        name = item.get("name", "")
        parts = [name]
        for field in extra_fields:
            value = item.get(field)
            if isinstance(value, list):
                parts.extend(value)
            elif value:
                parts.append(value)
        searchable = " ".join(parts)

        file_body = ""
        if doc_field and item.get(doc_field):
            file_body = _read_file_body(vault_root, item[doc_field])
            searchable += " " + file_body

        score = searchable.lower().count(query_lower)
        if score <= 0:
            continue

        snippet = extract_snippet(
            vault_root,
            item.get(doc_field) or "",
            query_tokens,
            body=file_body or searchable,
        )

        results.append(
            {
                "path": item.get(doc_field, "") if doc_field else "",
                "title": name,
                "type": resource,
                "status": None,
                "score": round(score, 4),
                "snippet": snippet,
            }
        )

    results.sort(key=lambda result: result["score"], reverse=True)
    return results[:top_k]
