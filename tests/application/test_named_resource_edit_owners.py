"""Owner behaviour for granular named-document mutations."""

from __future__ import annotations

import edit
import pytest

from _application._document_edit import EditScope
from _application._mutation_support import FrontmatterField, InlineContent, StagedContent
from _application._named_edit_requests import NAMED_EDIT_REQUEST_TYPES
from _application.registry import current_application_catalogue, current_request_resolver
from _application.results import ErrorCode
from _application.types import Authority, EffectClass, RetryClass
from _common import parse_frontmatter
from _staging import read_staged_body, stage_body
from command_application import application_for


RESOURCE_CASES = {
    "memory": ("brain-core-reference", "# Brain Core Reference", "Brain-core is"),
    "skill": ("vault-maintenance", "# Vault Maintenance", "Read the vault's router"),
    "style": ("writing", "# Writing Style", "Use Australian English"),
    "template": ("designs", "## Open Decisions", "What needs to be decided"),
}


def _request_for(request_type):
    resource, verb = request_type.COMMAND_ID.split(".", 1)
    name, delete_target, old_text = RESOURCE_CASES[resource]
    if verb == "edit":
        return request_type(
            name,
            InlineContent("# Replaced Document\n\nEdited through typed ownership.\n"),
            target=":body",
            scope=EditScope.SECTION,
        ), "Edited through typed ownership."
    if verb == "append":
        return request_type(
            name,
            InlineContent("\nAppended through typed ownership.\n"),
            target=":body",
            scope=EditScope.SECTION,
        ), "Appended through typed ownership."
    if verb == "prepend":
        return request_type(
            name,
            InlineContent("Prepended through typed ownership.\n\n"),
            target=":body",
            scope=EditScope.SECTION,
        ), "Prepended through typed ownership."
    if verb == "delete-section":
        return request_type(name, delete_target), None
    assert verb == "replace-text"
    return request_type(name, old_text, "Replaced through typed ownership"), (
        "Replaced through typed ownership"
    )


@pytest.mark.parametrize("request_type", NAMED_EDIT_REQUEST_TYPES)
def test_named_resource_edit_owners_keep_distinct_command_identity(
    command_vault_clone,
    request_type,
):
    command_request, expected_text = _request_for(request_type)
    application = application_for(command_vault_clone.vault_root)

    result = application.invoke(command_request)

    assert result.status == "ok"
    assert result.committed_effects[0].kind == request_type.COMMAND_ID
    assert result.result.operation == request_type.COMMAND_ID.split(".", 1)[1]
    written = (command_vault_clone.vault_root / result.result.path).read_text()
    if expected_text is not None:
        assert expected_text in written
    else:
        assert command_request.target not in written


def test_named_append_preserves_resource_frontmatter_merge_mode(
    command_vault_clone,
):
    from _application.memory.append import MemoryAppendRequest

    application = application_for(command_vault_clone.vault_root)
    result = application.invoke(
        MemoryAppendRequest(
            "brain-core-reference",
            frontmatter=(FrontmatterField("triggers", ("typed-edit",)),),
        )
    )

    fields, _body = parse_frontmatter(
        (command_vault_clone.vault_root / result.result.path).read_text()
    )
    assert "brain core" in fields["triggers"]
    assert "typed-edit" in fields["triggers"]


def test_named_edit_consumes_stage_only_after_commit(command_vault_clone):
    from _application.style.edit import StyleEditRequest

    vault_root = str(command_vault_clone.vault_root)
    handle = stage_body(vault_root, "# Staged Style\n")["handle"]
    application = application_for(command_vault_clone.vault_root)

    result = application.invoke(
        StyleEditRequest(
            "writing",
            StagedContent(handle),
            target=":body",
            scope=EditScope.SECTION,
        )
    )

    assert result.status == "ok"
    assert result.result.staged_handle_consumed is True
    with pytest.raises(ValueError, match="already-consumed"):
        read_staged_body(vault_root, handle)


def test_named_edit_not_found_is_a_no_effect_result(command_vault_clone):
    from _application.style.replace_text import StyleReplaceTextRequest

    application = application_for(command_vault_clone.vault_root)
    result = application.invoke(
        StyleReplaceTextRequest("missing-style", "old", "new")
    )

    assert result.error.code is ErrorCode.NOT_FOUND
    assert result.effects == "none"


def test_named_edit_post_commit_failure_is_honestly_unknown(
    command_vault_clone,
    monkeypatch,
):
    from _application.memory.replace_text import MemoryReplaceTextRequest

    real_edit = edit.edit_resource

    def commit_then_fail(*args, **kwargs):
        real_edit(*args, **kwargs)
        raise OSError("response failed after named edit commit")

    monkeypatch.setattr(edit, "edit_resource", commit_then_fail)
    application = application_for(command_vault_clone.vault_root)

    result = application.invoke(
        MemoryReplaceTextRequest(
            "brain-core-reference",
            "Brain-core is",
            "Brain Core remains",
        )
    )

    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
    assert result.effects == "unknown"


def _transport_payload(command_id: str) -> dict:
    resource, verb = command_id.split(".", 1)
    name = RESOURCE_CASES[resource][0]
    if verb in {"edit", "append", "prepend"}:
        return {
            "name": name,
            "content": {"source": "inline", "content": "Body.\n"},
            "target": ":body",
            "scope": "section",
        }
    if verb == "delete-section":
        return {"name": name, "target": RESOURCE_CASES[resource][1]}
    return {"name": name, "old_text": "old", "new_text": "new"}


def test_named_edit_transports_are_granular_and_strict():
    resolver = current_request_resolver()
    catalogue = current_application_catalogue()

    for request_type in NAMED_EDIT_REQUEST_TYPES:
        request = resolver.resolve(
            request_type.COMMAND_ID,
            _transport_payload(request_type.COMMAND_ID),
        )
        assert type(request) is request_type
        entry = catalogue.resolve(request)
        assert entry.authority is Authority.CONTRIBUTOR
        assert entry.effect_class is EffectClass.SELECTED_BRAIN_MUTATION
        assert entry.retry_class is RetryClass.RECEIPT_REQUIRED

    with pytest.raises(ValueError, match="source"):
        resolver.resolve(
            "skill.edit",
            {
                "name": "vault-maintenance",
                "content": {"source": "file", "path": "/tmp/body.md"},
                "target": ":body",
                "scope": "section",
            },
        )
    with pytest.raises(ValueError, match="unexpected request fields"):
        resolver.resolve(
            "template.append",
            {
                "name": "designs",
                "content": {"source": "inline", "content": "Body.\n"},
                "target": ":body",
                "scope": "section",
                "fix_links": True,
            },
        )
