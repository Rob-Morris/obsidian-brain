"""Owner behaviour for retry-safe attachment uploads."""

from __future__ import annotations

import base64

import pytest
import upload_attachment

from _application.attachment.upload import AttachmentUploadRequest
from _application.attachment.upload import AttachmentDestinationKind
from _application.registry import current_application_catalogue, current_request_resolver
from _application.results import ErrorCode
from command_application import application_for


def _encoded(content: bytes) -> str:
    return base64.b64encode(content).decode("ascii")


def test_attachment_upload_is_typed_and_idempotent(command_vault_clone):
    application = application_for(command_vault_clone.vault_root)
    request = AttachmentUploadRequest(
        "project/command-fixture",
        "diagram.svg",
        _encoded(b"<svg/>"),
    )

    created = application.invoke(request)
    retried = application.invoke(request)

    expected_path = "_Assets/Attachments/project~command-fixture/diagram.svg"
    assert created.status == "ok"
    assert created.result.destination.kind is AttachmentDestinationKind.ARTEFACT
    assert created.result.destination.key == "project/command-fixture"
    assert created.result.path == expected_path
    assert created.result.embed == f"![[{expected_path}]]"
    assert created.result.bytes == 6
    assert created.result.created is True
    assert created.result.would_create is True
    assert created.result.dry_run is False
    assert created.committed_effects[0].subject == expected_path
    assert (command_vault_clone.vault_root / expected_path).read_bytes() == b"<svg/>"
    assert retried.result.created is False
    assert retried.committed_effects == ()


def test_attachment_upload_validates_before_effect_and_reports_collisions(
    command_vault_clone,
):
    application = application_for(command_vault_clone.vault_root)

    invalid = application.invoke(
        AttachmentUploadRequest("asset-key", "unsafe.md", "not base64")
    )
    assert invalid.error.code is ErrorCode.INVALID_REQUEST
    assert not (
        command_vault_clone.vault_root
        / "_Assets/Attachments/asset-key/unsafe.md"
    ).exists()

    first = AttachmentUploadRequest(
        "asset-key",
        "diagram.png",
        _encoded(b"first"),
    )
    collision = AttachmentUploadRequest(
        "asset-key",
        "diagram.png",
        _encoded(b"different"),
    )
    assert application.invoke(first).status == "ok"
    rejected = application.invoke(collision)
    assert rejected.error.code is ErrorCode.CONFLICT
    assert rejected.effects == "none"
    assert (
        command_vault_clone.vault_root
        / "_Assets/Attachments/asset-key/diagram.png"
    ).read_bytes() == b"first"


def test_attachment_upload_dry_run_returns_a_plan_without_writing(
    command_vault_clone,
):
    application = application_for(command_vault_clone.vault_root, dry_run=True)

    result = application.invoke(
        AttachmentUploadRequest(
            "asset-key",
            "planned.txt",
            _encoded(b"planned"),
        )
    )

    assert result.status == "ok"
    assert result.result.dry_run is True
    assert result.result.created is False
    assert result.result.would_create is True
    assert result.committed_effects == ()
    assert not (command_vault_clone.vault_root / result.result.path).exists()


def test_attachment_post_commit_failure_is_honestly_unknown(
    command_vault_clone,
    monkeypatch,
):
    real_upload = upload_attachment.apply_attachment_upload

    def commit_then_fail(*args, **kwargs):
        real_upload(*args, **kwargs)
        raise OSError("response failed after attachment commit")

    monkeypatch.setattr(upload_attachment, "apply_attachment_upload", commit_then_fail)
    application = application_for(command_vault_clone.vault_root)
    request = AttachmentUploadRequest(
        "asset-key",
        "uncertain.txt",
        _encoded(b"uncertain"),
    )

    result = application.invoke(request)

    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
    assert result.effects == "unknown"
    assert (
        command_vault_clone.vault_root
        / "_Assets/Attachments/asset-key/uncertain.txt"
    ).read_bytes() == b"uncertain"


def test_attachment_transport_is_inline_and_strict():
    resolver = current_request_resolver()
    request = resolver.resolve(
        "attachment.upload",
        {
            "destination_key": "asset-key",
            "name": "empty.bin",
            "content_base64": "",
        },
    )

    assert type(request) is AttachmentUploadRequest
    assert current_application_catalogue().resolve(request).command_id == (
        "attachment.upload"
    )
    with pytest.raises(ValueError, match="source_file"):
        resolver.resolve(
            "attachment.upload",
            {
                "destination_key": "asset-key",
                "name": "source.bin",
                "content_base64": "",
                "source_file": "/caller/file",
            },
        )
