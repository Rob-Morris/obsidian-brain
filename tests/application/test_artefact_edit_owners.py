"""Behaviour of cohesive artefact document mutation owners."""

from __future__ import annotations

from contextlib import contextmanager

import edit
import fix_links
import pytest
import _common

from _application._mutation_support import FrontmatterField, InlineContent, StagedContent
from _application.artefact.read import ArtefactReadRequest
from _application.document._types import DocumentLocator, DocumentResource
from _application.document.edit import (
    CalloutPart,
    CalloutSelection,
    DeleteStructure,
    DocumentEditRequest,
    HeadingBlockSelection,
    HeadingPart,
    HeadingSelection,
    InsertPosition,
    InsertStructure,
    ReplaceStructure,
)
from _application.document.patch import (
    AllMatches,
    DocumentPatchRequest,
    OccurrenceMatch,
    UniqueMatch,
)
from _application.document.update_frontmatter import DocumentUpdateFrontmatterRequest
from _application.document.write import DocumentWriteOperation, DocumentWriteRequest
from _application.registry import current_request_resolver
from _application.results import ErrorCode
from _common import document_revision_at, parse_frontmatter
from _staging import read_staged_body, stage_body
from command_application import application_for


PATH = "Designs/project~command-fixture/Command Fixture Design.md"
DOCUMENT = DocumentLocator(DocumentResource.ARTEFACT, PATH)


def _revision(vault_root):
    return document_revision_at(vault_root / PATH)


@pytest.mark.parametrize(
    ("operation", "content", "present"),
    (
        (DocumentWriteOperation.REPLACE, "# Replaced\n", "# Replaced"),
        (DocumentWriteOperation.APPEND, "\nAppended.\n", "Appended."),
        (DocumentWriteOperation.PREPEND, "Prepended.\n\n", "Prepended."),
    ),
)
def test_document_write_mutates_only_the_complete_body(
    command_vault_clone,
    operation,
    content,
    present,
):
    path = command_vault_clone.vault_root / PATH
    before_fields, _before_body = parse_frontmatter(path.read_text())

    result = application_for(command_vault_clone.vault_root).invoke(
        DocumentWriteRequest(
            DOCUMENT,
            _revision(command_vault_clone.vault_root),
            operation,
            InlineContent(content),
        )
    )

    assert result.status == "ok"
    assert result.result.operation == operation.value
    assert result.result.revision == _revision(command_vault_clone.vault_root)
    after_fields, after_body = parse_frontmatter(path.read_text())
    assert present in after_body
    assert after_fields.keys() == before_fields.keys()


def test_document_patch_exposes_explicit_match_policies(command_vault_clone):
    application = application_for(command_vault_clone.vault_root)
    ambiguous = application.invoke(
        DocumentPatchRequest(
            DOCUMENT,
            _revision(command_vault_clone.vault_root),
            "occurrence.",
            "match.",
            UniqueMatch(),
        )
    )
    assert ambiguous.error.code is ErrorCode.INVALID_REQUEST

    one = application.invoke(
        DocumentPatchRequest(
            DOCUMENT,
            _revision(command_vault_clone.vault_root),
            "occurrence.",
            "selected.",
            OccurrenceMatch(2),
        )
    )
    assert one.status == "ok"
    assert one.result.match_count == 2
    assert one.result.replacement_count == 1

    all_matches = application.invoke(
        DocumentPatchRequest(
            DOCUMENT,
            _revision(command_vault_clone.vault_root),
            "Repeated Target",
            "Repeated Heading",
            AllMatches(),
        )
    )
    assert all_matches.status == "ok"
    assert all_matches.result.replacement_count == 2


def test_document_edit_uses_typed_markdown_selection(command_vault_clone):
    result = application_for(command_vault_clone.vault_root).invoke(
        DocumentEditRequest(
            DOCUMENT,
            _revision(command_vault_clone.vault_root),
            ReplaceStructure(
                HeadingSelection(
                    "Repeated Target",
                    HeadingPart.BODY,
                    level=2,
                    occurrence=2,
                ),
                InlineContent("Updated structurally.\n"),
            ),
        )
    )

    assert result.status == "ok"
    assert result.result.operation == "replace"
    assert result.result.structural_target.kind == "heading"
    assert result.result.structural_target.part == "body"
    written = (command_vault_clone.vault_root / PATH).read_text()
    assert "Updated structurally." in written
    assert "First occurrence." in written


