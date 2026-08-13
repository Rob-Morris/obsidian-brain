"""Typed relevance-ranked ``artefact.search`` owner."""

from __future__ import annotations

from .._decoding import optional_string, reject_unexpected

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Mapping

from .._search_support import (
    DEFAULT_TOP_K,
    SearchPayload,
    catalogue_entry as _catalogue_entry,
    error,
    load_router,
    load_semantic_state,
    search_payload,
    semantic_provider_ready,
    semantic_unavailable,
    validate_query_and_limit,
)
from ..context import InvocationContext
from ..results import Error, ErrorCode, Ok


class ArtefactSearchMode(str, Enum):
    AUTO = "auto"
    LEXICAL = "lexical"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"


@dataclass(frozen=True, slots=True)
class ArtefactSearchRequest:
    COMMAND_ID: ClassVar[str] = "artefact.search"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = SearchPayload

    query: str
    type_filter: str | None = None
    tag: str | None = None
    status: str | None = None
    mode: ArtefactSearchMode = ArtefactSearchMode.AUTO
    top_k: int = DEFAULT_TOP_K

    def __post_init__(self) -> None:
        validate_query_and_limit(self.COMMAND_ID, self.query, self.top_k)
        for name in ("type_filter", "tag", "status"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, str) or not value.strip()
            ):
                raise ValueError(
                    f"{self.COMMAND_ID} {name} must be a non-empty string"
                )
        if not isinstance(self.mode, ArtefactSearchMode):
            raise ValueError("artefact.search mode must use ArtefactSearchMode")


def execute(context: InvocationContext, request: ArtefactSearchRequest):
    from _search.filters import SearchFilters
    from _search.lexical_query import IndexNotFoundError, load_index
    from _search.mode import SearchModeUnavailableError, dispatch_search

    router = load_router(context, ArtefactSearchRequest)
    if isinstance(router, Error):
        return router
    if request.type_filter is not None:
        configured_types = {
            item.get("frontmatter_type")
            for item in router.get("artefacts", ())
            if item.get("configured")
        }
        if request.type_filter not in configured_types:
            return error(
                ArtefactSearchRequest,
                ErrorCode.INVALID_REQUEST,
                f"unknown configured artefact type: {request.type_filter}",
                "type_filter",
            )
    semantic_ready = semantic_provider_ready(context)
    if request.mode in {
        ArtefactSearchMode.SEMANTIC,
        ArtefactSearchMode.HYBRID,
    } and not semantic_ready:
        return semantic_unavailable(ArtefactSearchRequest, context)
    resolved_mode = request.mode.value
    if request.mode is ArtefactSearchMode.AUTO:
        resolved_mode = "hybrid" if semantic_ready else "lexical"

    semantic_state = (None, None, None, None)
    if resolved_mode in {"semantic", "hybrid"}:
        semantic_state = load_semantic_state(
            context,
            ArtefactSearchRequest,
            router,
            selection="documents",
        )
        if isinstance(semantic_state, Error):
            return semantic_state
    config, _type_embeddings, doc_embeddings, metadata = semantic_state
    index = None
    if resolved_mode in {"lexical", "hybrid"}:
        try:
            index = load_index(context.selected_brain.vault_root)
        except (IndexNotFoundError, OSError, ValueError) as exc:
            return error(ArtefactSearchRequest, ErrorCode.CONFLICT, str(exc), None)
    filters = SearchFilters(
        type=request.type_filter,
        tag=request.tag,
        status=request.status,
    )
    try:
        if resolved_mode in {"semantic", "hybrid"}:
            from _search.mode import resolve_search_mode

            resolved_mode = resolve_search_mode(
                context.selected_brain.vault_root,
                resolved_mode,
                config=config,
                doc_embeddings=doc_embeddings,
                embeddings_meta=metadata,
            )
        results = dispatch_search(
            index,
            request.query,
            context.selected_brain.vault_root,
            resolved_mode,
            filters=filters,
            top_k=request.top_k,
            doc_embeddings=doc_embeddings,
            embeddings_meta=metadata,
        )
    except (OSError, ValueError, SearchModeUnavailableError) as exc:
        return error(ArtefactSearchRequest, ErrorCode.CONFLICT, str(exc), None)
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        search_payload(resolved_mode, results),
    )

def _optional_string(payload: Mapping[str, object], name: str) -> str | None:
    return optional_string(payload.get(name), name)


def decode(payload: Mapping[str, object]) -> ArtefactSearchRequest:
    allowed = {"query", "type_filter", "tag", "status", "mode", "top_k"}
    reject_unexpected(payload, allowed)
    query = payload.get("query")
    if not isinstance(query, str):
        raise ValueError("query must be a string")
    mode = payload.get("mode", ArtefactSearchMode.AUTO.value)
    if not isinstance(mode, str):
        raise ValueError("mode must be a string")
    top_k = payload.get("top_k", DEFAULT_TOP_K)
    if not isinstance(top_k, int) or isinstance(top_k, bool):
        raise ValueError("top_k must be an integer")
    return ArtefactSearchRequest(
        query=query,
        type_filter=_optional_string(payload, "type_filter"),
        tag=_optional_string(payload, "tag"),
        status=_optional_string(payload, "status"),
        mode=ArtefactSearchMode(mode),
        top_k=top_k,
    )


def catalogue_entry():
    return _catalogue_entry(
        ArtefactSearchRequest,
        execute,
        optional_semantic=True,
    )
