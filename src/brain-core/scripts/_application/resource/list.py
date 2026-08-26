"""Typed ``resource.list`` owner for named Brain resources."""

from __future__ import annotations

from .._decoding import reject_unexpected

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import ClassVar, Literal, Mapping

from .._read_support import catalogue_entry as read_catalogue_entry
from .._read_support import command_error
from ..context import InvocationContext
from ..results import Error, ErrorCode, Ok
from ..type._classification import ArtefactTypeClassification
from ._types import SkillSource, TriggerCategory


class ListableResource(str, Enum):
    MEMORY = "memory"
    PLUGIN = "plugin"
    SKILL = "skill"
    STYLE = "style"
    TEMPLATE = "template"
    TRIGGER = "trigger"
    TYPE = "type"


@dataclass(frozen=True, slots=True)
class MemoryListItem:
    name: str
    triggers: tuple[str, ...]
    resource: Literal["memory"] = field(default="memory", init=False)


@dataclass(frozen=True, slots=True)
class PluginListItem:
    name: str
    resource: Literal["plugin"] = field(default="plugin", init=False)


@dataclass(frozen=True, slots=True)
class SkillListItem:
    name: str
    source: SkillSource
    effective: bool
    shadowed: bool
    resource: Literal["skill"] = field(default="skill", init=False)


@dataclass(frozen=True, slots=True)
class StyleListItem:
    name: str
    resource: Literal["style"] = field(default="style", init=False)


@dataclass(frozen=True, slots=True)
class TemplateListItem:
    type_key: str
    artefact_type: str
    path: str
    resource: Literal["template"] = field(default="template", init=False)


@dataclass(frozen=True, slots=True)
class TriggerListItem:
    condition: str
    target: str
    category: TriggerCategory
    detail: str | None
    resource: Literal["trigger"] = field(default="trigger", init=False)


@dataclass(frozen=True, slots=True)
class TypeListItem:
    key: str
    classification: ArtefactTypeClassification
    frontmatter_type: str
    configured: bool
    has_template: bool
    resource: Literal["type"] = field(default="type", init=False)


ResourceListItem = (
    MemoryListItem
    | PluginListItem
    | SkillListItem
    | StyleListItem
    | TemplateListItem
    | TriggerListItem
    | TypeListItem
)


@dataclass(frozen=True, slots=True)
class MemoryListPayload:
    items: tuple[MemoryListItem, ...]
    total: int
    resource: Literal["memory"] = field(default="memory", init=False)


@dataclass(frozen=True, slots=True)
class PluginListPayload:
    items: tuple[PluginListItem, ...]
    total: int
    resource: Literal["plugin"] = field(default="plugin", init=False)


@dataclass(frozen=True, slots=True)
class SkillListPayload:
    items: tuple[SkillListItem, ...]
    total: int
    resource: Literal["skill"] = field(default="skill", init=False)


@dataclass(frozen=True, slots=True)
class StyleListPayload:
    items: tuple[StyleListItem, ...]
    total: int
    resource: Literal["style"] = field(default="style", init=False)


@dataclass(frozen=True, slots=True)
class TemplateListPayload:
    items: tuple[TemplateListItem, ...]
    total: int
    resource: Literal["template"] = field(default="template", init=False)


@dataclass(frozen=True, slots=True)
class TriggerListPayload:
    items: tuple[TriggerListItem, ...]
    total: int
    resource: Literal["trigger"] = field(default="trigger", init=False)


@dataclass(frozen=True, slots=True)
class TypeListPayload:
    items: tuple[TypeListItem, ...]
    total: int
    resource: Literal["type"] = field(default="type", init=False)


ResourceListPayload = (
    MemoryListPayload
    | PluginListPayload
    | SkillListPayload
    | StyleListPayload
    | TemplateListPayload
    | TriggerListPayload
    | TypeListPayload
)


@dataclass(frozen=True, slots=True)
class ResourceListRequest:
    COMMAND_ID: ClassVar[str] = "resource.list"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = ResourceListPayload
    FIELD_DESCRIPTIONS: ClassVar[dict[str, str]] = {
        "resource": "Named resource collection: memory, plugin, skill, style, template, trigger or type.",
        "query": "Optional non-empty name filter; null lists the complete collection.",
    }

    resource: ListableResource
    query: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.resource, ListableResource):
            raise ValueError("resource.list resource is invalid")
        if self.query is not None and (
            not isinstance(self.query, str) or not self.query.strip()
        ):
            raise ValueError("resource.list query must be a non-empty string")


