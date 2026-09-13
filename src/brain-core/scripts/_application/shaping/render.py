"""Typed ``shaping.render`` owner for presentation and printable output."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import ClassVar, Literal, Mapping

from .._decoding import optional_bool, reject_unexpected
from .._document_rendering import (
    catalogue_entry as rendering_catalogue_entry,
    execute_render,
    optional_string,
    require_non_empty,
)
from ..context import InvocationContext


@dataclass(frozen=True, slots=True)
class PresentationOutput:
    kind: Literal["presentation"]
    preview: bool = True

    def __post_init__(self) -> None:
        if self.kind != "presentation" or not isinstance(self.preview, bool):
            raise ValueError("presentation output requires kind and boolean preview")


@dataclass(frozen=True, slots=True)
class PrintableOutput:
    kind: Literal["printable"]
    keep_heading_with_next: bool | None = None
    pdf_engine: str | None = None

    def __post_init__(self) -> None:
        if self.kind != "printable":
            raise ValueError("printable output requires kind printable")
        if self.keep_heading_with_next is not None and not isinstance(
            self.keep_heading_with_next, bool
        ):
            raise ValueError("keep_heading_with_next must be a boolean")
        optional_string(self.pdf_engine, "pdf_engine")


RenderOutput = PresentationOutput | PrintableOutput


class RenderStatus(str, Enum):
    PLANNED = "planned"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class PresentationRenderPayload:
    kind: Literal["presentation"]
    status: RenderStatus
    dry_run: bool
    path: str | None
    created: bool
    rendered: bool
    pdf_path: str | None
    warning: str | None
    theme_path: str | None
    preview_pid: int | None


@dataclass(frozen=True, slots=True)
class PrintableRenderPayload:
    kind: Literal["printable"]
    status: RenderStatus
    dry_run: bool
    path: str | None
    created: bool
    rendered: bool
    pdf_path: str | None
    warning: str | None
    pdf_engine: str | None
    keep_heading_with_next: bool | None


ShapingRenderPayload = PresentationRenderPayload | PrintableRenderPayload


@dataclass(frozen=True, slots=True)
class ShapingRenderRequest:
    COMMAND_ID: ClassVar[str] = "shaping.render"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = ShapingRenderPayload
    MINIMAL_EXAMPLE: ClassVar[dict[str, object]] = {
        "source": "Designs/example.md",
        "slug": "example",
        "output": {"kind": "presentation"},
    }
    FIELD_DESCRIPTIONS: ClassVar[dict[str, str]] = {
        "source": "Vault-relative source artefact path.",
        "slug": "Output slug for the generated presentation or printable.",
        "render": "Whether to render the generated document to PDF.",
        "output": "Strict presentation or printable output options.",
    }

    source: str
    slug: str
    output: RenderOutput
    render: bool = True

    def __post_init__(self) -> None:
        require_non_empty(self.source, "source")
        require_non_empty(self.slug, "slug")
        if not isinstance(self.output, (PresentationOutput, PrintableOutput)):
            raise ValueError("shaping.render output has an invalid variant")
        if not isinstance(self.render, bool):
            raise ValueError("render must be a boolean")


def execute(context: InvocationContext, request: ShapingRenderRequest):
    if isinstance(request.output, PresentationOutput):
        import shape_presentation

        params = {
            "source": request.source,
            "slug": request.slug,
            "render": request.render,
            "preview": request.output.preview,
        }
        return execute_render(
            context,
            request,
            operation="presentation rendering",
            invoke=lambda root, **options: shape_presentation.shape(root, params, **options),
            build_payload=_presentation_payload,
        )

    import shape_printable

    params = {
        "source": request.source,
        "slug": request.slug,
        "render": request.render,
    }
    if request.output.keep_heading_with_next is not None:
        params["keep_heading_with_next"] = request.output.keep_heading_with_next
    if request.output.pdf_engine is not None:
        params["pdf_engine"] = request.output.pdf_engine
    return execute_render(
        context,
        request,
        operation="printable rendering",
        invoke=lambda root, **options: shape_printable.shape(root, params, **options),
        build_payload=_printable_payload,
    )

def _common(result: Mapping[str, object], dry_run: bool):
    return (
        RenderStatus.PLANNED if dry_run else RenderStatus.COMPLETE,
        dry_run,
        result.get("path") if isinstance(result.get("path"), str) else None,
        bool(result.get("created")),
        bool(result.get("rendered")),
        result.get("pdf_path") if isinstance(result.get("pdf_path"), str) else None,
        result.get("warning") if isinstance(result.get("warning"), str) else None,
    )


def _presentation_payload(result: Mapping[str, object], *, dry_run: bool):
    return PresentationRenderPayload(
        "presentation",
        *_common(result, dry_run),
        result.get("theme_path") if isinstance(result.get("theme_path"), str) else None,
        result.get("preview_pid") if isinstance(result.get("preview_pid"), int) else None,
    )


def _printable_payload(result: Mapping[str, object], *, dry_run: bool):
    return PrintableRenderPayload(
        "printable",
        *_common(result, dry_run),
        result.get("pdf_engine") if isinstance(result.get("pdf_engine"), str) else None,
        (
            result.get("keep_heading_with_next")
            if isinstance(result.get("keep_heading_with_next"), bool)
            else None
        ),
    )


def decode(payload: Mapping[str, object]) -> ShapingRenderRequest:
    reject_unexpected(payload, {"source", "slug", "render", "output"})
    output = payload.get("output")
    if not isinstance(output, Mapping):
        raise ValueError("output must be an object")
    kind = output.get("kind")
    if kind == "presentation":
        reject_unexpected(output, {"kind", "preview"})
        typed_output: RenderOutput = PresentationOutput(
            "presentation",
            optional_bool(output.get("preview"), "preview", default=True),
        )
    elif kind == "printable":
        reject_unexpected(
            output,
            {"kind", "keep_heading_with_next", "pdf_engine"},
        )
        keep = output.get("keep_heading_with_next")
        typed_output = PrintableOutput(
            "printable",
            (
                None
                if keep is None
                else optional_bool(
                    keep,
                    "keep_heading_with_next",
                    default=True,
                )
            ),
            optional_string(output.get("pdf_engine"), "pdf_engine"),
        )
    else:
        raise ValueError("output kind must be presentation or printable")
    return ShapingRenderRequest(
        require_non_empty(payload.get("source"), "source"),
        require_non_empty(payload.get("slug"), "slug"),
        typed_output,
        optional_bool(payload.get("render"), "render", default=True),
    )


def catalogue_entry():
    return replace(
        rendering_catalogue_entry(ShapingRenderRequest, execute),
        summary="Render a presentation or printable from one shaped artefact.",
    )
