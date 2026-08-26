"""Portable named-document resource reads and lists."""

from __future__ import annotations

from _common import load_compiled_router, read_file_content
from .skill_resolution import annotate_skill_records, resolve_skill_record


_RESOURCE_SPECS = {
    "skill": ("skills", "skill_doc"),
    "style": ("styles", "style_doc"),
    "plugin": ("plugins", "skill_doc"),
}


def _spec(resource):
    try:
        return _RESOURCE_SPECS[resource]
    except KeyError as exc:
        raise ValueError(f"unsupported named-document resource: {resource}") from exc


def resolve_named_document(router, resource, reference):
    """Resolve one exact named resource entry or return a legacy error value."""
    router_key, _doc_field = _spec(resource)
    records = router.get(router_key, ())
    match = (
        resolve_skill_record(records, reference)
        if resource == "skill"
        else next((item for item in records if item.get("name") == reference), None)
    )
    if match is None:
        return {"error": f"No {resource} matching '{reference}'"}
    return dict(match)


def resolve_named_document_path(router, resource, reference):
    """Resolve the persisted path for one existing named document."""
    _router_key, doc_field = _spec(resource)
    match = resolve_named_document(router, resource, reference)
    if "error" in match:
        raise FileNotFoundError(match["error"])
    return match[doc_field]


def read_named_document(router, vault_root, resource, reference):
    """Read one exact named document while preserving the legacy return shape."""
    _router_key, doc_field = _spec(resource)
    match = resolve_named_document(router, resource, reference)
    if "error" in match:
        return match
    return read_file_content(vault_root, match[doc_field])


def read_named_document_from_vault(vault_root, resource, reference):
    """Load router state and return both exact metadata and document content."""
    router = load_compiled_router(vault_root)
    if "error" in router:
        raise FileNotFoundError(router["error"])
    match = resolve_named_document(router, resource, reference)
    if "error" in match:
        return match
    _router_key, doc_field = _spec(resource)
    content = read_file_content(vault_root, match[doc_field])
    return match, content


def list_named_documents(router, resource, query=None):
    """List exact named-resource metadata with optional case-insensitive filtering."""
    router_key, _doc_field = _spec(resource)
    records = router.get(router_key, ())
    items = (
        annotate_skill_records(records)
        if resource == "skill"
        else [dict(item) for item in records]
    )
    if query is None:
        return items
    folded = query.casefold()
    return [item for item in items if folded in item.get("name", "").casefold()]


def list_named_documents_from_vault(vault_root, resource, query=None):
    router = load_compiled_router(vault_root)
    if "error" in router:
        raise FileNotFoundError(router["error"])
    return list_named_documents(router, resource, query)
