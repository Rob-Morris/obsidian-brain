"""Typed ``artefact.list`` command and internal executor."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Mapping

from ..context import InvocationContext
from ..results import CommandError, Error, ErrorCode, Ok, RequestErrorDetails
from ..types import (
    Authority,
    DependencyTier,
    EffectClass,
    Locality,
    Projection,
    ProjectionEligibility,
    RetryClass,
)


class ArtefactSort(str, Enum):
    DATE_DESC = "date_desc"
    DATE_ASC = "date_asc"
    MODIFIED_DESC = "modified_desc"
    MODIFIED_ASC = "modified_asc"
    TITLE = "title"


@dataclass(frozen=True, slots=True)
class ArtefactListItem:
    reference: str
    path: str
    title: str
    artefact_type: str
    created: str
    modified: str
    status: str
    frontmatter_key: str | None = None
    parent: str | None = None
    children_count: int | None = None


@dataclass(frozen=True, slots=True)
class ArtefactListPayload:
    items: tuple[ArtefactListItem, ...]
    total: int
    returned: int
    truncated: bool
    next_cursor: str | None
    omitted_missing_created: int


@dataclass(frozen=True, slots=True)
class ArtefactListRequest:
    COMMAND_ID: ClassVar[str] = "artefact.list"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = ArtefactListPayload

    type_filter: str | None = None
    since: str | None = None
    until: str | None = None
    modified_since: str | None = None
    modified_until: str | None = None
    tag: str | None = None
    parent: str | None = None
    sort: ArtefactSort = ArtefactSort.DATE_DESC
    cursor: str | None = None
    page_size: int = 500

    def __post_init__(self) -> None:
        for name in (
            "type_filter",
            "since",
            "until",
            "modified_since",
            "modified_until",
            "tag",
            "parent",
            "cursor",
        ):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"artefact.list {name} must be a non-empty string")
        if not isinstance(self.sort, ArtefactSort):
            raise ValueError("artefact.list sort must use ArtefactSort")
        if (
            not isinstance(self.page_size, int)
            or isinstance(self.page_size, bool)
            or not 1 <= self.page_size <= 500
        ):
            raise ValueError("artefact.list page_size must be between 1 and 500")


def execute(context: InvocationContext, request: ArtefactListRequest):
    from _common import artefact_type_prefix, make_artefact_key
    from _portable.artefact_listing import list_from_vault
    from _search.lexical_query import IndexNotFoundError

    try:
        page = list_from_vault(
            context.selected_brain.vault_root,
            type_filter=request.type_filter,
            since=request.since,
            until=request.until,
            modified_since=request.modified_since,
            modified_until=request.modified_until,
            tag=request.tag,
            parent=request.parent,
            top_k=request.page_size,
            sort=request.sort.value,
            cursor=request.cursor,
        )
    except (FileNotFoundError, IndexNotFoundError) as exc:
        return _error(ErrorCode.CONFLICT, str(exc), None)
    except (TypeError, ValueError) as exc:
        return _error(ErrorCode.INVALID_REQUEST, str(exc), None)
    items = tuple(
        ArtefactListItem(
            reference=(
                make_artefact_key(
                    artefact_type_prefix(item["type"]),
                    item["key"],
                )
                if item.get("key") and item["type"].startswith("living/")
                else item["path"]
            ),
            path=item["path"],
            title=item["title"],
            artefact_type=item["type"],
            created=item["created"],
            modified=item["modified"],
            status=item["status"],
            frontmatter_key=item.get("key"),
            parent=item.get("parent"),
            children_count=item.get("children_count"),
        )
        for item in page["items"]
    )
    return Ok(
        ArtefactListRequest.COMMAND_ID,
        ArtefactListRequest.COMMAND_VERSION,
        ArtefactListPayload(
            items=items,
            total=page["total"],
            returned=page["returned"],
            truncated=page["truncated"],
            next_cursor=page["next_cursor"],
            omitted_missing_created=page["omitted_missing_created"],
        ),
    )


def _error(code: ErrorCode, message: str, field: str | None) -> Error:
    return Error(
        ArtefactListRequest.COMMAND_ID,
        ArtefactListRequest.COMMAND_VERSION,
        CommandError(code, message, RequestErrorDetails(field, message)),
    )


def _optional_string(payload: Mapping[str, object], name: str) -> str | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def decode(payload: Mapping[str, object]) -> ArtefactListRequest:
    allowed = {
        "type_filter",
        "since",
        "until",
        "modified_since",
        "modified_until",
        "tag",
        "parent",
        "sort",
        "cursor",
        "page_size",
    }
    unexpected = sorted(set(payload) - allowed)
    if unexpected:
        raise ValueError(f"unexpected fields: {', '.join(unexpected)}")
    page_size = payload.get("page_size", 500)
    if not isinstance(page_size, int) or isinstance(page_size, bool):
        raise ValueError("page_size must be an integer")
    sort = payload.get("sort", ArtefactSort.DATE_DESC.value)
    if not isinstance(sort, str):
        raise ValueError("sort must be a string")
    return ArtefactListRequest(
        type_filter=_optional_string(payload, "type_filter"),
        since=_optional_string(payload, "since"),
        until=_optional_string(payload, "until"),
        modified_since=_optional_string(payload, "modified_since"),
        modified_until=_optional_string(payload, "modified_until"),
        tag=_optional_string(payload, "tag"),
        parent=_optional_string(payload, "parent"),
        sort=ArtefactSort(sort),
        cursor=_optional_string(payload, "cursor"),
        page_size=page_size,
    )


def catalogue_entry():
    from ..catalogue import ApplicationEntry

    return ApplicationEntry(
        request_type=ArtefactListRequest,
        executor=execute,
        dependency_tier=DependencyTier.PORTABLE,
        locality=Locality.SELECTED_BRAIN_LOCAL,
        required_providers=(),
        optional_providers=(),
        authority=Authority.READER,
        effect_class=EffectClass.NONE,
        retry_class=RetryClass.SAFE,
        projections=tuple(
            ProjectionEligibility(projection, True)
            for projection in (
                Projection.MCP,
                Projection.CLI,
                Projection.SCRIPT,
                Projection.PYTHON,
            )
        ),
    )


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(ArtefactListRequest, decode)
