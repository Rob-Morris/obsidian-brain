"""Typed ``resource.read`` owner for exact named Brain resources."""

from __future__ import annotations

from .._decoding import reject_unexpected

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import ClassVar, Literal, Mapping

from .._read_support import catalogue_entry as read_catalogue_entry
from .._read_support import command_error
from ..context import InvocationContext
from .._response_budget import (ContentRange, TextCursor, bounded_text_result,
    decode_text_cursor, validate_text_window, DEFAULT_TEXT_CHARACTERS, TEXT_WINDOW_DESCRIPTIONS)
from ..results import Error, ErrorCode, Ok
from ..type._classification import ArtefactTypeClassification
from ._types import SkillSource, TriggerCategory


class ReadableResource(str, Enum):
    MEMORY = "memory"
    PLUGIN = "plugin"
    SKILL = "skill"
    STYLE = "style"
    TEMPLATE = "template"
    TRIGGER = "trigger"
    TYPE = "type"


@dataclass(frozen=True, slots=True)
class MemoryReadItem:
    name: str
    triggers: tuple[str, ...]
    content: str
    revision: str
    range: ContentRange
    resource: Literal["memory"] = field(default="memory", init=False)


@dataclass(frozen=True, slots=True)
class PluginReadItem:
    name: str
    content: str
    revision: str
    range: ContentRange
    resource: Literal["plugin"] = field(default="plugin", init=False)


@dataclass(frozen=True, slots=True)
class SkillReadItem:
    name: str
    source: SkillSource
    content: str
    revision: str
    range: ContentRange
    resource: Literal["skill"] = field(default="skill", init=False)


@dataclass(frozen=True, slots=True)
class StyleReadItem:
    name: str
    content: str
    revision: str
    range: ContentRange
    resource: Literal["style"] = field(default="style", init=False)


@dataclass(frozen=True, slots=True)
class TemplateReadItem:
    type_key: str
    artefact_type: str
    path: str
    content: str
    revision: str
    range: ContentRange
    resource: Literal["template"] = field(default="template", init=False)


@dataclass(frozen=True, slots=True)
class TriggerReadItem:
    condition: str
    target: str
    category: TriggerCategory
    detail: str | None
    resource: Literal["trigger"] = field(default="trigger", init=False)


@dataclass(frozen=True, slots=True)
class TypeReadItem:
    key: str
    classification: ArtefactTypeClassification
    frontmatter_type: str
    folder: str
    path: str
    configured: bool
    taxonomy_path: str | None
    template_path: str | None
    definition: str | None
    revision: str | None
    range: ContentRange | None
    resource: Literal["type"] = field(default="type", init=False)


ResourceReadItem = (
    MemoryReadItem
    | PluginReadItem
    | SkillReadItem
    | StyleReadItem
    | TemplateReadItem
    | TriggerReadItem
    | TypeReadItem
)


@dataclass(frozen=True, slots=True)
class ResourceReadRequest:
    COMMAND_ID: ClassVar[str] = "resource.read"
    COMMAND_VERSION: ClassVar[int] = 3
    RESULT_TYPE: ClassVar[type] = ResourceReadItem
    FIELD_DESCRIPTIONS: ClassVar[dict[str, str]] = {
        **TEXT_WINDOW_DESCRIPTIONS,
        "resource": "Named resource collection: memory, plugin, skill, style, template, trigger or type.",
        "reference": "Exact resource name, type key or trigger condition.",
    }

    resource: ReadableResource
    reference: str
    cursor: TextCursor | None = None
    max_characters: int = DEFAULT_TEXT_CHARACTERS

    def __post_init__(self) -> None:
        validate_text_window(self.cursor, self.max_characters)
        if not isinstance(self.resource, ReadableResource):
            raise ValueError("resource.read resource is invalid")
        if not isinstance(self.reference, str) or not self.reference.strip():
            raise ValueError("resource.read reference must be a non-empty string")


def execute(context: InvocationContext, request: ResourceReadRequest):
    result = _READERS[request.resource](
        context.selected_brain.vault_root,
        request.reference,
    )
    if isinstance(result, Error):
        return result
    if isinstance(result, (MemoryReadItem, PluginReadItem, SkillReadItem,
                           StyleReadItem, TemplateReadItem)):
        field_name = "content"
        content = result.content
    elif isinstance(result, TypeReadItem) and result.definition is not None:
        field_name = "definition"
        content = result.definition
    else:
        if request.cursor is not None:
            return _error(ErrorCode.INVALID_REQUEST, "This resource has no document continuation.", "cursor")
        return Ok(request.COMMAND_ID, request.COMMAND_VERSION, result)
    return bounded_text_result(ResourceReadRequest, content, result.revision,
        cursor=request.cursor, max_characters=request.max_characters,
        payload=lambda text, window: replace(result, **{field_name: text, "range": window}))


def _full_range(content):
    return ContentRange(0, len(content), len(content), None)


def _error(code, message, field=None):
    return command_error(ResourceReadRequest, code, message, field)


