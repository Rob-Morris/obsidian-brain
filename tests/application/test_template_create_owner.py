"""Owner behaviour for create-only template creation."""

from __future__ import annotations

import compile_router
import create
import pytest

from _application._mutation_support import InlineContent, StagedContent
from _application.registry import current_application_catalogue, current_request_resolver
from _application.resource.create import ResourceCreateRequest, TemplateCreateTarget
from _application.results import ErrorCode
from _common import load_compiled_router
from _staging import read_staged_body, stage_body
from command_application import application_for


BODY = "---\ntype: living/project\ntags: []\n---\n\n# New Project Template\n"


def _remove_project_template_and_recompile(vault_root):
    router = load_compiled_router(vault_root)
    rel_path = create.config_resource_rel_path(router, "template", "projects")
    (vault_root / rel_path).unlink()
    compiled = compile_router.compile(str(vault_root))
    compile_router.persist_compiled_router(str(vault_root), compiled)
    return rel_path


def test_template_create_writes_only_when_template_is_absent(command_vault_clone):
    rel_path = _remove_project_template_and_recompile(command_vault_clone.vault_root)
    application = application_for(command_vault_clone.vault_root)

    result = application.invoke(
        ResourceCreateRequest(
            TemplateCreateTarget("template", "projects"),
            InlineContent(BODY),
        )
    )

    assert result.status == "ok"
    assert result.result.resource == "template"
    assert result.result.name == "projects"
    assert result.result.path == rel_path
    assert result.committed_effects[0].kind == "template.created"
    assert (command_vault_clone.vault_root / rel_path).read_text() == BODY


def test_template_create_never_overwrites_existing_content(command_vault_clone):
    router = load_compiled_router(command_vault_clone.vault_root)
    rel_path = create.config_resource_rel_path(router, "template", "projects")
    target = command_vault_clone.vault_root / rel_path
    before = target.read_bytes()
    handle = stage_body(str(command_vault_clone.vault_root), BODY)["handle"]
    application = application_for(command_vault_clone.vault_root)

    result = application.invoke(
        ResourceCreateRequest(
            TemplateCreateTarget("template", "projects"),
            StagedContent(handle),
        )
    )

    assert result.error.code is ErrorCode.CONFLICT
    assert result.effects == "none"
    assert target.read_bytes() == before
    assert read_staged_body(str(command_vault_clone.vault_root), handle) == BODY


def test_template_create_rejects_body_without_full_frontmatter(
    command_vault_clone,
):
    _remove_project_template_and_recompile(command_vault_clone.vault_root)
    application = application_for(command_vault_clone.vault_root)

    result = application.invoke(
        ResourceCreateRequest(
            TemplateCreateTarget("template", "projects"),
            InlineContent("# Missing frontmatter\n"),
        )
    )

    assert result.error.code is ErrorCode.INVALID_REQUEST
    assert result.effects == "none"


def test_template_create_post_commit_failure_is_honestly_unknown(
    command_vault_clone,
    monkeypatch,
):
    rel_path = _remove_project_template_and_recompile(command_vault_clone.vault_root)
    real_create = create.create_resource

    def commit_then_fail(*args, **kwargs):
        real_create(*args, **kwargs)
        raise OSError("response failed after template commit")

    monkeypatch.setattr(create, "create_resource", commit_then_fail)
    application = application_for(command_vault_clone.vault_root)

    result = application.invoke(
        ResourceCreateRequest(
            TemplateCreateTarget("template", "projects"),
            InlineContent(BODY),
        )
    )

    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
    assert result.effects == "unknown"
    assert (command_vault_clone.vault_root / rel_path).read_text() == BODY


def test_template_create_transport_excludes_separate_frontmatter():
    resolver = current_request_resolver()
    request = resolver.resolve(
        "resource.create",
        {
            "target": {"resource": "template", "name": "projects"},
            "content": {"source": "inline", "content": BODY},
        },
    )

    assert type(request) is ResourceCreateRequest
    assert type(request.target) is TemplateCreateTarget
    assert current_application_catalogue().resolve(request).command_id == (
        "resource.create"
    )
    with pytest.raises(ValueError, match="unexpected template target fields"):
        resolver.resolve(
            "resource.create",
            {
                "target": {
                    "resource": "template",
                    "name": "projects",
                    "frontmatter": {"audience": "agents"},
                },
                "content": {"source": "inline", "content": BODY},
            },
        )
