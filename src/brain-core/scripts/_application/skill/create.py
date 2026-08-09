"""Typed ``skill.create`` owner."""

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._mutation_support import FrontmatterField, MutationContent
from .._named_create import (
    NamedResourceCreatePayload,
    catalogue_entry as _catalogue_entry,
    decode_named_create,
    execute_named_create,
    validate_named_create_request,
)
from ..context import InvocationContext


@dataclass(frozen=True, slots=True)
class SkillCreateRequest:
    COMMAND_ID: ClassVar[str] = "skill.create"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = NamedResourceCreatePayload
    name: str
    content: MutationContent
    frontmatter: tuple[FrontmatterField, ...] = ()

    def __post_init__(self) -> None:
        validate_named_create_request(self)


def execute(context: InvocationContext, request: SkillCreateRequest):
    return execute_named_create(context, request, resource="skill")


def decode(payload: Mapping[str, object]) -> SkillCreateRequest:
    return decode_named_create(payload, SkillCreateRequest)


def catalogue_entry():
    return _catalogue_entry(SkillCreateRequest, execute)


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(SkillCreateRequest, decode)
