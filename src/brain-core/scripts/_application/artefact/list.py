"""Typed ``artefact.list`` command and internal executor."""

from __future__ import annotations

from .._decoding import optional_string, reject_unexpected
from .._read_support import catalogue_entry as portable_reader_entry

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Mapping

from ..context import InvocationContext
from ..results import Error, ErrorCode, Ok, request_error


class ArtefactSort(str, Enum):
    DATE_DESC = "date_desc"
    DATE_ASC = "date_asc"
    MODIFIED_DESC = "modified_desc"
    MODIFIED_ASC = "modified_asc"
    TITLE = "title"


class ArtefactListLocation(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    ALL = "all"


@dataclass(frozen=True, slots=True)
class ArtefactListItem:
    reference: str
    path: str
    location: ArtefactListLocation
    title: str
    artefact_type: str
    created: str
    modified: str
    status: str
    frontmatter_key: str | None = None
    parent: str | None = None
    children_count: int | None = None
    archived_date: str | None = None


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
    COMMAND_VERSION: ClassVar[int] = 2
    RESULT_TYPE: ClassVar[type] = ArtefactListPayload

    location: ArtefactListLocation = ArtefactListLocation.ACTIVE
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
        if not isinstance(self.location, ArtefactListLocation):
            raise ValueError("artefact.list location must use ArtefactListLocation")
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
    from _portable.artefact_listing import list_combined_from_vault
    from _search.lexical_query import IndexNotFoundError

    try:
        snapshots = context.derived_snapshots
        page = list_combined_from_vault(
            context.selected_brain.vault_root,
            location=request.location.value,
            router=(snapshots.load_router() if snapshots is not None else None),
            index=(
                snapshots.load_lexical_index()
                if snapshots is not None
                and request.location
                in {ArtefactListLocation.ACTIVE, ArtefactListLocation.ALL}
                else None
            ),
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
                if item.get("location", "active") == "active"
                and item.get("key")
                and item["type"].startswith("living/")
                else item["path"]
            ),
            path=item["path"],
            location=ArtefactListLocation(item.get("location", "active")),
            title=item["title"],
            artefact_type=item["type"],
            created=item["created"],
            modified=item["modified"],
            status=item["status"],
            frontmatter_key=item.get("key"),
            parent=item.get("parent"),
            children_count=item.get("children_count"),
            archived_date=item.get("archiveddate"),
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
    return request_error(ArtefactListRequest, code, message, field)


def decode(payload: Mapping[str, object]) -> ArtefactListRequest:
    allowed = {
        "location",
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
    reject_unexpected(payload, allowed)
    page_size = payload.get("page_size", 500)
    if not isinstance(page_size, int) or isinstance(page_size, bool):
        raise ValueError("page_size must be an integer")
    sort = payload.get("sort", ArtefactSort.DATE_DESC.value)
    if not isinstance(sort, str):
        raise ValueError("sort must be a string")
    location = payload.get("location", ArtefactListLocation.ACTIVE.value)
    if not isinstance(location, str):
        raise ValueError("location must be a string")
    try:
        location_value = ArtefactListLocation(location)
    except ValueError as exc:
        raise ValueError("location must be active, archived, or all") from exc
    return ArtefactListRequest(
        location=location_value,
        type_filter=optional_string(payload.get("type_filter"), "type_filter"),
        since=optional_string(payload.get("since"), "since"),
        until=optional_string(payload.get("until"), "until"),
        modified_since=optional_string(
            payload.get("modified_since"), "modified_since"
        ),
        modified_until=optional_string(
            payload.get("modified_until"), "modified_until"
        ),
        tag=optional_string(payload.get("tag"), "tag"),
        parent=optional_string(payload.get("parent"), "parent"),
        sort=ArtefactSort(sort),
        cursor=optional_string(payload.get("cursor"), "cursor"),
        page_size=page_size,
    )


def catalogue_entry():
    return portable_reader_entry(ArtefactListRequest, execute)
