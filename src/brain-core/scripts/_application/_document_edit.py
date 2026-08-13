"""Shared typed mechanics for structural document mutation commands."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from ._decoding import reject_unexpected
from ._mutation_support import (
    FrontmatterField,
    InlineContent,
    StagedContent,
    frontmatter_mapping,
    no_effect_error,
    resolve_mutation_content,
)
from ._wikilink_results import (
    WikilinkFinding,
    WikilinkFix,
    wikilink_result_values,
)
from .context import InvocationContext
from .receipts import CommittedEffect
from .results import CommandWarning, ErrorCode, Ok, WarningCode


class EditScope(str, Enum):
    SECTION = "section"
    INTRO = "intro"
    BODY = "body"
    HEADING = "heading"
    HEADER = "header"


@dataclass(frozen=True, slots=True)
class SelectorWithinStep:
    target: str
    occurrence: int | None = None

    def __post_init__(self) -> None:
        _validate_target(self.target, "selector within target")
        _validate_occurrence(self.occurrence, "selector within occurrence")


@dataclass(frozen=True, slots=True)
class StructuralSelector:
    occurrence: int | None = None
    within: tuple[SelectorWithinStep, ...] = ()

    def __post_init__(self) -> None:
        _validate_occurrence(self.occurrence, "selector occurrence")
        if not isinstance(self.within, tuple) or any(
            not isinstance(step, SelectorWithinStep) for step in self.within
        ):
            raise ValueError("selector within must contain typed ancestor steps")


@dataclass(frozen=True, slots=True)
class StructuralTargetResult:
    kind: str
    raw: str
    scope: EditScope
    display: str


@dataclass(frozen=True, slots=True)
class DocumentEditPayload:
    path: str
    resolved_path: str
    operation: str
    old_body_line_count: int
    new_body_line_count: int
    structural_target: StructuralTargetResult | None
    match_count: int | None
    replacement_count: int | None
    wikilink_warnings: tuple[WikilinkFinding, ...]
    wikilink_fixes: tuple[WikilinkFix, ...]
    wikilink_substitutions: int
    staged_handle_consumed: bool


def validate_structural_request(request, *, subject_field: str | None) -> None:
    if subject_field is not None:
        _validate_subject(request, subject_field)
    if request.content is not None and not isinstance(
        request.content, (InlineContent, StagedContent)
    ):
        raise ValueError(f"{request.COMMAND_ID} content has an invalid variant")
    _validate_frontmatter(request)
    _validate_target(request.target, "target", optional=True)
    _validate_selector(request.selector)
    _validate_scope(request.scope)
    _validate_fix_links(request)


def validate_delete_request(request, *, subject_field: str | None) -> None:
    if subject_field is not None:
        _validate_subject(request, subject_field)
    _validate_target(request.target, "target")
    _validate_selector(request.selector)
    _validate_frontmatter(request)
    _validate_fix_links(request)


def validate_replace_request(request, *, subject_field: str | None) -> None:
    if subject_field is not None:
        _validate_subject(request, subject_field)
    if not isinstance(request.old_text, str) or not request.old_text:
        raise ValueError(f"{request.COMMAND_ID} old_text must be non-empty")
    if not isinstance(request.new_text, str):
        raise ValueError(f"{request.COMMAND_ID} new_text must be a string")
    _validate_target(request.target, "target", optional=True)
    _validate_selector(request.selector)
    _validate_scope(request.scope)
    if (request.target is None) != (request.scope is None):
        raise ValueError("replace-text target and scope must be supplied together")
    if request.selector is not None and request.target is None:
        raise ValueError("replace-text selector requires target and scope")
    _validate_occurrence(request.match_occurrence, "match_occurrence")
    if not isinstance(request.replace_all, bool):
        raise ValueError(f"{request.COMMAND_ID} replace_all must be a boolean")
    if request.replace_all and request.match_occurrence is not None:
        raise ValueError("replace_all and match_occurrence are mutually exclusive")
    _validate_fix_links(request)


def execute_document_edit(
    context: InvocationContext,
    request,
    *,
    resource: str,
    operation: str,
    subject_field: str,
    result_operation: str | None = None,
):
    from _common import (
        MutationLockError,
        public_mutation_error_message,
        vault_mutation_lock,
    )
    from _lifecycle.derived_cache_state import load_fresh_compiled_router
    from _staging import finalise_staged_body
    import edit

    if context.dry_run:
        return no_effect_error(
            type(request),
            ErrorCode.INVALID_REQUEST,
            f"{request.COMMAND_ID} does not support dry-run",
        )
    try:
        _preflight_request(edit, request, operation)
    except ValueError as exc:
        return no_effect_error(
            type(request), ErrorCode.INVALID_REQUEST, str(exc)
        )

    vault_root = str(context.selected_brain.vault_root)
    router = load_fresh_compiled_router(vault_root)
    if "error" in router:
        return no_effect_error(
            type(request), ErrorCode.CONFLICT, router["error"]
        )

    try:
        with vault_mutation_lock(vault_root):
            body, staged_handle = _resolve_body(vault_root, request)
            kwargs = _edit_kwargs(
                request,
                resource=resource,
                operation=operation,
                subject_field=subject_field,
                body=body,
            )
            result = edit.edit_resource(vault_root, router, **kwargs)
            staging_warning = finalise_staged_body(vault_root, staged_handle)
    except MutationLockError as exc:
        return no_effect_error(
            type(request),
            ErrorCode.CONFLICT,
            public_mutation_error_message(exc),
            retryable=True,
        )
    except FileNotFoundError as exc:
        return no_effect_error(
            type(request), ErrorCode.NOT_FOUND, str(exc), subject_field
        )
    except ValueError as exc:
        return no_effect_error(
            type(request), ErrorCode.INVALID_REQUEST, str(exc)
        )

    payload = _payload(
        result,
        staged_handle,
        staging_warning,
        operation=result_operation or operation.replace("_", "-"),
    )
    warnings = []
    if staging_warning:
        warnings.append(
            CommandWarning(WarningCode.FOLLOW_UP_REQUIRED, staging_warning)
        )
    if payload.wikilink_warnings:
        warnings.append(
            CommandWarning(
                WarningCode.FOLLOW_UP_REQUIRED,
                f"Edited document has {len(payload.wikilink_warnings)} unresolved wikilink finding(s).",
            )
        )
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        payload,
        committed_effects=(
            CommittedEffect(request.COMMAND_ID, payload.path),
        ),
        warnings=tuple(warnings),
    )


def decode_selector(value: object) -> StructuralSelector | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("selector must be an object")
    reject_unexpected(value, {"occurrence", "within"}, label="selector fields")
    occurrence = value.get("occurrence")
    if occurrence is not None and (
        not isinstance(occurrence, int) or isinstance(occurrence, bool)
    ):
        raise ValueError("selector occurrence must be an integer or null")
    raw_within = value.get("within")
    if raw_within is None:
        within = ()
    elif isinstance(raw_within, list):
        steps = []
        for item in raw_within:
            if not isinstance(item, Mapping):
                raise ValueError("selector within steps must be objects")
            reject_unexpected(
                item,
                {"target", "occurrence"},
                label="selector within step fields",
            )
            target = item.get("target")
            step_occurrence = item.get("occurrence")
            if not isinstance(target, str):
                raise ValueError("selector within target must be a string")
            if step_occurrence is not None and (
                not isinstance(step_occurrence, int)
                or isinstance(step_occurrence, bool)
            ):
                raise ValueError(
                    "selector within occurrence must be an integer or null"
                )
            steps.append(SelectorWithinStep(target, step_occurrence))
        within = tuple(steps)
    else:
        raise ValueError("selector within must be a list or null")
    return StructuralSelector(occurrence, within)


def decode_scope(value: object) -> EditScope | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("scope must be a string or null")
    try:
        return EditScope(value)
    except ValueError as exc:
        raise ValueError(
            "scope must be section, intro, body, heading, header, or null"
        ) from exc


def _preflight_request(edit, request, operation: str) -> None:
    if operation in {"edit", "append", "prepend"}:
        edit.preflight_request_contract(
            operation,
            has_body=request.content is not None,
            frontmatter_changes=frontmatter_mapping(request.frontmatter),
            target=request.target,
            selector=_selector_mapping(request.selector),
            scope=_scope_value(request.scope),
        )
    elif operation == "delete_section":
        edit.preflight_request_contract(
            operation,
            frontmatter_changes=frontmatter_mapping(request.frontmatter),
            target=request.target,
            selector=_selector_mapping(request.selector),
        )


def _resolve_body(vault_root: str, request) -> tuple[str, str | None]:
    content = getattr(request, "content", None)
    if content is None:
        return "", None
    return resolve_mutation_content(vault_root, content)


def _edit_kwargs(
    request,
    *,
    resource: str,
    operation: str,
    subject_field: str,
    body: str,
) -> dict:
    kwargs = {
        "resource": resource,
        "operation": operation,
        subject_field: getattr(request, subject_field),
        "fix_links": bool(getattr(request, "fix_links", False)),
    }
    if operation in {"edit", "append", "prepend"}:
        kwargs.update(
            body=body,
            frontmatter_changes=frontmatter_mapping(request.frontmatter),
            target=request.target,
            selector=_selector_mapping(request.selector),
            scope=_scope_value(request.scope),
        )
    elif operation == "delete_section":
        kwargs.update(
            frontmatter_changes=frontmatter_mapping(request.frontmatter),
            target=request.target,
            selector=_selector_mapping(request.selector),
        )
    else:
        kwargs.update(
            old_text=request.old_text,
            new_text=request.new_text,
            target=request.target,
            selector=_selector_mapping(request.selector),
            scope=_scope_value(request.scope),
            match_occurrence=request.match_occurrence,
            replace_all=request.replace_all,
        )
    return kwargs


def _payload(
    result,
    staged_handle,
    staging_warning,
    *,
    operation: str,
) -> DocumentEditPayload:
    raw_target = result.get("structural_target")
    target = None
    if raw_target is not None:
        target = StructuralTargetResult(
            kind=raw_target["kind"],
            raw=raw_target["raw"],
            scope=EditScope(raw_target["scope"]),
            display=raw_target["display"],
        )
    findings, fixes, substitutions = wikilink_result_values(result)
    return DocumentEditPayload(
        path=result["path"],
        resolved_path=result["resolved_path"],
        operation=operation,
        old_body_line_count=int(result["old_body_line_count"]),
        new_body_line_count=int(result["new_body_line_count"]),
        structural_target=target,
        match_count=result.get("match_count"),
        replacement_count=result.get("replacement_count"),
        wikilink_warnings=findings,
        wikilink_fixes=fixes,
        wikilink_substitutions=substitutions,
        staged_handle_consumed=(
            staged_handle is not None and staging_warning is None
        ),
    )


def _selector_mapping(selector: StructuralSelector | None):
    if selector is None:
        return None
    return {
        "occurrence": selector.occurrence,
        "within": [
            {"target": step.target, "occurrence": step.occurrence}
            for step in selector.within
        ],
    }


def _scope_value(scope: EditScope | None) -> str | None:
    return None if scope is None else scope.value


def _validate_subject(request, field_name: str) -> None:
    value = getattr(request, field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"{request.COMMAND_ID} {field_name} must be a non-empty string"
        )


def _validate_frontmatter(request) -> None:
    if not isinstance(request.frontmatter, tuple) or any(
        not isinstance(item, FrontmatterField) for item in request.frontmatter
    ):
        raise ValueError(f"{request.COMMAND_ID} frontmatter must be typed fields")
    names = tuple(item.name for item in request.frontmatter)
    if len(names) != len(set(names)) or names != tuple(sorted(names)):
        raise ValueError(
            f"{request.COMMAND_ID} frontmatter fields must be unique and ordered"
        )


def _validate_target(value, label: str, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")


def _validate_selector(value) -> None:
    if value is not None and not isinstance(value, StructuralSelector):
        raise ValueError("selector must be a StructuralSelector or null")


def _validate_scope(value) -> None:
    if value is not None and not isinstance(value, EditScope):
        raise ValueError("scope must be an EditScope or null")


def _validate_occurrence(value, label: str) -> None:
    if value is not None and (
        not isinstance(value, int) or isinstance(value, bool) or value < 1
    ):
        raise ValueError(f"{label} must be a positive integer or null")


def _validate_fix_links(request) -> None:
    if hasattr(request, "fix_links") and not isinstance(request.fix_links, bool):
        raise ValueError(f"{request.COMMAND_ID} fix_links must be a boolean")
