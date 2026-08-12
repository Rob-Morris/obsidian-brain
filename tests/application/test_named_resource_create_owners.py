"""Owner behaviour for named memory, skill, and style creation."""

from __future__ import annotations

import create
import pytest

from _application._mutation_support import (
    FrontmatterField,
    InlineContent,
    StagedContent,
)
from _application.registry import current_application_catalogue, current_request_resolver
from _application.resource.create import (
    MemoryCreateTarget,
    ResourceCreateRequest,
    SkillCreateTarget,
    StyleCreateTarget,
    TemplateCreateTarget,
)
from _application.results import ErrorCode
from _application.types import Authority, EffectClass, RetryClass
from _common import parse_frontmatter
from _staging import read_staged_body, stage_body
from command_application import application_for


@pytest.mark.parametrize(
    ("command_request", "expected_path", "expected_resource"),
    (
        (
            ResourceCreateRequest(
                MemoryCreateTarget(
                    "memory",
                    "command-memory",
                    (FrontmatterField("triggers", ("command", "boundary")),),
                ),
                InlineContent("Remember the command boundary.\n"),
            ),
            "_Config/Memories/command-memory.md",
            "memory",
        ),
        (
            ResourceCreateRequest(
                SkillCreateTarget("skill", "command-skill"),
                InlineContent("# Command Skill\n\nUse typed commands.\n"),
            ),
            "_Config/Skills/command-skill/SKILL.md",
            "skill",
        ),
        (
            ResourceCreateRequest(
                StyleCreateTarget("style", "command-style"),
                InlineContent("# Command Style\n\nBe explicit.\n"),
            ),
            "_Config/Styles/command-style.md",
            "style",
        ),
    ),
)
def test_named_resource_create_owners_return_typed_effects(
    command_vault_clone,
    command_request,
    expected_path,
    expected_resource,
):
    application = application_for(command_vault_clone.vault_root)

    result = application.invoke(command_request)

    assert result.status == "ok"
    assert result.result.resource == expected_resource
    assert result.result.path == expected_path
    assert result.result.staged_handle_consumed is False
    assert result.committed_effects[0].kind == f"{expected_resource}.created"
    assert result.committed_effects[0].subject == expected_path
    assert (command_vault_clone.vault_root / expected_path).is_file()


def test_memory_create_preserves_typed_frontmatter(command_vault_clone):
    application = application_for(command_vault_clone.vault_root)
    result = application.invoke(
        ResourceCreateRequest(
            MemoryCreateTarget(
                "memory",
                "typed-memory",
                (
                    FrontmatterField("priority", 3),
                    FrontmatterField("triggers", ("typed", "memory")),
                ),
            ),
            InlineContent("Typed memory body.\n"),
        )
    )

    fields, body = parse_frontmatter(
        (command_vault_clone.vault_root / result.result.path).read_text()
    )
    assert fields == {"priority": "3", "triggers": ["typed", "memory"]}
    assert "Typed memory body." in body


def test_named_create_consumes_stage_only_after_success(command_vault_clone):
    vault_root = str(command_vault_clone.vault_root)
    handle = stage_body(vault_root, "# Staged Skill\n\nBody.\n")["handle"]
    application = application_for(command_vault_clone.vault_root)

    result = application.invoke(
        ResourceCreateRequest(
            SkillCreateTarget("skill", "staged-skill"),
            StagedContent(handle),
        )
    )

    assert result.status == "ok"
    assert result.result.staged_handle_consumed is True
    with pytest.raises(ValueError, match="already-consumed"):
        read_staged_body(vault_root, handle)

    duplicate = stage_body(vault_root, "duplicate")["handle"]
    failed = application.invoke(
        ResourceCreateRequest(
            SkillCreateTarget("skill", "staged-skill"),
            StagedContent(duplicate),
        )
    )
    assert failed.error.code is ErrorCode.CONFLICT
    assert "skills-count-drift" in failed.error.message
    assert read_staged_body(vault_root, duplicate) == "duplicate"


def test_named_create_dry_run_refuses_to_invent_a_created_resource(
    command_vault_clone,
):
    application = application_for(command_vault_clone.vault_root, dry_run=True)

    result = application.invoke(
        ResourceCreateRequest(
            StyleCreateTarget("style", "dry-style"),
            InlineContent("body"),
        )
    )

    assert result.error.code is ErrorCode.INVALID_REQUEST
    assert not (
        command_vault_clone.vault_root / "_Config/Styles/dry-style.md"
    ).exists()


def test_named_create_post_commit_failure_is_honestly_unknown(
    command_vault_clone,
    monkeypatch,
):
    real_create = create.create_resource

    def commit_then_fail(*args, **kwargs):
        real_create(*args, **kwargs)
        raise OSError("response failed after resource commit")

    monkeypatch.setattr(create, "create_resource", commit_then_fail)
    application = application_for(command_vault_clone.vault_root)

    result = application.invoke(
        ResourceCreateRequest(
            MemoryCreateTarget("memory", "uncertain-memory"),
            InlineContent("body"),
        )
    )

    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
    assert result.effects == "unknown"
    assert (
        command_vault_clone.vault_root
        / "_Config/Memories/uncertain-memory.md"
    ).exists()


def test_named_create_transport_is_consolidated_and_strict():
    resolver = current_request_resolver()
    cases = (
        ("memory", MemoryCreateTarget),
        ("skill", SkillCreateTarget),
        ("style", StyleCreateTarget),
    )
    for resource, target_type in cases:
        request = resolver.resolve(
            "resource.create",
            {
                "target": {"resource": resource, "name": "example", "frontmatter": {"tags": ["one", "two"]}},
                "content": {"source": "inline", "content": "body"},
            },
        )
        assert type(request) is ResourceCreateRequest
        assert type(request.target) is target_type
        entry = current_application_catalogue().resolve(request)
        assert entry.authority is Authority.CONTRIBUTOR
        assert entry.effect_class is EffectClass.SELECTED_BRAIN_MUTATION
        assert entry.retry_class is RetryClass.RECEIPT_REQUIRED

    with pytest.raises(ValueError, match="source"):
        resolver.resolve(
            "resource.create",
            {
                "target": {"resource": "memory", "name": "bad"},
                "content": {"kind": "inline", "content": "body"},
            },
        )
    with pytest.raises(ValueError, match="nested"):
        resolver.resolve(
            "resource.create",
            {
                "target": {
                    "resource": "memory",
                    "name": "bad",
                    "frontmatter": {"nested": {"value": 1}},
                },
                "content": {"source": "inline", "content": "body"},
            },
        )

    template = resolver.resolve(
        "resource.create",
        {
            "target": {"resource": "template", "name": "designs"},
            "content": {"source": "inline", "content": "body"},
        },
    )
    assert type(template.target) is TemplateCreateTarget
    with pytest.raises(ValueError, match="unexpected template target fields"):
        resolver.resolve(
            "resource.create",
            {
                "target": {
                    "resource": "template",
                    "name": "designs",
                    "frontmatter": {},
                },
                "content": {"source": "inline", "content": "body"},
            },
        )