def execute(context: InvocationContext, request: ResourceListRequest):
    try:
        payload = _LISTERS[request.resource](
            context.selected_brain.vault_root,
            request.query,
        )
    except FileNotFoundError as exc:
        return command_error(ResourceListRequest, ErrorCode.CONFLICT, str(exc), None)
    if isinstance(payload, Error):
        return payload
    return Ok(request.COMMAND_ID, request.COMMAND_VERSION, payload)


def _list_memory(root, query):
    from _portable.router_collections import list_memories_from_vault

    resources = list_memories_from_vault(root, query)
    items = tuple(
        MemoryListItem(item["name"], tuple(item.get("triggers") or ()))
        for item in sorted(resources, key=lambda item: item["name"].casefold())
    )
    return MemoryListPayload(items, len(items))


def _list_named(root, query, resource, item_builder, payload_type):
    from .._named_documents import list_portable

    resources = list_portable(root, resource, query)
    items = tuple(
        item_builder(item)
        for item in sorted(resources, key=lambda item: item["name"].casefold())
    )
    return payload_type(items, len(items))


def _list_template(root, query):
    from _portable.type_definitions import list_templates_from_vault

    resources = list_templates_from_vault(root, query)
    items = tuple(
        TemplateListItem(item["name"], item["type"], item["template_file"])
        for item in sorted(resources, key=lambda item: item["name"].casefold())
    )
    return TemplateListPayload(items, len(items))


def _list_trigger(root, query):
    from _portable.router_collections import list_triggers_from_vault

    resources = list_triggers_from_vault(root, query)
    items = tuple(
        TriggerListItem(
            item["condition"],
            item["target"],
            TriggerCategory(item["category"]),
            item.get("detail"),
        )
        for item in sorted(
            resources,
            key=lambda item: (item["condition"].casefold(), item["target"].casefold()),
        )
    )
    return TriggerListPayload(items, len(items))


def _list_type(root, query):
    from _portable.type_definitions import list_types_from_vault

    resources = list_types_from_vault(root, query)
    items = tuple(
        TypeListItem(
            item["key"],
            ArtefactTypeClassification(item["classification"]),
            item["frontmatter_type"],
            bool(item["configured"]),
            bool(item.get("template_file")),
        )
        for item in sorted(resources, key=lambda item: item["key"].casefold())
    )
    return TypeListPayload(items, len(items))


_LISTERS = {
    ListableResource.MEMORY: _list_memory,
    ListableResource.PLUGIN: lambda root, query: _list_named(
        root,
        query,
        "plugin",
        lambda item: PluginListItem(item["name"]),
        PluginListPayload,
    ),
    ListableResource.SKILL: lambda root, query: _list_named(
        root,
        query,
        "skill",
        lambda item: SkillListItem(
            item["name"],
            SkillSource(item["source"]),
            bool(item["effective"]),
            bool(item["shadowed"]),
        ),
        SkillListPayload,
    ),
    ListableResource.STYLE: lambda root, query: _list_named(
        root,
        query,
        "style",
        lambda item: StyleListItem(item["name"]),
        StyleListPayload,
    ),
    ListableResource.TEMPLATE: _list_template,
    ListableResource.TRIGGER: _list_trigger,
    ListableResource.TYPE: _list_type,
}


def decode(payload: Mapping[str, object]) -> ResourceListRequest:
    reject_unexpected(payload, {"resource", "query"})
    try:
        resource = ListableResource(payload.get("resource"))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "resource must be memory, plugin, skill, style, template, trigger or type"
        ) from exc
    query = payload.get("query")
    if query is not None and not isinstance(query, str):
        raise ValueError("query must be a string")
    return ResourceListRequest(resource, query)


def catalogue_entry():
    return replace(
        read_catalogue_entry(ResourceListRequest, execute),
        summary="List memories, plugins, skills, styles, templates, triggers or types.",
    )