def test_document_edit_insert_and_delete_are_distinct_structural_changes(
    command_vault_clone,
):
    application = application_for(command_vault_clone.vault_root)
    inserted = application.invoke(
        DocumentEditRequest(
            DOCUMENT,
            _revision(command_vault_clone.vault_root),
            InsertStructure(
                HeadingSelection("Repeated Target", HeadingPart.BODY, level=2, occurrence=1),
                InsertPosition.END,
                InlineContent("Inserted structurally.\n"),
            ),
        )
    )
    assert inserted.status == "ok"

    deleted = application.invoke(
        DocumentEditRequest(
            DOCUMENT,
            _revision(command_vault_clone.vault_root),
            DeleteStructure(HeadingBlockSelection("Repeated Target", level=2, occurrence=1)),
        )
    )
    assert deleted.status == "ok"
    assert deleted.result.operation == "delete"
    assert "First occurrence." not in (command_vault_clone.vault_root / PATH).read_text()


def test_document_edit_insert_rejects_non_container_header_ranges():
    with pytest.raises(ValueError, match="content range"):
        InsertStructure(
            HeadingSelection("Title", HeadingPart.HEADING),
            InsertPosition.END,
            InlineContent("not a heading suffix"),
        )
    with pytest.raises(ValueError, match="content range"):
        InsertStructure(
            CalloutSelection("note", CalloutPart.HEADER),
            InsertPosition.END,
            InlineContent("not a callout header suffix"),
        )


def test_document_edit_exposes_callouts_as_semantic_selections(command_vault_clone):
    vault_root = command_vault_clone.vault_root
    path = vault_root / PATH
    fields, body = parse_frontmatter(path.read_text())
    body += "\n> [!note] Status\n> Original.\n"
    from _common import serialize_frontmatter

    path.write_text(serialize_frontmatter(fields) + body)
    result = application_for(vault_root).invoke(
        DocumentEditRequest(
            DOCUMENT,
            _revision(vault_root),
            ReplaceStructure(
                CalloutSelection("note", CalloutPart.BODY, title="Status"),
                InlineContent("> Updated.\n"),
            ),
        )
    )

    assert result.status == "ok"
    assert result.result.structural_target.kind == "callout"
    assert result.result.structural_target.part == "body"
    assert "> [!note] Status\n> Updated.\n" in path.read_text()


def test_successful_staged_write_consumes_handle_after_commit(command_vault_clone):
    vault_root = command_vault_clone.vault_root
    handle = stage_body(str(vault_root), "# Staged replacement\n")['handle']

    result = application_for(vault_root).invoke(
        DocumentWriteRequest(
            DOCUMENT,
            _revision(vault_root),
            DocumentWriteOperation.REPLACE,
            StagedContent(handle),
        )
    )

    assert result.status == "ok"
    assert result.result.staged_handle_consumed is True
    with pytest.raises(ValueError, match="already-consumed"):
        read_staged_body(str(vault_root), handle)


def test_frontmatter_update_has_one_set_or_remove_policy(command_vault_clone):
    path = command_vault_clone.vault_root / PATH
    fields, _body = parse_frontmatter(path.read_text())
    removable = next(name for name in fields if name not in {"status", "key", "parent"})
    result = application_for(command_vault_clone.vault_root).invoke(
        DocumentUpdateFrontmatterRequest(
            DOCUMENT,
            _revision(command_vault_clone.vault_root),
            (
                FrontmatterField("reviewed", True),
                FrontmatterField(removable, None),
            )
            if "reviewed" < removable
            else (
                FrontmatterField(removable, None),
                FrontmatterField("reviewed", True),
            ),
        )
    )

    assert result.status == "ok"
    assert result.result.updated_fields == ("reviewed",)
    assert result.result.removed_fields == (removable,)
    updated, _body = parse_frontmatter(path.read_text())
    assert updated["reviewed"] in {True, "True"}
    assert removable not in updated


def test_stale_revision_fails_before_stage_consumption_or_effect(command_vault_clone):
    vault_root = command_vault_clone.vault_root
    path = vault_root / PATH
    stale_revision = _revision(vault_root)
    handle = stage_body(str(vault_root), "Staged replacement.\n")["handle"]
    path.write_text(path.read_text() + "\nHuman change.\n")
    before = path.read_bytes()

    result = application_for(vault_root).invoke(
        DocumentWriteRequest(
            DOCUMENT,
            stale_revision,
            DocumentWriteOperation.REPLACE,
            StagedContent(handle),
        )
    )

    assert result.error.code is ErrorCode.CONFLICT
    assert result.effects == "none"
    assert result.error.details.field == "expected_revision"
    assert result.error.next_action.command_id == "artefact.read"
    assert path.read_bytes() == before
    assert read_staged_body(str(vault_root), handle) == "Staged replacement.\n"


def test_reads_and_mutations_share_the_same_revision(command_vault_clone):
    application = application_for(command_vault_clone.vault_root)
    read_result = application.invoke(ArtefactReadRequest(PATH))
    assert read_result.result.revision == _revision(command_vault_clone.vault_root)

    mutation = application.invoke(
        DocumentPatchRequest(
            DOCUMENT,
            read_result.result.revision,
            "Second occurrence.",
            "Revision checked.",
            UniqueMatch(),
        )
    )
    assert mutation.result.revision == _revision(command_vault_clone.vault_root)
    assert mutation.result.revision != read_result.result.revision


