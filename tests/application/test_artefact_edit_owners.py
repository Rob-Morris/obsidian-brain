"""Owner behaviour for cohesive artefact document edits."""

from __future__ import annotations

import edit
import pytest

from _application._document_edit import EditScope, StructuralSelector
from _application._mutation_support import FrontmatterField, InlineContent, StagedContent
from _application.document.edit import (
    AppendChange,
    DeleteSectionChange,
    DocumentEditRequest,
    DocumentResource,
    DocumentTarget,
    PrependChange,
    ReplaceChange,
    ReplaceTextChange,
)
from _application.registry import current_application_catalogue, current_request_resolver
from _application.results import ErrorCode
from _application.types import Authority, EffectClass, RetryClass
from _staging import read_staged_body, stage_body
from command_application import application_for


PATH = "Designs/project~command-fixture/Command Fixture Design.md"
TARGET = DocumentTarget(DocumentResource.ARTEFACT, PATH)


@pytest.mark.parametrize(
    ("change", "present", "absent", "operation"),
    (
        (
            ReplaceChange(
                "replace",
                InlineContent("Edited second occurrence.\n"),
                target="## Repeated Target",
                selector=StructuralSelector(occurrence=2),
                scope=EditScope.BODY,
            ),
            "Edited second occurrence.",
            "Second occurrence.",
            "replace",
        ),
        (
            AppendChange(
                "append",
                InlineContent("\nAppended by command.\n"),
                target=":body",
                scope=EditScope.SECTION,
            ),
            "Appended by command.",
            None,
            "append",
        ),
        (
            PrependChange(
                "prepend",
                InlineContent("Preface by command.\n\n"),
                target=":body",
                scope=EditScope.SECTION,
            ),
            "Preface by command.",
            None,
            "prepend",
        ),
        (
            DeleteSectionChange(
                "delete-section",
                "## Repeated Target",
                StructuralSelector(occurrence=1),
            ),
            "Second occurrence.",
            "First occurrence.",
            "delete-section",
        ),
        (
            ReplaceTextChange(
                "replace-text",
                "Second occurrence.",
                "Replaced by command.",
            ),
            "Replaced by command.",
            "Second occurrence.",
            "replace-text",
        ),
    ),
)
def test_document_edit_preserves_every_artefact_change_semantic(
    command_vault_clone,
    change,
    present,
    absent,
    operation,
):
    result = application_for(command_vault_clone.vault_root).invoke(
        DocumentEditRequest(TARGET, change)
    )

    assert result.status == "ok"
    assert result.result.operation == operation
    assert result.result.path == PATH
    assert result.committed_effects[0].kind == "document.edit"
    written = (command_vault_clone.vault_root / PATH).read_text()
    assert present in written
    if absent is not None:
        assert absent not in written


def test_document_edit_returns_typed_structural_target(command_vault_clone):
    result = application_for(command_vault_clone.vault_root).invoke(
        DocumentEditRequest(
            TARGET,
            ReplaceChange(
                "replace",
                InlineContent("Updated.\n"),
                target="## Repeated Target",
                selector=StructuralSelector(occurrence=2),
                scope=EditScope.BODY,
            ),
        )
    )

    target = result.result.structural_target
    assert target.kind == "heading"
    assert target.raw == "## Repeated Target"
    assert target.scope is EditScope.BODY


def test_document_edit_rejects_lifecycle_frontmatter(command_vault_clone):
    before = (command_vault_clone.vault_root / PATH).read_bytes()
    result = application_for(command_vault_clone.vault_root).invoke(
        DocumentEditRequest(
            TARGET,
            ReplaceChange(
                "replace",
                frontmatter=(FrontmatterField("status", "implemented"),),
            ),
        )
    )

    assert result.error.code is ErrorCode.INVALID_REQUEST
    assert "lifecycle-owned" in result.error.message
    assert (command_vault_clone.vault_root / PATH).read_bytes() == before


def test_document_edit_consumes_stage_only_after_commit(command_vault_clone):
    vault_root = str(command_vault_clone.vault_root)
    handle = stage_body(vault_root, "Staged replacement.\n")["handle"]
    result = application_for(command_vault_clone.vault_root).invoke(
        DocumentEditRequest(
            TARGET,
            ReplaceChange(
                "replace",
                StagedContent(handle),
                target="## Repeated Target",
                selector=StructuralSelector(occurrence=2),
                scope=EditScope.BODY,
            ),
        )
    )

    assert result.status == "ok"
    assert result.result.staged_handle_consumed is True
    with pytest.raises(ValueError, match="already-consumed"):
        read_staged_body(vault_root, handle)


