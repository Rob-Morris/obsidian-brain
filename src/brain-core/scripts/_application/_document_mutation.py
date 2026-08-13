"""Shared execution boundary for cohesive document mutation commands."""

from __future__ import annotations

from dataclasses import dataclass

from ._mutation_support import (
    FrontmatterField,
    MutationContent,
    frontmatter_mapping,
    no_effect_error,
    resolve_mutation_content,
)
from ._wikilink_results import WikilinkFinding, WikilinkFix, wikilink_result_values
from .context import InvocationContext
from .receipts import CommittedEffect
from .results import (
    CommandArgument,
    CommandError,
    CommandNextAction,
    CommandWarning,
    Error,
    ErrorCode,
    Ok,
    RequestErrorDetails,
    WarningCode,
)


@dataclass(frozen=True, slots=True)
class StructuralTargetResult:
    kind: str
    raw: str
    part: str
    display: str


@dataclass(frozen=True, slots=True)
class DocumentWritePayload:
    path: str
    resolved_path: str
    operation: str
    old_body_line_count: int
    new_body_line_count: int
    revision: str
    wikilink_warnings: tuple[WikilinkFinding, ...]
    wikilink_fixes: tuple[WikilinkFix, ...]
    wikilink_substitutions: int
    staged_handle_consumed: bool


@dataclass(frozen=True, slots=True)
class DocumentPatchPayload:
    path: str
    resolved_path: str
    match_count: int
    replacement_count: int
    old_body_line_count: int
    new_body_line_count: int
    revision: str
    wikilink_warnings: tuple[WikilinkFinding, ...]
    wikilink_fixes: tuple[WikilinkFix, ...]
    wikilink_substitutions: int


@dataclass(frozen=True, slots=True)
class DocumentEditPayload:
    path: str
    resolved_path: str
    operation: str
    old_body_line_count: int
    new_body_line_count: int
    structural_target: StructuralTargetResult
    revision: str
    wikilink_warnings: tuple[WikilinkFinding, ...]
    wikilink_fixes: tuple[WikilinkFix, ...]
    wikilink_substitutions: int
    staged_handle_consumed: bool


@dataclass(frozen=True, slots=True)
class DocumentFrontmatterUpdatePayload:
    path: str
    resolved_path: str
    updated_fields: tuple[str, ...]
    removed_fields: tuple[str, ...]
    revision: str


@dataclass(frozen=True, slots=True)
class DocumentMutationIntent:
    resource: str
    reference: str
    expected_revision: str
    operation: str
    result_operation: str
    content: MutationContent | None = None
    frontmatter: tuple[FrontmatterField, ...] = ()
    target: str | None = None
    selector: dict | None = None
    scope: str | None = None
    old_text: str = ""
    new_text: str = ""
    match_occurrence: int | None = None
    replace_all: bool = False
    fix_links: bool = False


def execute_document_mutation(
    context: InvocationContext,
    request,
    intent: DocumentMutationIntent,
):
    from _common import (
        DocumentRevisionConflict,
        MutationLockError,
        public_mutation_error_message,
        validate_document_revision,
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
        _preflight_request(edit, intent)
    except ValueError as exc:
        return no_effect_error(type(request), ErrorCode.INVALID_REQUEST, str(exc))

    vault_root = str(context.selected_brain.vault_root)
    router = load_fresh_compiled_router(vault_root)
    if "error" in router:
        return no_effect_error(type(request), ErrorCode.CONFLICT, router["error"])

    subject_field = "path" if intent.resource == "artefact" else "name"
    subject_kwargs = {subject_field: intent.reference}
    try:
        with vault_mutation_lock(vault_root):
            current_revision = edit.current_document_revision(
                vault_root,
                router,
                intent.resource,
                **subject_kwargs,
            )
            validate_document_revision(
                intent.expected_revision,
                label="expected_revision",
            )
            if current_revision != intent.expected_revision:
                raise DocumentRevisionConflict(
                    "document changed since it was read; re-read it and retry "
                    f"with the current revision ({current_revision})"
                )
            body, staged_handle = _resolve_body(vault_root, intent.content)
            result = edit.edit_resource(
                vault_root,
                router,
                resource=intent.resource,
                operation=intent.operation,
                body=body,
                frontmatter_changes=frontmatter_mapping(intent.frontmatter),
                target=intent.target,
                selector=intent.selector,
                scope=intent.scope,
                old_text=intent.old_text,
                new_text=intent.new_text,
                match_occurrence=intent.match_occurrence,
                replace_all=intent.replace_all,
                fix_links=intent.fix_links,
                expected_revision=intent.expected_revision,
                **subject_kwargs,
            )
            staging_warning = finalise_staged_body(vault_root, staged_handle)
    except DocumentRevisionConflict as exc:
        return _revision_conflict(type(request), request, str(exc))
    except MutationLockError as exc:
        return no_effect_error(
            type(request),
            ErrorCode.CONFLICT,
            public_mutation_error_message(exc),
            retryable=True,
        )
    except FileNotFoundError as exc:
        return no_effect_error(
            type(request), ErrorCode.NOT_FOUND, str(exc), "document"
        )
    except ValueError as exc:
        return no_effect_error(type(request), ErrorCode.INVALID_REQUEST, str(exc))

    payload = _payload(request, intent, result, staged_handle, staging_warning)
    warnings = []
    if staging_warning:
        warnings.append(CommandWarning(WarningCode.FOLLOW_UP_REQUIRED, staging_warning))
    findings = tuple(getattr(payload, "wikilink_warnings", ()))
    if findings:
        warnings.append(
            CommandWarning(
                WarningCode.FOLLOW_UP_REQUIRED,
                f"Edited document has {len(findings)} unresolved wikilink finding(s).",
            )
        )
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        payload,
        committed_effects=(CommittedEffect(request.COMMAND_ID, payload.path),),
        warnings=tuple(warnings),
    )


