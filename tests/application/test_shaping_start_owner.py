"""Owner behaviour for opening and continuing shaping sessions."""

from __future__ import annotations

import pytest

import start_shaping_session
from _application.registry import current_application_catalogue, current_request_resolver
from _application.results import ErrorCode
from _application.shaping.start import (
    ShapingMode,
    ShapingStartRequest,
    StatusBehaviour,
    TranscriptOperation,
)
from _application.types import Authority, EffectClass, RetryClass
from _common import PartialApplyError, parse_frontmatter
from command_application import application_for


DESIGN_REFERENCE = "design/command-fixture-design"


def test_shaping_start_creates_transcript_and_transitions_status(
    command_vault_clone,
):
    root = command_vault_clone.vault_root

    result = application_for(root).invoke(
        ShapingStartRequest(DESIGN_REFERENCE, ShapingMode.REFINE)
    )

    assert result.status == "ok"
    assert result.result.mode is ShapingMode.REFINE
    assert result.result.status_behaviour is StatusBehaviour.TRANSITION
    assert result.result.status_changed is True
    assert result.result.transcript_operation is TranscriptOperation.CREATED
    assert (root / result.result.transcript_path).is_file()
    fields, _body = parse_frontmatter((root / result.result.target_path).read_text())
    assert fields["status"] == "shaping"
    assert tuple(effect.subject for effect in result.committed_effects) == (
        result.result.changed_paths
    )


def test_shaping_start_continues_same_day_transcript(command_vault_clone):
    root = command_vault_clone.vault_root
    application = application_for(root)
    first = application.invoke(
        ShapingStartRequest(DESIGN_REFERENCE, ShapingMode.REFINE)
    )
    second = application.invoke(
        ShapingStartRequest(DESIGN_REFERENCE, ShapingMode.BRAINSTORM)
    )

    assert second.status == "ok"
    assert second.result.transcript_path == first.result.transcript_path
    assert second.result.transcript_operation is TranscriptOperation.APPENDED
    assert second.result.status_changed is False
    assert second.result.changed_paths == (second.result.transcript_path,)


def test_shaping_start_rejects_unshapeable_target_without_effect(
    command_vault_clone,
):
    result = application_for(command_vault_clone.vault_root).invoke(
        ShapingStartRequest("project/command-fixture", ShapingMode.REFINE)
    )

    assert result.error.code is ErrorCode.INVALID_REQUEST
    assert result.effects == "none"
    assert "not shapeable" in result.error.message


def test_shaping_start_requires_fresh_router(command_vault_clone):
    root = command_vault_clone.vault_root
    (root / ".brain/local/compiled-router.json").unlink()

    result = application_for(root).invoke(
        ShapingStartRequest(DESIGN_REFERENCE, ShapingMode.REFINE)
    )

    assert result.error.code is ErrorCode.CONFLICT
    assert result.effects == "none"


def test_shaping_start_rejects_dry_run_without_effect(command_vault_clone):
    result = application_for(
        command_vault_clone.vault_root,
        dry_run=True,
    ).invoke(ShapingStartRequest(DESIGN_REFERENCE, ShapingMode.REFINE))

    assert result.error.code is ErrorCode.INVALID_REQUEST
    assert result.effects == "none"


def test_shaping_start_preserves_known_partial_application(
    command_vault_clone,
    monkeypatch,
):
    monkeypatch.setattr(
        start_shaping_session,
        "start_shaping_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PartialApplyError("transcript created but backlink failed")
        ),
    )

    result = application_for(command_vault_clone.vault_root).invoke(
        ShapingStartRequest(DESIGN_REFERENCE, ShapingMode.REFINE)
    )

    assert result.status == "partial"
    assert result.error.code is ErrorCode.CONFLICT
    assert result.committed_effects[0].subject == DESIGN_REFERENCE


def test_shaping_start_transport_is_strict_contributor_command():
    request = current_request_resolver().resolve(
        "shaping.start",
        {"target": DESIGN_REFERENCE, "mode": "discover"},
    )
    entry = current_application_catalogue().resolve(request)

    assert type(request) is ShapingStartRequest
    assert request.mode is ShapingMode.DISCOVER
    assert entry.authority is Authority.CONTRIBUTOR
    assert entry.effect_class is EffectClass.SELECTED_BRAIN_MUTATION
    assert entry.retry_class is RetryClass.RECEIPT_REQUIRED
    with pytest.raises(ValueError, match="unexpected fields"):
        current_request_resolver().resolve(
            "shaping.start",
            {"target": DESIGN_REFERENCE, "mode": "refine", "title": "legacy"},
        )
    with pytest.raises(ValueError, match="mode must be"):
        current_request_resolver().resolve(
            "shaping.start",
            {"target": DESIGN_REFERENCE, "mode": "custom"},
        )
