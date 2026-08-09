"""Owner behaviour for granular artefact document mutations."""

from __future__ import annotations

import edit
import pytest

from _application._document_edit import EditScope, StructuralSelector
from _application._mutation_support import FrontmatterField, InlineContent, StagedContent
from _application.artefact.append import ArtefactAppendRequest
from _application.artefact.delete_section import ArtefactDeleteSectionRequest
from _application.artefact.edit import ArtefactEditRequest
from _application.artefact.prepend import ArtefactPrependRequest
from _application.artefact.replace_text import ArtefactReplaceTextRequest
from _application.registry import current_application_catalogue, current_request_resolver
from _application.results import ErrorCode
from _application.types import Authority, EffectClass, RetryClass
from _staging import read_staged_body, stage_body
from command_application import application_for


PATH = "Designs/project~command-fixture/Command Fixture Design.md"


@pytest.mark.parametrize(
    ("command_request", "present", "absent", "operation"),
    (
        (
            ArtefactEditRequest(
                PATH,
                InlineContent("Edited second occurrence.\n"),
                target="## Repeated Target",
                selector=StructuralSelector(occurrence=2),
                scope=EditScope.BODY,
            ),
            "Edited second occurrence.",
            "Second occurrence.",
            "edit",
        ),
        (
            ArtefactAppendRequest(
                PATH,
                InlineContent("\nAppended by command.\n"),
                target=":body",
                scope=EditScope.SECTION,
            ),
            "Appended by command.",
            None,
            "append",
        ),
        (
            ArtefactPrependRequest(
                PATH,
                InlineContent("Preface by command.\n\n"),
                target=":body",
                scope=EditScope.SECTION,
            ),
            "Preface by command.",
            None,
            "prepend",
        ),
        (
            ArtefactDeleteSectionRequest(
                PATH,
                "## Repeated Target",
                StructuralSelector(occurrence=1),
            ),
            "Second occurrence.",
            "First occurrence.",
            "delete-section",
        ),
        (
            ArtefactReplaceTextRequest(
                PATH,
                "Second occurrence.",
                "Replaced by command.",
            ),
            "Replaced by command.",
            "Second occurrence.",
            "replace-text",
        ),
    ),
)
def test_artefact_edit_owners_preserve_structural_semantics(
    command_vault_clone,
    command_request,
    present,
    absent,
    operation,
):
    application = application_for(command_vault_clone.vault_root)

    result = application.invoke(command_request)

    assert result.status == "ok"
    assert result.result.operation == operation
    assert result.result.path == PATH
    assert len(result.committed_effects) == 1
    assert result.committed_effects[0].kind == command_request.COMMAND_ID
    written = (command_vault_clone.vault_root / PATH).read_text()
    assert present in written
    if absent is not None:
        assert absent not in written


def test_artefact_edit_returns_typed_structural_target(command_vault_clone):
    application = application_for(command_vault_clone.vault_root)

    result = application.invoke(
        ArtefactEditRequest(
            PATH,
            InlineContent("Updated.\n"),
            target="## Repeated Target",
            selector=StructuralSelector(occurrence=2),
            scope=EditScope.BODY,
        )
    )

    target = result.result.structural_target
    assert target.kind == "heading"
    assert target.raw == "## Repeated Target"
    assert target.scope is EditScope.BODY
    assert "Repeated Target" in target.display


def test_artefact_edit_rejects_lifecycle_frontmatter(command_vault_clone):
    application = application_for(command_vault_clone.vault_root)
    before = (command_vault_clone.vault_root / PATH).read_bytes()

    result = application.invoke(
        ArtefactEditRequest(
            PATH,
            frontmatter=(FrontmatterField("status", "implemented"),),
        )
    )

    assert result.error.code is ErrorCode.INVALID_REQUEST
    assert "lifecycle-owned" in result.error.message
    assert (command_vault_clone.vault_root / PATH).read_bytes() == before


def test_artefact_edit_consumes_stage_only_after_commit(command_vault_clone):
    vault_root = str(command_vault_clone.vault_root)
    handle = stage_body(vault_root, "Staged replacement.\n")["handle"]
    application = application_for(command_vault_clone.vault_root)

    result = application.invoke(
        ArtefactEditRequest(
            PATH,
            StagedContent(handle),
            target="## Repeated Target",
            selector=StructuralSelector(occurrence=2),
            scope=EditScope.BODY,
        )
    )

    assert result.status == "ok"
    assert result.result.staged_handle_consumed is True
    with pytest.raises(ValueError, match="already-consumed"):
        read_staged_body(vault_root, handle)


