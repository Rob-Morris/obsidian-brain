"""Shared typed mechanics for selected-Brain search command owners."""

from __future__ import annotations

from dataclasses import dataclass
from .context import InvocationContext
from .results import (
    CapabilityUnavailableDetails,
    CommandError,
    Error,
    ErrorCode,
    InstructionNextAction,
    Ok,
    request_error,
)
from .types import (
    Authority,
    Availability,
    DependencyTier,
    EffectClass,
    Locality,
    RetryClass,
)


SEMANTIC_PROVIDER = "semantic_retrieval"
DEFAULT_TOP_K = 10
MAX_TOP_K = 100


@dataclass(frozen=True, slots=True)
class SearchItem:
    path: str
    title: str
    resource_type: str
    status: str | None
    score: float
    snippet: str


@dataclass(frozen=True, slots=True)
class SearchPayload:
    retrieval_mode: str
    items: tuple[SearchItem, ...]
    returned: int


def validate_query_and_limit(command_id: str, query: str, top_k: int) -> None:
    if not isinstance(query, str) or not query.strip():
        raise ValueError(f"{command_id} query must be a non-empty string")
    if (
        not isinstance(top_k, int)
        or isinstance(top_k, bool)
        or not 1 <= top_k <= MAX_TOP_K
    ):
        raise ValueError(f"{command_id} top_k must be between 1 and {MAX_TOP_K}")


def load_router(context: InvocationContext, request_type):
    if context.derived_snapshots is not None:
        router = context.derived_snapshots.load_router()
    else:
        from _common import load_compiled_router

        router = load_compiled_router(context.selected_brain.vault_root)
    if "error" in router:
        return error(request_type, ErrorCode.CONFLICT, router["error"], None)
    return router


def load_lexical_index(context: InvocationContext):
    if context.derived_snapshots is not None:
        return context.derived_snapshots.load_lexical_index()
    from _search.lexical_query import load_index

    return load_index(context.selected_brain.vault_root)


def resource_search(
    context: InvocationContext,
    request,
    resource: str,
):
    from _lifecycle.retrieval_errors import UnreadableRetrievalSourceError
    from _search.resource import search_resource

    router = load_router(context, type(request))
    if isinstance(router, Error):
        return router
    try:
        results = search_resource(
            router,
            context.selected_brain.vault_root,
            resource,
            request.query,
            top_k=request.top_k,
        )
    except (OSError, UnreadableRetrievalSourceError, ValueError) as exc:
        return error(type(request), ErrorCode.CONFLICT, str(exc), None)
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        search_payload("lexical", results),
    )


def search_payload(mode: str, results) -> SearchPayload:
    items = tuple(
        SearchItem(
            path=str(item.get("path") or ""),
            title=str(item.get("title") or ""),
            resource_type=str(item.get("type") or ""),
            status=(
                str(item["status"])
                if item.get("status") is not None
                else None
            ),
            score=float(item.get("score") or 0.0),
            snippet=str(item.get("snippet") or ""),
        )
        for item in results
    )
    return SearchPayload(mode, items, len(items))


def semantic_provider_ready(context: InvocationContext) -> bool:
    return (
        context.providers.get(SEMANTIC_PROVIDER) is not None
        and context.capabilities.availability_of(SEMANTIC_PROVIDER)
        is Availability.AVAILABLE
    )


def semantic_unavailable(request_type, context: InvocationContext) -> Error:
    missing = []
    if context.providers.get(SEMANTIC_PROVIDER) is None:
        missing.append(f"provider:{SEMANTIC_PROVIDER}")
    if (
        context.capabilities.availability_of(SEMANTIC_PROVIDER)
        is not Availability.AVAILABLE
    ):
        missing.append(f"capability:{SEMANTIC_PROVIDER}")
    if not missing:
        missing.append(f"capability:{SEMANTIC_PROVIDER}")
    details = CapabilityUnavailableDetails(
        required_tier=DependencyTier.PORTABLE,
        current_tier=context.dependency_tier,
        locality=Locality.SELECTED_BRAIN_LOCAL,
        missing=tuple(missing),
        snapshot_freshness=context.capabilities.freshness,
        recoverable=True,
    )
    return Error(
        request_type.COMMAND_ID,
        request_type.COMMAND_VERSION,
        CommandError(
            ErrorCode.CAPABILITY_UNAVAILABLE,
            "Semantic retrieval is unavailable for this request.",
            details,
            next_action=InstructionNextAction(
                "Enable semantic retrieval, refresh capabilities, or use the portable lexical mode."
            ),
        ),
    )


def load_semantic_state(
    context: InvocationContext,
    request_type,
    router,
    *,
    selection: str,
):
    """Load selected-Brain semantic sidecars after trusted availability gating."""
    from _semantic import config as semantic_config
    from _semantic import runtime as semantic_runtime

    try:
        config = semantic_config.load_config_checked(
            context.selected_brain.vault_root
        )
        type_embeddings, doc_embeddings, metadata = semantic_runtime.load_embeddings_state(
            context.selected_brain.vault_root,
            selection=selection,
        )
        if metadata is None:
            raise RuntimeError("semantic embeddings metadata is missing")
        if not semantic_runtime.embeddings_meta_matches_router(metadata, router):
            raise RuntimeError(
                "semantic embeddings were built for a different compiled router"
            )
    except (OSError, RuntimeError, ValueError) as exc:
        return error(request_type, ErrorCode.CONFLICT, str(exc), None)
    return config, type_embeddings, doc_embeddings, metadata


def error(request_type, code: ErrorCode, message: str, field: str | None) -> Error:
    return request_error(request_type, code, message, field)


def catalogue_entry(request_type, executor, *, optional_semantic: bool = False):
    from .catalogue import ALL_APPLICATION_PROJECTIONS, ApplicationEntry

    return ApplicationEntry(
        request_type=request_type,
        executor=executor,
        dependency_tier=DependencyTier.PORTABLE,
        locality=Locality.SELECTED_BRAIN_LOCAL,
        required_providers=(),
        optional_providers=(SEMANTIC_PROVIDER,) if optional_semantic else (),
        authority=Authority.READER,
        effect_class=EffectClass.NONE,
        retry_class=RetryClass.SAFE,
        projections=ALL_APPLICATION_PROJECTIONS,
    )
