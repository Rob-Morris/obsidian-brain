"""Owner behaviour for retry-safe staged mutation bodies."""

from __future__ import annotations

import _staging
import pytest

from _application.registry import current_application_catalogue, current_request_resolver
from _application.results import ErrorCode
from _application.stage.create import StageCreateRequest
from _application.stage.discard import StageDiscardRequest
from _application.types import Authority, EffectClass, RetryClass
from command_application import application_for


def test_stage_create_and_discard_return_typed_effects(command_vault_clone):
    application = application_for(command_vault_clone.vault_root)

    created = application.invoke(StageCreateRequest("# Staged\n\nBody."))

    assert created.status == "ok"
    assert created.result.handle.startswith("body:")
    assert created.result.bytes == len("# Staged\n\nBody.".encode("utf-8"))
    assert created.result.expires_in_seconds == _staging.STAGING_TTL_SECONDS
    assert created.committed_effects[0].kind == "stage.created"
    assert created.committed_effects[0].subject == created.result.handle
    assert (
        _staging.read_staged_body(
            str(command_vault_clone.vault_root),
            created.result.handle,
        )
        == "# Staged\n\nBody."
    )

    discarded = application.invoke(StageDiscardRequest(created.result.handle))
    absent = application.invoke(StageDiscardRequest(created.result.handle))

    assert discarded.result.discarded is True
    assert discarded.committed_effects[0].kind == "stage.discarded"
    assert absent.result.discarded is False
    assert absent.committed_effects == ()


def test_stage_dry_run_never_allocates_or_discards(command_vault_clone):
    dry_run = application_for(command_vault_clone.vault_root, dry_run=True)

    create = dry_run.invoke(StageCreateRequest("content"))

    assert create.error.code is ErrorCode.INVALID_REQUEST
    staging_dir = command_vault_clone.vault_root / _staging.STAGING_DIR
    assert not staging_dir.exists()

    handle = _staging.stage_body(str(command_vault_clone.vault_root), "keep")[
        "handle"
    ]
    discard = dry_run.invoke(StageDiscardRequest(handle))
    assert discard.status == "ok"
    assert discard.result.discarded is False
    assert _staging.read_staged_body(
        str(command_vault_clone.vault_root), handle
    ) == "keep"

    invalid = dry_run.invoke(StageDiscardRequest("not-a-handle"))
    assert invalid.error.code is ErrorCode.INVALID_REQUEST


def test_stage_authority_is_checked_before_filesystem_effects(command_vault_clone):
    class Denied:
        def allows(self, **_kwargs):
            return False

        def ceiling_allows(self, _command_id):
            return False

        def consume(self, _command_id):
            return False

    application = application_for(
        command_vault_clone.vault_root,
        authority=Denied(),
    )

    result = application.invoke(StageCreateRequest("denied"))

    assert result.error.code is ErrorCode.AUTHORITY_DENIED
    assert not (command_vault_clone.vault_root / _staging.STAGING_DIR).exists()


def test_stage_write_failure_after_commit_is_honestly_unknown(
    command_vault_clone,
    monkeypatch,
):
    real_stage_body = _staging.stage_body

    def commit_then_fail(vault_root, content):
        real_stage_body(vault_root, content)
        raise OSError("response path failed after staging")

    monkeypatch.setattr(_staging, "stage_body", commit_then_fail)
    application = application_for(command_vault_clone.vault_root)

    result = application.invoke(StageCreateRequest("uncertain"))

    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
    assert result.effects == "unknown"
    assert result.retryable is False
    assert result.outcome_reference.invocation_id == "inv-read"
    staging_dir = command_vault_clone.vault_root / _staging.STAGING_DIR
    assert list(staging_dir.glob("*.body"))


def test_stage_transport_and_catalogue_contracts_are_strict():
    resolver = current_request_resolver()
    create = resolver.resolve("stage.create", {"content": ""})
    discard = resolver.resolve("stage.discard", {"handle": "body:" + "a" * 32})

    assert type(create) is StageCreateRequest
    assert type(discard) is StageDiscardRequest
    for request in (create, discard):
        entry = current_application_catalogue().resolve(request)
        assert entry.authority is Authority.CONTRIBUTOR
        assert entry.effect_class is EffectClass.SELECTED_BRAIN_MUTATION
        assert entry.retry_class is RetryClass.RECEIPT_REQUIRED
    with pytest.raises(ValueError):
        resolver.resolve("stage.create", {"content": "body", "body_file": "x"})
    with pytest.raises(ValueError):
        resolver.resolve("stage.discard", {"handle": ""})
