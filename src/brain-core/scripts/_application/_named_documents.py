"""Shared mechanics for exact named-document command owners."""

from __future__ import annotations

def read_portable(vault_root, resource: str, reference: str):
    from _portable.named_documents import read_named_document_from_vault

    return read_named_document_from_vault(vault_root, resource, reference)


def list_portable(vault_root, resource: str, query: str | None):
    from _portable.named_documents import list_named_documents_from_vault

    return list_named_documents_from_vault(vault_root, resource, query)
