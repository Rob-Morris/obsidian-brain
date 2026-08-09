"""Typed ``shaping.render-presentation`` owner."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Mapping

from .._document_rendering import (
    catalogue_entry as rendering_catalogue_entry,
    execute_render,
    optional_bool,
    reject_unexpected,
    require_non_empty,
)
from ..context import InvocationContext


class PresentationRenderStatus(str, Enum):
    PLANNED = "planned"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class PresentationRenderPayload:
    status: PresentationRenderStatus
    dry_run: bool
    path: str | None
    created: bool
    rendered: bool
    pdf_path: str | None
    theme_path: str | None
    preview_pid: int | None
    warning: str | None


@dataclass(frozen=True, slots=True)
class ShapingRenderPresentationRequest:
    COMMAND_ID: ClassVar[str] = "shaping.render-presentation"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = PresentationRenderPayload

    source: str
    slug: str
    render: bool = True
    preview: bool = True

    def __post_init__(self) -> None:
        require_non_empty(self.source, "source")
        require_non_empty(self.slug, "slug")
        if not isinstance(self.render, bool) or not isinstance(self.preview, bool):
            raise ValueError("render and preview must be booleans")


def execute(context: InvocationContext, request: ShapingRenderPresentationRequest):
    import shape_presentation

    params = {
        "source": request.source,
        "slug": request.slug,
        "render": request.render,
        "preview": request.preview,
    }
    return execute_render(
        context,
        request,
        operation="presentation rendering",
        invoke=lambda root: shape_presentation.shape(root, params),
        build_payload=_payload,
    )


def _payload(result: Mapping[str, object], *, dry_run: bool):
    return PresentationRenderPayload(
        (
            PresentationRenderStatus.PLANNED
            if dry_run
            else PresentationRenderStatus.COMPLETE
        ),
        dry_run,
        result.get("path") if isinstance(result.get("path"), str) else None,
        bool(result.get("created")),
        bool(result.get("rendered")),
        result.get("pdf_path") if isinstance(result.get("pdf_path"), str) else None,
        result.get("theme_path") if isinstance(result.get("theme_path"), str) else None,
        result.get("preview_pid") if isinstance(result.get("preview_pid"), int) else None,
        result.get("warning") if isinstance(result.get("warning"), str) else None,
    )


def decode(payload: Mapping[str, object]) -> ShapingRenderPresentationRequest:
    allowed = {"source", "slug", "render", "preview"}
    reject_unexpected(payload, allowed)
    return ShapingRenderPresentationRequest(
        require_non_empty(payload.get("source"), "source"),
        require_non_empty(payload.get("slug"), "slug"),
        optional_bool(payload.get("render"), "render", default=True),
        optional_bool(payload.get("preview"), "preview", default=True),
    )


def catalogue_entry():
    return rendering_catalogue_entry(ShapingRenderPresentationRequest, execute)


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(ShapingRenderPresentationRequest, decode)
