"""Owner behaviour for destructive artefact transitions."""

from __future__ import annotations

import rename
import pytest
import compile_router

from _application.artefact.archive import ArtefactArchiveRequest
from _application.artefact.convert import ArtefactConvertRequest
from _application.artefact.delete import ArtefactDeleteRequest
from _application.artefact.rename import ArtefactRenameRequest
from _application.artefact.unarchive import ArtefactUnarchiveRequest
from _application.registry import current_application_catalogue, current_request_resolver
from _application.results import ErrorCode
from _application.types import Authority, EffectClass, RetryClass
from _common import PartialApplyError
from command_application import application_for


CANDIDATE = "Ideas/Command Fixture Candidate.md"
TERMINAL = "Ideas/+Adopted/Command Fixture Adopted.md"
DESIGN = "Designs/project~command-fixture/Command Fixture Design.md"


def test_artefact_rename_stays_within_type_and_updates_links(command_vault_clone):
    destination = (
        "Designs/project~command-fixture/Renamed Command Fixture Design.md"
    )
    result = application_for(command_vault_clone.vault_root).invoke(
        ArtefactRenameRequest(DESIGN, destination)
    )

    assert result.status == "ok"
    assert result.result.old_path == DESIGN
    assert result.result.new_path == destination
    assert not (command_vault_clone.vault_root / DESIGN).exists()
    assert (command_vault_clone.vault_root / destination).is_file()
    project = (command_vault_clone.vault_root / "Projects/Command Fixture.md").read_text()
    assert "[[Renamed Command Fixture Design]]" in project


def test_artefact_rename_rejects_type_conversion(command_vault_clone):
    result = application_for(command_vault_clone.vault_root).invoke(
        ArtefactRenameRequest(CANDIDATE, "Designs/Command Fixture Candidate.md")
    )

    assert result.error.code is ErrorCode.INVALID_REQUEST
    assert "convert" in result.error.message
    assert (command_vault_clone.vault_root / CANDIDATE).is_file()


def test_artefact_convert_uses_type_and_owner_projection(command_vault_clone):
    result = application_for(command_vault_clone.vault_root).invoke(
        ArtefactConvertRequest(
            CANDIDATE,
            "design",
            parent="project/command-fixture",
        )
    )

    assert result.status == "ok"
    assert result.result.old_path == CANDIDATE
    assert result.result.new_path.startswith("Designs/project~command-fixture/")
    assert result.result.type == "living/design"
    assert (command_vault_clone.vault_root / result.result.new_path).is_file()
    written = (command_vault_clone.vault_root / result.result.new_path).read_text()
    assert "type: living/design" in written
    assert "parent: project/command-fixture" in written


def test_artefact_archive_and_unarchive_round_trip(command_vault_clone):
    archived = application_for(command_vault_clone.vault_root).invoke(
        ArtefactArchiveRequest(TERMINAL)
    )

    assert archived.status == "ok"
    assert archived.result.new_path.startswith("_Archive/Ideas/")
    assert archived.result.archived[0].old_path == TERMINAL
    assert not (command_vault_clone.vault_root / TERMINAL).exists()
    assert (command_vault_clone.vault_root / archived.result.new_path).is_file()
    router = compile_router.compile(str(command_vault_clone.vault_root))
    compile_router.persist_compiled_router(
        str(command_vault_clone.vault_root), router
    )

    restored = application_for(command_vault_clone.vault_root).invoke(
        ArtefactUnarchiveRequest(archived.result.new_path)
    )

    assert restored.status == "ok", restored.error.message
    assert restored.result.old_path == archived.result.new_path
    assert restored.result.new_path == TERMINAL
    assert restored.result.uninspected == ()
    assert (command_vault_clone.vault_root / restored.result.new_path).is_file()


def test_artefact_archive_accepts_nonterminal_status(command_vault_clone):
    result = application_for(command_vault_clone.vault_root).invoke(
        ArtefactArchiveRequest(CANDIDATE)
    )

    assert result.status == "ok"
    assert not (command_vault_clone.vault_root / CANDIDATE).exists()


