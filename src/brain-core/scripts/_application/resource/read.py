"""Typed ``resource.read`` owner for exact named Brain resources."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import ClassVar, Literal, Mapping

from ..memory import read as memory_read
from ..plugin import read as plugin_read
from ..skill import read as skill_read
from ..style import read as style_read
from ..template import read as template_read
from ..trigger import read as trigger_read
from ..type import read as type_read
from .._read_support import catalogue_entry as read_catalogue_entry
from ..context import InvocationContext
from ._result import compose_result


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
    resource: Literal["memory"] = field(default="memory", init=False)


@dataclass(frozen=True, slots=True)
class PluginReadItem:
    name: str
    content: str
    resource: Literal["plugin"] = field(default="plugin", init=False)


@dataclass(frozen=True, slots=True)
class SkillReadItem:
    name: str
    source: skill_read.SkillSource
    content: str
    resource: Literal["skill"] = field(default="skill", init=False)


@dataclass(frozen=True, slots=True)
class StyleReadItem:
    name: str
    content: str
    resource: Literal["style"] = field(default="style", init=False)


@dataclass(frozen=True, slots=True)
class TemplateReadItem:
    type_key: str
    artefact_type: str
    path: str
    content: str
    resource: Literal["template"] = field(default="template", init=False)


@dataclass(frozen=True, slots=True)
class TriggerReadItem:
    condition: str
    target: str
    category: trigger_read.TriggerCategory
    detail: str | None
    resource: Literal["trigger"] = field(default="trigger", init=False)


@dataclass(frozen=True, slots=True)
class TypeReadItem:
    key: str
    classification: type_read.ArtefactTypeClassification
    frontmatter_type: str
    folder: str
    path: str
    configured: bool
    taxonomy_path: str | None
    template_path: str | None
    definition: str | None
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
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = ResourceReadItem
    FIELD_DESCRIPTIONS: ClassVar[dict[str, str]] = {
        "resource": "Named resource collection: memory, plugin, skill, style, template, trigger or type.",
        "reference": "Exact resource name, type key or trigger condition.",
    }

    resource: ReadableResource
    reference: str

    def __post_init__(self) -> None:
        if not isinstance(self.resource, ReadableResource):
            raise ValueError("resource.read resource is invalid")
        if not isinstance(self.reference, str) or not self.reference.strip():
            raise ValueError("resource.read reference must be a non-empty string")


_IMPLEMENTATIONS = {
    ReadableResource.MEMORY: (memory_read.MemoryReadRequest, memory_read.execute),
    ReadableResource.PLUGIN: (plugin_read.PluginReadRequest, plugin_read.execute),
    ReadableResource.SKILL: (skill_read.SkillReadRequest, skill_read.execute),
    ReadableResource.STYLE: (style_read.StyleReadRequest, style_read.execute),
    ReadableResource.TEMPLATE: (
        template_read.TemplateReadRequest,
        template_read.execute,
    ),
    ReadableResource.TRIGGER: (trigger_read.TriggerReadRequest, trigger_read.execute),
    ReadableResource.TYPE: (
        type_read.ArtefactTypeReadRequest,
        type_read.execute,
    ),
}


def execute(context: InvocationContext, request: ResourceReadRequest):
    request_type, executor = _IMPLEMENTATIONS[request.resource]
    result = executor(context, request_type(request.reference))
    aliases = {"condition": "reference"} if request.resource is ReadableResource.TRIGGER else None
    return compose_result(
        result,
        type(request),
        lambda payload: _item(request.resource, payload),
        field_aliases=aliases,
    )


def _item(resource: ReadableResource, item) -> ResourceReadItem:
    builders = {
        ReadableResource.MEMORY: lambda value: MemoryReadItem(
            value.name, value.triggers, value.content
        ),
        ReadableResource.PLUGIN: lambda value: PluginReadItem(
            value.name, value.content
        ),
        ReadableResource.SKILL: lambda value: SkillReadItem(
            value.name, value.source, value.content
        ),
        ReadableResource.STYLE: lambda value: StyleReadItem(
            value.name, value.content
        ),
        ReadableResource.TEMPLATE: lambda value: TemplateReadItem(
            value.type_key, value.artefact_type, value.path, value.content
        ),
        ReadableResource.TRIGGER: lambda value: TriggerReadItem(
            value.condition, value.target, value.category, value.detail
        ),
        ReadableResource.TYPE: lambda value: TypeReadItem(
            value.key,
            value.classification,
            value.frontmatter_type,
            value.folder,
            value.path,
            value.configured,
            value.taxonomy_path,
            value.template_path,
            value.definition,
        ),
    }
    return builders[resource](item)


def decode(payload: Mapping[str, object]) -> ResourceReadRequest:
    unexpected = sorted(set(payload) - {"resource", "reference"})
    if unexpected:
        raise ValueError(f"unexpected fields: {', '.join(unexpected)}")
    try:
        resource = ReadableResource(payload.get("resource"))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "resource must be memory, plugin, skill, style, template, trigger or type"
        ) from exc
    reference = payload.get("reference")
    if not isinstance(reference, str):
        raise ValueError("reference must be a string")
    return ResourceReadRequest(resource, reference)


def catalogue_entry():
    return replace(
        read_catalogue_entry(ResourceReadRequest, execute),
        summary="Read one memory, plugin, skill, style, template, trigger or type.",
    )


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(ResourceReadRequest, decode)
