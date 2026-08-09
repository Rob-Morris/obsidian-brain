"""Shared construction and decoding mechanics for portable read owners."""

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


def command_error(request_type, code: ErrorCode, message: str, field: str | None):
    return Error(
        request_type.COMMAND_ID,
        request_type.COMMAND_VERSION,
        CommandError(code, message, RequestErrorDetails(field, message)),
    )


def decode_required_string(
    payload: Mapping[str, object],
    field: str,
    request_type,
):
    unexpected = sorted(set(payload) - {field})
    if unexpected:
        raise ValueError(f"unexpected fields: {', '.join(unexpected)}")
    value = payload.get(field)
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return request_type(value)


def decode_reference(payload: Mapping[str, object], request_type):
    return decode_required_string(payload, "reference", request_type)


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
