"""Typed ``resource.search`` owner for named text resources."""

from __future__ import annotations

from .._decoding import reject_unexpected

from dataclasses import dataclass, replace
from enum import Enum
from typing import ClassVar, Mapping

from .._search_support import (
    DEFAULT_TOP_K,
    SearchPayload,
    catalogue_entry as search_catalogue_entry,
    resource_search,
    validate_query_and_limit,
)
from ..context import InvocationContext


class SearchableResource(str, Enum):
    MEMORY = "memory"
    PLUGIN = "plugin"
    SKILL = "skill"
    STYLE = "style"
    TRIGGER = "trigger"


@dataclass(frozen=True, slots=True)
class ResourceSearchRequest:
    COMMAND_ID: ClassVar[str] = "resource.search"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = SearchPayload
    FIELD_DESCRIPTIONS: ClassVar[dict[str, str]] = {
        "resource": "Named resource collection to search: memory, plugin, skill, style or trigger.",
        "query": "Non-empty text query matched against resource content.",
        "top_k": "Maximum number of results from 1 to 100.",
    }

    resource: SearchableResource
    query: str
    top_k: int = DEFAULT_TOP_K

    def __post_init__(self) -> None:
        if not isinstance(self.resource, SearchableResource):
            raise ValueError("resource.search resource is invalid")
        validate_query_and_limit(self.COMMAND_ID, self.query, self.top_k)


def execute(context: InvocationContext, request: ResourceSearchRequest):
    return resource_search(context, request, request.resource.value)


def decode(payload: Mapping[str, object]) -> ResourceSearchRequest:
    reject_unexpected(payload, {"resource", "query", "top_k"})
    try:
        resource = SearchableResource(payload.get("resource"))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "resource must be memory, plugin, skill, style or trigger"
        ) from exc
    return ResourceSearchRequest(
        resource,
        payload.get("query"),
        payload.get("top_k", DEFAULT_TOP_K),
    )


def catalogue_entry():
    return replace(
        search_catalogue_entry(ResourceSearchRequest, execute),
        summary="Search memories, plugins, skills, styles or triggers.",
    )
