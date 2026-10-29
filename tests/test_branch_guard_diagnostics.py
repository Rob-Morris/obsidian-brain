"""Native refusal evidence survives partial results, index failures and receipts."""

import json
from types import SimpleNamespace

import pytest

from _bootstrap import branch_guard
from _bootstrap.local_config import patch_local_config
from _application.types import BranchGuardDetails


@pytest.fixture
def guarded(tmp_path, monkeypatch):
    state = {"branch": "dev"}
    patch_local_config(tmp_path, lambda data: data.update(
        branch_guard={"allowed_branches": ["dev", "feature"]}))
    monkeypatch.setattr(branch_guard, "observe_git", lambda root:
                        branch_guard.GitState(str(tmp_path), state["branch"]))
    return tmp_path, state


def test_completed_records_merge_and_survive_json_handoff():
    refusal = branch_guard.BranchGuardError("branch-changed")
    refusal.record_completed("move", "before.md", destination="after.md")
    refusal.record_completed("write", "metadata.md")
    refusal.record_completed("move", "before.md", destination="after.md")
    refusal.record_completed("delete", "removed.md")
    child = branch_guard.BranchGuardError(refusal.reason, json.loads(json.dumps(refusal.facts)))
    assert child.completed_effects == refusal.completed_effects
    assert len(child.completed_effects) == 3
    assert BranchGuardDetails.from_error(child).committed_paths == (
        "before.md", "after.md", "metadata.md", "removed.md")


def test_completed_diagnostic_paths_remain_bounded():
    refusal = branch_guard.BranchGuardError("branch-changed")
    for number in range(70):
        refusal.record_completed("write", f"{number}-" + "x" * 2100)
    details = BranchGuardDetails.from_error(refusal)
    assert details.truncated
    assert len(details.committed_paths) == 64
    assert all(len(path) == 2048 for path in details.committed_paths)


def test_partial_backlink_writes_are_recorded(guarded, monkeypatch):
    from _common import _wikilinks
    root, state = guarded
    notes = root / "Notes"
    notes.mkdir()
    for name in ("one", "two"):
        (notes / f"{name}.md").write_text("before")
    plan = SimpleNamespace(unreadable=(), writes=[
        SimpleNamespace(path=f"Notes/{name}.md", after="after", substitutions=1)
        for name in ("one", "two")])
    write = _wikilinks.safe_write_active_or_archived_artefact

    def switch_after_write(*args, **kwargs):
        write(*args, **kwargs)
        state["branch"] = "feature"

    monkeypatch.setattr(_wikilinks, "safe_write_active_or_archived_artefact", switch_after_write)
    with pytest.raises(branch_guard.BranchGuardError) as caught:
        _wikilinks.apply_wikilink_rewrites(root, plan)
    assert BranchGuardDetails.from_error(caught.value).committed_paths == ("Notes/one.md",)
    assert (notes / "one.md").read_text() == "after"
    assert (notes / "two.md").read_text() == "before"


