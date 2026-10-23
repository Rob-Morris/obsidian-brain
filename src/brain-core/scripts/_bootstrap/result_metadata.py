"""Stdlib-only delivery metadata shared by application and transport projections."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class Delivery:
    """Describe delivery separately from the source command's execution outcome."""

    status: Literal["complete", "truncated", "unavailable", "out_of_range"] = "unavailable"
    availability: str = "unavailable"
    result_id: str | None = None
    view_id: str | None = None
    backing: str = "retained"
    consistency: str = "immutable-output"
    count_scope: str = "produced-output"
    unit: str | None = None
    requested_page_size: int | None = None
    page_size: int | None = None
    page: int | None = None
    first_item: int | None = None
    last_item: int | None = None
    total_items: int | None = None
    total_pages: int | None = None
    selection_id: str | None = None
    start_page: int | None = None
    max_pages: int | None = None
    end_page: int | None = None
    selected_pages: int | None = None
    remaining_pages: int | None = None
    has_more: bool = False
    pages_before_selection: int | None = None
    pages_after_selection: int | None = None
    has_more_outside_selection: bool = False
    continuation: dict | str | None = None
    outside_continuation: dict | str | None = None
    reason: str | None = None
    message: str | None = None
    source_revision: str | None = None
    change_notice: str | None = None
    requested_anchor: int | None = None
    overlap_notice: str | None = None
    superseded: bool = False


def compact_delivery(delivery: Delivery | dict) -> dict:
    """Keep navigation facts, omitting inapplicable values and redundant defaults."""
    values = asdict(delivery) if isinstance(delivery, Delivery) else dict(delivery)
    defaults = {"backing": "retained", "consistency": "immutable-output",
                "count_scope": "produced-output", "superseded": False}
    if values.get("start_page", 1) == 1 and values.get("max_pages") is None:
        for name in ("selection_id", "start_page", "end_page", "selected_pages",
                     "pages_before_selection", "pages_after_selection"):
            values.pop(name, None)
    if values.get("requested_page_size") == values.get("page_size"):
        values.pop("requested_page_size", None)
    compact = {key: value for key, value in values.items()
               if (value is not None or key == "continuation")
               and not (key in defaults and value == defaults[key])}
    compact.setdefault("continuation", None)
    return compact
