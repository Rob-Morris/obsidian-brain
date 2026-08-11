"""Owner behaviour for artefact-library type install and sync."""

from __future__ import annotations

import sync_definitions
import pytest

from _application.registry import current_application_catalogue, current_request_resolver
from _application.results import ErrorCode
from _application.type.sync import TypeSyncRequest
from _application.types import Authority, EffectClass, RetryClass
from command_application import application_for


TYPE_KEY = "living/journals"
TAXONOMY_PATH = "_Config/Taxonomy/Living/journals.md"
TEMPLATE_PATH = "_Config/Templates/Living/Journals.md"


def test_type_sync_installs_when_absent_and_is_structural(command_vault_clone):
    root = command_vault_clone.vault_root
    result = application_for(root).invoke(TypeSyncRequest(TYPE_KEY))

    assert result.status == "ok"
    assert result.result.type_key == TYPE_KEY
    assert result.result.forced is False
    assert result.result.dry_run is False
    assert {(item.role, item.target, item.action) for item in result.result.updated} == {
        ("taxonomy", TAXONOMY_PATH, "new"),
        ("template", TEMPLATE_PATH, "new"),
    }
    assert result.committed_effects[0].kind == "type.sync"
    assert result.committed_effects[0].subject == TYPE_KEY
    assert (root / TAXONOMY_PATH).is_file()
    assert (root / TEMPLATE_PATH).is_file()
    assert (root / "Journals").is_dir()

    duplicate = application_for(root).invoke(TypeSyncRequest(TYPE_KEY))
    assert duplicate.status == "ok"
    assert duplicate.result.updated == ()
    assert duplicate.committed_effects == ()


def test_type_sync_install_dry_run_reports_without_writing(command_vault_clone):
    root = command_vault_clone.vault_root
    result = application_for(root, dry_run=True).invoke(TypeSyncRequest(TYPE_KEY))

    assert result.status == "ok"
    assert result.result.dry_run is True
    assert len(result.result.updated) == 2
    assert result.committed_effects == ()
    assert not (root / TAXONOMY_PATH).exists()
    assert not (root / TEMPLATE_PATH).exists()
    assert not (root / "Journals").exists()


def test_type_sync_preserves_customisation_until_force(command_vault_clone):
    root = command_vault_clone.vault_root
    application_for(root).invoke(TypeSyncRequest(TYPE_KEY))
    taxonomy = root / TAXONOMY_PATH
    taxonomy.write_text("# Local journal definition\n")

    refused = application_for(root).invoke(TypeSyncRequest(TYPE_KEY))

    assert refused.error.code is ErrorCode.CONFLICT
    assert "locally customised" in refused.error.message
    assert refused.effects == "none"
    assert taxonomy.read_text() == "# Local journal definition\n"

    forced = application_for(root).invoke(TypeSyncRequest(TYPE_KEY, force=True))

    assert forced.status == "ok"
    assert forced.result.forced is True
    assert any(item.role == "taxonomy" for item in forced.result.updated)
    assert forced.committed_effects[0].kind == "type.sync"
    assert taxonomy.read_text().startswith("# Journals\n")


def test_type_sync_installs_uninstalled_and_rejects_unknown_types(command_vault_clone):
    root = command_vault_clone.vault_root

    uninstalled = application_for(root).invoke(TypeSyncRequest(TYPE_KEY))
    unknown = application_for(root).invoke(TypeSyncRequest("living/not-a-type"))

    assert uninstalled.status == "ok"
    assert len(uninstalled.result.updated) == 2
    assert unknown.error.code is ErrorCode.NOT_FOUND
    assert unknown.effects == "none"


def test_type_sync_in_sync_is_an_explicit_noop(command_vault_clone):
    root = command_vault_clone.vault_root
    application_for(root).invoke(TypeSyncRequest(TYPE_KEY))

    result = application_for(root).invoke(TypeSyncRequest(TYPE_KEY))

    assert result.status == "ok"
    assert result.result.updated == ()
    assert {item.reason for item in result.result.skipped} == {"in_sync"}
    assert result.committed_effects == ()


def test_type_sync_transport_is_a_maintainer_command():
    command_id = "type.sync"
    payload = {"type_key": TYPE_KEY, "force": True}
    request = current_request_resolver().resolve(command_id, payload)
    entry = current_application_catalogue().resolve(request)

    assert type(request) is TypeSyncRequest
    assert entry.authority is Authority.MAINTAINER
    assert entry.effect_class is EffectClass.SELECTED_BRAIN_MUTATION
    assert entry.retry_class is RetryClass.RECEIPT_REQUIRED


def test_type_library_transports_reject_cross_verb_fields():
    resolver = current_request_resolver()
    with pytest.raises(ValueError, match="boolean"):
        resolver.resolve("type.sync", {"type_key": TYPE_KEY, "force": "yes"})


def test_type_sync_install_post_commit_failure_is_honestly_unknown(
    command_vault_clone,
    monkeypatch,
):
    root = command_vault_clone.vault_root
    real_sync = sync_definitions.sync_definitions

    def commit_then_fail(*args, **kwargs):
        real_sync(*args, **kwargs)
        raise OSError("response failed after type installation")

    monkeypatch.setattr(sync_definitions, "sync_definitions", commit_then_fail)
    result = application_for(root).invoke(TypeSyncRequest(TYPE_KEY))

    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
    assert result.effects == "unknown"
    assert (root / TAXONOMY_PATH).is_file()
    assert (root / TEMPLATE_PATH).is_file()
