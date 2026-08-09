"""Typed ``skill.search`` resource-text owner."""

from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._search_support import (
    DEFAULT_TOP_K,
    SearchPayload,
    catalogue_entry as _catalogue_entry,
    decode_query_and_limit,
    resource_search,
    validate_query_and_limit,
)
from ..context import InvocationContext


@dataclass(frozen=True, slots=True)
class SkillSearchRequest:
    COMMAND_ID: ClassVar[str] = "skill.search"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = SearchPayload
    query: str
    top_k: int = DEFAULT_TOP_K

    def __post_init__(self) -> None:
        validate_query_and_limit(self.COMMAND_ID, self.query, self.top_k)


def execute(context: InvocationContext, request: SkillSearchRequest):
    return resource_search(context, request, "skill")


def decode(payload: Mapping[str, object]) -> SkillSearchRequest:
    return decode_query_and_limit(payload, SkillSearchRequest)


def catalogue_entry():
    return _catalogue_entry(SkillSearchRequest, execute)


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(SkillSearchRequest, decode)
