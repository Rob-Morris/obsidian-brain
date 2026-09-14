"""Shared execution boundary for cohesive document mutation commands."""

from __future__ import annotations

from dataclasses import dataclass, replace

from ._mutation_support import (
    FrontmatterField,
    MutationContent,
    frontmatter_mapping,
    no_effect_error,
    resolve_mutation_content,
)
from ._wikilink_results import WikilinkFinding, WikilinkFix, wikilink_result_values
from .context import InvocationContext
from .preparation import (
    ObservedResource, admit_owner, bind_operation, canonical_json, content_digest,
    prepare_content,
)
from .receipts import CommittedEffect
from .results import (
    CommandArgument,
    CommandError,
    CommandNextAction,
    CommandWarning,
    Error,
    ErrorCode,
    Ok,
    Partial,
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
class DocumentWriteBodyPayload:
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
class DocumentReplaceTextPayload:
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
class DocumentStructuredEditPayload:
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
class DocumentWriteBodyIntent:
    resource: str
    reference: str
    expected_revision: str
    operation: str
    result_operation: str
    content: MutationContent
    fix_links: bool = False


@dataclass(frozen=True, slots=True)
class DocumentReplaceTextIntent:
    resource: str
    reference: str
    expected_revision: str
    old_text: str
    new_text: str
    match_occurrence: int | None
    replace_all: bool
    fix_links: bool = False


@dataclass(frozen=True, slots=True)
class DocumentStructuredEditIntent:
    resource: str
    reference: str
    expected_revision: str
    operation: str
    result_operation: str
    content: MutationContent | None
    target: str
    selector: dict | None
    scope: str | None
    fix_links: bool = False


@dataclass(frozen=True, slots=True)
class DocumentFrontmatterIntent:
    resource: str
    reference: str
    expected_revision: str
    frontmatter: tuple[FrontmatterField, ...]


DocumentMutationIntent = (
    DocumentWriteBodyIntent
    | DocumentReplaceTextIntent
    | DocumentStructuredEditIntent
    | DocumentFrontmatterIntent
)


class MutationOutcomeUncertain(RuntimeError):
    """A document mutation may have committed before its owner failed."""


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
    from _lifecycle.derived_cache_state import require_fresh_compiled_router
    from _staging import finalise_staged_body
    import edit
    import fix_links

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

    subject_field = "path" if intent.resource == "artefact" else "name"
    subject_kwargs = {subject_field: intent.reference}
    materialised = None
    try:
        with vault_mutation_lock(vault_root):
            router = require_fresh_compiled_router(vault_root)
            opened = edit.open_document(
                vault_root,
                router,
                intent.resource,
                intent.reference,
                allow_core_skill_read=(
                    intent.resource == "skill" and ":" not in intent.reference
                ),
            )
            current_revision = opened.revision
            validate_document_revision(
                intent.expected_revision,
                label="expected_revision",
            )
            if current_revision != intent.expected_revision:
                raise DocumentRevisionConflict(
                    "document changed since it was read; re-read it and retry "
                    f"with the current revision ({current_revision})"
                )
            body, staged_handle = _resolve_body(vault_root, intent, context=context)
            file_index = None
            if _may_contain_wikilinks(opened, intent, body):
                file_index = fix_links.file_index_for_mutation(vault_root)
            plan = edit.plan_document_edit(opened, body=body,
                                            **_plan_arguments(intent))
            admit_owner(context, request, document_binding, intent=intent,
                        opened=opened, body=body, file_index=file_index)
            if intent.resource == "skill" and ":" not in intent.reference:
                from _skill_library import materialise_core_skill_for_edit

                materialised = materialise_core_skill_for_edit(
                    vault_root,
                    name=intent.reference,
                    lock_held=True,
                )
                if materialised is not None:
                    from _portable.skill_resolution import skill_record

                    router = dict(router)
                    router["skills"] = [
                        skill_record(
                            intent.reference,
                            f"_Config/Skills/{intent.reference}/SKILL.md",
                            "user",
                        ),
                        *router.get("skills", ()),
                    ]
                    opened = edit.open_document(
                        vault_root, router, intent.resource, intent.reference
                    )
                    plan = replace(plan, opened=opened)
            try:
                result = edit.edit_resource(
                    vault_root,
                    router,
                    resource=intent.resource,
                    body=body,
                    file_index=file_index,
                    opened=opened,
                    prepared_plan=plan,
                    **_edit_arguments(intent),
                    **subject_kwargs,
                )
                staging_warning = finalise_staged_body(vault_root, staged_handle)
            except FileNotFoundError as exc:
                raise MutationOutcomeUncertain(
                    "document mutation outcome could not be classified"
                ) from exc
            except ValueError as exc:
                if materialised is not None:
                    raise MutationOutcomeUncertain(
                        "core skill materialisation committed before the edit failed"
                    ) from exc
                try:
                    revision_after_failure = edit.current_document_revision(
                        vault_root,
                        router,
                        intent.resource,
                        **subject_kwargs,
                    )
                except (OSError, ValueError) as probe_error:
                    raise MutationOutcomeUncertain(
                        "document mutation outcome could not be classified"
                    ) from probe_error
                if revision_after_failure != intent.expected_revision:
                    raise MutationOutcomeUncertain(
                        "document mutation outcome could not be classified"
                    ) from exc
                raise
    except DocumentRevisionConflict as exc:
        return _revision_conflict(type(request), request, str(exc))
    except MutationLockError as exc:
        return no_effect_error(
            type(request),
            ErrorCode.CONFLICT,
            public_mutation_error_message(exc),
            retryable=True,
        )
    except fix_links.WikilinkProcessingError as exc:
        message = str(exc)
        return Partial(
            request.COMMAND_ID,
            request.COMMAND_VERSION,
            CommandError(
                ErrorCode.CONFLICT,
                message,
                RequestErrorDetails(None, message),
                next_action=CommandNextAction(
                    "artefact.read",
                    (CommandArgument("reference", exc.path),),
                ),
            ),
            (CommittedEffect(request.COMMAND_ID, exc.path),),
            warnings=(
                CommandWarning(
                    WarningCode.FOLLOW_UP_REQUIRED,
                    "The document edit committed, but requested wikilink processing did not complete.",
                ),
            ),
        )
    except FileNotFoundError as exc:
        return no_effect_error(
            type(request), ErrorCode.NOT_FOUND, str(exc), "document"
        )
    except ValueError as exc:
        return no_effect_error(type(request), ErrorCode.INVALID_REQUEST, str(exc))

    payload = _payload(intent, result, staged_handle, staging_warning)
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
        committed_effects=(
            CommittedEffect(request.COMMAND_ID, payload.path),
            *(
                CommittedEffect(request.COMMAND_ID, path)
                for path in (
                    materialised.changed_paths if materialised is not None else ()
                )
                if path != payload.path
            ),
        ),
        warnings=tuple(warnings),
    )


