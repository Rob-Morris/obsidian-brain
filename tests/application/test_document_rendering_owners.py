"""Owner behaviour for managed printable and presentation rendering."""

from __future__ import annotations

import pytest

from _application.context import Capability
from _application.registry import current_application_catalogue, current_request_resolver
from _application.results import ErrorCode
from _application.shaping.render import (
    PresentationOutput,
    PrintableOutput,
    RenderStatus,
    ShapingRenderRequest,
)
from _application.types import (
    Authority,
    Availability,
    DependencyTier,
    EffectClass,
    RetryClass,
)
from command_application import application_for
import shape_presentation
import shape_printable


class _Renderer:
    provider_id = "document_renderer"


def _managed_application(root, *, dry_run=False):
    return application_for(
        root,
        dependency_tier=DependencyTier.MANAGED,
        providers=(_Renderer(),),
        capabilities=(Capability("document_renderer", Availability.AVAILABLE),),
        dry_run=dry_run,
    )


def test_printable_dry_run_does_not_invoke_renderer(command_vault_clone, monkeypatch):
    monkeypatch.setattr(
        shape_printable,
        "shape",
        lambda *_args, **_kwargs: pytest.fail("dry-run must not invoke renderer"),
    )

    result = _managed_application(
        command_vault_clone.vault_root,
        dry_run=True,
    ).invoke(
        ShapingRenderRequest(
            "Projects/Command Fixture.md",
            "board-brief",
            PrintableOutput("printable"),
        )
    )

    assert result.status == "ok"
    assert result.result.status is RenderStatus.PLANNED
    assert result.result.dry_run is True
    assert result.committed_effects == ()


def test_presentation_success_reports_markdown_pdf_and_preview_effects(
    command_vault_clone,
    monkeypatch,
):
    monkeypatch.setattr(
        shape_presentation,
        "shape",
        lambda _root, params: {
            "status": "ok",
            "path": "_Temporal/Presentations/deck.md",
            "created": True,
            "rendered": True,
            "pdf_path": "_Assets/Generated/Presentations/deck.pdf",
            "preview_pid": 321,
            "theme_path": "_Config/Skills/presentations/theme.css",
            "params": params,
        },
    )

    result = _managed_application(command_vault_clone.vault_root).invoke(
        ShapingRenderRequest(
            "Projects/Command Fixture.md",
            "deck",
            PresentationOutput("presentation", preview=True),
        )
    )

    assert result.status == "ok"
    assert result.result.status is RenderStatus.COMPLETE
    assert result.result.preview_pid == 321
    assert tuple(effect.subject for effect in result.committed_effects) == (
        "_Temporal/Presentations/deck.md",
        "_Assets/Generated/Presentations/deck.pdf",
        "preview-process:321",
    )


def test_printable_renderer_failure_after_creation_is_known_partial(
    command_vault_clone,
    monkeypatch,
):
    monkeypatch.setattr(
        shape_printable,
        "shape",
        lambda *_args, **_kwargs: {
            "status": "partial",
            "path": "_Temporal/Printables/brief.md",
            "created": True,
            "rendered": False,
            "warning": "pandoc not installed",
        },
    )

    result = _managed_application(command_vault_clone.vault_root).invoke(
        ShapingRenderRequest(
            "Projects/Command Fixture.md",
            "brief",
            PrintableOutput("printable"),
        )
    )

    assert result.status == "partial"
    assert result.error.code is ErrorCode.CONFLICT
    assert result.committed_effects[0].subject.endswith("brief.md")


def test_renderer_failure_without_committed_output_is_no_effect(
    command_vault_clone,
    monkeypatch,
):
    monkeypatch.setattr(
        shape_printable,
        "shape",
        lambda *_args, **_kwargs: {
            "status": "partial",
            "created": False,
            "rendered": False,
            "warning": "pandoc not installed",
        },
    )

    result = _managed_application(command_vault_clone.vault_root).invoke(
        ShapingRenderRequest(
            "Projects/Command Fixture.md",
            "brief",
            PrintableOutput("printable"),
        )
    )

    assert result.status == "error"
    assert result.effects == "none"


def test_rendering_requires_managed_renderer_provider(command_vault_clone):
    result = application_for(
        command_vault_clone.vault_root,
        dependency_tier=DependencyTier.MANAGED,
    ).invoke(
        ShapingRenderRequest(
            "Projects/source.md",
            "deck",
            PresentationOutput("presentation"),
        )
    )

    assert result.error.code is ErrorCode.CAPABILITY_UNAVAILABLE
    assert "provider:document_renderer" in result.error.details.missing


@pytest.mark.parametrize(
    ("kind", "payload", "wrong_field"),
    (
        (
            "presentation",
            {
                "source": "Projects/source.md",
                "slug": "deck",
                "output": {"kind": "presentation"},
            },
            "pdf_engine",
        ),
        (
            "printable",
            {
                "source": "Projects/source.md",
                "slug": "brief",
                "output": {"kind": "printable"},
            },
            "preview",
        ),
    ),
)
def test_render_transports_are_strict_managed_contributor_commands(
    kind,
    payload,
    wrong_field,
):
    request = current_request_resolver().resolve("shaping.render", payload)
    entry = current_application_catalogue().resolve(request)

    assert type(request) is ShapingRenderRequest
    assert request.output.kind == kind
    assert entry.dependency_tier is DependencyTier.MANAGED
    assert entry.required_providers == ("document_renderer",)
    assert entry.authority is Authority.CONTRIBUTOR
    assert entry.effect_class is EffectClass.SELECTED_BRAIN_MUTATION
    assert entry.retry_class is RetryClass.RECEIPT_REQUIRED
    with pytest.raises(ValueError, match="unexpected fields"):
        current_request_resolver().resolve(
            "shaping.render",
            {**payload, "output": {**payload["output"], wrong_field: True}},
        )