def test_document_mutation_reuses_one_prewrite_snapshot(
    command_vault_clone,
    monkeypatch,
):
    vault_root = command_vault_clone.vault_root
    target = str(vault_root / PATH)
    reads = 0
    original = edit.read_exact_file_content

    def count_target_read(path):
        nonlocal reads
        if str(path) == target:
            reads += 1
        return original(path)

    monkeypatch.setattr(edit, "read_exact_file_content", count_target_read)
    result = application_for(vault_root).invoke(
        DocumentPatchRequest(
            DOCUMENT,
            _revision(vault_root),
            "Second occurrence.",
            "Snapshot reused.",
            UniqueMatch(),
        )
    )

    assert result.status == "ok"
    assert reads == 1


def test_crlf_read_revision_can_be_used_for_an_immediate_mutation(
    command_vault_clone,
):
    vault_root = command_vault_clone.vault_root
    path = vault_root / PATH
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    application = application_for(vault_root)

    read_result = application.invoke(ArtefactReadRequest(PATH))

    assert "\r" not in read_result.result.content
    assert read_result.result.revision == document_revision_at(path)
    mutation = application.invoke(
        DocumentPatchRequest(
            DOCUMENT,
            read_result.result.revision,
            "Second occurrence.",
            "CRLF revision checked.",
            UniqueMatch(),
        )
    )
    assert mutation.status == "ok"
    assert mutation.result.revision == document_revision_at(path)


@pytest.mark.parametrize("failure_type", (OSError, ValueError, FileNotFoundError))
def test_document_mutation_post_commit_failure_is_honestly_unknown(
    command_vault_clone,
    monkeypatch,
    failure_type,
):
    real_edit = edit.edit_resource

    def commit_then_fail(*args, **kwargs):
        real_edit(*args, **kwargs)
        raise failure_type("response failed after edit commit")

    monkeypatch.setattr(edit, "edit_resource", commit_then_fail)
    result = application_for(command_vault_clone.vault_root).invoke(
        DocumentPatchRequest(
            DOCUMENT,
            _revision(command_vault_clone.vault_root),
            "Second occurrence.",
            "Uncertain edit.",
            UniqueMatch(),
        )
    )

    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
    assert result.effects == "unknown"
    assert "Uncertain edit." in (command_vault_clone.vault_root / PATH).read_text()


def test_post_failure_revision_classification_occurs_under_mutation_lock(
    command_vault_clone,
    monkeypatch,
):
    lock_active = False
    real_edit = edit.edit_resource
    real_revision = edit.current_document_revision

    @contextmanager
    def tracked_lock(*_args, **_kwargs):
        nonlocal lock_active
        lock_active = True
        try:
            yield
        finally:
            lock_active = False

    def commit_then_fail(*args, **kwargs):
        real_edit(*args, **kwargs)
        raise ValueError("response failed after edit commit")

    def classify_revision(*args, **kwargs):
        assert lock_active is True
        return real_revision(*args, **kwargs)

    monkeypatch.setattr(_common, "vault_mutation_lock", tracked_lock)
    monkeypatch.setattr(edit, "edit_resource", commit_then_fail)
    monkeypatch.setattr(edit, "current_document_revision", classify_revision)

    result = application_for(command_vault_clone.vault_root).invoke(
        DocumentPatchRequest(
            DOCUMENT,
            _revision(command_vault_clone.vault_root),
            "Second occurrence.",
            "Classified under lock.",
            UniqueMatch(),
        )
    )

    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN


def test_requested_wikilink_failure_reports_known_partial_commit(
    command_vault_clone,
    monkeypatch,
):
    def fail_processing(*_args, **_kwargs):
        raise ValueError("link index unavailable")

    monkeypatch.setattr(fix_links, "check_wikilinks_in_file", fail_processing)
    result = application_for(command_vault_clone.vault_root).invoke(
        DocumentWriteRequest(
            DOCUMENT,
            _revision(command_vault_clone.vault_root),
            DocumentWriteOperation.APPEND,
            InlineContent("\nCommitted with [[missing-link]].\n"),
            fix_links=True,
        )
    )

    assert result.status == "partial"
    assert result.committed_effects[0].subject == PATH
    assert result.error.code is ErrorCode.CONFLICT
    assert "wikilink processing failed" in result.error.message
    assert "Committed with [[missing-link]]." in (
        command_vault_clone.vault_root / PATH
    ).read_text()


def test_document_command_transport_rejects_old_aggregate_shape():
    resolver = current_request_resolver()
    with pytest.raises(ValueError, match="unexpected fields"):
        resolver.resolve(
            "document.edit",
            {
                "target": {"resource": "artefact", "reference": PATH},
                "change": {"operation": "replace-text", "old_text": "old", "new_text": "new"},
            },
        )