def _preflight_request(edit, intent: DocumentMutationIntent) -> None:
    if isinstance(intent, DocumentWriteBodyIntent):
        edit.preflight_request_contract(
            intent.operation,
            has_body=True,
            target=":body",
            scope="section",
        )
    elif isinstance(intent, DocumentStructuredEditIntent):
        edit.preflight_request_contract(
            intent.operation,
            has_body=intent.content is not None,
            target=intent.target,
            selector=intent.selector,
            scope=intent.scope,
        )
    elif isinstance(intent, DocumentFrontmatterIntent):
        edit.preflight_request_contract(
            "edit",
            has_body=False,
            frontmatter_changes=frontmatter_mapping(intent.frontmatter),
        )


def _resolve_body(
    vault_root: str,
    intent: DocumentMutationIntent,
    *, context=None,
) -> tuple[str, str | None]:
    content = (
        intent.content
        if isinstance(intent, (DocumentWriteBodyIntent, DocumentStructuredEditIntent))
        else None
    )
    if content is None:
        return "", None
    return resolve_mutation_content(vault_root, content, context=context)


def _plan_arguments(intent):
    return {name: value for name, value in _edit_arguments(intent).items()
            if name != "fix_links"}


def document_binding(context, request, *, intent, opened, body,
                     file_index=None, frozen_inputs=None):
    observations = [ObservedResource("document", opened.path, opened.revision)]
    if opened.artefact is not None:
        observations.append(ObservedResource("definition", opened.path,
                                             content_digest(canonical_json(opened.artefact))))
    if getattr(intent, "fix_links", False) and file_index is not None:
        index = dict(file_index)
        index["md_relpaths"] = sorted(index.get("md_relpaths", ()))
        observations.append(ObservedResource("link-resolution", opened.path,
                                             content_digest(canonical_json(index))))
    if opened.resource == "skill" and ":" not in intent.reference:
        from pathlib import Path
        from _skill_library.packages import inspect_package

        package_root = Path(opened.abs_path).parent
        package = inspect_package(package_root, expected_name=intent.reference)
        observations.append(ObservedResource("package", opened.path, package.package_sha256))
        user_root = context.selected_brain.vault_root / "_Config" / "Skills" / intent.reference
        observations.append(ObservedResource("skill-substrate", intent.reference,
                                             "user" if user_root.exists() else "core"))
    observations.append(ObservedResource("body", opened.path, content_digest(body)))
    return bind_operation(request, observations=observations,
                          frozen_inputs=frozen_inputs,
                          review={"document": opened.path, "revision": opened.revision,
                                  "body_sha256": content_digest(body),
                                  "operation": getattr(intent, "result_operation", "frontmatter"),
                                  "fix_links": getattr(intent, "fix_links", False)})