@pytest.mark.parametrize("operation", ["move", "delete"])
@pytest.mark.parametrize("refusal_phase", ["first-effect", "prune"])
def test_successful_backlinks_survive_later_native_refusal_and_receipt(
    guarded, monkeypatch, operation, refusal_phase,
):
    from datetime import datetime, timezone
    from _common import _wikilinks
    from _application._transition_indexes import BranchGuardPartial, branch_partial_effects, transition_error
    from _application.receipts import OutcomeReceipt, OutcomeReference, ReceiptState
    from _command_interface.receipts import _decode, _encode
    import rename

    root, state = guarded
    notes = root / "Notes"
    notes.mkdir()
    source = notes / "source.md"
    destination = notes / "renamed.md"
    backlink = notes / "backlink.md"
    source.write_text("source content")
    backlink.write_text("See [[source]].")
    if operation == "move":
        plan = rename.plan_move_and_links(root, [{"source": "Notes/source.md", "dest": "Notes/renamed.md"}],
                                          prune_router={} if refusal_phase == "prune" else None)
        apply = rename.apply_move_and_links
        command_id = "artefact.rename"
    else:
        plan = rename.plan_artefact_delete(root, "Notes/source.md",
                                           prune_router={} if refusal_phase == "prune" else None)
        apply = rename.apply_artefact_delete
        command_id = "artefact.delete"
    assert tuple(write.path for write in plan.links.writes) == ("Notes/backlink.md",)
    rewrite = _wikilinks.safe_write_active_or_archived_artefact

    def switch_after_last_backlink(*args, **kwargs):
        rewrite(*args, **kwargs)
        if refusal_phase == "first-effect":
            state["branch"] = "feature"

    def refuse_pruning(*args, **kwargs):
        state["branch"] = "feature"
        branch_guard.checkpoint(root)

    monkeypatch.setattr(_wikilinks, "safe_write_active_or_archived_artefact", switch_after_last_backlink)
    monkeypatch.setattr(rename, "prune_vacated_owner_folders", refuse_pruning)
    with pytest.raises(branch_guard.BranchGuardError) as caught:
        apply(root, plan)
    assert backlink.read_text() == plan.links.writes[0].after
    assert backlink.read_text() != "See [[source]]."
    expected = {"Notes/backlink.md"}
    if refusal_phase == "first-effect":
        assert source.read_text() == "source content"
        assert not destination.exists()
    else:
        assert not source.exists()
        expected.add("Notes/source.md")
        if operation == "move":
            expected.add("Notes/renamed.md")
            assert destination.read_text() == "source content"
        else:
            assert not destination.exists()
    partial = BranchGuardPartial(caught.value)
    details = transition_error(partial).details
    assert details.reason == "branch-changed"
    assert set(details.committed_paths) == expected
    effects = branch_partial_effects(partial, command_id)
    assert {effect.subject for effect in effects} == expected
    receipt = OutcomeReceipt(OutcomeReference("backlink-phase-refusal"), command_id, 1,
                             ReceiptState.KNOWN_PARTIAL, datetime(2026, 10, 29, tzinfo=timezone.utc),
                             effects, details)
    assert _decode(_encode(receipt)) == receipt


def test_nested_metadata_and_moves_merge_completed_evidence(guarded, monkeypatch):
    import edit
    import rename
    root, state = guarded
    notes = root / "Notes"
    notes.mkdir()
    for name in ("metadata", "one", "two"):
        (notes / f"{name}.md").write_text("before")
    movement = rename.plan_move_and_links(root, [
        {"source": f"Notes/{name}.md", "dest": f"Notes/{name}-moved.md"}
        for name in ("one", "two")])
    plan = SimpleNamespace(writes=[{"path": "Notes/metadata.md", "fields": {}, "body": "after"}],
                           operation="repair", movement=movement)
    move = rename.os.rename

    def switch_after_move(*args, **kwargs):
        move(*args, **kwargs)
        state["branch"] = "feature"

    monkeypatch.setattr(rename.os, "rename", switch_after_move)
    with pytest.raises(branch_guard.BranchGuardError) as caught:
        edit.apply_artefact_transition(root, plan)
    details = BranchGuardDetails.from_error(caught.value)
    assert set(details.committed_paths) == {
        "Notes/one.md", "Notes/one-moved.md", "Notes/metadata.md"}
    assert not (notes / "one.md").exists()
    assert (notes / "one-moved.md").read_text() == "before"
    assert (notes / "two.md").read_text() == "before"
    assert "after" in (notes / "metadata.md").read_text()