def test_artefact_edit_invalid_target_retains_stage(command_vault_clone):
    vault_root = str(command_vault_clone.vault_root)
    handle = stage_body(vault_root, "Staged replacement.\n")["handle"]
    application = application_for(command_vault_clone.vault_root)

    result = application.invoke(
        ArtefactEditRequest(
            PATH,
            StagedContent(handle),
            target="## Missing Target",
            scope=EditScope.BODY,
        )
    )

    assert result.error.code is ErrorCode.INVALID_REQUEST
    assert read_staged_body(vault_root, handle) == "Staged replacement.\n"


def test_artefact_edit_dry_run_refuses_to_mutate(command_vault_clone):
    before = (command_vault_clone.vault_root / PATH).read_bytes()
    application = application_for(command_vault_clone.vault_root, dry_run=True)

    result = application.invoke(
        ArtefactReplaceTextRequest(PATH, "Second occurrence.", "Dry run.")
    )

    assert result.error.code is ErrorCode.INVALID_REQUEST
    assert (command_vault_clone.vault_root / PATH).read_bytes() == before


def test_artefact_edit_post_commit_failure_is_honestly_unknown(
    command_vault_clone,
    monkeypatch,
):
    real_edit = edit.edit_resource

    def commit_then_fail(*args, **kwargs):
        real_edit(*args, **kwargs)
        raise OSError("response failed after edit commit")

    monkeypatch.setattr(edit, "edit_resource", commit_then_fail)
    application = application_for(command_vault_clone.vault_root)

    result = application.invoke(
        ArtefactReplaceTextRequest(PATH, "Second occurrence.", "Uncertain edit.")
    )

    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
    assert result.effects == "unknown"
    assert "Uncertain edit." in (
        command_vault_clone.vault_root / PATH
    ).read_text()


@pytest.mark.parametrize(
    ("command_id", "payload", "request_type"),
    (
        (
            "artefact.edit",
            {
                "path": PATH,
                "content": {"source": "inline", "content": "Edited.\n"},
                "target": "## Repeated Target",
                "selector": {"occurrence": 2},
                "scope": "body",
            },
            ArtefactEditRequest,
        ),
        (
            "artefact.append",
            {
                "path": PATH,
                "content": {"source": "inline", "content": "Append.\n"},
                "target": ":body",
                "scope": "section",
            },
            ArtefactAppendRequest,
        ),
        (
            "artefact.prepend",
            {
                "path": PATH,
                "content": {"source": "inline", "content": "Prepend.\n"},
                "target": ":body",
                "scope": "section",
            },
            ArtefactPrependRequest,
        ),
        (
            "artefact.delete-section",
            {"path": PATH, "target": "## Repeated Target"},
            ArtefactDeleteSectionRequest,
        ),
        (
            "artefact.replace-text",
            {"path": PATH, "old_text": "old", "new_text": "new"},
            ArtefactReplaceTextRequest,
        ),
    ),
)
def test_artefact_edit_transports_are_granular_and_strict(
    command_id,
    payload,
    request_type,
):
    resolver = current_request_resolver()

    request = resolver.resolve(command_id, payload)

    assert type(request) is request_type
    entry = current_application_catalogue().resolve(request)
    assert entry.authority is Authority.CONTRIBUTOR
    assert entry.effect_class is EffectClass.SELECTED_BRAIN_MUTATION
    assert entry.retry_class is RetryClass.RECEIPT_REQUIRED


def test_artefact_edit_transport_rejects_caller_file_and_cross_verb_fields():
    resolver = current_request_resolver()
    with pytest.raises(ValueError, match="source"):
        resolver.resolve(
            "artefact.edit",
            {
                "path": PATH,
                "content": {"source": "file", "path": "/tmp/body.md"},
                "target": ":body",
                "scope": "section",
            },
        )
    with pytest.raises(ValueError, match="unexpected request fields"):
        resolver.resolve(
            "artefact.delete-section",
            {
                "path": PATH,
                "target": "## Repeated Target",
                "content": {"source": "inline", "content": "wrong verb"},
            },
        )
    with pytest.raises(ValueError, match="unexpected request fields"):
        resolver.resolve(
            "artefact.replace-text",
            {
                "path": PATH,
                "old_text": "old",
                "new_text": "new",
                "frontmatter": {"summary": "wrong verb"},
            },
        )
    with pytest.raises(ValueError, match="target and scope"):
        resolver.resolve(
            "artefact.replace-text",
            {
                "path": PATH,
                "old_text": "old",
                "new_text": "new",
                "target": ":body",
            },
        )
