"""Owner behaviour for atomic artefact-type definition bundles."""

from __future__ import annotations

import define
import pytest

from _application._mutation_support import InlineContent, StagedContent
from _application.registry import current_application_catalogue, current_request_resolver
from _application.results import ErrorCode
from _application.type._classification import ArtefactTypeClassification
from _application.type.create import TypeCreateRequest
from _application.type.replace import TypeReplaceRequest
from _application.types import Authority, EffectClass, RetryClass
from _staging import read_staged_body, stage_body
from command_application import application_for


TYPE_DEFINITION = """# Widgets

## Naming

`{Title}.md` in `Widgets/`

## Frontmatter

```yaml
---
type: living/widget
status: new
---
```

## Template

[[_Config/Templates/Living/Widgets]]
"""

TYPE_TEMPLATE = """---
type: living/widget
status: new
---
# {{title}}
"""


def test_type_create_commits_and_consumes_one_atomic_staged_bundle(
    command_vault_clone,
):
    root = command_vault_clone.vault_root
    definition_handle = stage_body(str(root), TYPE_DEFINITION)["handle"]
    template_handle = stage_body(str(root), TYPE_TEMPLATE)["handle"]

    result = application_for(root).invoke(
        TypeCreateRequest(
            "widgets",
            ArtefactTypeClassification.LIVING,
            StagedContent(definition_handle),
            StagedContent(template_handle),
        )
    )

    assert result.status == "ok", result.error.message
    assert result.result.name == "widgets"
    assert result.result.classification is ArtefactTypeClassification.LIVING
    assert result.result.path == "_Config/Taxonomy/Living/widgets.md"
    assert result.result.template_path == "_Config/Templates/Living/Widgets.md"
    assert result.result.artefact_folder == "Widgets"
    assert result.result.frontmatter_type == "living/widget"
    assert result.result.before_sha256 is None
    assert result.result.before_template_sha256 is None
    assert result.result.definition_staged_handle_consumed is True
    assert result.result.template_staged_handle_consumed is True
    assert result.committed_effects[0].kind == "type.create"
    assert (root / result.result.path).read_text() == TYPE_DEFINITION
    assert (root / result.result.template_path).read_text() == TYPE_TEMPLATE
    assert (root / result.result.artefact_folder).is_dir()
    for handle in (definition_handle, template_handle):
        with pytest.raises(ValueError, match="already-consumed"):
            read_staged_body(str(root), handle)


def test_type_replace_requires_both_hashes_and_retains_stages_until_commit(
    command_vault_clone,
):
    root = command_vault_clone.vault_root
    created = application_for(root).invoke(
        TypeCreateRequest(
            "widgets",
            ArtefactTypeClassification.LIVING,
            InlineContent(TYPE_DEFINITION),
            InlineContent(TYPE_TEMPLATE),
        )
    )
    assert created.status == "ok", created.error.message
    replacement_definition = TYPE_DEFINITION + "\nReplacement guidance.\n"
    replacement_template = TYPE_TEMPLATE + "\nReplacement template.\n"
    definition_handle = stage_body(str(root), replacement_definition)["handle"]
    template_handle = stage_body(str(root), replacement_template)["handle"]

    stale = application_for(root).invoke(
        TypeReplaceRequest(
            "widgets",
            ArtefactTypeClassification.LIVING,
            StagedContent(definition_handle),
            StagedContent(template_handle),
            created.result.sha256,
            "0" * 64,
        )
    )

    assert stale.error.code is ErrorCode.INVALID_REQUEST
    assert stale.effects == "none"
    assert read_staged_body(str(root), definition_handle) == replacement_definition
    assert read_staged_body(str(root), template_handle) == replacement_template
    assert (root / created.result.path).read_text() == TYPE_DEFINITION
    assert (root / created.result.template_path).read_text() == TYPE_TEMPLATE

    replaced = application_for(root).invoke(
        TypeReplaceRequest(
            "widgets",
            ArtefactTypeClassification.LIVING,
            StagedContent(definition_handle),
            StagedContent(template_handle),
            created.result.sha256,
            created.result.template_sha256,
        )
    )

    assert replaced.status == "ok"
    assert replaced.result.before_sha256 == created.result.sha256
    assert (
        replaced.result.before_template_sha256 == created.result.template_sha256
    )
    assert replaced.result.definition_staged_handle_consumed is True
    assert replaced.result.template_staged_handle_consumed is True
    assert (root / replaced.result.path).read_text() == replacement_definition
    assert (root / replaced.result.template_path).read_text() == replacement_template


