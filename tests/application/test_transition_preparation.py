"""Exact transition consent follows matching worksets rather than unrelated content."""

from dataclasses import replace

from _application.artefact.rename import ArtefactRenameRequest, catalogue_entry, execute
from command_application import application_for


SOURCE = "Designs/project~command-fixture/Command Fixture Design.md"
DEST = "Designs/project~command-fixture/Prepared Command Fixture Design.md"


class Admission:
    requires_binding = True

    def __init__(self, binding):
        self.binding = binding
        self.frozen_inputs = binding.frozen_inputs
        self.calls = []

    def admit(self, binding):
        if binding.digest != self.binding.digest:
            raise ValueError("prepared operation changed")
        self.calls.append(binding)


def test_new_matching_backlink_stales_transition_before_effect(command_vault_clone):
    root = command_vault_clone.vault_root
    context = application_for(root)._context
    request = ArtefactRenameRequest(SOURCE, DEST)
    binding = catalogue_entry().preparation.prepare(context, request)
    backlink = root / "Ideas" / "New backlink.md"
    backlink.write_text("[[Command Fixture Design]]\n")
    admission = Admission(binding)
    result = execute(replace(context, admission=admission), request)
    assert result.status == "error"
    assert admission.calls == []
    assert (root / SOURCE).exists() and not (root / DEST).exists()
    assert backlink.read_text() == "[[Command Fixture Design]]\n"


def test_unrelated_body_change_does_not_stale_matching_transition(command_vault_clone):
    root = command_vault_clone.vault_root
    context = application_for(root)._context
    unrelated = root / "Ideas" / "Unrelated.md"
    unrelated.write_text("No references here.\n")
    request = ArtefactRenameRequest(SOURCE, DEST)
    binding = catalogue_entry().preparation.prepare(context, request)
    unrelated.write_text("Still no references here.\n")
    admission = Admission(binding)
    result = execute(replace(context, admission=admission), request)
    assert result.status == "ok"
    assert len(admission.calls) == 1
    assert (root / DEST).exists()


def test_archive_plan_composes_metadata_and_self_reference_rewrite(command_vault_clone):
    import edit
    from _lifecycle.derived_cache_state import load_fresh_compiled_router

    root = command_vault_clone.vault_root
    with (root / SOURCE).open("a") as handle:
        handle.write("\n[[Command Fixture Design]]\n")
    router = load_fresh_compiled_router(str(root))
    plan = edit.plan_archive(str(root), router, SOURCE, effective_at="2026-09-14T12:00:00+00:00")
    before = (root / SOURCE).read_text()
    assert "archiveddate:" not in before
    result = edit.apply_artefact_transition(str(root), plan)
    content = (root / result["new_path"]).read_text()
    assert "archiveddate:" in content
    assert "[[Command Fixture Design]]" not in content


def test_status_consent_freezes_date_inputs(command_vault_clone):
    from datetime import datetime, timezone
    from _application.artefact.set_status import ArtefactSetStatusRequest, catalogue_entry, execute

    class Clock:
        def now(self):
            return datetime(2030, 1, 2, tzinfo=timezone.utc)

    root = command_vault_clone.vault_root
    context = application_for(root)._context
    request = ArtefactSetStatusRequest("Ideas/Command Fixture Candidate.md", "adopted")
    binding = catalogue_entry().preparation.prepare(context, request)
    admission = Admission(binding)
    result = execute(replace(context, admission=admission, clock=Clock()), request)
    assert result.status == "ok"
    assert len(admission.calls) == 1
    assert binding.frozen_inputs["effective_at"][:10] in (root / result.result.path).read_text()


def test_body_edit_without_moves_does_not_inventory_names(command_vault_clone, monkeypatch):
    import edit
    import rename
    from _lifecycle.derived_cache_state import load_fresh_compiled_router

    root = command_vault_clone.vault_root
    router = load_fresh_compiled_router(str(root))
    monkeypatch.setattr(rename, "build_md_basename_counts", lambda _root: (_ for _ in ()).throw(
        AssertionError("a body-only edit must not inventory move names")))
    edit.edit_artefact(str(root), router, SOURCE, "A replacement body.", target=":body", scope="section")


def test_type_sync_preserves_unrelated_tracking_after_preparation(command_vault_clone):
    import sync_definitions
    from _application.type.sync import TypeSyncRequest, catalogue_entry, execute

    root = command_vault_clone.vault_root
    context = application_for(root)._context
    request = TypeSyncRequest("living/journals")
    binding = catalogue_entry().preparation.prepare(context, request)
    tracking = sync_definitions.load_tracking(str(root))
    tracking["installed"]["living/unrelated"] = {"files": {}, "sentinel": "preserved"}
    sync_definitions.save_tracking(str(root), tracking)
    admission = Admission(binding)
    result = execute(replace(context, admission=admission), request)
    assert result.status == "ok"
    assert len(admission.calls) == 1
    assert sync_definitions.load_tracking(str(root))["installed"]["living/unrelated"]["sentinel"] == "preserved"


def test_empty_folder_growth_rejects_prepared_removal(command_vault_clone):
    import pytest
    from _application.artefact.repair import ArtefactRepairRequest, ArtefactRepairScope, catalogue_entry, execute

    root = command_vault_clone.vault_root
    folder = root / "Designs" / "project~command-fixture" / "vacated"
    folder.mkdir()
    context = application_for(root)._context
    request = ArtefactRepairRequest(ArtefactRepairScope.EMPTY_FOLDERS)
    binding = catalogue_entry().preparation.prepare(context, request)
    real_content = folder / "Keep.md"
    real_content.write_text("Real content arrived after preparation.\n")
    admission = Admission(binding)
    with pytest.raises(ValueError, match="prepared operation changed"):
        execute(replace(context, admission=admission), request)
    assert admission.calls == []
    assert real_content.exists()


def test_repair_noop_still_enters_admission(command_vault_clone):
    from _application.artefact.repair import ArtefactRepairRequest, ArtefactRepairScope, catalogue_entry, execute

    context = application_for(command_vault_clone.vault_root)._context
    request = ArtefactRepairRequest(ArtefactRepairScope.FRONTMATTER)
    binding = catalogue_entry().preparation.prepare(context, request)
    admission = Admission(binding)
    result = execute(replace(context, admission=admission), request)
    assert result.status == "ok"
    assert result.committed_effects == ()
    assert len(admission.calls) == 1