def test_artefact_delete_returns_exact_removed_paths_and_cleans_links(
    command_vault_clone,
):
    result = application_for(command_vault_clone.vault_root).invoke(
        ArtefactDeleteRequest(DESIGN)
    )

    assert result.status == "ok"
    assert result.result.path == DESIGN
    assert result.result.deleted == (DESIGN,)
    assert not (command_vault_clone.vault_root / DESIGN).exists()
    project = (command_vault_clone.vault_root / "Projects/Command Fixture.md").read_text()
    assert "~~Command Fixture Design~~" in project


@pytest.mark.parametrize(
    ("command_id", "payload", "request_type", "authority"),
    (
        (
            "artefact.rename",
            {"source": DESIGN, "dest": "Designs/Renamed.md"},
            ArtefactRenameRequest,
            Authority.CONTRIBUTOR,
        ),
        (
            "artefact.convert",
            {"path": CANDIDATE, "target_type": "design", "recursive": True},
            ArtefactConvertRequest,
            Authority.CONTRIBUTOR,
        ),
        (
            "artefact.archive",
            {"path": TERMINAL, "recursive": False},
            ArtefactArchiveRequest,
            Authority.CONTRIBUTOR,
        ),
        (
            "artefact.unarchive",
            {"path": "_Archive/Ideas/example.md"},
            ArtefactUnarchiveRequest,
            Authority.CONTRIBUTOR,
        ),
        (
            "artefact.delete",
            {"path": DESIGN, "recursive": True},
            ArtefactDeleteRequest,
            Authority.ADMINISTRATOR,
        ),
    ),
)
def test_transition_transport_has_user_centred_authority(
    command_id,
    payload,
    request_type,
    authority,
):
    request = current_request_resolver().resolve(command_id, payload)
    entry = current_application_catalogue().resolve(request)

    assert type(request) is request_type
    assert entry.authority is authority
    assert entry.effect_class is EffectClass.SELECTED_BRAIN_MUTATION
    assert entry.retry_class is RetryClass.RECEIPT_REQUIRED


def test_transition_transport_rejects_cross_verb_fields():
    with pytest.raises(ValueError, match="unexpected fields"):
        current_request_resolver().resolve(
            "artefact.archive", {"path": TERMINAL, "dest": "elsewhere"}
        )


def test_transition_dry_run_refuses_to_invent_destructive_effects(
    command_vault_clone,
):
    result = application_for(command_vault_clone.vault_root, dry_run=True).invoke(
        ArtefactDeleteRequest(DESIGN)
    )

    assert result.error.code is ErrorCode.INVALID_REQUEST
    assert (command_vault_clone.vault_root / DESIGN).is_file()


def test_transition_partial_apply_is_structural_and_receipted(
    command_vault_clone,
    monkeypatch,
):
    import edit
    real_rename = edit.apply_artefact_transition
    destination = (
        "Designs/project~command-fixture/Uncertain Command Fixture Design.md"
    )

    def commit_then_report_partial(*args, **kwargs):
        real_rename(*args, **kwargs)
        raise PartialApplyError("links changed and move committed; refresh failed")

    monkeypatch.setattr(edit, "apply_artefact_transition", commit_then_report_partial)
    result = application_for(command_vault_clone.vault_root).invoke(
        ArtefactRenameRequest(DESIGN, destination)
    )

    assert result.status == "partial"
    assert result.error.code is ErrorCode.CONFLICT
    assert result.committed_effects[0].kind == "artefact.rename"
    assert (command_vault_clone.vault_root / destination).is_file()


def test_transition_unexpected_post_commit_failure_is_honestly_unknown(
    command_vault_clone,
    monkeypatch,
):
    real_delete = rename.apply_artefact_delete

    def commit_then_fail(*args, **kwargs):
        real_delete(*args, **kwargs)
        raise OSError("response failed after delete commit")

    monkeypatch.setattr(rename, "apply_artefact_delete", commit_then_fail)
    result = application_for(command_vault_clone.vault_root).invoke(
        ArtefactDeleteRequest(DESIGN)
    )

    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
    assert result.effects == "unknown"
    assert not (command_vault_clone.vault_root / DESIGN).exists()


