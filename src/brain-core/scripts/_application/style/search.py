"""Typed ``style.search`` resource-text owner."""

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
class StyleSearchRequest:
    COMMAND_ID: ClassVar[str] = "style.search"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = SearchPayload
    query: str
    top_k: int = DEFAULT_TOP_K

    def __post_init__(self) -> None:
        validate_query_and_limit(self.COMMAND_ID, self.query, self.top_k)


def execute(context: InvocationContext, request: StyleSearchRequest):
    return resource_search(context, request, "style")


def decode(payload: Mapping[str, object]) -> StyleSearchRequest:
    return decode_query_and_limit(payload, StyleSearchRequest)


def catalogue_entry():
    return _catalogue_entry(StyleSearchRequest, execute)


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(StyleSearchRequest, decode)
