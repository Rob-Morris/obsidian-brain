"""Typed ``style.create`` owner."""

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
class StyleCreateRequest:
    COMMAND_ID: ClassVar[str] = "style.create"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = NamedResourceCreatePayload
    name: str
    content: MutationContent
    frontmatter: tuple[FrontmatterField, ...] = ()

    def __post_init__(self) -> None:
        validate_named_create_request(self)


def execute(context: InvocationContext, request: StyleCreateRequest):
    return execute_named_create(context, request, resource="style")


def decode(payload: Mapping[str, object]) -> StyleCreateRequest:
    return decode_named_create(payload, StyleCreateRequest)


def catalogue_entry():
    return _catalogue_entry(StyleCreateRequest, execute)


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(StyleCreateRequest, decode)