def _preflight_request(edit, intent: DocumentMutationIntent) -> None:
    if intent.operation in {"edit", "append", "prepend"}:
        edit.preflight_request_contract(
            intent.operation,
            has_body=intent.content is not None,
            frontmatter_changes=frontmatter_mapping(intent.frontmatter),
            target=intent.target,
            selector=intent.selector,
            scope=intent.scope,
        )
    elif intent.operation == "delete_section":
        edit.preflight_request_contract(
            intent.operation,
            frontmatter_changes=frontmatter_mapping(intent.frontmatter),
            target=intent.target,
            selector=intent.selector,
        )


def _resolve_body(
    vault_root: str,
    content: MutationContent | None,
) -> tuple[str, str | None]:
    if content is None:
        return "", None
    return resolve_mutation_content(vault_root, content)


def _payload(request, intent, result, staged_handle, staging_warning):
    findings, fixes, substitutions = wikilink_result_values(result)
    common = {
        "path": result["path"],
        "resolved_path": result["resolved_path"],
        "revision": result["revision"],
    }
    if request.RESULT_TYPE is DocumentWritePayload:
        return DocumentWritePayload(
            **common,
            operation=intent.result_operation,
            old_body_line_count=int(result["old_body_line_count"]),
            new_body_line_count=int(result["new_body_line_count"]),
            wikilink_warnings=findings,
            wikilink_fixes=fixes,
            wikilink_substitutions=substitutions,
            staged_handle_consumed=staged_handle is not None and staging_warning is None,
        )
    if request.RESULT_TYPE is DocumentPatchPayload:
        return DocumentPatchPayload(
            **common,
            match_count=int(result["match_count"]),
            replacement_count=int(result["replacement_count"]),
            old_body_line_count=int(result["old_body_line_count"]),
            new_body_line_count=int(result["new_body_line_count"]),
            wikilink_warnings=findings,
            wikilink_fixes=fixes,
            wikilink_substitutions=substitutions,
        )
    if request.RESULT_TYPE is DocumentEditPayload:
        raw_target = result.get("structural_target")
        if raw_target is None:
            raise RuntimeError("semantic document edit did not resolve a structural target")
        return DocumentEditPayload(
            **common,
            operation=intent.result_operation,
            old_body_line_count=int(result["old_body_line_count"]),
            new_body_line_count=int(result["new_body_line_count"]),
            structural_target=StructuralTargetResult(
                kind=raw_target["kind"],
                raw=raw_target["raw"],
                part=raw_target["scope"],
                display=raw_target["display"],
            ),
            wikilink_warnings=findings,
            wikilink_fixes=fixes,
            wikilink_substitutions=substitutions,
            staged_handle_consumed=staged_handle is not None and staging_warning is None,
        )
    if request.RESULT_TYPE is DocumentFrontmatterUpdatePayload:
        updated = tuple(item.name for item in intent.frontmatter if item.value is not None)
        removed = tuple(item.name for item in intent.frontmatter if item.value is None)
        return DocumentFrontmatterUpdatePayload(
            **common,
            updated_fields=updated,
            removed_fields=removed,
        )
    raise TypeError(f"unsupported document mutation result type: {request.RESULT_TYPE!r}")


def _revision_conflict(request_type, request, message: str) -> Error:
    document = request.document
    if document.resource.value == "artefact":
        next_action = CommandNextAction(
            "artefact.read",
            (CommandArgument("reference", document.reference),),
        )
    else:
        next_action = CommandNextAction(
            "resource.read",
            (
                CommandArgument("resource", document.resource.value),
                CommandArgument("reference", document.reference),
            ),
        )
    return Error(
        request_type.COMMAND_ID,
        request_type.COMMAND_VERSION,
        CommandError(
            ErrorCode.CONFLICT,
            message,
            RequestErrorDetails("expected_revision", message),
            next_action,
        ),
    )
