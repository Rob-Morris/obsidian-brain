"""Typed ``resource.create`` owner for named configuration documents."""

from __future__ import annotations

from .._decoding import reject_unexpected

from dataclasses import dataclass
from typing import ClassVar, Literal, Mapping

from .._mutation_support import (
    FrontmatterField,
    InlineContent,
    MutationContent,
    StagedContent,
    contributor_mutation_entry,
    decode_frontmatter,
    decode_mutation_content,
)
from .._named_create import (
    NamedResourceCreatePayload,
    execute_named_create_values,
)
from ..context import InvocationContext


@dataclass(frozen=True, slots=True)
class MemoryCreateTarget:
    resource: Literal["memory"]
    name: str
    frontmatter: tuple[FrontmatterField, ...] = ()


@dataclass(frozen=True, slots=True)
class SkillCreateTarget:
    resource: Literal["skill"]
    name: str
    frontmatter: tuple[FrontmatterField, ...] = ()


@dataclass(frozen=True, slots=True)
class StyleCreateTarget:
    resource: Literal["style"]
    name: str
    frontmatter: tuple[FrontmatterField, ...] = ()


@dataclass(frozen=True, slots=True)
class TemplateCreateTarget:
    resource: Literal["template"]
    name: str


ResourceCreateTarget = (
    MemoryCreateTarget
    | SkillCreateTarget
    | StyleCreateTarget
    | TemplateCreateTarget
)


@dataclass(frozen=True, slots=True)
class ResourceCreateRequest:
    COMMAND_ID: ClassVar[str] = "resource.create"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = NamedResourceCreatePayload
    MINIMAL_EXAMPLE: ClassVar[dict[str, object]] = {
        "target": {"resource": "memory", "name": "example"},
        "content": {"source": "inline", "content": "Example"},
    }
    FIELD_DESCRIPTIONS: ClassVar[dict[str, str]] = {
        "target": "Named memory, skill, style or template to create; templates do not accept frontmatter.",
        "content": "Inline content or a Brain-owned staged content handle.",
    }

    target: ResourceCreateTarget
    content: MutationContent

    def __post_init__(self) -> None:
        if not isinstance(
            self.target,
            (
                MemoryCreateTarget,
                SkillCreateTarget,
                StyleCreateTarget,
                TemplateCreateTarget,
            ),
        ):
            raise ValueError("resource.create target has an invalid variant")
        expected_resource = {
            MemoryCreateTarget: "memory",
            SkillCreateTarget: "skill",
            StyleCreateTarget: "style",
            TemplateCreateTarget: "template",
        }[type(self.target)]
        if self.target.resource != expected_resource:
            raise ValueError(
                "resource.create target resource does not match its target variant"
            )
        if not isinstance(self.target.name, str) or not self.target.name.strip():
            raise ValueError("resource.create target name must be a non-empty string")
        if hasattr(self.target, "frontmatter"):
            frontmatter = self.target.frontmatter
            if not isinstance(frontmatter, tuple) or any(
                not isinstance(item, FrontmatterField) for item in frontmatter
            ):
                raise ValueError("resource.create frontmatter must be typed fields")
            names = tuple(item.name for item in frontmatter)
            if len(names) != len(set(names)) or names != tuple(sorted(names)):
                raise ValueError(
                    "resource.create frontmatter fields must be unique and sorted"
                )
        if not isinstance(self.content, (InlineContent, StagedContent)):
            raise ValueError("resource.create content must be inline or staged content")


def execute(context: InvocationContext, request: ResourceCreateRequest):
    frontmatter = getattr(request.target, "frontmatter", None)
    return execute_named_create_values(
        context,
        request,
        resource=request.target.resource,
        name=request.target.name,
        content=request.content,
        frontmatter=frontmatter,
    )

def decode(payload: Mapping[str, object]) -> ResourceCreateRequest:
    reject_unexpected(payload, {"target", "content"})
    target = payload.get("target")
    if not isinstance(target, Mapping):
        raise ValueError("target must be an object")
    resource = target.get("resource")
    name = target.get("name")
    if not isinstance(resource, str) or not isinstance(name, str):
        raise ValueError("target resource and name must be strings")
    if resource == "template":
        reject_unexpected(
            target,
            {"resource", "name"},
            label="template target fields",
        )
        typed_target: ResourceCreateTarget = TemplateCreateTarget(resource, name)
    elif resource in {"memory", "skill", "style"}:
        reject_unexpected(
            target,
            {"resource", "name", "frontmatter"},
            label=f"{resource} target fields",
        )
        frontmatter = decode_frontmatter(target.get("frontmatter"))
        target_type = {
            "memory": MemoryCreateTarget,
            "skill": SkillCreateTarget,
            "style": StyleCreateTarget,
        }[resource]
        typed_target = target_type(resource, name, frontmatter)
    else:
        raise ValueError("target resource must be memory, skill, style or template")
    return ResourceCreateRequest(
        typed_target,
        decode_mutation_content(payload.get("content")),
    )


def catalogue_entry():
    from dataclasses import replace

    return replace(
        contributor_mutation_entry(ResourceCreateRequest, execute),
        summary="Create one named memory, skill, style or template.",
    )