def test_type_bundle_failure_rolls_back_both_files_and_retains_stages(
    command_vault_clone,
    monkeypatch,
):
    root = command_vault_clone.vault_root
    created = application_for(root).invoke(
        TypeCreateRequest(
            "widgets",
            ArtefactTypeClassification.LIVING,
            InlineContent(TYPE_DEFINITION),
            InlineContent(TYPE_TEMPLATE),
        )
    )
    assert created.status == "ok", created.error.message
    replacement_definition = TYPE_DEFINITION + "\nCandidate change.\n"
    replacement_template = TYPE_TEMPLATE + "\nCandidate template.\n"
    definition_handle = stage_body(str(root), replacement_definition)["handle"]
    template_handle = stage_body(str(root), replacement_template)["handle"]
    real_safe_write = define.safe_write
    calls = 0

    def fail_template_write(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic template failure")
        return real_safe_write(*args, **kwargs)

    monkeypatch.setattr(define, "safe_write", fail_template_write)
    result = application_for(root).invoke(
        TypeReplaceRequest(
            "widgets",
            ArtefactTypeClassification.LIVING,
            StagedContent(definition_handle),
            StagedContent(template_handle),
            created.result.sha256,
            created.result.template_sha256,
        )
    )

    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
    assert result.effects == "unknown"
    assert (root / created.result.path).read_text() == TYPE_DEFINITION
    assert (root / created.result.template_path).read_text() == TYPE_TEMPLATE
    assert read_staged_body(str(root), definition_handle) == replacement_definition
    assert read_staged_body(str(root), template_handle) == replacement_template


def test_type_bundle_rejects_one_handle_for_two_distinct_documents():
    with pytest.raises(ValueError, match="distinct staged handles"):
        TypeCreateRequest(
            "widgets",
            ArtefactTypeClassification.LIVING,
            StagedContent("body:" + "a" * 32),
            StagedContent("body:" + "a" * 32),
        )


@pytest.mark.parametrize(
    ("command_id", "payload", "request_type"),
    (
        (
            "type.create",
            {
                "name": "widgets",
                "classification": "living",
                "definition": {"source": "inline", "content": TYPE_DEFINITION},
                "template": {"source": "inline", "content": TYPE_TEMPLATE},
            },
            TypeCreateRequest,
        ),
        (
            "type.replace",
            {
                "name": "widgets",
                "classification": "living",
                "definition": {"source": "stage", "handle": "body:" + "1" * 32},
                "template": {"source": "stage", "handle": "body:" + "2" * 32},
                "expected_sha256": "3" * 64,
                "expected_template_sha256": "4" * 64,
            },
            TypeReplaceRequest,
        ),
    ),
)
def test_type_definition_transports_are_granular_operator_commands(
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


def test_type_definition_transports_reject_cross_verb_and_bad_enum_fields():
    resolver = current_request_resolver()
    with pytest.raises(ValueError, match="unexpected fields"):
        resolver.resolve(
            "type.create",
            {
                "name": "widgets",
                "classification": "living",
                "definition": {"source": "inline", "content": TYPE_DEFINITION},
                "template": {"source": "inline", "content": TYPE_TEMPLATE},
                "expected_sha256": "0" * 64,
            },
        )
    with pytest.raises(ValueError, match="living.*temporal"):
        resolver.resolve(
            "type.create",
            {
                "name": "widgets",
                "classification": "remote",
                "definition": {"source": "inline", "content": TYPE_DEFINITION},
                "template": {"source": "inline", "content": TYPE_TEMPLATE},
            },
        )


def test_type_definition_mutations_reject_context_dry_run(command_vault_clone):
    result = application_for(command_vault_clone.vault_root, dry_run=True).invoke(
        TypeCreateRequest(
            "widgets",
            ArtefactTypeClassification.LIVING,
            InlineContent(TYPE_DEFINITION),
            InlineContent(TYPE_TEMPLATE),
        )
    )

    assert result.error.code is ErrorCode.INVALID_REQUEST
    assert result.effects == "none"
    assert not (
        command_vault_clone.vault_root / "_Config/Taxonomy/Living/widgets.md"
    ).exists()
