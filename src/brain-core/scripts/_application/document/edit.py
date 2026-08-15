"""Typed ``document.edit`` owner for structural Markdown mutation."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar, Literal, Mapping

from .._decoding import decode_bool, reject_unexpected
from .._document_mutation import (
    DocumentEditPayload,
    DocumentEditIntent,
    execute_document_mutation,
)
from .._mutation_support import (
    InlineContent,
    MutationContent,
    StagedContent,
    contributor_mutation_entry,
    decode_mutation_content,
)
from ..context import InvocationContext
from ._types import DocumentLocator, decode_document, validate_document_request


class HeadingPart(str, Enum):
    SECTION = "section"
    BODY = "body"
    INTRO = "intro"
    HEADING = "heading"


class CalloutPart(str, Enum):
    SECTION = "section"
    BODY = "body"
    HEADER = "header"


class InsertPosition(str, Enum):
    START = "start"
    END = "end"


@dataclass(frozen=True, slots=True)
class HeadingAncestor:
    text: str
    level: int | None = None
    occurrence: int | None = None
    kind: Literal["heading"] = field(default="heading", init=False)

    def __post_init__(self) -> None:
        _validate_heading_reference(self.text, self.level, self.occurrence)


@dataclass(frozen=True, slots=True)
class CalloutAncestor:
    callout_type: str
    title: str | None = None
    occurrence: int | None = None
    kind: Literal["callout"] = field(default="callout", init=False)

    def __post_init__(self) -> None:
        _validate_callout_reference(self.callout_type, self.title, self.occurrence)


StructuralAncestor = HeadingAncestor | CalloutAncestor


@dataclass(frozen=True, slots=True)
class DocumentIntroSelection:
    kind: Literal["document"] = field(default="document", init=False)
    part: Literal["intro"] = field(default="intro", init=False)


@dataclass(frozen=True, slots=True)
class HeadingSelection:
    text: str
    part: HeadingPart
    level: int | None = None
    occurrence: int | None = None
    ancestors: tuple[StructuralAncestor, ...] = ()
    kind: Literal["heading"] = field(default="heading", init=False)

    def __post_init__(self) -> None:
        _validate_heading_reference(self.text, self.level, self.occurrence)
        _validate_ancestors(self.ancestors)
        if not isinstance(self.part, HeadingPart):
            raise ValueError("heading selection part is invalid")


@dataclass(frozen=True, slots=True)
class CalloutSelection:
    callout_type: str
    part: CalloutPart
    title: str | None = None
    occurrence: int | None = None
    ancestors: tuple[StructuralAncestor, ...] = ()
    kind: Literal["callout"] = field(default="callout", init=False)

    def __post_init__(self) -> None:
        _validate_callout_reference(self.callout_type, self.title, self.occurrence)
        _validate_ancestors(self.ancestors)
        if not isinstance(self.part, CalloutPart):
            raise ValueError("callout selection part is invalid")


EditableSelection = DocumentIntroSelection | HeadingSelection | CalloutSelection


@dataclass(frozen=True, slots=True)
class HeadingBlockSelection:
    text: str
    level: int | None = None
    occurrence: int | None = None
    ancestors: tuple[StructuralAncestor, ...] = ()
    kind: Literal["heading"] = field(default="heading", init=False)

    def __post_init__(self) -> None:
        _validate_heading_reference(self.text, self.level, self.occurrence)
        _validate_ancestors(self.ancestors)


@dataclass(frozen=True, slots=True)
class CalloutBlockSelection:
    callout_type: str
    title: str | None = None
    occurrence: int | None = None
    ancestors: tuple[StructuralAncestor, ...] = ()
    kind: Literal["callout"] = field(default="callout", init=False)

    def __post_init__(self) -> None:
        _validate_callout_reference(self.callout_type, self.title, self.occurrence)
        _validate_ancestors(self.ancestors)


DeletableSelection = HeadingBlockSelection | CalloutBlockSelection


@dataclass(frozen=True, slots=True)
class ReplaceStructure:
    selection: EditableSelection
    content: MutationContent
    operation: Literal["replace"] = field(default="replace", init=False)

    def __post_init__(self) -> None:
        _validate_editable_selection(self.selection)
        _validate_content(self.content)


@dataclass(frozen=True, slots=True)
class InsertStructure:
    selection: EditableSelection
    position: InsertPosition
    content: MutationContent
    operation: Literal["insert"] = field(default="insert", init=False)

    def __post_init__(self) -> None:
        _validate_editable_selection(self.selection)
        if not isinstance(self.position, InsertPosition):
            raise ValueError("document.edit insert position is invalid")
        if (
            isinstance(self.selection, HeadingSelection)
            and self.selection.part is HeadingPart.HEADING
        ) or (
            isinstance(self.selection, CalloutSelection)
            and self.selection.part is CalloutPart.HEADER
        ):
            raise ValueError(
                "document.edit insert requires a content range, not a heading or "
                "callout header"
            )
        _validate_content(self.content)


@dataclass(frozen=True, slots=True)
class DeleteStructure:
    selection: DeletableSelection
    operation: Literal["delete"] = field(default="delete", init=False)

    def __post_init__(self) -> None:
        if not isinstance(
            self.selection,
            (HeadingBlockSelection, CalloutBlockSelection),
        ):
            raise ValueError("document.edit delete selection must be a complete structure")


DocumentStructuralChange = ReplaceStructure | InsertStructure | DeleteStructure


@dataclass(frozen=True, slots=True)
class DocumentEditRequest:
    COMMAND_ID: ClassVar[str] = "document.edit"
    COMMAND_VERSION: ClassVar[int] = 2
    RESULT_TYPE: ClassVar[type] = DocumentEditPayload
    FIELD_DESCRIPTIONS: ClassVar[dict[str, str]] = {
        "document": "Existing editable Brain document.",
        "expected_revision": "Revision returned by the most recent document read.",
        "change": "Replace, insert into, or delete a selected Markdown structure.",
        "fix_links": "Resolve safe wikilink substitutions for an artefact document.",
    }
    MINIMAL_EXAMPLE: ClassVar[dict[str, object]] = {
        "document": {"resource": "artefact", "reference": "Designs/Example.md"},
        "expected_revision": "sha256:" + "0" * 64,
        "change": {
            "operation": "replace",
            "selection": {"kind": "heading", "text": "Open Decisions", "level": 2, "part": "body"},
            "content": {"source": "inline", "content": "No open decisions.\n"},
        },
    }

    document: DocumentLocator
    expected_revision: str
    change: DocumentStructuralChange
    fix_links: bool = False

    def __post_init__(self) -> None:
        validate_document_request(self)
        if not isinstance(self.change, (ReplaceStructure, InsertStructure, DeleteStructure)):
            raise ValueError("document.edit change has an invalid variant")


def execute(context: InvocationContext, request: DocumentEditRequest):
    change = request.change
    target, selector, scope = _engine_selection(change.selection)
    if isinstance(change, ReplaceStructure):
        operation = "edit"
        content = change.content
        result_operation = "replace"
    elif isinstance(change, InsertStructure):
        operation = "prepend" if change.position is InsertPosition.START else "append"
        content = change.content
        result_operation = "insert"
    else:
        operation = "delete_section"
        content = None
        scope = None
        result_operation = "delete"
    return execute_document_mutation(
        context,
        request,
        DocumentEditIntent(
            resource=request.document.resource.value,
            reference=request.document.reference,
            expected_revision=request.expected_revision,
            operation=operation,
            result_operation=result_operation,
            content=content,
            target=target,
            selector=selector,
            scope=scope,
            fix_links=request.fix_links,
        ),
    )


def decode(payload: Mapping[str, object]) -> DocumentEditRequest:
    reject_unexpected(payload, {"document", "expected_revision", "change", "fix_links"})
    expected_revision = payload.get("expected_revision")
    if not isinstance(expected_revision, str):
        raise ValueError("expected_revision must be a string")
    raw_change = payload.get("change")
    if not isinstance(raw_change, Mapping):
        raise ValueError("change must be an object")
    return DocumentEditRequest(
        decode_document(payload.get("document")),
        expected_revision,
        _decode_change(raw_change),
        decode_bool(payload, "fix_links"),
    )


def _decode_change(value: Mapping[str, object]) -> DocumentStructuralChange:
    operation = value.get("operation")
    if operation in {"replace", "insert"}:
        allowed = {"operation", "selection", "content"}
        if operation == "insert":
            allowed.add("position")
        reject_unexpected(value, allowed, label=f"{operation} change fields")
        selection = _decode_editable_selection(value.get("selection"))
        content = decode_mutation_content(value.get("content"))
        if operation == "replace":
            return ReplaceStructure(selection, content)
        position = value.get("position")
        if not isinstance(position, str):
            raise ValueError("insert change position must be a string")
        try:
            return InsertStructure(selection, InsertPosition(position), content)
        except ValueError as exc:
            if position not in {item.value for item in InsertPosition}:
                raise ValueError("insert position must be start or end") from exc
            raise
    if operation == "delete":
        reject_unexpected(value, {"operation", "selection"}, label="delete change fields")
        return DeleteStructure(_decode_deletable_selection(value.get("selection")))
    raise ValueError("change operation must be replace, insert, or delete")


def _decode_editable_selection(value: object):
    if not isinstance(value, Mapping):
        raise ValueError("selection must be an object")
    kind = value.get("kind")
    if kind == "document":
        reject_unexpected(value, {"kind", "part"}, label="document selection fields")
        if value.get("part") != "intro":
            raise ValueError("document selection part must be intro")
        return DocumentIntroSelection()
    if kind == "heading":
        reject_unexpected(
            value,
            {"kind", "text", "level", "occurrence", "ancestors", "part"},
            label="heading selection fields",
        )
        part = value.get("part")
        if not isinstance(part, str):
            raise ValueError("heading selection part must be a string")
        try:
            return HeadingSelection(part=HeadingPart(part), **_heading_selection_fields(value))
        except ValueError as exc:
            if part not in {item.value for item in HeadingPart}:
                raise ValueError("heading part must be section, body, intro, or heading") from exc
            raise
    if kind == "callout":
        reject_unexpected(
            value,
            {"kind", "callout_type", "title", "occurrence", "ancestors", "part"},
            label="callout selection fields",
        )
        part = value.get("part")
        if not isinstance(part, str):
            raise ValueError("callout selection part must be a string")
        try:
            return CalloutSelection(part=CalloutPart(part), **_callout_selection_fields(value))
        except ValueError as exc:
            if part not in {item.value for item in CalloutPart}:
                raise ValueError("callout part must be section, body, or header") from exc
            raise
    raise ValueError("selection kind must be document, heading, or callout")


def _decode_deletable_selection(value: object):
    if not isinstance(value, Mapping):
        raise ValueError("selection must be an object")
    kind = value.get("kind")
    if kind == "document":
        raise ValueError("a complete document is not a deletable structure")
    if kind == "heading":
        reject_unexpected(
            value,
            {"kind", "text", "level", "occurrence", "ancestors"},
            label="heading selection fields",
        )
        return HeadingBlockSelection(**_heading_selection_fields(value))
    if kind == "callout":
        reject_unexpected(
            value,
            {"kind", "callout_type", "title", "occurrence", "ancestors"},
            label="callout selection fields",
        )
        return CalloutBlockSelection(**_callout_selection_fields(value))
    raise ValueError("selection kind must be heading or callout")


def _heading_selection_fields(value: Mapping[str, object]) -> dict[str, object]:
    text = value.get("text")
    if not isinstance(text, str):
        raise ValueError("heading selection text must be a string")
    return {
        "text": text,
        "level": _optional_int(value.get("level"), "heading level"),
        "occurrence": _optional_int(value.get("occurrence"), "heading occurrence"),
        "ancestors": _decode_ancestors(value.get("ancestors")),
    }


def _callout_selection_fields(value: Mapping[str, object]) -> dict[str, object]:
    callout_type = value.get("callout_type")
    title = value.get("title")
    if not isinstance(callout_type, str) or (
        title is not None and not isinstance(title, str)
    ):
        raise ValueError(
            "callout selection requires a string callout_type and optional title"
        )
    return {
        "callout_type": callout_type,
        "title": title,
        "occurrence": _optional_int(value.get("occurrence"), "callout occurrence"),
        "ancestors": _decode_ancestors(value.get("ancestors")),
    }


def _decode_ancestors(value: object) -> tuple[StructuralAncestor, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("selection ancestors must be a list")
    ancestors = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("selection ancestors must be objects")
        kind = item.get("kind")
        if kind == "heading":
            reject_unexpected(item, {"kind", "text", "level", "occurrence"}, label="heading ancestor fields")
            text = item.get("text")
            if not isinstance(text, str):
                raise ValueError("heading ancestor text must be a string")
            ancestors.append(
                HeadingAncestor(
                    text,
                    _optional_int(item.get("level"), "heading ancestor level"),
                    _optional_int(item.get("occurrence"), "heading ancestor occurrence"),
                )
            )
        elif kind == "callout":
            reject_unexpected(item, {"kind", "callout_type", "title", "occurrence"}, label="callout ancestor fields")
            callout_type = item.get("callout_type")
            title = item.get("title")
            if not isinstance(callout_type, str) or (title is not None and not isinstance(title, str)):
                raise ValueError("callout ancestor requires a string callout_type and optional title")
            ancestors.append(
                CalloutAncestor(
                    callout_type,
                    title,
                    _optional_int(item.get("occurrence"), "callout ancestor occurrence"),
                )
            )
        else:
            raise ValueError("ancestor kind must be heading or callout")
    return tuple(ancestors)


def _engine_selection(selection):
    if isinstance(selection, DocumentIntroSelection):
        return ":body", None, "intro"
    target = _reference_target(selection)
    selector = {
        "occurrence": selection.occurrence,
        "within": [
            {"target": _reference_target(ancestor), "occurrence": ancestor.occurrence}
            for ancestor in selection.ancestors
        ],
    }
    if isinstance(selection, (HeadingBlockSelection, CalloutBlockSelection)):
        return target, selector, "section"
    return target, selector, selection.part.value


def _reference_target(reference) -> str:
    if isinstance(reference, (HeadingAncestor, HeadingSelection, HeadingBlockSelection)):
        prefix = f"{'#' * reference.level} " if reference.level is not None else ""
        return f"{prefix}{reference.text}"
    title = f" {reference.title}" if reference.title else ""
    return f"[!{reference.callout_type}]{title}"


def _validate_heading_reference(text: str, level: int | None, occurrence: int | None) -> None:
    if not isinstance(text, str) or not text.strip() or "\n" in text:
        raise ValueError("heading text must be one non-empty line")
    if level is not None and (type(level) is not int or not 1 <= level <= 6):
        raise ValueError("heading level must be an integer from 1 to 6")
    _validate_occurrence(occurrence)


def _validate_callout_reference(callout_type: str, title: str | None, occurrence: int | None) -> None:
    if (
        not isinstance(callout_type, str)
        or not callout_type.strip()
        or any(character in callout_type for character in "]\n")
    ):
        raise ValueError("callout_type must be non-empty and cannot contain ']' or newlines")
    if title is not None and (not isinstance(title, str) or "\n" in title):
        raise ValueError("callout title must be one line or null")
    _validate_occurrence(occurrence)


def _validate_occurrence(value: int | None) -> None:
    if value is not None and (type(value) is not int or value < 1):
        raise ValueError("selection occurrence must be a positive integer")


def _validate_ancestors(value: tuple[StructuralAncestor, ...]) -> None:
    if not isinstance(value, tuple) or any(
        not isinstance(item, (HeadingAncestor, CalloutAncestor)) for item in value
    ):
        raise ValueError("selection ancestors must be typed structural references")


def _validate_editable_selection(value: EditableSelection) -> None:
    if not isinstance(value, (DocumentIntroSelection, HeadingSelection, CalloutSelection)):
        raise ValueError("document.edit selection is invalid")


def _validate_content(value: MutationContent) -> None:
    if not isinstance(value, (InlineContent, StagedContent)):
        raise ValueError("document.edit content has an invalid variant")


def _optional_int(value: object, label: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise ValueError(f"{label} must be an integer or null")
    return value


def catalogue_entry():
    return contributor_mutation_entry(DocumentEditRequest, execute)
