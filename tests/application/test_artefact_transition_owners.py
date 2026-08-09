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
    assert result.result.type == "living/designs"
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
    assert restored.result.new_path == "Ideas/Command Fixture Adopted.md"
    assert restored.result.uninspected == ()
    assert (command_vault_clone.vault_root / restored.result.new_path).is_file()


def test_artefact_archive_requires_terminal_status(command_vault_clone):
    result = application_for(command_vault_clone.vault_root).invoke(
        ArtefactArchiveRequest(CANDIDATE)
    )

    assert result.error.code is ErrorCode.INVALID_REQUEST
    assert "is not terminal" in result.error.message
    assert (command_vault_clone.vault_root / CANDIDATE).is_file()


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
    ("command_id", "payload", "request_type"),
    (
        (
            "artefact.rename",
            {"source": DESIGN, "dest": "Designs/Renamed.md"},
            ArtefactRenameRequest,
        ),
        (
            "artefact.convert",
            {"path": CANDIDATE, "target_type": "design", "recursive": True},
            ArtefactConvertRequest,
        ),
        (
            "artefact.archive",
            {"path": TERMINAL, "recursive": False},
            ArtefactArchiveRequest,
        ),
        (
            "artefact.unarchive",
            {"path": "_Archive/Ideas/example.md"},
            ArtefactUnarchiveRequest,
        ),
        (
            "artefact.delete",
            {"path": DESIGN, "recursive": True},
            ArtefactDeleteRequest,
        ),
    ),
)
def test_transition_transport_and_operator_catalogue_contract(
    command_id,
    payload,
    request_type,
):
    request = current_request_resolver().resolve(command_id, payload)
    entry = current_application_catalogue().resolve(request)

    assert type(request) is request_type
    assert entry.authority is Authority.OPERATOR
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
    real_rename = rename.rename_artefact
    destination = (
        "Designs/project~command-fixture/Uncertain Command Fixture Design.md"
    )

    def commit_then_report_partial(*args, **kwargs):
        real_rename(*args, **kwargs)
        raise PartialApplyError("links changed and move committed; refresh failed")

    monkeypatch.setattr(rename, "rename_artefact", commit_then_report_partial)
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
    real_delete = rename.delete_and_clean_links

    def commit_then_fail(*args, **kwargs):
        real_delete(*args, **kwargs)
        raise OSError("response failed after delete commit")

    monkeypatch.setattr(rename, "delete_and_clean_links", commit_then_fail)
    result = application_for(command_vault_clone.vault_root).invoke(
        ArtefactDeleteRequest(DESIGN)
    )

    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
    assert result.effects == "unknown"
    assert not (command_vault_clone.vault_root / DESIGN).exists()
