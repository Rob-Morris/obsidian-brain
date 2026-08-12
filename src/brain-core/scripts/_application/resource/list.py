"""Typed ``resource.list`` owner for named Brain resources."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import ClassVar, Literal, Mapping

from ..memory import list as memory_list
from ..plugin import list as plugin_list
from ..skill import list as skill_list
from ..skill import read as skill_read
from ..style import list as style_list
from ..template import list as template_list
from ..trigger import list as trigger_list
from ..trigger import read as trigger_read
from ..type import list as type_list
from ..type import read as type_read
from .._read_support import catalogue_entry as read_catalogue_entry
from ..context import InvocationContext
from ._result import compose_result


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
    source: skill_read.SkillSource
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
    category: trigger_read.TriggerCategory
    detail: str | None
    resource: Literal["trigger"] = field(default="trigger", init=False)


@dataclass(frozen=True, slots=True)
class TypeListItem:
    key: str
    classification: type_read.ArtefactTypeClassification
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


_IMPLEMENTATIONS = {
    ListableResource.MEMORY: (memory_list.MemoryListRequest, memory_list.execute),
    ListableResource.PLUGIN: (plugin_list.PluginListRequest, plugin_list.execute),
    ListableResource.SKILL: (skill_list.SkillListRequest, skill_list.execute),
    ListableResource.STYLE: (style_list.StyleListRequest, style_list.execute),
    ListableResource.TEMPLATE: (
        template_list.TemplateListRequest,
        template_list.execute,
    ),
    ListableResource.TRIGGER: (trigger_list.TriggerListRequest, trigger_list.execute),
    ListableResource.TYPE: (
        type_list.ArtefactTypeListRequest,
        type_list.execute,
    ),
}


def execute(context: InvocationContext, request: ResourceListRequest):
    request_type, executor = _IMPLEMENTATIONS[request.resource]
    result = executor(context, request_type(request.query))
    return compose_result(
        result,
        type(request),
        lambda payload: _payload(request.resource, payload),
    )


def _payload(resource: ListableResource, payload) -> ResourceListPayload:
    builders = {
        ListableResource.MEMORY: lambda item: MemoryListItem(
            item.name, item.triggers
        ),
        ListableResource.PLUGIN: lambda item: PluginListItem(item.name),
        ListableResource.SKILL: lambda item: SkillListItem(item.name, item.source),
        ListableResource.STYLE: lambda item: StyleListItem(item.name),
        ListableResource.TEMPLATE: lambda item: TemplateListItem(
            item.type_key, item.artefact_type, item.path
        ),
        ListableResource.TRIGGER: lambda item: TriggerListItem(
            item.condition, item.target, item.category, item.detail
        ),
        ListableResource.TYPE: lambda item: TypeListItem(
            item.key,
            item.classification,
            item.frontmatter_type,
            item.configured,
            item.has_template,
        ),
    }
    items = tuple(builders[resource](item) for item in payload.items)
    payload_types = {
        ListableResource.MEMORY: MemoryListPayload,
        ListableResource.PLUGIN: PluginListPayload,
        ListableResource.SKILL: SkillListPayload,
        ListableResource.STYLE: StyleListPayload,
        ListableResource.TEMPLATE: TemplateListPayload,
        ListableResource.TRIGGER: TriggerListPayload,
        ListableResource.TYPE: TypeListPayload,
    }
    return payload_types[resource](items, payload.total)


def decode(payload: Mapping[str, object]) -> ResourceListRequest:
    unexpected = sorted(set(payload) - {"resource", "query"})
    if unexpected:
        raise ValueError(f"unexpected fields: {', '.join(unexpected)}")
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


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(ResourceListRequest, decode)
