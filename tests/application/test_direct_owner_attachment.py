"""Trusted composition retains private owner state independently of command payloads."""

from contextlib import closing

import pytest

from _bootstrap.consent_owner import ConsentOwner, OwnerConnectionError
from _bootstrap.owner_attachment import OwnerAttachment, ProcessIdentity
from _command_interface.direct import DirectContextComposer, DirectContextError, initialise_attached_job_owner


def test_composer_reuses_attached_owner_but_close_does_not_close_job(command_vault_clone, tmp_path):
    root = command_vault_clone.vault_root
    with closing(ConsentOwner(root)) as owner:
        initialise_attached_job_owner(vault_root=root, owner_attachment=OwnerAttachment.for_job(owner),
            transport_identity=ProcessIdentity("cli-job", owner.identity.context_id))
        attachment = OwnerAttachment.for_job(owner)
        composer = DirectContextComposer(vault_root=root, owner_attachment=attachment)
        store = composer.owner_store
        snapshot = store.snapshot(("test-private-record",))
        assert store.compare_exchange(snapshot.versions, {"test-private-record": {"value": "one"}})
        first = composer.compose(command_id="command.list")
        second = composer.compose(command_id="command.list")
        assert first.invocation_id != second.invocation_id
        assert composer.owner_store is store
        assert composer.owner_kind == "cli-job"
        composer.close()
        composer.close()
        with pytest.raises(DirectContextError, match="closed"):
            composer.compose(command_id="command.list")
        fresh = OwnerAttachment.for_job(owner)
        try:
            assert fresh.connect(root).snapshot(("test-private-record",)).values["test-private-record"] == {"value": "one"}
        finally:
            fresh.close()


def test_foreign_owner_is_rejected_before_identity_or_provider_work(command_vault_clone, tmp_path, monkeypatch):
    root = command_vault_clone.vault_root
    foreign = tmp_path / "other-brain"
    foreign.mkdir()
    with closing(ConsentOwner(foreign)) as owner:
        attachment = OwnerAttachment.for_job(owner)
        def unexpected(*_args, **_kwargs):
            raise AssertionError("identity/provider work occurred before owner validation")
        monkeypatch.setattr(DirectContextComposer, "identity", unexpected)
        try:
            with pytest.raises(OwnerConnectionError, match="different Brain"):
                DirectContextComposer(vault_root=root, owner_attachment=attachment)
        finally:
            attachment.close()


def test_owner_absence_and_explicit_unavailability_remain_distinct(command_vault_clone):
    root = command_vault_clone.vault_root
    absent = DirectContextComposer(vault_root=root)
    unavailable = DirectContextComposer(vault_root=root, owner_unavailable_reason="unsupported-platform")
    assert absent.owner_store is None
    assert absent.owner_kind is None
    assert absent.owner_unavailable_reason is None
    assert unavailable.owner_store is None
    assert unavailable.owner_unavailable_reason == "unsupported-platform"
    assert unavailable.compose(command_id="command.list").selected_brain.vault_root == root
    absent.close()
    unavailable.close()
