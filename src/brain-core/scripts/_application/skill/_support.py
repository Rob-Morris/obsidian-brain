"""Shared execution and decoding mechanics for skill lifecycle owners."""

from __future__ import annotations

from .._mutation_support import contributor_mutation_entry, no_effect_error
from ..receipts import CommittedEffect
from ..results import ErrorCode, Ok
from ._types import mutation_payload


def execute_mutation(context, request, operation):
    from _skill_library import SkillLibraryError

    if context.dry_run:
        return no_effect_error(
            type(request),
            ErrorCode.INVALID_REQUEST,
            f"{request.COMMAND_ID} does not support dry-run",
        )
    try:
        value = operation()
    except (OSError, ValueError, SkillLibraryError) as exc:
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))
    payload = mutation_payload(value)
    effects = tuple(
        CommittedEffect(request.COMMAND_ID, path)
        for path in payload.changed_paths
    )
    return Ok(request.COMMAND_ID, request.COMMAND_VERSION, payload, effects)


def mutation_catalogue_entry(request_type, executor):
    return contributor_mutation_entry(request_type, executor)