def test_partial_deletes_are_recorded(guarded, monkeypatch):
    import rename
    root, state = guarded
    notes = root / "Notes"
    notes.mkdir()
    paths = tuple(f"Notes/{name}.md" for name in ("one", "two"))
    for path in paths:
        (root / path).write_text("before")
    plan = SimpleNamespace(paths=paths, abs_paths=tuple(str(root / path) for path in paths),
                           links=SimpleNamespace(unreadable=(), writes=()), prune_router=None)
    remove = rename.os.remove

    def switch_after_delete(*args, **kwargs):
        remove(*args, **kwargs)
        state["branch"] = "feature"

    monkeypatch.setattr(rename.os, "remove", switch_after_delete)
    with pytest.raises(branch_guard.BranchGuardError) as caught:
        rename.apply_artefact_delete(root, plan)
    assert BranchGuardDetails.from_error(caught.value).committed_paths == (paths[0],)
    assert not (root / paths[0]).exists()
    assert (root / paths[1]).read_text() == "before"


def test_skill_backup_pruning_keeps_completed_deletions(guarded, monkeypatch):
    from _skill_library import service
    root, state = guarded
    backups = root / service.BACKUPS_REL
    for number in range(3):
        package = backups / f"example.previous-{number}"
        package.mkdir(parents=True)
        (package / "SKILL.md").write_text(str(number))
    monkeypatch.setattr(service, "MAX_BACKUPS_PER_SKILL", 1)
    remove = service.shutil.rmtree

    def switch_after_delete(*args, **kwargs):
        remove(*args, **kwargs)
        state["branch"] = "feature"

    monkeypatch.setattr(service.shutil, "rmtree", switch_after_delete)
    with pytest.raises(branch_guard.BranchGuardError) as caught:
        service._trim_backups(root, "example")
    paths = BranchGuardDetails.from_error(caught.value).committed_paths
    assert len(paths) == 1
    assert not (root / paths[0]).exists()
    assert len(list(backups.iterdir())) == 2


def test_attachment_scope_pruning_keeps_completed_deletions(guarded, monkeypatch):
    from pathlib import Path
    import upload_attachment
    root, state = guarded
    scope = root / upload_attachment.attachment_scope_rel_dir("design/example")
    child = scope / "vacated"
    child.mkdir(parents=True)
    remove = Path.rmdir

    def switch_after_delete(path):
        remove(path)
        if path == child:
            state["branch"] = "feature"

    monkeypatch.setattr(Path, "rmdir", switch_after_delete)
    with pytest.raises(branch_guard.BranchGuardError) as caught:
        upload_attachment.prune_vacated_attachment_scope(root, "design/example")
    assert BranchGuardDetails.from_error(caught.value).committed_paths == (
        child.relative_to(root).as_posix(),)
    assert not child.exists()
    assert scope.is_dir()


def test_definition_copy_refusal_projects_canonical_completed_writes(guarded, monkeypatch):
    import _common
    import sync_definitions
    root, state = guarded
    write = _common.safe_write_via
    copies = [SimpleNamespace(target=f"_Config/Types/{name}/taxonomy.md", content=b"body")
              for name in ("one", "two")]

    def switch_after_write(*args, **kwargs):
        write(*args, **kwargs)
        state["branch"] = "feature"

    monkeypatch.setattr(_common, "safe_write_via", switch_after_write)
    with pytest.raises(branch_guard.BranchGuardError) as caught:
        sync_definitions._apply_definition_copies(root, copies)
    assert BranchGuardDetails.from_error(caught.value).committed_paths == (copies[0].target,)
    assert (root / copies[0].target).read_bytes() == b"body"
    assert not (root / copies[1].target).exists()


