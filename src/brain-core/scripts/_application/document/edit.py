"""Typed ``document.edit`` owner across editable Brain documents."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Literal, Mapping

from .._document_edit import (
    DocumentEditPayload,
    EditScope,
    StructuralSelector,
    _decode_bool,
    _decode_optional_string,
    _reject_unexpected,
    decode_scope,
    decode_selector,
    execute_document_edit,
    validate_delete_request,
    validate_replace_request,
    validate_structural_request,
)
from .._mutation_support import (
    FrontmatterField,
    MutationContent,
    contributor_mutation_entry,
    decode_frontmatter,
    decode_mutation_content,
)
from ..context import InvocationContext


class DocumentResource(str, Enum):
    ARTEFACT = "artefact"
    MEMORY = "memory"
    SKILL = "skill"
    STYLE = "style"
    TEMPLATE = "template"


@dataclass(frozen=True, slots=True)
class DocumentTarget:
    FIELD_DESCRIPTIONS: ClassVar[dict[str, str]] = {
        "resource": "Kind of Brain document to edit.",
        "reference": "Artefact key/path or named memory, skill, style or template.",
    }

    resource: DocumentResource
    reference: str

    def __post_init__(self) -> None:
        if not isinstance(self.resource, DocumentResource):
            raise ValueError("document.edit target resource is invalid")
        if not isinstance(self.reference, str) or not self.reference.strip():
            raise ValueError("document.edit target reference must be non-empty")


@dataclass(frozen=True, slots=True)
class ReplaceChange:
    FIELD_DESCRIPTIONS: ClassVar[dict[str, str]] = {
        "operation": "Replace document content, frontmatter or a structural target.",
        "content": "Inline content or a staged-content handle.",
        "frontmatter": "Sorted frontmatter field updates.",
        "target": "Optional structural heading or section target.",
        "selector": "Optional occurrence and ancestor selector for the target.",
        "scope": "Structural interpretation of target.",
    }

    operation: Literal["replace"]
    content: MutationContent | None = None
    frontmatter: tuple[FrontmatterField, ...] = ()
    target: str | None = None
    selector: StructuralSelector | None = None
    scope: EditScope | None = None

    def __post_init__(self) -> None:
        if self.operation != "replace":
            raise ValueError("document.edit replace change has an invalid operation")
        validate_structural_request(self, subject_field=None)


@dataclass(frozen=True, slots=True)
class AppendChange:
    FIELD_DESCRIPTIONS: ClassVar[dict[str, str]] = {
        **ReplaceChange.FIELD_DESCRIPTIONS,
        "operation": "Append content at the selected document location.",
    }

    operation: Literal["append"]
    content: MutationContent | None = None
    frontmatter: tuple[FrontmatterField, ...] = ()
    target: str | None = None
    selector: StructuralSelector | None = None
    scope: EditScope | None = None

    def __post_init__(self) -> None:
        if self.operation != "append":
            raise ValueError("document.edit append change has an invalid operation")
        validate_structural_request(self, subject_field=None)


@dataclass(frozen=True, slots=True)
class PrependChange:
    FIELD_DESCRIPTIONS: ClassVar[dict[str, str]] = {
        **ReplaceChange.FIELD_DESCRIPTIONS,
        "operation": "Prepend content at the selected document location.",
    }

    operation: Literal["prepend"]
    content: MutationContent | None = None
    frontmatter: tuple[FrontmatterField, ...] = ()
    target: str | None = None
    selector: StructuralSelector | None = None
    scope: EditScope | None = None

    def __post_init__(self) -> None:
        if self.operation != "prepend":
            raise ValueError("document.edit prepend change has an invalid operation")
        validate_structural_request(self, subject_field=None)


@dataclass(frozen=True, slots=True)
class DeleteSectionChange:
    FIELD_DESCRIPTIONS: ClassVar[dict[str, str]] = {
        "operation": "Delete one selected document section.",
        "target": "Required heading or section target to delete.",
        "selector": "Optional occurrence and ancestor selector for the target.",
        "frontmatter": "Sorted frontmatter field updates applied with the deletion.",
    }

    operation: Literal["delete-section"]
    target: str
    selector: StructuralSelector | None = None
    frontmatter: tuple[FrontmatterField, ...] = ()

    def __post_init__(self) -> None:
        if self.operation != "delete-section":
            raise ValueError("document.edit delete-section change has an invalid operation")
        validate_delete_request(self, subject_field=None)


@dataclass(frozen=True, slots=True)
class ReplaceTextChange:
    FIELD_DESCRIPTIONS: ClassVar[dict[str, str]] = {
        "operation": "Replace exact text within the selected document scope.",
        "old_text": "Exact non-empty text to find.",
        "new_text": "Replacement text, which may be empty.",
        "target": "Optional structural target restricting the replacement.",
        "selector": "Optional occurrence and ancestor selector for the target.",
        "scope": "Structural interpretation of target.",
        "match_occurrence": "One-based exact match occurrence to replace.",
        "replace_all": "Replace every exact match instead of one occurrence.",
    }

    operation: Literal["replace-text"]
    old_text: str
    new_text: str
    target: str | None = None
    selector: StructuralSelector | None = None
    scope: EditScope | None = None
    match_occurrence: int | None = None
    replace_all: bool = False

    def __post_init__(self) -> None:
        if self.operation != "replace-text":
            raise ValueError("document.edit replace-text change has an invalid operation")
        validate_replace_request(self, subject_field=None)


DocumentChange = (
    ReplaceChange
    | AppendChange
    | PrependChange
    | DeleteSectionChange
    | ReplaceTextChange
)


@dataclass(frozen=True, slots=True)
class DocumentEditRequest:
    COMMAND_ID: ClassVar[str] = "document.edit"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = DocumentEditPayload
    FIELD_DESCRIPTIONS: ClassVar[dict[str, str]] = {
        "target": "Brain document resource and reference to edit.",
        "change": "One bounded replace, append, prepend, section-delete or text-replace change.",
        "fix_links": "Resolve safe wikilink substitutions while editing an artefact.",
    }
    MINIMAL_EXAMPLE: ClassVar[dict[str, object]] = {
        "target": {"resource": "artefact", "reference": "Designs/Example.md"},
        "change": {
            "operation": "replace",
            "content": {"source": "inline", "content": "# Example\n"},
        },
    }

    target: DocumentTarget
    change: DocumentChange
    fix_links: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.target, DocumentTarget):
            raise ValueError("document.edit target must be a DocumentTarget")
        if not isinstance(
            self.change,
            (ReplaceChange, AppendChange, PrependChange, DeleteSectionChange, ReplaceTextChange),
        ):
            raise ValueError("document.edit change has an invalid variant")
        if not isinstance(self.fix_links, bool):
            raise ValueError("document.edit fix_links must be a boolean")
        if self.fix_links and self.target.resource is not DocumentResource.ARTEFACT:
            raise ValueError("document.edit fix_links is available only for artefacts")


@dataclass(frozen=True, slots=True)
class _EditInvocation:
    COMMAND_ID: ClassVar[str] = DocumentEditRequest.COMMAND_ID
    COMMAND_VERSION: ClassVar[int] = DocumentEditRequest.COMMAND_VERSION
    RESULT_TYPE: ClassVar[type] = DocumentEditPayload

    subject: str
    content: MutationContent | None = None
    frontmatter: tuple[FrontmatterField, ...] = ()
    target: str | None = None
    selector: StructuralSelector | None = None
    scope: EditScope | None = None
    old_text: str = ""
    new_text: str = ""
    match_occurrence: int | None = None
    replace_all: bool = False
    fix_links: bool = False

    @property
    def path(self) -> str:
        return self.subject

    @property
    def name(self) -> str:
        return self.subject


def execute(context: InvocationContext, request: DocumentEditRequest):
    change = request.change
    operation = {
        "replace": "edit",
        "append": "append",
        "prepend": "prepend",
        "delete-section": "delete_section",
        "replace-text": "replace_text",
    }[change.operation]
    invocation = _EditInvocation(
        subject=request.target.reference,
        content=getattr(change, "content", None),
        frontmatter=getattr(change, "frontmatter", ()),
        target=getattr(change, "target", None),
        selector=getattr(change, "selector", None),
        scope=getattr(change, "scope", None),
        old_text=getattr(change, "old_text", ""),
        new_text=getattr(change, "new_text", ""),
        match_occurrence=getattr(change, "match_occurrence", None),
        replace_all=getattr(change, "replace_all", False),
        fix_links=request.fix_links,
    )
    subject_field = (
        "path"
        if request.target.resource is DocumentResource.ARTEFACT
        else "name"
    )
    return execute_document_edit(
        context,
        invocation,
        resource=request.target.resource.value,
        operation=operation,
        subject_field=subject_field,
        result_operation=change.operation,
    )


def decode(payload: Mapping[str, object]) -> DocumentEditRequest:
    _reject_unexpected(payload, {"target", "change", "fix_links"})
    raw_target = payload.get("target")
    if not isinstance(raw_target, Mapping):
        raise ValueError("target must be an object")
    _reject_unexpected(raw_target, {"resource", "reference"}, label="target")
    resource = raw_target.get("resource")
    reference = raw_target.get("reference")
    if not isinstance(resource, str) or not isinstance(reference, str):
        raise ValueError("target requires string resource and reference fields")
    try:
        target = DocumentTarget(DocumentResource(resource), reference)
    except ValueError as exc:
        raise ValueError(
            "target resource must be artefact, memory, skill, style, or template"
        ) from exc
    raw_change = payload.get("change")
    if not isinstance(raw_change, Mapping):
        raise ValueError("change must be an object")
    change = _decode_change(raw_change)
    return DocumentEditRequest(
        target,
        change,
        _decode_bool(payload, "fix_links", False),
    )


def _decode_change(payload: Mapping[str, object]) -> DocumentChange:
    operation = payload.get("operation")
    if operation in {"replace", "append", "prepend"}:
        _reject_unexpected(
            payload,
            {"operation", "content", "frontmatter", "target", "selector", "scope"},
            label="change",
        )
        raw_content = payload.get("content")
        values = {
            "operation": operation,
            "content": None if raw_content is None else decode_mutation_content(raw_content),
            "frontmatter": decode_frontmatter(payload.get("frontmatter")),
            "target": _decode_optional_string(payload.get("target"), "target"),
            "selector": decode_selector(payload.get("selector")),
            "scope": decode_scope(payload.get("scope")),
        }
        request_type = {
            "replace": ReplaceChange,
            "append": AppendChange,
            "prepend": PrependChange,
        }[operation]
        return request_type(**values)
    if operation == "delete-section":
        _reject_unexpected(
            payload,
            {"operation", "target", "selector", "frontmatter"},
            label="change",
        )
        target = payload.get("target")
        if not isinstance(target, str):
            raise ValueError("delete-section change target must be a string")
        return DeleteSectionChange(
            operation,
            target,
            decode_selector(payload.get("selector")),
            decode_frontmatter(payload.get("frontmatter")),
        )
    if operation == "replace-text":
        _reject_unexpected(
            payload,
            {
                "operation",
                "old_text",
                "new_text",
                "target",
                "selector",
                "scope",
                "match_occurrence",
                "replace_all",
            },
            label="change",
        )
        old_text = payload.get("old_text")
        new_text = payload.get("new_text")
        if not isinstance(old_text, str) or not isinstance(new_text, str):
            raise ValueError("replace-text change requires string old_text and new_text")
        occurrence = payload.get("match_occurrence")
        if occurrence is not None and (
            not isinstance(occurrence, int) or isinstance(occurrence, bool)
        ):
            raise ValueError("match_occurrence must be an integer or null")
        return ReplaceTextChange(
            operation,
            old_text,
            new_text,
            _decode_optional_string(payload.get("target"), "target"),
            decode_selector(payload.get("selector")),
            decode_scope(payload.get("scope")),
            occurrence,
            _decode_bool(payload, "replace_all", False),
        )
    raise ValueError(
        "change operation must be replace, append, prepend, delete-section, or replace-text"
    )


def catalogue_entry():
    return contributor_mutation_entry(DocumentEditRequest, execute)


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(DocumentEditRequest, decode)
