"""Unsafe lock layouts refuse before entering command owners or changing data."""

from _application.results import ErrorCode
from _application.stage.create import StageCreateRequest
from command_application import application_for


def test_stage_refuses_unsafe_lock_before_planning(monkeypatch, tmp_path):
    import _application.stage.create as owner

    directory = tmp_path / ".brain/local"
    directory.mkdir(parents=True)
    ordinary = tmp_path / "ordinary"
    ordinary.write_text("unchanged")
    (directory / "mutation.lock").symlink_to(ordinary)
    application = application_for(tmp_path)

    def unexpected(*args, **kwargs):
        raise AssertionError("owner planning must not start")

    monkeypatch.setattr(owner, "prepare", unexpected)
    result = application.invoke(StageCreateRequest("valid content"))
    assert result.status == "error"
    assert result.error.code is ErrorCode.CONFLICT
    assert result.retryable is False
    assert result.effects == "none"
    assert ordinary.read_text() == "unchanged"
    assert not (directory / "staging").exists()


def test_explicit_preparation_layout_refusal_is_not_unknown(command_vault_clone):
    from _application.access.prepare import AccessPrepareRequest, PrepareCommand

    root = command_vault_clone.vault_root
    staging = root / ".brain/local/staging"
    staging.mkdir(parents=True, exist_ok=True)
    (staging / ("a" * 32 + ".body")).mkdir()
    application = application_for(root)
    result = application.invoke(AccessPrepareRequest(PrepareCommand(
        "stage.discard", {"handle": "body:" + "a" * 32},
    )))
    assert result.status == "error"
    assert result.error.code is ErrorCode.CONFLICT
    assert not result.retryable
    assert result.effects == "none"
    assert "filesystem layout" in result.error.next_action.instruction
