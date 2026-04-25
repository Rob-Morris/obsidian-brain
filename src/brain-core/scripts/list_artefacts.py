"""list_artefacts — enumerate vault artefacts and other resources.

Unlike search_index (BM25, relevance-ranked, capped), this module filters the
in-memory index directly and returns all matching documents up to top_k. The
index already contains type, tags, modified, path, title, and status for every
vault artefact — no filesystem walk needed.

For non-artefact resources (skills, styles, triggers, etc.), listing reads from
the compiled router's small collections with optional text filtering.
"""

import os

from _common import (
    match_artefact,
    normalize_artefact_key,
    parse_frontmatter,
    resolve_artefact_key_entry,
    temporal_display_name,
)


def list_artefacts(index, router, type_filter=None, since=None, until=None,
                   tag=None, parent=None, top_k=500, sort="date_desc"):
    """Return all vault artefacts matching the given filters, sorted and capped.

    Args:
        index:       the in-memory BM25 index dict (index["documents"])
        router:      compiled router dict (router["artefacts"])
        type_filter: optional type key or full type string (e.g. "research" or
                     "temporal/research"); resolved via match_artefact
        since:       optional ISO date string (e.g. "2026-03-20"); inclusive lower bound
        until:       optional ISO date string (e.g. "2026-04-04"); inclusive upper bound
        tag:         optional tag string; only docs containing this tag are returned
        parent:      optional canonical parent artefact key; only owned children are returned
        top_k:       maximum results to return (default 500)
        sort:        "date_desc" (default), "date_asc", or "title"

    Returns:
        list of dicts: [{path, title, type, date, status}, ...]
    """
    resolved_type = None
    if type_filter and router:
        art = match_artefact(router.get("artefacts", []), type_filter)
        if art:
            resolved_type = art["frontmatter_type"]
        else:
            # Unknown type — return empty list (not an error)
            return []

    resolved_parent = None
    if parent:
        resolved_parent = normalize_artefact_key(parent)
        if not resolved_parent:
            raise ValueError(
                "parent filter must use canonical artefact key form {type-prefix}/{key}"
            )
        if router and not resolve_artefact_key_entry(router, resolved_parent):
            raise ValueError(f"No artefact matching parent '{parent}'")

    docs = index.get("documents", [])
    results = []
    artefact_index = router.get("artefact_index") or {}
    by_path = {entry["path"]: entry for entry in artefact_index.values()}

    for doc in docs:
        if resolved_type is not None and doc.get("type") != resolved_type:
            continue
        if resolved_parent and doc.get("parent") != resolved_parent:
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
        display = temporal_display_name(stem)
        title = display if display is not None else stem
        artefact_meta = by_path.get(doc.get("path", ""))

        result = {
            "path": doc.get("path", ""),
            "title": title,
            "type": doc.get("type", ""),
            "date": doc_date,
            "status": doc.get("status", ""),
        }
        key = doc.get("key") or (artefact_meta or {}).get("key")
        if key:
            result["key"] = key
        parent_key = doc.get("parent") or (artefact_meta or {}).get("parent")
        if parent_key:
            result["parent"] = parent_key
        if artefact_meta is not None:
            result["children_count"] = artefact_meta.get("children_count", 0)
        results.append(result)

    if sort == "date_asc":
        results.sort(key=lambda r: r["date"])
    elif sort == "title":
        results.sort(key=lambda r: r["title"].lower())
    else:  # date_desc (default)
        results.sort(key=lambda r: r["date"], reverse=True)

    return results[:top_k]


# ---------------------------------------------------------------------------
# Non-artefact resource listing
# ---------------------------------------------------------------------------

# Router keys for small collections that list via _list_collection().
# Resources with custom listing logic (artefact, type, template, archive,
# workspace) are handled by explicit branches in list_resources().
_COLLECTION_MAP = {
    "skill": ("skills", "name"),
    "trigger": ("triggers", "name"),
    "style": ("styles", "name"),
    "plugin": ("plugins", "name"),
    "memory": ("memories", "name"),
}