def test_document_edit_invalid_target_retains_stage(command_vault_clone):
    vault_root = str(command_vault_clone.vault_root)
    handle = stage_body(vault_root, "Staged replacement.\n")["handle"]
    result = application_for(command_vault_clone.vault_root).invoke(
        DocumentEditRequest(
            TARGET,
            ReplaceChange(
                "replace",
                StagedContent(handle),
                target="## Missing Target",
                scope=EditScope.BODY,
            ),
        )
    )

    assert result.error.code is ErrorCode.INVALID_REQUEST
    assert read_staged_body(vault_root, handle) == "Staged replacement.\n"


def test_document_edit_dry_run_refuses_to_mutate(command_vault_clone):
    before = (command_vault_clone.vault_root / PATH).read_bytes()
    result = application_for(command_vault_clone.vault_root, dry_run=True).invoke(
        DocumentEditRequest(
            TARGET,
            ReplaceTextChange(
                "replace-text",
                "Second occurrence.",
                "Dry run.",
            ),
        )
    )

    assert result.error.code is ErrorCode.INVALID_REQUEST
    assert (command_vault_clone.vault_root / PATH).read_bytes() == before


def test_document_edit_post_commit_failure_is_honestly_unknown(
    command_vault_clone,
    monkeypatch,
):
    real_edit = edit.edit_resource

    def commit_then_fail(*args, **kwargs):
        real_edit(*args, **kwargs)
        raise OSError("response failed after edit commit")

    monkeypatch.setattr(edit, "edit_resource", commit_then_fail)
    result = application_for(command_vault_clone.vault_root).invoke(
        DocumentEditRequest(
            TARGET,
            ReplaceTextChange(
                "replace-text",
                "Second occurrence.",
                "Uncertain edit.",
            ),
        )
    )

    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
    assert result.effects == "unknown"
    assert "Uncertain edit." in (command_vault_clone.vault_root / PATH).read_text()


def test_document_edit_transport_is_strict_and_contributor_authorised():
    resolver = current_request_resolver()
    payload = {
        "target": {"resource": "artefact", "reference": PATH},
        "change": {
            "operation": "replace",
            "content": {"source": "inline", "content": "Edited.\n"},
            "target": "## Repeated Target",
            "selector": {"occurrence": 2},
            "scope": "body",
        },
    }
    request = resolver.resolve("document.edit", payload)

    assert type(request) is DocumentEditRequest
    entry = current_application_catalogue().resolve(request)
    assert entry.authority is Authority.CONTRIBUTOR
    assert entry.effect_class is EffectClass.SELECTED_BRAIN_MUTATION
    assert entry.retry_class is RetryClass.RECEIPT_REQUIRED

    with pytest.raises(ValueError, match="source"):
        resolver.resolve(
            "document.edit",
            {
                "target": {"resource": "artefact", "reference": PATH},
                "change": {
                    "operation": "replace",
                    "content": {"source": "file", "path": "/tmp/body.md"},
                },
            },
        )
    with pytest.raises(ValueError, match="unexpected change fields"):
        resolver.resolve(
            "document.edit",
            {
                "target": {"resource": "artefact", "reference": PATH},
                "change": {
                    "operation": "delete-section",
                    "target": "## Repeated Target",
                    "content": {"source": "inline", "content": "wrong mode"},
                },
            },
        )
    with pytest.raises(ValueError, match="target and scope"):
        resolver.resolve(
            "document.edit",
            {
                "target": {"resource": "artefact", "reference": PATH},
                "change": {
                    "operation": "replace-text",
                    "old_text": "old",
                    "new_text": "new",
                    "target": ":body",
                },
            },
        )


def test_document_edit_rejects_non_artefact_link_fix_before_execution():
    with pytest.raises(ValueError, match="only for artefacts"):
        DocumentEditRequest(
            DocumentTarget(DocumentResource.MEMORY, "brain-core-reference"),
            AppendChange("append", InlineContent("text")),
            fix_links=True,
        )
