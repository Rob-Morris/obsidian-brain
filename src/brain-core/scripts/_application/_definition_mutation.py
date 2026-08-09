"""Typed mechanics shared by granular definition mutation owners."""

from __future__ import annotations

from dataclasses import dataclass

from ._mutation_support import (
    MutationContent,
    no_effect_error,
    operator_mutation_entry,
    resolve_mutation_content,
)
from .context import InvocationContext
from .receipts import CommittedEffect
from .results import CommandWarning, ErrorCode, Ok, WarningCode


@dataclass(frozen=True, slots=True)
class DefinitionMutationPayload:
    kind: str
    operation: str
    path: str
    name: str | None
    condition: str | None
    target: str | None
    before_sha256: str | None
    sha256: str
    staged_handle_consumed: bool


def validate_nonempty(command_id: str, field: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{command_id} {field} must be a non-empty string")


def validate_content(command_id: str, content: MutationContent) -> None:
    from ._mutation_support import InlineContent, StagedContent

    if not isinstance(content, (InlineContent, StagedContent)):
        raise ValueError(f"{command_id} content has an invalid variant")


def execute_definition(
    context: InvocationContext,
    request,
    *,
    operation,
    content: MutationContent | None = None,
):
    from _common import (
        MutationLockError,
        public_mutation_error_message,
        vault_mutation_lock,
    )
    from _staging import finalise_staged_body

    if context.dry_run:
        return no_effect_error(
            type(request),
            ErrorCode.INVALID_REQUEST,
            f"{request.COMMAND_ID} does not support dry-run",
        )
    root = str(context.selected_brain.vault_root)
    try:
        with vault_mutation_lock(root):
            if content is None:
                body, staged_handle = None, None
            else:
                body, staged_handle = resolve_mutation_content(root, content)
            result = operation(root, body)
            staging_warning = finalise_staged_body(root, staged_handle)
    except MutationLockError as exc:
        return no_effect_error(
            type(request),
            ErrorCode.CONFLICT,
            public_mutation_error_message(exc),
            retryable=True,
        )
    except FileNotFoundError as exc:
        return no_effect_error(type(request), ErrorCode.NOT_FOUND, str(exc))
    except ValueError as exc:
        return no_effect_error(type(request), ErrorCode.INVALID_REQUEST, str(exc))

    payload = DefinitionMutationPayload(
        kind=result["kind"],
        operation=result["operation"],
        path=result["path"],
        name=result.get("name"),
        condition=result.get("condition"),
        target=result.get("target"),
        before_sha256=result.get("before_sha256"),
        sha256=result["sha256"],
        staged_handle_consumed=bool(staged_handle and not staging_warning),
    )
    effects = (
        (CommittedEffect(request.COMMAND_ID, payload.path),)
        if payload.before_sha256 != payload.sha256
        else ()
    )
    warnings = (
        (CommandWarning(WarningCode.FOLLOW_UP_REQUIRED, staging_warning),)
        if staging_warning
        else ()
    )
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        payload,
        committed_effects=effects,
        warnings=warnings,
    )


def catalogue_entry(request_type, executor):
    return operator_mutation_entry(request_type, executor)
