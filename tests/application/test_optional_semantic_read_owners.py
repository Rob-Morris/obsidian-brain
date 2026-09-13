"""Owner behaviour for portable reads with optional semantic enhancement."""

from __future__ import annotations

import pytest
from _common import load_compiled_router, resolve_type

import _application.artefact.search as artefact_search
import _application.content.classify as content_classify
import _application.content.ingest as content_ingest
import _application.content.resolve as content_resolve
from _application._mutation_support import InlineContent
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
from _application.content.ingest import ContentIngestRequest
from _application.context import Capability
from _application.registry import current_application_catalogue, current_request_resolver
from _application.resource.search import ResourceSearchRequest, SearchableResource
from _application.results import ErrorCode, request_error
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
    monkeypatch,
):
    application = application_for(command_vault_baseline.vault_root)
    monkeypatch.setattr(
        "_search.lexical_query.load_index",
        lambda *_args, **_kwargs: pytest.fail(
            "explicit semantic search must not load the lexical index"
        ),
    )

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
    observed = {}

    def load_state(_context, _request_type, _router, *, selection):
        observed["selection"] = selection
        return object(), None, object(), object()

    monkeypatch.setattr(
        artefact_search,
        "load_semantic_state",
        load_state,
    )

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
    assert observed == {"selection": "documents", "mode": "hybrid"}
    assert result.result.retrieval_mode == "hybrid"


@pytest.mark.parametrize(
    ("module", "command_request", "selection", "dependency_tier"),
    (
        (
            content_classify,
            ContentClassifyRequest("candidate", ContentClassifyMode.EMBEDDING),
            "types",
            DependencyTier.PORTABLE,
        ),
        (
            content_resolve,
            ContentResolveRequest("candidate", "ideas", "Novel Candidate"),
            "documents",
            DependencyTier.PORTABLE,
        ),
        (
            content_ingest,
            ContentIngestRequest(
                InlineContent("candidate"),
                mode=ContentClassifyMode.EMBEDDING,
            ),
            "all",
            DependencyTier.MANAGED,
        ),
    ),
)
def test_semantic_callers_request_only_the_arrays_they_use(
    command_vault_clone,
    monkeypatch,
    module,
    command_request,
    selection,
    dependency_tier,
):
    class SemanticProvider:
        provider_id = "semantic_retrieval"

    observed = []

    def load_state(_context, request_type, _router, *, selection):
        observed.append(selection)
        return request_error(request_type, ErrorCode.CONFLICT, "stop after wiring check")

    monkeypatch.setattr(module, "load_semantic_state", load_state)
    result = application_for(
        command_vault_clone.vault_root,
        dependency_tier=dependency_tier,
        providers=(SemanticProvider(),),
        capabilities=(Capability("semantic_retrieval", Availability.AVAILABLE),),
    ).invoke(command_request)

    assert result.error.code is ErrorCode.CONFLICT
    assert observed == [selection]


def test_content_classify_exposes_typed_context_without_an_open_result_bag(
    command_vault_baseline,
    monkeypatch,
):
    application = application_for(command_vault_baseline.vault_root)
    monkeypatch.setattr(
        "_search.lexical_query.load_index",
        lambda *_args, **_kwargs: pytest.fail(
            "context assembly must not load the lexical index"
        ),
    )

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
        item.artefact_type
        == resolve_type(
            load_compiled_router(command_vault_baseline.vault_root),
            item.type_key,
        )["frontmatter_type"]
        and item.description
        for item in result.result.classification.type_descriptions
    )


def test_content_classify_degrades_to_context_when_lexical_index_is_missing(
    command_vault_clone,
):
    index = command_vault_clone.vault_root / ".brain/local/retrieval-index.json"
    index.unlink()

    result = application_for(command_vault_clone.vault_root).invoke(
        ContentClassifyRequest("A proposed command-interface improvement.")
    )

    assert result.status == "ok"
    assert result.result.strategy is ContentClassifyMode.CONTEXT_ASSEMBLY
    assert isinstance(result.result.classification, ClassificationContext)
    assert result.result.classification.type_descriptions


def test_content_resolve_preserves_exact_filename_decision(
    command_vault_baseline,
    monkeypatch,
):
    application = application_for(command_vault_baseline.vault_root)
    monkeypatch.setattr(
        "_search.lexical_query.load_index",
        lambda *_args, **_kwargs: pytest.fail(
            "exact content resolution must not load the lexical index"
        ),
    )

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
        (ResourceSearchRequest(SearchableResource.SKILL, "shaping"), "skill"),
        (ResourceSearchRequest(SearchableResource.STYLE, "obsidian"), "style"),
        (ResourceSearchRequest(SearchableResource.MEMORY, "brain-core"), "memory"),
        (
            ResourceSearchRequest(SearchableResource.TRIGGER, "meaningful work"),
            "trigger",
        ),
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
    lexical_resource = {"resource.search"}
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
        resolver.resolve(
            "resource.search", {"resource": "plugin", "query": "example"}
        )
    ) is ResourceSearchRequest
    with pytest.raises(ValueError):
        resolver.resolve(
            "resource.search",
            {"query": "shape", "resource": "unknown"},
        )
    with pytest.raises(ValueError, match="query must be a non-empty string"):
        resolver.resolve(
            "resource.search",
            {"query": None, "resource": "plugin"},
        )
    with pytest.raises(ValueError, match="top_k must be between"):
        resolver.resolve(
            "resource.search",
            {"query": "shape", "resource": "plugin", "top_k": "ten"},
        )


@pytest.mark.parametrize(
    "selector", ["plan", "plans", "temporal/plan", "temporal/plans"]
)
def test_artefact_type_selectors_agree_across_create_list_and_search(
    command_vault_clone, selector
):
    from _application.artefact.create import ArtefactCreateRequest
    from _application.artefact.list import ArtefactListRequest
    from _portable.lexical_maintenance import maintain_lexical_index

    root = command_vault_clone.vault_root
    app = application_for(root)
    created = app.invoke(ArtefactCreateRequest(selector, "Selector Regression"))
    assert created.status == "ok"
    assert created.result.type == "temporal/plan"
    maintain_lexical_index(root, dry_run=False, force=True)
    listed = app.invoke(ArtefactListRequest(type_filter=selector))
    searched = app.invoke(
        ArtefactSearchRequest(
            "Selector Regression", type_filter=selector, mode=ArtefactSearchMode.LEXICAL
        )
    )
    assert listed.status == searched.status == "ok"
    assert created.result.path in {item.path for item in listed.result.items}
    assert created.result.path in {item.path for item in searched.result.items}
    assert all(item.artefact_type == "temporal/plan" for item in listed.result.items)
    assert all(item.resource_type == "temporal/plan" for item in searched.result.items)
