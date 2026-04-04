"""list_artefacts — enumerate vault artefacts by type, date range, or tag.

Unlike search_index (BM25, relevance-ranked, capped), this module filters the
in-memory index directly and returns all matching documents up to top_k. The
index already contains type, tags, modified, path, title, and status for every
vault artefact — no filesystem walk needed.
"""

import _common


def list_artefacts(index, router, type_filter=None, since=None, until=None,
                   tag=None, top_k=500, sort="date_desc"):
    """Return all vault artefacts matching the given filters, sorted and capped.

    Args:
        index:       the in-memory BM25 index dict (index["documents"])
        router:      compiled router dict (router["artefacts"])
        type_filter: optional type key or full type string (e.g. "research" or
                     "temporal/research"); resolved via match_artefact
        since:       optional ISO date string (e.g. "2026-03-20"); inclusive lower bound
        until:       optional ISO date string (e.g. "2026-04-04"); inclusive upper bound
        tag:         optional tag string; only docs containing this tag are returned
        top_k:       maximum results to return (default 500)
        sort:        "date_desc" (default), "date_asc", or "title"

    Returns:
        list of dicts: [{path, title, type, date, status}, ...]
    """
    resolved_type = None
    if type_filter and router:
        art = _common.match_artefact(router.get("artefacts", []), type_filter)
        if art:
            resolved_type = art["type"]
        else:
            # Unknown type — return empty list (not an error)
            return []

    docs = index.get("documents", [])
    results = []

    for doc in docs:
        if resolved_type is not None and doc.get("type") != resolved_type:
            continue

        # ISO date prefix — lexicographic comparison is valid for YYYY-MM-DD
        doc_date = (doc.get("modified") or "")[:10]
        if since and doc_date < since:
            continue
        if until and doc_date > until:
            continue

        if tag and tag not in doc.get("tags", []):
            continue

        stem = doc.get("title", "")
        display = _common._temporal_display_name(stem)
        title = display if display is not None else stem

        results.append({
            "path": doc.get("path", ""),
            "title": title,
            "type": doc.get("type", ""),
            "date": doc_date,
            "status": doc.get("status", ""),
        })

    if sort == "date_asc":
        results.sort(key=lambda r: r["date"])
    elif sort == "title":
        results.sort(key=lambda r: r["title"].lower())
    else:  # date_desc (default)
        results.sort(key=lambda r: r["date"], reverse=True)

    return results[:top_k]
