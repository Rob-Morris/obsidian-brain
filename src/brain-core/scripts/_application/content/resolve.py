"""Typed portable ``content.resolve`` duplicate-decision owner."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Mapping

from .._search_support import (
    catalogue_entry as _catalogue_entry,
    error,
    load_router,
    load_semantic_state,
    semantic_provider_ready,
)
from ..context import InvocationContext
from ..results import Error, ErrorCode, Ok


class ContentResolutionAction(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class ContentResolvePayload:
    action: ContentResolutionAction
    artefact_type: str
    type_key: str
    title: str
    target_path: str | None
    candidates: tuple[str, ...]
    reasoning: str


@dataclass(frozen=True, slots=True)
class ContentResolveRequest:
    COMMAND_ID: ClassVar[str] = "content.resolve"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = ContentResolvePayload

    content: str
    type_key: str
    title: str

    def __post_init__(self) -> None:
        for name in ("type_key", "title"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"content.resolve {name} must be a non-empty string"
                )
        if not isinstance(self.content, str):
            raise ValueError("content.resolve content must be a string")


def execute(context: InvocationContext, request: ContentResolveRequest):
    import process
    from _search.lexical_query import IndexNotFoundError, load_index

    router = load_router(context, ContentResolveRequest)
    if isinstance(router, Error):
        return router
    exact_type_keys = {
        item.get("key")
        for item in router.get("artefacts", ())
        if item.get("configured")
    }
    if request.type_key not in exact_type_keys:
        return error(
            ContentResolveRequest,
            ErrorCode.INVALID_REQUEST,
            f"unknown configured artefact type key: {request.type_key}",
            "type_key",
        )
    try:
        index = load_index(context.selected_brain.vault_root)
    except (IndexNotFoundError, OSError, ValueError) as exc:
        return error(ContentResolveRequest, ErrorCode.CONFLICT, str(exc), None)

    doc_embeddings = metadata = None
    if semantic_provider_ready(context):
        semantic_state = load_semantic_state(context, ContentResolveRequest)
        if isinstance(semantic_state, Error):
            return semantic_state
        _config, _type_embeddings, doc_embeddings, metadata = semantic_state
    try:
        result = process.resolve_content(
            router,
            context.selected_brain.vault_root,
            request.type_key,
            request.title,
            content=request.content,
            index=index,
            doc_embeddings=doc_embeddings,
            doc_embeddings_meta=metadata,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return error(ContentResolveRequest, ErrorCode.CONFLICT, str(exc), None)
    if result.get("action") == "error":
        return error(
            ContentResolveRequest,
            ErrorCode.INVALID_REQUEST,
            result["reasoning"],
            "type_key",
        )
    payload = ContentResolvePayload(
        action=ContentResolutionAction(result["action"]),
        artefact_type=result["type"],
        type_key=result["key"],
        title=result["title"],
        target_path=result.get("target_path"),
        candidates=tuple(result.get("candidates") or ()),
        reasoning=result["reasoning"],
    )
    return Ok(request.COMMAND_ID, request.COMMAND_VERSION, payload)


def decode(payload: Mapping[str, object]) -> ContentResolveRequest:
    allowed = {"content", "type_key", "title"}
    unexpected = sorted(set(payload) - allowed)
    if unexpected:
        raise ValueError(f"unexpected fields: {', '.join(unexpected)}")
    values = {name: payload.get(name) for name in allowed}
    if any(not isinstance(value, str) for value in values.values()):
        raise ValueError("content, type_key and title must be strings")
    return ContentResolveRequest(
        content=values["content"],
        type_key=values["type_key"],
        title=values["title"],
    )


def catalogue_entry():
    return _catalogue_entry(
        ContentResolveRequest,
        execute,
        optional_semantic=True,
    )


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(ContentResolveRequest, decode)
