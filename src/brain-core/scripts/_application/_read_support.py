"""Shared construction and decoding mechanics for portable read owners."""

from __future__ import annotations

from ._decoding import reject_unexpected

from typing import Mapping

from .results import request_error
from .types import (
    Authority,
    DependencyTier,
    EffectClass,
    Locality,
    RetryClass,
)


command_error = request_error


def decode_required_string(
    payload: Mapping[str, object],
    field: str,
    request_type,
):
    reject_unexpected(payload, {field})
    value = payload.get(field)
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return request_type(value)


def decode_reference(payload: Mapping[str, object], request_type):
    return decode_required_string(payload, "reference", request_type)


def decode_query(payload: Mapping[str, object], request_type):
    reject_unexpected(payload, {"query"})
    query = payload.get("query")
    if query is not None and not isinstance(query, str):
        raise ValueError("query must be a string")
    return request_type(query)


def catalogue_entry(request_type, executor):
    from .catalogue import ALL_APPLICATION_PROJECTIONS, ApplicationEntry
    from .preparation import LIVE_QUERY

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
        projections=ALL_APPLICATION_PROJECTIONS,
        preparation=LIVE_QUERY,
    )