def _read_memory(root, reference):
    from _common import PersistedDocumentContent
    from _portable.router_collections import read_memory_exact_from_vault

    try:
        result = read_memory_exact_from_vault(root, reference)
    except FileNotFoundError as exc:
        return _error(ErrorCode.CONFLICT, str(exc))
    if isinstance(result, dict):
        return _error(ErrorCode.NOT_FOUND, str(result["error"]), "reference")
    metadata, content = result
    if not isinstance(content, PersistedDocumentContent):
        return _error(ErrorCode.NOT_FOUND, content.message, "reference")
    return MemoryReadItem(
        metadata["name"],
        tuple(metadata.get("triggers") or ()),
        content,
        content.revision,
        _full_range(content),
    )

def _read_named(root, reference, resource, item_builder):
    from _common import PersistedDocumentContent
    from .._named_documents import read_portable

    try:
        result = read_portable(root, resource, reference)
    except FileNotFoundError as exc:
        return _error(ErrorCode.CONFLICT, str(exc))
    if isinstance(result, dict):
        return _error(ErrorCode.NOT_FOUND, str(result["error"]), "reference")
    metadata, content = result
    if not isinstance(content, PersistedDocumentContent):
        return _error(ErrorCode.NOT_FOUND, content.message, "reference")
    return item_builder(metadata, content, content.revision)


def _read_template(root, reference):
    from _common import MissingFileResult, PersistedDocumentContent
    from _portable.type_definitions import read_template_exact_from_vault

    try:
        result = read_template_exact_from_vault(root, reference)
    except (FileNotFoundError, ValueError) as exc:
        return _error(ErrorCode.CONFLICT, str(exc))
    if isinstance(result, dict):
        return _error(ErrorCode.NOT_FOUND, str(result["error"]), "reference")
    metadata, content = result
    if isinstance(content, MissingFileResult):
        return _error(ErrorCode.CONFLICT, content.message)
    if not isinstance(content, PersistedDocumentContent):
        raise TypeError("portable template reader returned non-persisted document text")
    return TemplateReadItem(
        metadata["key"],
        metadata["frontmatter_type"],
        metadata["template_file"],
        content,
        content.revision,
        _full_range(content),
    )


def _read_trigger(root, reference):
    from _portable.router_collections import read_trigger_exact_from_vault

    try:
        trigger = read_trigger_exact_from_vault(root, reference)
    except FileNotFoundError as exc:
        return _error(ErrorCode.CONFLICT, str(exc))
    except ValueError as exc:
        return _error(ErrorCode.CONFLICT, str(exc), "reference")
    if "error" in trigger:
        return _error(ErrorCode.NOT_FOUND, str(trigger["error"]), "reference")
    return TriggerReadItem(
        trigger["condition"],
        trigger["target"],
        TriggerCategory(trigger["category"]),
        trigger.get("detail"),
    )


def _read_type(root, reference):
    from _common import MissingFileResult
    from _portable.type_definitions import read_type_exact_from_vault

    try:
        result = read_type_exact_from_vault(root, reference)
    except (FileNotFoundError, ValueError) as exc:
        return _error(ErrorCode.CONFLICT, str(exc))
    if isinstance(result, dict):
        return _error(ErrorCode.NOT_FOUND, str(result["error"]), "reference")
    metadata, definition = result
    if isinstance(definition, MissingFileResult):
        return _error(ErrorCode.CONFLICT, definition.message)
    return TypeReadItem(
        metadata["key"],
        ArtefactTypeClassification(metadata["classification"]),
        metadata["frontmatter_type"],
        metadata["folder"],
        metadata["path"],
        bool(metadata["configured"]),
        metadata.get("taxonomy_file"),
        metadata.get("template_file"),
        definition,
        None if definition is None else definition.revision,
        None if definition is None else _full_range(definition),
    )


_READERS = {
    ReadableResource.MEMORY: _read_memory,
    ReadableResource.PLUGIN: lambda root, reference: _read_named(
        root,
        reference,
        "plugin",
        lambda metadata, content, revision: PluginReadItem(metadata["name"], content, revision, _full_range(content)),
    ),
    ReadableResource.SKILL: lambda root, reference: _read_named(
        root,
        reference,
        "skill",
        lambda metadata, content, revision: SkillReadItem(
            metadata["name"], SkillSource(metadata["source"]), content, revision, _full_range(content)
        ),
    ),
    ReadableResource.STYLE: lambda root, reference: _read_named(
        root,
        reference,
        "style",
        lambda metadata, content, revision: StyleReadItem(
            metadata["name"], content, revision, _full_range(content)
        ),
    ),
    ReadableResource.TEMPLATE: _read_template,
    ReadableResource.TRIGGER: _read_trigger,
    ReadableResource.TYPE: _read_type,
}


def decode(payload: Mapping[str, object]) -> ResourceReadRequest:
    reject_unexpected(payload, {"resource", "reference", "cursor", "max_characters"})
    try:
        resource = ReadableResource(payload.get("resource"))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "resource must be memory, plugin, skill, style, template, trigger or type"
        ) from exc
    reference = payload.get("reference")
    if not isinstance(reference, str):
        raise ValueError("reference must be a string")
    return ResourceReadRequest(resource, reference,
        decode_text_cursor(payload.get("cursor")),
        payload.get("max_characters", DEFAULT_TEXT_CHARACTERS))


def catalogue_entry():
    return replace(
        read_catalogue_entry(ResourceReadRequest, execute),
        summary="Read one memory, plugin, skill, style, template, trigger or type.",
    )
