"""Cross-resource behaviour for the cohesive document editor."""

from __future__ import annotations

import edit
import pytest

from _application._document_edit import EditScope
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
from _application.registry import current_request_resolver
from _application.results import ErrorCode
from _common import parse_frontmatter
from _staging import read_staged_body, stage_body
from command_application import application_for


RESOURCE_CASES = {
    DocumentResource.MEMORY: (
        "brain-core-reference",
        "# Brain Core Reference",
        "Brain-core is",
    ),
    DocumentResource.SKILL: (
        "vault-maintenance",
        "# Vault Maintenance",
        "Read the vault's router",
    ),
    DocumentResource.STYLE: (
        "writing",
        "# Writing Style",
        "Use Australian English",
    ),
    DocumentResource.TEMPLATE: (
        "designs",
        "## Open Decisions",
        "What needs to be decided",
    ),
}


def _change(operation, heading, old_text):
    if operation == "replace":
        return ReplaceChange(
            "replace",
            InlineContent("# Replaced Document\n\nEdited through ownership.\n"),
            target=":body",
            scope=EditScope.SECTION,
        ), "Edited through ownership."
    if operation == "append":
        return AppendChange(
            "append",
            InlineContent("\nAppended through ownership.\n"),
            target=":body",
            scope=EditScope.SECTION,
        ), "Appended through ownership."
    if operation == "prepend":
        return PrependChange(
            "prepend",
            InlineContent("Prepended through ownership.\n\n"),
            target=":body",
            scope=EditScope.SECTION,
        ), "Prepended through ownership."
    if operation == "delete-section":
        return DeleteSectionChange("delete-section", heading), None
    return ReplaceTextChange(
        "replace-text",
        old_text,
        "Replaced through ownership",
    ), "Replaced through ownership"


@pytest.mark.parametrize("resource", tuple(RESOURCE_CASES))
@pytest.mark.parametrize(
    "operation",
    ("replace", "append", "prepend", "delete-section", "replace-text"),
)
def test_document_edit_preserves_all_named_target_and_change_combinations(
    command_vault_clone,
    resource,
    operation,
):
    name, heading, old_text = RESOURCE_CASES[resource]
    change, expected_text = _change(operation, heading, old_text)
    result = application_for(command_vault_clone.vault_root).invoke(
        DocumentEditRequest(DocumentTarget(resource, name), change)
    )

    assert result.status == "ok"
    assert result.committed_effects[0].kind == "document.edit"
    assert result.result.operation == operation
    written = (command_vault_clone.vault_root / result.result.path).read_text()
    if expected_text is None:
        assert heading not in written
    else:
        assert expected_text in written


def test_named_append_preserves_resource_frontmatter_merge_mode(command_vault_clone):
    result = application_for(command_vault_clone.vault_root).invoke(
        DocumentEditRequest(
            DocumentTarget(DocumentResource.MEMORY, "brain-core-reference"),
            AppendChange(
                "append",
                frontmatter=(FrontmatterField("triggers", ("typed-edit",)),),
            ),
        )
    )

    fields, _body = parse_frontmatter(
        (command_vault_clone.vault_root / result.result.path).read_text()
    )
    assert "brain core" in fields["triggers"]
    assert "typed-edit" in fields["triggers"]


def test_named_edit_consumes_stage_only_after_commit(command_vault_clone):
    vault_root = str(command_vault_clone.vault_root)
    handle = stage_body(vault_root, "# Staged Style\n")["handle"]
    result = application_for(command_vault_clone.vault_root).invoke(
        DocumentEditRequest(
            DocumentTarget(DocumentResource.STYLE, "writing"),
            ReplaceChange(
                "replace",
                StagedContent(handle),
                target=":body",
                scope=EditScope.SECTION,
            ),
        )
    )

    assert result.status == "ok"
    assert result.result.staged_handle_consumed is True
    with pytest.raises(ValueError, match="already-consumed"):
        read_staged_body(vault_root, handle)


def test_named_edit_not_found_is_a_no_effect_result(command_vault_clone):
    result = application_for(command_vault_clone.vault_root).invoke(
        DocumentEditRequest(
            DocumentTarget(DocumentResource.STYLE, "missing-style"),
            ReplaceTextChange("replace-text", "old", "new"),
        )
    )

    assert result.error.code is ErrorCode.NOT_FOUND
    assert result.effects == "none"


def test_named_edit_post_commit_failure_is_honestly_unknown(
    command_vault_clone,
    monkeypatch,
):
    real_edit = edit.edit_resource

    def commit_then_fail(*args, **kwargs):
        real_edit(*args, **kwargs)
        raise OSError("response failed after named edit commit")

    monkeypatch.setattr(edit, "edit_resource", commit_then_fail)
    result = application_for(command_vault_clone.vault_root).invoke(
        DocumentEditRequest(
            DocumentTarget(DocumentResource.MEMORY, "brain-core-reference"),
            ReplaceTextChange(
                "replace-text",
                "Brain-core is",
                "Brain Core remains",
            ),
        )
    )

    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
    assert result.effects == "unknown"


def test_named_document_transport_uses_one_explicit_target_contract():
    resolver = current_request_resolver()
    request = resolver.resolve(
        "document.edit",
        {
            "target": {"resource": "skill", "reference": "vault-maintenance"},
            "change": {
                "operation": "append",
                "content": {"source": "inline", "content": "Body.\n"},
                "target": ":body",
                "scope": "section",
            },
        },
    )
    assert request.target.resource is DocumentResource.SKILL
    assert request.change.operation == "append"

    with pytest.raises(ValueError, match="only for artefacts"):
        resolver.resolve(
            "document.edit",
            {
                "target": {"resource": "template", "reference": "designs"},
                "change": {
                    "operation": "append",
                    "content": {"source": "inline", "content": "Body.\n"},
                },
                "fix_links": True,
            },
        )