def _list_templates(router, query=None):
    """List available templates derived from artefact type definitions."""
    results = []
    for art in router.get("artefacts", []):
        tpl = art.get("template_file")
        if not tpl:
            continue
        name = art.get("key", "")
        if query and query.lower() not in name.lower():
            continue
        results.append({
            "name": name,
            "type": art.get("frontmatter_type", art.get("type", "")),
            "template_file": tpl,
        })
    return results


def _list_collection(router, router_key, name_field, query=None):
    """List items from a router collection with optional text filter."""
    items = router.get(router_key, [])
    if not query:
        return list(items)  # copy — caller may mutate (e.g. sort)
    lower_q = query.lower()
    return [i for i in items if lower_q in i.get(name_field, "").lower()]


def _list_archive(router, vault_root):
    """List all archived files. Extracted from read.py for shared use."""
    vault_root = str(vault_root)
    results = []
    seen = set()

    def _scan_dir(base_dir):
        if not os.path.isdir(base_dir):
            return
        for dirpath, dirnames, filenames in os.walk(base_dir):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fname in filenames:
                if not fname.endswith(".md"):
                    continue
                abs_path = os.path.join(dirpath, fname)
                rel_path = os.path.relpath(abs_path, vault_root)
                if rel_path in seen:
                    continue
                seen.add(rel_path)
                try:
                    with open(abs_path, "r", encoding="utf-8") as f:
                        fields, _ = parse_frontmatter(f.read())
                except Exception:
                    fields = {}
                results.append({
                    "path": rel_path,
                    "title": os.path.splitext(fname)[0],
                    "type": fields.get("type", ""),
                    "status": fields.get("status", ""),
                    "archiveddate": fields.get("archiveddate", ""),
                })

    _scan_dir(os.path.join(vault_root, "_Archive"))

    for art in router.get("artefacts", []):
        art_dir = os.path.join(vault_root, art["path"])
        if not os.path.isdir(art_dir):
            continue
        for entry in os.listdir(art_dir):
            if entry == "_Archive":
                _scan_dir(os.path.join(art_dir, "_Archive"))
            sub = os.path.join(art_dir, entry)
            if os.path.isdir(sub) and not entry.startswith((".", "_", "+")):
                archive_sub = os.path.join(sub, "_Archive")
                if os.path.isdir(archive_sub):
                    _scan_dir(archive_sub)

    results.sort(key=lambda r: r.get("archiveddate", ""), reverse=True)
    return results


def list_resources(index, router, vault_root, resource="artefact", query=None,
                   **kwargs):
    """List resources of a given kind.

    For artefacts: delegates to list_artefacts() with full filtering (type,
    since, until, tag, top_k, sort).
    For other resources: reads from router, optional query text filter.

    Args:
        index:     in-memory BM25 index dict (required for artefact listing)
        router:    compiled router dict
        vault_root: absolute path to the vault root
        resource:  which collection to list (default "artefact")
        query:     optional text filter (substring match on name, for non-artefact resources)
        **kwargs:  passed through to list_artefacts for artefact-specific filters

    Returns:
        list of dicts (format varies by resource kind).

    Raises:
        ValueError: if resource is not listable.
    """
    if resource == "artefact":
        return list_artefacts(index, router, **kwargs)

    if resource == "type":
        arts = router.get("artefacts", [])
        if query:
            lower_q = query.lower()
            arts = [a for a in arts if lower_q in a.get("key", "").lower()
                    or lower_q in a.get("frontmatter_type", "").lower()]
        return arts

    if resource == "template":
        return _list_templates(router, query)

    if resource == "archive":
        return _list_archive(router, vault_root)

    if resource in _COLLECTION_MAP:
        router_key, name_field = _COLLECTION_MAP[resource]
        return _list_collection(router, router_key, name_field, query)

    _listable = sorted({"artefact", "archive", "template", "type", "workspace"}
                        | set(_COLLECTION_MAP))
    raise ValueError(
        f"Resource '{resource}' is not listable. "
        f"Listable resources: {', '.join(_listable)}"
    )
