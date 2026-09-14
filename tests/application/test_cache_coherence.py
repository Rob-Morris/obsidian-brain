"""Mutation completion and bounded router recovery across independent clients."""

from concurrent.futures import ThreadPoolExecutor
import os

import pytest

from _application.artefact.set_status import ArtefactSetStatusRequest
from _application.artefact.list import ArtefactListRequest
from _application.runtime.refresh_router import RuntimeRefreshRouterRequest
from _application.runtime.status import RuntimeStatusRequest
from _command_interface.derived_snapshots import FileDerivedSnapshotStore
from _lifecycle.derived_cache_state import inspect_router_cache
from command_application import application_for

CANDIDATE = "Ideas/Command Fixture Candidate.md"


def test_lifecycle_move_is_visible_to_other_reader_and_next_writer(command_vault_clone):
    root = command_vault_clone.vault_root
    observer = FileDerivedSnapshotStore(root)
    observer.load_router()
    observer.load_lexical_index()
    moved = application_for(root).invoke(ArtefactSetStatusRequest(CANDIDATE, "adopted"))
    assert moved.status == "ok"
    assert inspect_router_cache(root, verify_content=True).reason == "fresh"
    listed = application_for(root, derived_snapshots=observer).invoke(ArtefactListRequest())
    paths = {row.path for row in listed.result.items}
    assert moved.result.path in paths
    assert CANDIDATE not in paths
    from _application.document.replace_text import DocumentReplaceTextRequest, UniqueMatch
    from _application.document._types import DocumentLocator, DocumentResource
    from _common import document_revision_at, read_artefact
    unrelated = "Designs/project~command-fixture/Command Fixture Design.md"
    _fields, body = read_artefact(root / unrelated)
    edited = application_for(root).invoke(DocumentReplaceTextRequest(
        DocumentLocator(DocumentResource.ARTEFACT, unrelated),
        document_revision_at(root / unrelated), body, body + "\nNew content.\n", UniqueMatch()))
    assert edited.status == "ok"

    # A second session can immediately operate without manual refresh.
    restored = application_for(root).invoke(ArtefactSetStatusRequest(moved.result.path, "ready"))
    assert restored.status == "ok"
    assert restored.result.path == CANDIDATE


def test_independent_concurrent_moves_leave_coherent_state(command_vault_clone):
    root = command_vault_clone.vault_root
    requests = [
        ArtefactSetStatusRequest(CANDIDATE, "adopted"),
        ArtefactSetStatusRequest("Designs/project~command-fixture/Command Fixture Design.md", "ready"),
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda r: application_for(root).invoke(r), requests))
    assert [r.status for r in results] == ["ok", "ok"]
    assert inspect_router_cache(root, verify_content=True).reason == "fresh"


def test_repair_and_check_detect_same_stat_frontmatter_drift(command_vault_clone):
    root = command_vault_clone.vault_root
    assert not inspect_router_cache(root).stale
    source = root / CANDIDATE
    stat = source.stat()
    source.write_text(source.read_text().replace("key: command-fixture-candidate", "key: command-fixture-different"))
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert source.stat().st_size == stat.st_size
    assert inspect_router_cache(root, verify_content=True).stale
    repaired = application_for(root).invoke(RuntimeRefreshRouterRequest())
    assert repaired.status == "ok"
    assert repaired.result.status == "changed"
    assert not inspect_router_cache(root, verify_content=True).stale


def test_repair_reports_changed_source_as_partial_without_loop(command_vault_clone, monkeypatch):
    import compile_router
    root = command_vault_clone.vault_root
    original = compile_router.persist_compiled_router
    calls = []

    def persist_then_move(vault, compiled):
        original(vault, compiled)
        (root / CANDIDATE).rename(root / "Ideas/Moved Outside Brain.md")
        calls.append(True)

    monkeypatch.setattr(compile_router, "persist_compiled_router", persist_then_move)
    result = application_for(root).invoke(RuntimeRefreshRouterRequest(force=True))
    assert result.status == "partial"
    assert result.error.details.reason == "artefact-index-source-unreadable"
    assert result.error.details.source_path == CANDIDATE
    assert result.error.next_action.command_id == "runtime.refresh-router"
    assert result.committed_effects
    assert len(calls) == 1


