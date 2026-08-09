"""Typed portable ``content.classify`` owner."""

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
    semantic_unavailable,
)
from ..context import InvocationContext
from ..results import Error, ErrorCode, Ok


class ContentClassifyMode(str, Enum):
    AUTO = "auto"
    EMBEDDING = "embedding"
    BM25_ONLY = "bm25_only"
    CONTEXT_ASSEMBLY = "context_assembly"


@dataclass(frozen=True, slots=True)
class ClassificationCandidate:
    artefact_type: str
    type_key: str
    confidence: float


@dataclass(frozen=True, slots=True)
class ClassificationTypeDescription:
    artefact_type: str
    type_key: str
    description: str


@dataclass(frozen=True, slots=True)
class RankedClassification:
    selected: ClassificationCandidate
    alternatives: tuple[ClassificationCandidate, ...]
    reasoning: str


@dataclass(frozen=True, slots=True)
class ClassificationContext:
    type_descriptions: tuple[ClassificationTypeDescription, ...]
    instruction: str


@dataclass(frozen=True, slots=True)
class ContentClassifyPayload:
    strategy: ContentClassifyMode
    classification: RankedClassification | ClassificationContext


@dataclass(frozen=True, slots=True)
class ContentClassifyRequest:
    COMMAND_ID: ClassVar[str] = "content.classify"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = ContentClassifyPayload

    content: str
    mode: ContentClassifyMode = ContentClassifyMode.AUTO

    def __post_init__(self) -> None:
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("content.classify content must be a non-empty string")
        if not isinstance(self.mode, ContentClassifyMode):
            raise ValueError("content.classify mode must use ContentClassifyMode")


def execute(context: InvocationContext, request: ContentClassifyRequest):
    import process
    from _search.lexical_query import IndexNotFoundError, load_index

    router = load_router(context, ContentClassifyRequest)
    if isinstance(router, Error):
        return router
    try:
        index = load_index(context.selected_brain.vault_root)
    except IndexNotFoundError:
        index = None
    except (OSError, ValueError) as exc:
        return error(ContentClassifyRequest, ErrorCode.CONFLICT, str(exc), None)

    type_embeddings = metadata = None
    if request.mode is ContentClassifyMode.EMBEDDING:
        if not semantic_provider_ready(context):
            return semantic_unavailable(ContentClassifyRequest, context)
        semantic_state = load_semantic_state(context, ContentClassifyRequest)
        if isinstance(semantic_state, Error):
            return semantic_state
        _config, type_embeddings, _doc_embeddings, metadata = semantic_state
        if type_embeddings is None:
            return error(
                ContentClassifyRequest,
                ErrorCode.CONFLICT,
                "semantic type embeddings are missing",
                None,
            )
    elif (
        request.mode is ContentClassifyMode.AUTO
        and semantic_provider_ready(context)
    ):
        semantic_state = load_semantic_state(context, ContentClassifyRequest)
        if isinstance(semantic_state, Error):
            return semantic_state
        _config, type_embeddings, _doc_embeddings, metadata = semantic_state

    try:
        result = process.classify_content(
            router,
            context.selected_brain.vault_root,
            request.content,
            index=index,
            type_embeddings=type_embeddings,
            type_embeddings_meta=metadata,
            mode=request.mode.value,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return error(ContentClassifyRequest, ErrorCode.CONFLICT, str(exc), None)
    payload = _payload(result)
    return Ok(request.COMMAND_ID, request.COMMAND_VERSION, payload)


def _payload(result) -> ContentClassifyPayload:
    strategy = ContentClassifyMode(result["mode"])
    if strategy is ContentClassifyMode.CONTEXT_ASSEMBLY:
        classification = ClassificationContext(
            tuple(
                ClassificationTypeDescription(
                    item["type"], item["key"], item["description"]
                )
                for item in result["type_descriptions"]
            ),
            result["instruction"],
        )
    else:
        classification = RankedClassification(
            ClassificationCandidate(
                result["type"], result["key"], float(result["confidence"])
            ),
            tuple(
                ClassificationCandidate(
                    item["type"], item["key"], float(item["confidence"])
                )
                for item in result.get("alternatives", ())
            ),
            result["reasoning"],
        )
    return ContentClassifyPayload(strategy, classification)


def decode(payload: Mapping[str, object]) -> ContentClassifyRequest:
    unexpected = sorted(set(payload) - {"content", "mode"})
    if unexpected:
        raise ValueError(f"unexpected fields: {', '.join(unexpected)}")
    content = payload.get("content")
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    mode = payload.get("mode", ContentClassifyMode.AUTO.value)
    if not isinstance(mode, str):
        raise ValueError("mode must be a string")
    return ContentClassifyRequest(content, ContentClassifyMode(mode))


def catalogue_entry():
    return _catalogue_entry(
        ContentClassifyRequest,
        execute,
        optional_semantic=True,
    )


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(ContentClassifyRequest, decode)