def prepare_document_mutation(context, request, intent, *, frozen_inputs=None):
    from _common import DocumentRevisionConflict, vault_mutation_lock
    from _lifecycle.derived_cache_state import require_fresh_compiled_router
    import edit
    import fix_links

    root = str(context.selected_brain.vault_root)
    _preflight_request(edit, intent)
    frozen = dict(frozen_inputs or {})
    with vault_mutation_lock(root):
        router = require_fresh_compiled_router(root)
        opened = edit.open_document(root, router, intent.resource, intent.reference,
                                   allow_core_skill_read=(intent.resource == "skill" and ":" not in intent.reference))
        if opened.revision != intent.expected_revision:
            raise DocumentRevisionConflict("document changed; re-read it before preparing the operation")
        body, frozen = prepare_content(context, getattr(intent, "content", None), frozen)
        edit.plan_document_edit(opened, body=body, **_plan_arguments(intent))
        index = fix_links.file_index_for_mutation(root) if _may_contain_wikilinks(opened, intent, body) else None
        return document_binding(context, request, intent=intent, opened=opened,
                                body=body, file_index=index, frozen_inputs=frozen)


def _may_contain_wikilinks(opened, intent: DocumentMutationIntent, body: str) -> bool:
    if "[[" in opened.body or "[[" in repr(opened.fields) or "[[" in body:
        return True
    if isinstance(intent, DocumentReplaceTextIntent):
        return "[[" in intent.new_text
    if isinstance(intent, DocumentFrontmatterIntent):
        return "[[" in repr(frontmatter_mapping(intent.frontmatter))
    return False


def _edit_arguments(intent: DocumentMutationIntent) -> dict:
    if isinstance(intent, DocumentWriteBodyIntent):
        return {
            "operation": intent.operation,
            "target": ":body",
            "scope": "section",
            "fix_links": intent.fix_links,
        }
    if isinstance(intent, DocumentReplaceTextIntent):
        return {
            "operation": "replace_text",
            "old_text": intent.old_text,
            "new_text": intent.new_text,
            "match_occurrence": intent.match_occurrence,
            "replace_all": intent.replace_all,
            "fix_links": intent.fix_links,
        }
    if isinstance(intent, DocumentStructuredEditIntent):
        return {
            "operation": intent.operation,
            "target": intent.target,
            "selector": intent.selector,
            "scope": intent.scope,
            "fix_links": intent.fix_links,
        }
    return {
        "operation": "edit",
        "frontmatter_changes": frontmatter_mapping(intent.frontmatter),
    }


def _payload(intent, result, staged_handle, staging_warning):
    findings, fixes, substitutions = wikilink_result_values(result)
    common = {
        "path": result["path"],
        "resolved_path": result["resolved_path"],
        "revision": result["revision"],
    }
    if isinstance(intent, DocumentWriteBodyIntent):
        return DocumentWriteBodyPayload(
            **common,
            operation=intent.result_operation,
            old_body_line_count=int(result["old_body_line_count"]),
            new_body_line_count=int(result["new_body_line_count"]),
            wikilink_warnings=findings,
            wikilink_fixes=fixes,
            wikilink_substitutions=substitutions,
            staged_handle_consumed=staged_handle is not None and staging_warning is None,
        )
    if isinstance(intent, DocumentReplaceTextIntent):
        return DocumentReplaceTextPayload(
            **common,
            match_count=int(result["match_count"]),
            replacement_count=int(result["replacement_count"]),
            old_body_line_count=int(result["old_body_line_count"]),
            new_body_line_count=int(result["new_body_line_count"]),
            wikilink_warnings=findings,
            wikilink_fixes=fixes,
            wikilink_substitutions=substitutions,
        )
    if isinstance(intent, DocumentStructuredEditIntent):
        raw_target = result.get("structural_target")
        if raw_target is None:
            raise RuntimeError("semantic document edit did not resolve a structural target")
        return DocumentStructuredEditPayload(
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
    if isinstance(intent, DocumentFrontmatterIntent):
        updated = tuple(item.name for item in intent.frontmatter if item.value is not None)
        removed = tuple(item.name for item in intent.frontmatter if item.value is None)
        return DocumentFrontmatterUpdatePayload(
            **common,
            updated_fields=updated,
            removed_fields=removed,
        )
    raise TypeError(f"unsupported document mutation intent: {type(intent)!r}")


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