def test_blocked_write_has_precise_recovery_and_no_effects(command_vault_clone):
    root = command_vault_clone.vault_root
    (root / CANDIDATE).rename(root / "Ideas/External Move.md")
    result = application_for(root).invoke(ArtefactSetStatusRequest(CANDIDATE, "adopted"))
    assert result.status == "error"
    assert result.effects == "none"
    assert result.error.code == "conflict"
    assert result.error.details.source_path == CANDIDATE
    assert result.error.next_action.command_id == "runtime.refresh-router"
    assert result.retryable is False  # Repair must precede another write attempt.


def test_lifecycle_index_failure_preserves_known_content_effects(command_vault_clone, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("private diagnostic")

    monkeypatch.setattr("_portable.lexical_maintenance.maintain_lexical_index", fail)
    root = command_vault_clone.vault_root
    result = application_for(root).invoke(ArtefactSetStatusRequest(CANDIDATE, "adopted"))
    assert result.status == "partial"
    assert not (root / CANDIDATE).exists()
    assert result.committed_effects
    assert "private diagnostic" not in result.error.message


def test_status_labels_historical_warmup_without_probing_cache(command_vault_clone, monkeypatch):
    from _bootstrap import readiness
    root = command_vault_clone.vault_root
    state = readiness._state_document(root, run_id="finished", state="ready", phase=None,
        router="ready", lexical="ready", semantic="disabled", started_at=readiness._now(), last_error=None)
    readiness._write_state(root, state)
    (root / ".brain/local/compiled-router.json").unlink()
    result = application_for(root).invoke(RuntimeStatusRequest())
    assert result.result.runtime_status.state == "ready"
    assert result.result.runtime_status.observation == "recorded-warmup"
    assert result.result.router_check.command_id == "vault.check"
    assert "not current cache health" in result.result.instruction


def test_mirror_failure_next_action_retries_the_mirror(command_vault_clone, monkeypatch):
    import compile_router
    root = command_vault_clone.vault_root
    original = compile_router.refresh_session_markdown
    calls = []

    def fail_once(*args):
        calls.append(True)
        if len(calls) == 1:
            raise OSError("private path")
        return original(*args)

    monkeypatch.setattr(compile_router, "refresh_session_markdown", fail_once)
    first = application_for(root).invoke(RuntimeRefreshRouterRequest(force=True))
    assert first.status == "partial"
    action = first.error.next_action
    request = RuntimeRefreshRouterRequest(**{a.name: a.value for a in action.arguments})
    second = application_for(root).invoke(request)
    assert second.status == "ok"
    assert len(calls) == 2


def test_in_place_status_uses_incremental_lexical_update(command_vault_clone, monkeypatch):
    from _portable.lexical_maintenance import maintain_lexical_index
    root = command_vault_clone.vault_root
    maintain_lexical_index(root, dry_run=False, force=True)

    def no_rebuild(*args, **kwargs):
        raise AssertionError("in-place status must not reparse the vault")

    monkeypatch.setattr("_search.index.build_index", no_rebuild)
    result = application_for(root).invoke(ArtefactSetStatusRequest(CANDIDATE, "shaping"))
    assert result.status == "ok"
    listed = application_for(root).invoke(ArtefactListRequest())
    assert next(x for x in listed.result.items if x.path == CANDIDATE).status == "shaping"


def test_empty_reparent_children_does_not_touch_indexes(command_vault_clone, monkeypatch):
    from _application.artefact.reparent_children import ArtefactReparentChildrenRequest, ReparentChildrenMode

    def no_rebuild(*args, **kwargs):
        raise AssertionError("empty operation must not maintain indexes")

    monkeypatch.setattr("_portable.lexical_maintenance.maintain_lexical_index", no_rebuild)
    result = application_for(command_vault_clone.vault_root).invoke(
        ArtefactReparentChildrenRequest(CANDIDATE, ReparentChildrenMode.TOP_LEVEL))
    assert result.status == "ok"
    assert result.committed_effects == ()


def test_partial_content_and_maintenance_failure_keep_recovery_action(command_vault_clone, monkeypatch):
    import edit
    from _common import PartialApplyError
    original = edit.apply_artefact_transition

    def partial_content(*args):
        original(*args)
        raise PartialApplyError("Some content was applied")

    def fail_index(*args, **kwargs):
        raise OSError("private detail")

    monkeypatch.setattr(edit, "apply_artefact_transition", partial_content)
    monkeypatch.setattr("_portable.lexical_maintenance.maintain_lexical_index", fail_index)
    result = application_for(command_vault_clone.vault_root).invoke(
        ArtefactSetStatusRequest(CANDIDATE, "adopted"))
    assert result.status == "partial"
    assert result.error.next_action.command_id == "retrieval.refresh-lexical"
    assert "Some content was applied" in result.error.message
    assert "private detail" not in result.error.message


def test_lexical_freshness_detects_count_preserving_external_move(command_vault_clone):
    from _lifecycle.derived_cache_state import inspect_lexical_cache
    from _portable.lexical_maintenance import maintain_lexical_index
    root = command_vault_clone.vault_root
    maintain_lexical_index(root, dry_run=False, force=True)
    (root / CANDIDATE).rename(root / "Ideas/Externally Renamed.md")
    state = inspect_lexical_cache(root)
    assert state.reason == "document-path-drift"
    repaired = maintain_lexical_index(root, dry_run=False, force=False)
    assert repaired.status == "changed"
    assert not inspect_lexical_cache(root).stale


def test_lexical_inventory_rejects_duplicate_rows(command_vault_clone):
    import json
    from _lifecycle.derived_cache_state import inspect_lexical_cache
    root = command_vault_clone.vault_root
    path = root / ".brain/local/retrieval-index.json"
    data = json.loads(path.read_text())
    data["documents"].append(data["documents"][0])
    path.write_text(json.dumps(data))
    assert inspect_lexical_cache(root).reason == "invalid-document-count"


def test_created_artefact_is_visible_and_immediately_mutable(command_vault_clone):
    from _application.artefact.create import ArtefactCreateRequest
    root = command_vault_clone.vault_root
    observer = FileDerivedSnapshotStore(root)
    observer.load_router()
    observer.load_lexical_index()
    created = application_for(root).invoke(ArtefactCreateRequest("ideas", "Creation Coherence"))
    assert created.status == "ok"
    assert not inspect_router_cache(root, verify_content=True).stale
    listed = application_for(root, derived_snapshots=observer).invoke(ArtefactListRequest())
    assert created.result.path in {row.path for row in listed.result.items}
    updated = application_for(root).invoke(ArtefactSetStatusRequest(created.result.path, "shaping"))
    assert updated.status == "ok"


def test_create_index_failure_preserves_created_content(command_vault_clone, monkeypatch):
    from _application.artefact.create import ArtefactCreateRequest
    from _application._mutation_support import StagedContent
    from _staging import stage_body, read_staged_body
    def fail(*args, **kwargs):
        raise OSError("private index failure")
    monkeypatch.setattr("_portable.lexical_maintenance.maintain_lexical_index", fail)
    root = command_vault_clone.vault_root
    handle = stage_body(str(root), "See [[missing-creation-target]].\n")["handle"]
    monkeypatch.setattr("_staging.finalise_staged_body", lambda *args: "Staged content cleanup needs attention.")
    result = application_for(root).invoke(ArtefactCreateRequest("ideas", "Partial Creation", StagedContent(handle)))
    assert result.status == "partial"
    assert result.committed_effects[0].kind == "artefact.created"
    assert (root / result.committed_effects[0].subject).is_file()
    assert result.error.next_action.command_id == "retrieval.refresh-lexical"
    assert "private index failure" not in result.error.message
    assert any("cleanup needs attention" in warning.message for warning in result.warnings)
    assert any("unresolved wikilink" in warning.message for warning in result.warnings)
    assert read_staged_body(str(root), handle)
