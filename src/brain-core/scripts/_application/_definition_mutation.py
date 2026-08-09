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
from .type._classification import ArtefactTypeClassification


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


@dataclass(frozen=True, slots=True)
class TypeDefinitionMutationPayload:
    operation: str
    name: str
    classification: ArtefactTypeClassification
    path: str
    template_path: str
    artefact_folder: str
    frontmatter_type: str
    status_enum: tuple[str, ...]
    before_sha256: str | None
    before_template_sha256: str | None
    sha256: str
    template_sha256: str
    definition_staged_handle_consumed: bool
    template_staged_handle_consumed: bool


def validate_nonempty(command_id: str, field: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{command_id} {field} must be a non-empty string")


def validate_content(command_id: str, content: MutationContent) -> None:
    from ._mutation_support import InlineContent, StagedContent

    if not isinstance(content, (InlineContent, StagedContent)):
        raise ValueError(f"{command_id} content has an invalid variant")


def validate_type_contents(
    command_id: str,
    definition: MutationContent,
    template: MutationContent,
) -> None:
    from ._mutation_support import StagedContent

    validate_content(command_id, definition)
    validate_content(command_id, template)
    if (
        isinstance(definition, StagedContent)
        and isinstance(template, StagedContent)
        and definition.handle == template.handle
    ):
        raise ValueError(
            f"{command_id} definition and template require distinct staged handles"
        )


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


def execute_type_definition(
    context: InvocationContext,
    request,
    *,
    operation,
    definition: MutationContent,
    template: MutationContent,
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
            definition_body, definition_handle = resolve_mutation_content(
                root, definition
            )
            template_body, template_handle = resolve_mutation_content(root, template)
            result = operation(root, definition_body, template_body)
            definition_warning = finalise_staged_body(root, definition_handle)
            template_warning = finalise_staged_body(root, template_handle)
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

    payload = TypeDefinitionMutationPayload(
        operation=result["operation"],
        name=result["name"],
        classification=request.classification,
        path=result["path"],
        template_path=result["template_path"],
        artefact_folder=result["artefact_folder"],
        frontmatter_type=result["type"],
        status_enum=tuple(result.get("status_enum") or ()),
        before_sha256=result.get("before_sha256"),
        before_template_sha256=result.get("before_template_sha256"),
        sha256=result["sha256"],
        template_sha256=result["template_sha256"],
        definition_staged_handle_consumed=bool(
            definition_handle and not definition_warning
        ),
        template_staged_handle_consumed=bool(template_handle and not template_warning),
    )
    changed = (
        payload.before_sha256 != payload.sha256
        or payload.before_template_sha256 != payload.template_sha256
    )
    warnings = tuple(
        CommandWarning(WarningCode.FOLLOW_UP_REQUIRED, f"{label}: {warning}")
        for label, warning in (
            ("Definition content", definition_warning),
            ("Template content", template_warning),
        )
        if warning
    )
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        payload,
        committed_effects=(CommittedEffect(request.COMMAND_ID, payload.path),)
        if changed
        else (),
        warnings=warnings,
    )


def catalogue_entry(request_type, executor):
    return operator_mutation_entry(request_type, executor)
