"""Owner behaviour for portable reads with optional semantic enhancement."""

from __future__ import annotations

import pytest

import _application.artefact.search as artefact_search
from _application.artefact.search import (
    ArtefactSearchMode,
    ArtefactSearchRequest,
)
from _application.content.classify import (
    ClassificationContext,
    ContentClassifyMode,
    ContentClassifyRequest,
)
from _application.content.resolve import (
    ContentResolutionAction,
    ContentResolveRequest,
)
from _application.context import Capability
from _application.memory.search import MemorySearchRequest
from _application.plugin.search import PluginSearchRequest
from _application.registry import current_application_catalogue, current_request_resolver
from _application.results import ErrorCode
from _application.skill.search import SkillSearchRequest
from _application.style.search import StyleSearchRequest
from _application.trigger.search import TriggerSearchRequest
from _application.types import (
    Authority,
    Availability,
    DependencyTier,
    EffectClass,
)
from command_application import application_for


def test_artefact_search_returns_typed_lexical_results(command_vault_baseline):
    application = application_for(command_vault_baseline.vault_root)

    result = application.invoke(
        ArtefactSearchRequest(
            "command fixture",
            type_filter="living/project",
            mode=ArtefactSearchMode.LEXICAL,
        )
    )

    assert result.status == "ok"
    assert result.result.retrieval_mode == "lexical"
    assert result.result.returned >= 1
    assert result.result.items[0].path == "Projects/Command Fixture.md"
    assert all(
        item.resource_type == "living/project" for item in result.result.items
    )


def test_explicit_semantic_search_fails_before_runtime_loading(
    command_vault_baseline,
):
    application = application_for(command_vault_baseline.vault_root)

    result = application.invoke(
        ArtefactSearchRequest("command", mode=ArtefactSearchMode.SEMANTIC)
    )

    assert result.error.code is ErrorCode.CAPABILITY_UNAVAILABLE
    assert result.effects == "none"
    assert set(result.error.details.missing) == {
        "provider:semantic_retrieval",
        "capability:semantic_retrieval",
    }


def test_auto_search_uses_semantic_enhancement_only_from_trusted_context(
    command_vault_baseline,
    monkeypatch,
):
    class SemanticProvider:
        provider_id = "semantic_retrieval"

    application = application_for(
        command_vault_baseline.vault_root,
        providers=(SemanticProvider(),),
        capabilities=(
            Capability("semantic_retrieval", Availability.AVAILABLE),
        ),
    )
    monkeypatch.setattr(
        artefact_search,
        "load_semantic_state",
        lambda _context, _request_type: (object(), None, object(), object()),
    )
    observed = {}

    def dispatch(
        _index,
        _query,
        _vault_root,
        mode,
        **_kwargs,
    ):
        observed["mode"] = mode
        return [
            {
                "path": "Projects/Command Fixture.md",
                "title": "Command Fixture",
                "type": "living/project",
                "status": None,
                "score": 0.99,
                "snippet": "semantic match",
            }
        ]

    monkeypatch.setattr("_search.mode.resolve_search_mode", lambda *_a, **_k: "hybrid")
    monkeypatch.setattr("_search.mode.dispatch_search", dispatch)

    result = application.invoke(ArtefactSearchRequest("command fixture"))

    assert result.status == "ok"
    assert observed == {"mode": "hybrid"}
    assert result.result.retrieval_mode == "hybrid"


def test_content_classify_exposes_typed_context_without_an_open_result_bag(
    command_vault_baseline,
):
    application = application_for(command_vault_baseline.vault_root)

    result = application.invoke(
        ContentClassifyRequest(
            "A proposed command-interface improvement.",
            ContentClassifyMode.CONTEXT_ASSEMBLY,
        )
    )

    assert result.status == "ok"
    assert result.result.strategy is ContentClassifyMode.CONTEXT_ASSEMBLY
    assert isinstance(result.result.classification, ClassificationContext)
    assert result.result.classification.type_descriptions
    assert all(
        item.artefact_type and item.type_key and item.description
        for item in result.result.classification.type_descriptions
    )


def test_content_resolve_preserves_exact_filename_decision(command_vault_baseline):
    application = application_for(command_vault_baseline.vault_root)

    result = application.invoke(
        ContentResolveRequest("", "projects", "Command Fixture")
    )

    assert result.status == "ok"
    assert result.result.action is ContentResolutionAction.UPDATE
    assert result.result.artefact_type == "living/project"
    assert result.result.target_path == "Projects/Command Fixture.md"
    assert result.result.candidates == ("Projects/Command Fixture.md",)

    alias = application.invoke(
        ContentResolveRequest("", "living/project", "Command Fixture")
    )
    assert alias.error.code is ErrorCode.INVALID_REQUEST
    assert alias.error.details.field == "type_key"


@pytest.mark.parametrize(
    ("command_request", "expected_type"),
    (
        (SkillSearchRequest("shaping"), "skill"),
        (StyleSearchRequest("obsidian"), "style"),
        (MemorySearchRequest("brain-core"), "memory"),
        (TriggerSearchRequest("meaningful work"), "trigger"),
    ),
)
def test_resource_search_owners_share_typed_text_results(
    command_vault_baseline,
    command_request,
    expected_type,
):
    application = application_for(command_vault_baseline.vault_root)

    result = application.invoke(command_request)

    assert result.status == "ok"
    assert result.result.retrieval_mode == "lexical"
    assert result.result.items
    assert all(item.resource_type == expected_type for item in result.result.items)


def test_optional_semantic_group_has_exact_catalogue_contract():
    optional_semantic = {
        "artefact.search",
        "content.classify",
        "content.resolve",
    }
    lexical_resource = {
        "memory.search",
        "plugin.search",
        "skill.search",
        "style.search",
        "trigger.search",
    }
    expected = optional_semantic | lexical_resource
    entries = {
        entry.command_id: entry
        for entry in current_application_catalogue().entries
        if entry.command_id in expected
    }

    assert set(entries) == expected
    for entry in entries.values():
        assert entry.dependency_tier is DependencyTier.PORTABLE
        assert entry.required_providers == ()
        assert entry.optional_providers == (
            ("semantic_retrieval",)
            if entry.command_id in optional_semantic
            else ()
        )
        assert entry.authority is Authority.READER
        assert entry.effect_class is EffectClass.NONE


def test_optional_semantic_transport_shapes_are_granular_and_strict():
    resolver = current_request_resolver()

    assert type(
        resolver.resolve(
            "artefact.search",
            {"query": "command", "mode": "lexical", "top_k": 3},
        )
    ) is ArtefactSearchRequest
    assert type(
        resolver.resolve(
            "content.classify",
            {"content": "candidate", "mode": "context_assembly"},
        )
    ) is ContentClassifyRequest
    assert type(
        resolver.resolve(
            "content.resolve",
            {"content": "", "type_key": "projects", "title": "Candidate"},
        )
    ) is ContentResolveRequest
    assert type(
        resolver.resolve("plugin.search", {"query": "example"})
    ) is PluginSearchRequest
    with pytest.raises(ValueError):
        resolver.resolve(
            "skill.search",
            {"query": "shape", "resource": "skill"},
        )
