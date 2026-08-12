"""Identity-preserving composition of resource-specific implementation results."""

from __future__ import annotations

from dataclasses import replace

from ..results import Error, Ok, Partial, RequestErrorDetails


def compose_result(result, request_type, transform, *, field_aliases=None):
    """Return one resource command result from a target-specific implementation."""

    command_id = request_type.COMMAND_ID
    command_version = request_type.COMMAND_VERSION
    if isinstance(result, Ok):
        return Ok(
            command_id,
            command_version,
            transform(result.result),
            committed_effects=result.committed_effects,
            warnings=result.warnings,
        )
    error = result.error
    aliases = field_aliases or {}
    if isinstance(error.details, RequestErrorDetails):
        field = aliases.get(error.details.field, error.details.field)
        error = replace(error, details=replace(error.details, field=field))
    if isinstance(result, Partial):
        return Partial(
            command_id,
            command_version,
            error,
            result.committed_effects,
            result.warnings,
        )
    if isinstance(result, Error):
        return Error(
            command_id,
            command_version,
            error,
            result.effects,
            result.outcome_reference,
            result.retryable,
            result.warnings,
        )
    raise TypeError("resource implementation returned an invalid command result")