@pytest.mark.parametrize(
    "type_name, frontmatter",
    [
        ("thought", {}),
        ("idea", {"status": "candidate"}),
        ("writing", {"status": "published", "publisheddate": "2026-08-01"}),
    ],
)
def test_archive_restore_delete_updates_long_lived_reads(
    command_vault_clone, type_name, frontmatter
):
    from _application.artefact.create import ArtefactCreateRequest
    from _application.artefact.list import ArtefactListRequest, ArtefactListLocation
    from _application.artefact.search import ArtefactSearchRequest
    from _application._mutation_support import decode_frontmatter, InlineContent
    from _common import parse_frontmatter
    from _portable.lexical_maintenance import maintain_lexical_index
    from _portable.router_maintenance import maintain_router

    root = command_vault_clone.vault_root
    app = application_for(root)
    created = app.invoke(
        ArtefactCreateRequest(
            type_name,
            "Archive Roundtrip Sentinel",
            content=InlineContent("archiveroundtripsentinel"),
            frontmatter=decode_frontmatter(frontmatter),
        )
    )
    assert created.status == "ok", created
    original = created.result.path
    maintain_router(root, dry_run=False, force=True)
    maintain_lexical_index(root, dry_run=False, force=True)

    def active_paths():
        listed = app.invoke(ArtefactListRequest())
        searched = app.invoke(ArtefactSearchRequest("archiveroundtripsentinel"))
        assert listed.status == searched.status == "ok"
        return {item.path for item in listed.result.items}, {
            item.path for item in searched.result.items
        }

    assert all(original in paths for paths in active_paths())
    archived = app.invoke(ArtefactArchiveRequest(original))
    assert archived.status == "ok", archived.error.message
    assert all(
        original not in paths and archived.result.new_path not in paths
        for paths in active_paths()
    )
    archived_list = app.invoke(
        ArtefactListRequest(location=ArtefactListLocation.ARCHIVED)
    )
    assert archived.result.new_path in {
        item.path for item in archived_list.result.items
    }
    fields, _ = parse_frontmatter((root / archived.result.new_path).read_text())
    assert fields.get("status") == frontmatter.get("status")
    restored = app.invoke(ArtefactUnarchiveRequest(archived.result.new_path))
    assert restored.status == "ok", restored
    assert restored.result.new_path == original
    assert all(original in paths for paths in active_paths())
    deleted = app.invoke(ArtefactDeleteRequest(original))
    assert deleted.status == "ok", deleted
    assert all(original not in paths for paths in active_paths())


def test_archive_index_failure_reports_committed_mutation(
    command_vault_clone, monkeypatch
):
    def fail(*_args, **_kwargs):
        raise OSError("private index failure")

    monkeypatch.setattr("_portable.lexical_maintenance.maintain_lexical_index", fail)
    result = application_for(command_vault_clone.vault_root).invoke(
        ArtefactArchiveRequest(CANDIDATE)
    )
    assert result.status == "partial"
    assert result.committed_effects
    assert not (command_vault_clone.vault_root / CANDIDATE).exists()
    assert "refresh-lexical" in result.error.message
    assert "private index failure" not in result.error.message


def test_archive_preserves_date_like_note_title(command_vault_clone):
    from _application.artefact.create import ArtefactCreateRequest

    app = application_for(command_vault_clone.vault_root)
    created = app.invoke(ArtefactCreateRequest("note", "20260912-Retrospective"))
    assert created.status == "ok", created
    from _portable.router_maintenance import maintain_router

    maintain_router(command_vault_clone.vault_root, dry_run=False, force=True)
    archived = app.invoke(ArtefactArchiveRequest(created.result.path))
    assert archived.status == "ok", archived.error.message
    restored = app.invoke(ArtefactUnarchiveRequest(archived.result.new_path))
    assert restored.status == "ok", restored
    assert restored.result.new_path == created.result.path


def test_partially_applied_archive_reconciles_active_index(
    command_vault_clone, monkeypatch
):
    import edit
    from _application.artefact.search import ArtefactSearchRequest

    original = edit.apply_artefact_transition

    def partially_apply(root, plan):
        original(root, plan)
        raise PartialApplyError("Archive moved the file before a later failure")

    monkeypatch.setattr(edit, "apply_artefact_transition", partially_apply)
    app = application_for(command_vault_clone.vault_root)
    result = app.invoke(ArtefactArchiveRequest(CANDIDATE))
    assert result.status == "partial"
    searched = app.invoke(ArtefactSearchRequest("command fixture candidate"))
    assert searched.status == "ok"
    assert CANDIDATE not in {item.path for item in searched.result.items}
