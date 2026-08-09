"""Typed ``shaping.render-printable`` owner."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Mapping

from .._document_rendering import (
    catalogue_entry as rendering_catalogue_entry,
    execute_render,
    optional_bool,
    optional_string,
    reject_unexpected,
    require_non_empty,
)
from ..context import InvocationContext


class PrintableRenderStatus(str, Enum):
    PLANNED = "planned"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class PrintableRenderPayload:
    status: PrintableRenderStatus
    dry_run: bool
    path: str | None
    created: bool
    rendered: bool
    pdf_path: str | None
    pdf_engine: str | None
    keep_heading_with_next: bool | None
    warning: str | None


@dataclass(frozen=True, slots=True)
class ShapingRenderPrintableRequest:
    COMMAND_ID: ClassVar[str] = "shaping.render-printable"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = PrintableRenderPayload

    source: str
    slug: str
    render: bool = True
    keep_heading_with_next: bool | None = None
    pdf_engine: str | None = None

    def __post_init__(self) -> None:
        require_non_empty(self.source, "source")
        require_non_empty(self.slug, "slug")
        if not isinstance(self.render, bool):
            raise ValueError("render must be a boolean")
        if self.keep_heading_with_next is not None and not isinstance(
            self.keep_heading_with_next,
            bool,
        ):
            raise ValueError("keep_heading_with_next must be a boolean")
        optional_string(self.pdf_engine, "pdf_engine")


def execute(context: InvocationContext, request: ShapingRenderPrintableRequest):
    import shape_printable

    params = {
        "source": request.source,
        "slug": request.slug,
        "render": request.render,
    }
    if request.keep_heading_with_next is not None:
        params["keep_heading_with_next"] = request.keep_heading_with_next
    if request.pdf_engine is not None:
        params["pdf_engine"] = request.pdf_engine
    return execute_render(
        context,
        request,
        operation="printable rendering",
        invoke=lambda root: shape_printable.shape(root, params),
        build_payload=_payload,
    )


def _payload(result: Mapping[str, object], *, dry_run: bool):
    return PrintableRenderPayload(
        PrintableRenderStatus.PLANNED if dry_run else PrintableRenderStatus.COMPLETE,
        dry_run,
        result.get("path") if isinstance(result.get("path"), str) else None,
        bool(result.get("created")),
        bool(result.get("rendered")),
        result.get("pdf_path") if isinstance(result.get("pdf_path"), str) else None,
        result.get("pdf_engine") if isinstance(result.get("pdf_engine"), str) else None,
        (
            result.get("keep_heading_with_next")
            if isinstance(result.get("keep_heading_with_next"), bool)
            else None
        ),
        result.get("warning") if isinstance(result.get("warning"), str) else None,
    )


def decode(payload: Mapping[str, object]) -> ShapingRenderPrintableRequest:
    allowed = {"source", "slug", "render", "keep_heading_with_next", "pdf_engine"}
    reject_unexpected(payload, allowed)
    return ShapingRenderPrintableRequest(
        require_non_empty(payload.get("source"), "source"),
        require_non_empty(payload.get("slug"), "slug"),
        optional_bool(payload.get("render"), "render", default=True),
        (
            None
            if payload.get("keep_heading_with_next") is None
            else optional_bool(
                payload.get("keep_heading_with_next"),
                "keep_heading_with_next",
                default=True,
            )
        ),
        optional_string(payload.get("pdf_engine"), "pdf_engine"),
    )


def catalogue_entry():
    return rendering_catalogue_entry(ShapingRenderPrintableRequest, execute)


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(ShapingRenderPrintableRequest, decode)
