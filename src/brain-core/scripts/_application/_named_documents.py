"""Shared mechanics for exact named-document command owners."""

from __future__ import annotations

from typing import Mapping

from .results import CommandError, Error, ErrorCode, RequestErrorDetails
from .types import (
    Authority,
    DependencyTier,
    EffectClass,
    Locality,
    Projection,
    ProjectionEligibility,
    RetryClass,
)


def read_portable(vault_root, resource: str, reference: str):
    from _portable.named_documents import read_named_document_from_vault

    return read_named_document_from_vault(vault_root, resource, reference)


def list_portable(vault_root, resource: str, query: str | None):
    from _portable.named_documents import list_named_documents_from_vault

    return list_named_documents_from_vault(vault_root, resource, query)


def command_error(request_type, code: ErrorCode, message: str, field: str | None):
    return Error(
        request_type.COMMAND_ID,
        request_type.COMMAND_VERSION,
        CommandError(code, message, RequestErrorDetails(field, message)),
    )


def decode_reference(payload: Mapping[str, object], request_type):
    unexpected = sorted(set(payload) - {"reference"})
    if unexpected:
        raise ValueError(f"unexpected fields: {', '.join(unexpected)}")
    reference = payload.get("reference")
    if not isinstance(reference, str):
        raise ValueError("reference must be a string")
    return request_type(reference)


def decode_query(payload: Mapping[str, object], request_type):
    unexpected = sorted(set(payload) - {"query"})
    if unexpected:
        raise ValueError(f"unexpected fields: {', '.join(unexpected)}")
    query = payload.get("query")
    if query is not None and not isinstance(query, str):
        raise ValueError("query must be a string")
    return request_type(query)


def catalogue_entry(request_type, executor):
    from .catalogue import ApplicationEntry

    return ApplicationEntry(
        request_type=request_type,
        executor=executor,
        dependency_tier=DependencyTier.PORTABLE,
        locality=Locality.SELECTED_BRAIN_LOCAL,
        required_providers=(),
        optional_providers=(),
        authority=Authority.READER,
        effect_class=EffectClass.NONE,
        retry_class=RetryClass.SAFE,
        projections=tuple(
            ProjectionEligibility(projection, True)
            for projection in (
                Projection.MCP,
                Projection.CLI,
                Projection.SCRIPT,
                Projection.PYTHON,
            )
        ),
    )


def resolver_entry(request_type, decoder):
    from .resolver import ResolverEntry

    return ResolverEntry(request_type, decoder)
