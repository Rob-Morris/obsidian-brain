"""Shared catalogue and no-effect error mechanics for Brain mutations."""

from __future__ import annotations

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


def no_effect_error(
    request_type,
    code: ErrorCode,
    message: str,
    field: str | None = None,
    *,
    retryable: bool = False,
) -> Error:
    return Error(
        request_type.COMMAND_ID,
        request_type.COMMAND_VERSION,
        CommandError(code, message, RequestErrorDetails(field, message)),
        retryable=retryable,
    )


def contributor_mutation_entry(request_type, executor):
    from .catalogue import ApplicationEntry

    return ApplicationEntry(
        request_type=request_type,
        executor=executor,
        dependency_tier=DependencyTier.PORTABLE,
        locality=Locality.SELECTED_BRAIN_LOCAL,
        required_providers=(),
        optional_providers=(),
        authority=Authority.CONTRIBUTOR,
        effect_class=EffectClass.SELECTED_BRAIN_MUTATION,
        retry_class=RetryClass.RECEIPT_REQUIRED,
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
