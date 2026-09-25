"""Portable artefact-list filtering and pagination semantics."""

from __future__ import annotations

from datetime import date

from _common import (
    load_compiled_router,
    match_artefact,
    normalize_artefact_key,
    parse_scalar_index_date,
    resolve_artefact_key_entry,
    temporal_display_name,
)
from _search import lexical_query


def _index_date(value):
    parsed = parse_scalar_index_date(value)
    if parsed is None:
        return ""
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return parsed.strftime("%Y-%m-%d")


def _validate_iso_date(value, field):
    if value is None:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an ISO date in YYYY-MM-DD form") from exc


def _cursor_offset(cursor):
    if cursor in (None, ""):
        return 0
    try:
        offset = int(cursor)
    except (TypeError, ValueError) as exc:
        raise ValueError("cursor must be the next_cursor value from a prior list") from exc
    if offset < 0:
        raise ValueError("cursor must not be negative")
    return offset


def _collect_artefacts(
    index,
    router,
    type_filter=None,
    since=None,
    until=None,
    modified_since=None,
    modified_until=None,
    tag=None,
    parent=None,
    sort="date_desc",
):
    since = _validate_iso_date(since, "since")
    until = _validate_iso_date(until, "until")
    modified_since = _validate_iso_date(modified_since, "modified_since")
    modified_until = _validate_iso_date(modified_until, "modified_until")
    if since and until and since > until:
        raise ValueError("since must be on or before until")
    if modified_since and modified_until and modified_since > modified_until:
        raise ValueError("modified_since must be on or before modified_until")

    resolved_type = None
    if type_filter and router:
        artefact_type = match_artefact(router.get("artefacts", []), type_filter)
        if artefact_type:
            resolved_type = artefact_type["frontmatter_type"]
        else:
            valid = sorted(
                {
                    value
                    for entry in router.get("artefacts", [])
                    for value in (entry.get("key"), entry.get("frontmatter_type"))
                    if value
                }
            )
            raise ValueError(
                f"No artefact type matching '{type_filter}'. "
                f"Valid type keys and values: {', '.join(valid)}"
            )

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
    omitted_missing_created = 0
    artefact_index = router.get("artefact_index") or {}
    by_path = {entry["path"]: entry for entry in artefact_index.values()}

    for doc in docs:
        if resolved_type is not None and doc.get("type") != resolved_type:
            continue
        if resolved_parent and doc.get("parent") != resolved_parent:
            continue

        created_date = _index_date(doc.get("created") or doc.get("archiveddate"))
        modified_date = _index_date(doc.get("modified"))
        missing_created_for_bound = bool((since or until) and not created_date)
        if since and created_date and created_date < since:
            continue
        if until and created_date and created_date > until:
            continue
        if modified_since and (not modified_date or modified_date < modified_since):
            continue
        if modified_until and (not modified_date or modified_date > modified_until):
            continue
        if tag and tag not in doc.get("tags", []):
            continue
        if missing_created_for_bound:
            omitted_missing_created += 1
            continue

        stem = doc.get("title", "")
        display = temporal_display_name(stem)
        title = display if display is not None else stem
        artefact_meta = by_path.get(doc.get("path", ""))
        result = {
            "path": doc.get("path", ""),
            "title": title,
            "type": doc.get("type", ""),
            "created": created_date,
            "modified": modified_date,
            "status": doc.get("status", ""),
            "location": doc.get("location", "active"),
        }
        if doc.get("archiveddate"):
            result["archiveddate"] = doc["archiveddate"]
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
        results.sort(key=lambda item: (item["created"], item["path"]))
    elif sort == "modified_desc":
        results.sort(
            key=lambda item: (item["modified"], item["path"]),
            reverse=True,
        )
    elif sort == "modified_asc":
        results.sort(key=lambda item: (item["modified"], item["path"]))
    elif sort == "title":
        results.sort(key=lambda item: (item["title"].lower(), item["path"]))
    else:
        results.sort(
            key=lambda item: (item["created"], item["path"]),
            reverse=True,
        )
    return results, omitted_missing_created


def list_artefacts_page(
    index,
    router,
    type_filter=None,
    since=None,
    until=None,
    modified_since=None,
    modified_until=None,
    tag=None,
    parent=None,
    top_k=500,
    sort="date_desc",
    cursor=None,
):
    if top_k is None:
        top_k = 500
    if top_k < 1:
        raise ValueError("top_k must be at least 1")
    offset = _cursor_offset(cursor)
    results, omitted_missing_created = _collect_artefacts(
        index,
        router,
        type_filter=type_filter,
        since=since,
        until=until,
        modified_since=modified_since,
        modified_until=modified_until,
        tag=tag,
        parent=parent,
        sort=sort,
    )
    total = len(results)
    items = results[offset : offset + top_k]
    next_offset = offset + len(items)
    truncated = next_offset < total
    return {
        "items": items,
        "total": total,
        "returned": len(items),
        "truncated": truncated,
        "next_cursor": str(next_offset) if truncated else None,
        "omitted_missing_created": omitted_missing_created,
    }


def list_artefacts(
    index,
    router,
    type_filter=None,
    since=None,
    until=None,
    modified_since=None,
    modified_until=None,
    tag=None,
    parent=None,
    top_k=500,
    sort="date_desc",
    cursor=None,
):
    return list_artefacts_page(
        index,
        router,
        type_filter=type_filter,
        since=since,
        until=until,
        modified_since=modified_since,
        modified_until=modified_until,
        tag=tag,
        parent=parent,
        top_k=top_k,
        sort=sort,
        cursor=cursor,
    )["items"]


def list_from_vault(vault_root, **kwargs):
    router = load_compiled_router(vault_root)
    if "error" in router:
        raise FileNotFoundError(router["error"])
    index = lexical_query.load_index(vault_root)
    return list_artefacts_page(index, router, **kwargs)


def list_combined_from_vault(
    vault_root,
    *,
    location="active",
    router=None,
    index=None,
    **kwargs,
):
    """List active, archived or all artefacts through one filter/page contract."""

    if location not in {"active", "archived", "all"}:
        raise ValueError("location must be active, archived, or all")
    router = load_compiled_router(vault_root) if router is None else router
    if "error" in router:
        raise FileNotFoundError(router["error"])
    documents = []
    if location in {"active", "all"}:
        index = lexical_query.load_index(vault_root) if index is None else index
        documents.extend(index.get("documents", ()))
    if location in {"archived", "all"}:
        from _portable.vault_files import list_archived_artefacts

        documents.extend(list_archived_artefacts(router, vault_root))
    return list_artefacts_page({"documents": documents}, router, **kwargs)
