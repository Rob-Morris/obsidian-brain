"""Owner behaviour for managed content ingestion."""

from __future__ import annotations

import pytest

import process
import _application.content.ingest as content_ingest
from _application._mutation_support import InlineContent, StagedContent
from _application.content.classify import ContentClassifyMode
from _application.content.ingest import (
    ContentIngestAction,
    ContentIngestRequest,
)
from _application.registry import current_application_catalogue, current_request_resolver
from _application.results import ErrorCode
from _application.types import (
    Authority,
    DependencyTier,
    EffectClass,
    RetryClass,
)
from _staging import read_staged_body, stage_body
from command_application import application_for


def _managed(root, **kwargs):
    return application_for(root, dependency_tier=DependencyTier.MANAGED, **kwargs)


def test_content_ingest_creates_typed_artefact(command_vault_clone):
    root = command_vault_clone.vault_root
    result = _managed(root).invoke(
        ContentIngestRequest(
            InlineContent("# Managed Ingest Candidate\n\nA concrete idea.\n"),
            type_key="ideas",
        )
    )

    assert result.status == "ok"
    assert result.result.action is ContentIngestAction.CREATED
    assert result.result.artefact_type == "living/idea"
    assert result.result.needs_decision is False
    assert result.committed_effects[0].subject == result.result.path
    assert (root / result.result.path).is_file()


def test_content_ingest_updates_exact_existing_title(command_vault_clone, monkeypatch):
    root = command_vault_clone.vault_root
    target = root / "Ideas" / "Command Fixture Candidate.md"
    before = target.read_text()
    monkeypatch.setattr(
        "_search.lexical_query.load_index",
        lambda *_args, **_kwargs: pytest.fail(
            "known exact ingest must not load the lexical index"
        ),
    )

    result = _managed(root).invoke(
        ContentIngestRequest(
            InlineContent("\n\nAn appended ingestion detail.\n"),
            type_key="ideas",
            title="Command Fixture Candidate",
        )
    )

    assert result.status == "ok"
    assert result.result.action is ContentIngestAction.UPDATED
    assert result.result.path == "Ideas/Command Fixture Candidate.md"
    assert "An appended ingestion detail." in target.read_text()
    assert target.read_text() != before


def test_content_ingest_pauses_for_explicit_classification_without_effect(
    command_vault_clone,
    monkeypatch,
):
    monkeypatch.setattr(
        "_search.lexical_query.load_index",
        lambda *_args, **_kwargs: pytest.fail(
            "context-only ingest must not load the lexical index"
        ),
    )
    monkeypatch.setattr(
        content_ingest,
        "load_semantic_state",
        lambda *_args, **_kwargs: pytest.fail(
            "context-only ingest must not load semantic state"
        ),
    )
    result = _managed(command_vault_clone.vault_root).invoke(
        ContentIngestRequest(
            InlineContent("Unclassified material."),
            mode=ContentClassifyMode.CONTEXT_ASSEMBLY,
        )
    )

    assert result.status == "ok"
    assert result.result.action is ContentIngestAction.NEEDS_CLASSIFICATION
    assert result.result.classification is not None
    assert result.result.needs_decision is True
    assert result.committed_effects == ()


def test_content_ingest_consumes_stage_only_after_commit(command_vault_clone):
    root = str(command_vault_clone.vault_root)
    handle = stage_body(root, "# Staged Ingest Candidate\n\nBody.\n")["handle"]

    result = _managed(command_vault_clone.vault_root).invoke(
        ContentIngestRequest(StagedContent(handle), type_key="ideas")
    )

    assert result.status == "ok"
    assert result.result.action is ContentIngestAction.CREATED
    assert result.result.staged_handle_consumed is True
    with pytest.raises(ValueError, match="already-consumed"):
        read_staged_body(root, handle)


def test_content_ingest_retains_stage_when_decision_is_required(
    command_vault_clone,
):
    root = str(command_vault_clone.vault_root)
    handle = stage_body(root, "Unclassified staged material.")["handle"]

    result = _managed(command_vault_clone.vault_root).invoke(
        ContentIngestRequest(
            StagedContent(handle),
            mode=ContentClassifyMode.CONTEXT_ASSEMBLY,
        )
    )

    assert result.result.action is ContentIngestAction.NEEDS_CLASSIFICATION
    assert result.result.staged_handle_consumed is False
    assert read_staged_body(root, handle) == "Unclassified staged material."


def test_content_ingest_requires_managed_tier(command_vault_clone):
    result = application_for(command_vault_clone.vault_root).invoke(
        ContentIngestRequest(InlineContent("Body."), type_key="ideas")
    )

    assert result.error.code is ErrorCode.CAPABILITY_UNAVAILABLE
    assert "tier:managed" in result.error.details.missing


def test_content_ingest_embedding_mode_requires_semantic_provider(
    command_vault_clone,
):
    result = _managed(command_vault_clone.vault_root).invoke(
        ContentIngestRequest(
            InlineContent("Body."),
            mode=ContentClassifyMode.EMBEDDING,
        )
    )

    assert result.error.code is ErrorCode.CAPABILITY_UNAVAILABLE
    assert result.effects == "none"


def test_content_ingest_rejects_dry_run_without_effect(command_vault_clone):
    result = _managed(command_vault_clone.vault_root, dry_run=True).invoke(
        ContentIngestRequest(InlineContent("Body."), type_key="ideas")
    )

    assert result.error.code is ErrorCode.INVALID_REQUEST
    assert result.effects == "none"


def test_content_ingest_post_commit_failure_is_honestly_unknown(
    command_vault_clone,
    monkeypatch,
):
    real_ingest = process.ingest_content

    def commit_then_fail(*args, **kwargs):
        real_ingest(*args, **kwargs)
        raise OSError("failed after ingest commit")

    monkeypatch.setattr(process, "ingest_content", commit_then_fail)
    result = _managed(command_vault_clone.vault_root).invoke(
        ContentIngestRequest(
            InlineContent("# Uncertain Ingest Candidate\n\nBody.\n"),
            type_key="ideas",
        )
    )

    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
    assert result.effects == "unknown"
    assert list(command_vault_clone.vault_root.rglob("*Uncertain Ingest Candidate*"))


def test_content_ingest_transport_is_strict_managed_contributor_command():
    request = current_request_resolver().resolve(
        "content.ingest",
        {
            "content": {"source": "inline", "content": "Body."},
            "type_key": "ideas",
            "title": "Transport Ingest",
            "mode": "bm25_only",
        },
    )
    entry = current_application_catalogue().resolve(request)

    assert type(request) is ContentIngestRequest
    assert request.mode is ContentClassifyMode.BM25_ONLY
    assert entry.dependency_tier is DependencyTier.MANAGED
    assert entry.optional_providers == ("semantic_retrieval",)
    assert entry.authority is Authority.CONTRIBUTOR
    assert entry.effect_class is EffectClass.SELECTED_BRAIN_MUTATION
    assert entry.retry_class is RetryClass.RECEIPT_REQUIRED
    with pytest.raises(ValueError, match="unexpected fields"):
        current_request_resolver().resolve(
            "content.ingest",
            {
                "content": {"source": "inline", "content": "Body."},
                "file": "/tmp/body.md",
            },
        )
