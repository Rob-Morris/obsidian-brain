"""Stdlib-only delivery metadata shared by application and transport projections."""
from __future__ import annotations

from dataclasses import dataclass
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
    source_lines: int | None = None
    fragments: int | None = None
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
    continuation: dict | None = None
    outside_continuation: dict | None = None
    reason: str | None = None
    message: str | None = None
    source_revision: str | None = None
    change_notice: str | None = None
    requested_anchor: int | None = None
    overlap_notice: str | None = None
    superseded: bool = False
