"""Owner behaviour for lexical-index repair and explicit rebuild."""

from __future__ import annotations

import pytest

from _application._lexical_maintenance import LexicalMaintenanceStatus
from _application.registry import current_application_catalogue, current_request_resolver
from _application.results import ErrorCode
from _application.retrieval.refresh_lexical import RetrievalRefreshLexicalRequest
from _application.types import Authority, EffectClass, RetryClass
from _portable import lexical_maintenance
from command_application import application_for


INDEX_PATH = ".brain/local/retrieval-index.json"
SIDECARS = (
    ".brain/local/type-embeddings.npy",
    ".brain/local/doc-embeddings.npy",
    ".brain/local/embeddings-meta.json",
)


def test_lexical_repair_is_noop_when_index_is_fresh(command_vault_clone):
    result = application_for(command_vault_clone.vault_root).invoke(
        RetrievalRefreshLexicalRequest()
    )

    assert result.status == "ok"
    assert result.result.status is LexicalMaintenanceStatus.NOOP
    assert result.result.forced is False
    assert result.result.document_count > 0
    assert result.result.term_count > 0
    assert result.committed_effects == ()


def test_lexical_repair_rebuilds_stale_index_and_invalidates_semantic_sidecars(
    command_vault_clone,
):
    root = command_vault_clone.vault_root
    (root / INDEX_PATH).unlink()
    for relative in SIDECARS:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("stale")

    result = application_for(root).invoke(RetrievalRefreshLexicalRequest())

    assert result.status == "ok"
    assert result.result.status is LexicalMaintenanceStatus.CHANGED
    assert result.result.reason == "missing"
    assert result.result.document_count > 0
    assert result.result.term_count > 0
    assert result.result.sidecars_removed == SIDECARS
    assert result.committed_effects[0].subject == INDEX_PATH
    assert (root / INDEX_PATH).is_file()
    assert not any((root / relative).exists() for relative in SIDECARS)


def test_lexical_rebuild_is_explicit_even_when_index_is_fresh(command_vault_clone):
    result = application_for(command_vault_clone.vault_root).invoke(
        RetrievalRefreshLexicalRequest(force=True)
    )

    assert result.status == "ok"
    assert result.result.status is LexicalMaintenanceStatus.CHANGED
    assert result.result.reason == "explicit-rebuild"
    assert result.result.forced is True
    assert result.committed_effects[0].kind == "retrieval.refresh-lexical"


def test_lexical_rebuild_dry_run_plans_without_writing(command_vault_clone):
    root = command_vault_clone.vault_root
    before = (root / INDEX_PATH).read_bytes()

    result = application_for(root, dry_run=True).invoke(
        RetrievalRefreshLexicalRequest(force=True)
    )

    assert result.status == "ok"
    assert result.result.status is LexicalMaintenanceStatus.PLANNED
    assert result.result.dry_run is True
    assert result.result.document_count is None
    assert result.committed_effects == ()
    assert (root / INDEX_PATH).read_bytes() == before


def test_lexical_source_failure_is_known_to_have_no_effect(command_vault_clone):
    root = command_vault_clone.vault_root
    before = (root / INDEX_PATH).read_bytes()
    (root / "Projects" / "unreadable.md").write_bytes(b"\xff\xfe\x00\x00")

    result = application_for(root).invoke(RetrievalRefreshLexicalRequest(force=True))

    assert result.error.code is ErrorCode.CONFLICT
    assert result.effects == "none"
    assert "unreadable retrieval source" in result.error.message
    assert (root / INDEX_PATH).read_bytes() == before


def test_lexical_post_commit_failure_is_honestly_unknown(
    command_vault_clone,
    monkeypatch,
):
    root = command_vault_clone.vault_root

    def fail_after_index_write(_root):
        raise OSError("sidecar cleanup failed after index write")

    monkeypatch.setattr(
        lexical_maintenance,
        "clear_embeddings_outputs",
        fail_after_index_write,
    )
    result = application_for(root).invoke(RetrievalRefreshLexicalRequest(force=True))

    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
    assert result.effects == "unknown"
    assert (root / INDEX_PATH).is_file()


def test_lexical_maintenance_transport_is_a_strict_maintainer_command():
    command_id = "retrieval.refresh-lexical"
    request = current_request_resolver().resolve(command_id, {"force": True})
    entry = current_application_catalogue().resolve(request)

    assert type(request) is RetrievalRefreshLexicalRequest
    assert request.force is True
    assert entry.authority is Authority.MAINTAINER
    assert entry.effect_class is EffectClass.SELECTED_BRAIN_MUTATION
    assert entry.retry_class is RetryClass.RECEIPT_REQUIRED
    with pytest.raises(ValueError, match="unexpected fields"):
        current_request_resolver().resolve(command_id, {"rebuild": True})