@pytest.mark.parametrize("owner", ["document", "status"])
@pytest.mark.parametrize("index_failure", [False, True])
def test_semantic_completion_refusal_keeps_partial_details_and_saved_receipt(
    command_vault_clone, monkeypatch, owner, index_failure,
):
    from command_application import application_for
    from command_vault import load_command_vault_manifest
    from _application import _transition_indexes
    from _application._mutation_support import FrontmatterField
    from _application.artefact.set_status import ArtefactSetStatusRequest
    from _application.document._types import DocumentLocator, DocumentResource
    from _application.document.update_frontmatter import DocumentUpdateFrontmatterRequest
    from _application.relationship.expose import RelationshipExposeRequest
    from _application.results import CommandError, CommandNextAction, ErrorCode, RequestErrorDetails
    from _application.receipts import ReceiptState
    from _command_interface.receipts import FileReceiptStore
    from _common import document_revision_at, parse_frontmatter
    from _relationships.model import Endpoint, NodeKind
    from _relationships.storage import Store

    root = command_vault_clone.vault_root
    artefact = load_command_vault_manifest()["stable_artefacts"]["owned_design"]
    path = root / artefact["path"]
    reference = artefact["key"]
    state = {"branch": "dev"}
    patch_local_config(root, lambda data: data.update(
        branch_guard={"allowed_branches": ["dev", "feature"]}))
    monkeypatch.setattr(branch_guard, "observe_git", lambda vault:
                        branch_guard.GitState(str(root), state["branch"]))
    app = application_for(root)
    base_tags = tuple(parse_frontmatter(path.read_text())[0]["tags"])
    if owner == "document":
        # Lifecycle fields have dedicated commands. Use the document owner's
        # supported relevance-tag edit to require semantic occurrence retirement.
        from _application.workspace.create import WorkspaceCreateRequest
        created = app.invoke(WorkspaceCreateRequest("diagnostic-relevance"))
        assert created.status == "ok", created
        seeded = app.invoke(DocumentUpdateFrontmatterRequest(
            DocumentLocator(DocumentResource.ARTEFACT, reference), document_revision_at(path),
            (FrontmatterField("tags", (*base_tags, "workspace/diagnostic-relevance")),)))
        assert seeded.status == "ok", seeded
    exposed = app.invoke(RelationshipExposeRequest(Endpoint(NodeKind.ARTEFACT, reference)))
    assert exposed.status == "ok", exposed
    publish = Store.publish

    def switch_before_publication(self, *args, **kwargs):
        state["branch"] = "feature"
        return publish(self, *args, **kwargs)

    monkeypatch.setattr(Store, "publish", switch_before_publication)
    if index_failure:
        def fail_indexes(*args, **kwargs):
            raise _transition_indexes.TransitionIndexesIncomplete(CommandError(
                ErrorCode.CONFLICT, "Index refresh also failed", RequestErrorDetails(None, "index-failure"),
                CommandNextAction("runtime.refresh-router")))
        monkeypatch.setattr(_transition_indexes, "reconcile_transition_indexes", fail_indexes)
    if owner == "document":
        request = DocumentUpdateFrontmatterRequest(
            DocumentLocator(DocumentResource.ARTEFACT, reference), document_revision_at(path),
            (FrontmatterField("tags", base_tags),))
    else:
        request = ArtefactSetStatusRequest(reference, "active")
    result = app.invoke(request)
    assert result.status == "partial", result
    assert result.committed_effects
    details = result.error.details
    assert isinstance(details, BranchGuardDetails)
    assert details.reason == "branch-changed"
    assert details.repository == str(root)
    assert details.observed_branch == "feature"
    assert details.allowed_branches == ("dev", "feature")
    assert details.committed_paths
    assert any((root / subject).is_file() for subject in details.committed_paths)
    assert details.inspection_command == "vault.read-config"
    assert any(location.kind == "journal_path" for location in details.recovery_locations)
    if index_failure:
        assert "Index refresh also failed" in result.error.message
        assert result.error.next_action.command_id == "runtime.refresh-router"
    else:
        assert "semantic publication incomplete" in result.error.message
    context = app._context
    receipt = context.authorisation.receipts.outcomes[context.invocation_id].receipt
    assert receipt.state is ReceiptState.KNOWN_PARTIAL
    assert receipt.branch_guard == details
    assert receipt.committed_effects == result.committed_effects
    receipts = FileReceiptStore(root, context.clock)
    receipts.write(receipt)
    assert receipts.read(receipt.reference) == receipt
    pending = next(Store(root).read_path(item) for item in (root / "_Relationships/operation").glob("*.json")
                   if Store(root).read_path(item)["state"] == "native-pending")
    assert Store(root)._guard_loss_path(pending["id"]).is_file()
