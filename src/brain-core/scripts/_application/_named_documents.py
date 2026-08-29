"""Shared mechanics for exact named-document command owners."""

from __future__ import annotations


def _current_router(vault_root):
    from _lifecycle.derived_cache_state import inspect_router_cache
    import compile_router

    state = inspect_router_cache(vault_root, verify_content=True)
    if not state.stale and state.payload is not None:
        return dict(state.payload)
    return compile_router.compile(str(vault_root))


def read_portable(vault_root, resource: str, reference: str):
    from _portable.named_documents import read_named_document, resolve_named_document

    router = _current_router(vault_root)
    metadata = resolve_named_document(router, resource, reference)
    if "error" in metadata:
        return metadata
    content = read_named_document(router, vault_root, resource, reference)
    return metadata, content


def list_portable(vault_root, resource: str, query: str | None):
    from _portable.named_documents import list_named_documents

    router = _current_router(vault_root)
    return list_named_documents(router, resource, query)
