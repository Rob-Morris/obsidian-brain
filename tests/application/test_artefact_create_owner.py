"""Owner behaviour for granular artefact creation."""

from __future__ import annotations

import create
import pytest

from _application._mutation_support import FrontmatterField, InlineContent, StagedContent
from _application.artefact.create import ArtefactCreateRequest, WikilinkFindingStatus
from _application.registry import current_application_catalogue, current_request_resolver
from _application.results import ErrorCode
from _application.types import Authority, EffectClass, RetryClass
from _common import parse_frontmatter
from _staging import read_staged_body, stage_body
from command_application import application_for


def test_artefact_create_uses_type_placement_and_typed_parent_context(
    command_vault_clone,
):
    application = application_for(command_vault_clone.vault_root)

    result = application.invoke(
        ArtefactCreateRequest(
            type="ideas",
            title="Typed Command Candidate",
            content=InlineContent("# Typed Command Candidate\n\nBody.\n"),
            frontmatter=(FrontmatterField("status", "ready"),),
            parent="project/command-fixture",
            key="typed-command-candidate",
        )
    )

    assert result.status == "ok"
    assert result.result.type == "living/ideas"
    assert result.result.key == "typed-command-candidate"
    assert result.result.parent == "project/command-fixture"
    assert result.result.parent_context.placed_under == "project/command-fixture"
    assert result.committed_effects[0].kind == "artefact.created"
    target = command_vault_clone.vault_root / result.result.path
    fields, body = parse_frontmatter(target.read_text())
    assert fields["status"] == "ready"
    assert fields["parent"] == "project/command-fixture"
    assert "project/command-fixture" in fields["tags"]
    assert "Body." in body


def test_artefact_create_omitted_content_uses_the_type_template(
    command_vault_clone,
):
    application = application_for(command_vault_clone.vault_root)

    result = application.invoke(ArtefactCreateRequest("ideas", "Template Backed Candidate"))

    assert result.status == "ok"
    written = (command_vault_clone.vault_root / result.result.path).read_text()
    assert "## The Idea" in written


def test_artefact_create_reports_structural_wikilink_findings(command_vault_clone):
    application = application_for(command_vault_clone.vault_root)

    result = application.invoke(
        ArtefactCreateRequest(
            "ideas",
            "Broken Link Candidate",
            InlineContent("See [[definitely-nowhere]].\n"),
        )
    )

    assert result.status == "ok"
    assert result.result.wikilink_warnings[0].stem == "definitely-nowhere"
    assert result.result.wikilink_warnings[0].status is WikilinkFindingStatus.BROKEN
    assert result.warnings


def test_artefact_create_consumes_stage_only_after_commit(command_vault_clone):
    vault_root = str(command_vault_clone.vault_root)
    handle = stage_body(vault_root, "Staged body.\n")["handle"]
    application = application_for(command_vault_clone.vault_root)

    result = application.invoke(
        ArtefactCreateRequest("ideas", "Staged Candidate", StagedContent(handle))
    )

    assert result.status == "ok"
    assert result.result.staged_handle_consumed is True
    with pytest.raises(ValueError, match="already-consumed"):
        read_staged_body(vault_root, handle)


def test_artefact_create_retains_stage_when_content_is_rejected(
    command_vault_clone,
):
    vault_root = str(command_vault_clone.vault_root)
    application = application_for(command_vault_clone.vault_root)
    rejected = stage_body(
        vault_root, "---\nstatus: ready\n---\n\nBad.\n"
    )["handle"]
    failed = application.invoke(
        ArtefactCreateRequest(
            "ideas",
            "Rejected Candidate",
            StagedContent(rejected),
        )
    )
    assert failed.error.code is ErrorCode.INVALID_REQUEST
    assert read_staged_body(vault_root, rejected).startswith("---")


def test_artefact_create_dry_run_refuses_to_invent_a_path(command_vault_clone):
    application = application_for(command_vault_clone.vault_root, dry_run=True)

    result = application.invoke(ArtefactCreateRequest("ideas", "Dry Run Candidate"))

    assert result.error.code is ErrorCode.INVALID_REQUEST
    assert not list(command_vault_clone.vault_root.rglob("*Dry Run Candidate*"))


def test_artefact_create_post_commit_failure_is_honestly_unknown(
    command_vault_clone,
    monkeypatch,
):
    real_create = create.create_resource

    def commit_then_fail(*args, **kwargs):
        result = real_create(*args, **kwargs)
        raise OSError(result["path"])

    monkeypatch.setattr(create, "create_resource", commit_then_fail)
    application = application_for(command_vault_clone.vault_root)

    result = application.invoke(ArtefactCreateRequest("ideas", "Uncertain Candidate"))

    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
    assert result.effects == "unknown"
    assert list(command_vault_clone.vault_root.rglob("*Uncertain Candidate*"))


def test_artefact_create_transport_is_granular_and_locality_safe():
    resolver = current_request_resolver()
    request = resolver.resolve(
        "artefact.create",
        {
            "type": "ideas",
            "title": "Transport Candidate",
            "content": {"source": "inline", "content": "Body.\n"},
            "frontmatter": {"status": "ready"},
            "parent": None,
            "fix_links": False,
        },
    )

    assert type(request) is ArtefactCreateRequest
    entry = current_application_catalogue().resolve(request)
    assert entry.authority is Authority.CONTRIBUTOR
    assert entry.effect_class is EffectClass.SELECTED_BRAIN_MUTATION
    assert entry.retry_class is RetryClass.RECEIPT_REQUIRED

    with pytest.raises(ValueError, match="source"):
        resolver.resolve(
            "artefact.create",
            {
                "type": "ideas",
                "title": "Local File Candidate",
                "content": {"source": "file", "path": "/tmp/body.md"},
            },
        )
    with pytest.raises(ValueError, match="unexpected fields"):
        resolver.resolve(
            "artefact.create",
            {
                "type": "ideas",
                "title": "Legacy Candidate",
                "template_vars": {"SOURCE": "legacy"},
            },
        )
