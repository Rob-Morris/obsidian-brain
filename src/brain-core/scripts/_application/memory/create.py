"""Typed ``memory.create`` owner."""

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
class MemoryCreateRequest:
    COMMAND_ID: ClassVar[str] = "memory.create"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = NamedResourceCreatePayload

    name: str
    content: MutationContent
    frontmatter: tuple[FrontmatterField, ...] = ()

    def __post_init__(self) -> None:
        validate_named_create_request(self)


def execute(context: InvocationContext, request: MemoryCreateRequest):
    return execute_named_create(context, request, resource="memory")


def decode(payload: Mapping[str, object]) -> MemoryCreateRequest:
    return decode_named_create(payload, MemoryCreateRequest)


def catalogue_entry():
    return _catalogue_entry(MemoryCreateRequest, execute)


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(MemoryCreateRequest, decode)
