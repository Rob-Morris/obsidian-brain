"""Typed managed ``content.ingest`` owner."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Mapping

from .._mutation_support import (
    InlineContent,
    MutationContent,
    StagedContent,
    decode_mutation_content,
    no_effect_error,
    resolve_mutation_content,
)
from .._search_support import (
    load_router,
    load_semantic_state,
    semantic_provider_ready,
    semantic_unavailable,
)
from ..context import InvocationContext
from ..receipts import CommittedEffect
from ..results import (
    CommandError,
    CommandWarning,
    Error,
    ErrorCode,
    Ok,
    Partial,
    RequestErrorDetails,
    WarningCode,
)
from ..types import (
    Authority,
    DependencyTier,
    EffectClass,
    Locality,
    Projection,
    ProjectionEligibility,
    RetryClass,
)
from .classify import (
    ContentClassifyMode,
    ContentClassifyPayload,
    _payload as classification_payload,
)
from .resolve import ContentResolvePayload, payload_from_result


class ContentIngestAction(str, Enum):
    NEEDS_CLASSIFICATION = "needs_classification"
    AMBIGUOUS = "ambiguous"
    CREATED = "created"
    UPDATED = "updated"


@dataclass(frozen=True, slots=True)
class ContentIngestPayload:
    action: ContentIngestAction
    path: str | None
    artefact_type: str | None
    title: str | None
    classification: ContentClassifyPayload | None
    resolution: ContentResolvePayload | None
    needs_decision: bool
    message: str
    staged_handle_consumed: bool


@dataclass(frozen=True, slots=True)
class ContentIngestRequest:
    COMMAND_ID: ClassVar[str] = "content.ingest"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = ContentIngestPayload

    content: MutationContent
    type_key: str | None = None
    title: str | None = None
    mode: ContentClassifyMode = ContentClassifyMode.AUTO

    def __post_init__(self) -> None:
        if not isinstance(self.content, (InlineContent, StagedContent)):
            raise ValueError("content.ingest content has an invalid variant")
        for name in ("type_key", "title"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, str) or not value.strip()
            ):
                raise ValueError(
                    f"content.ingest {name} must be null or a non-empty string"
                )
        if not isinstance(self.mode, ContentClassifyMode):
            raise ValueError("content.ingest mode must use ContentClassifyMode")


def execute(context: InvocationContext, request: ContentIngestRequest):
    import process
    from _common import (
        MutationLockError,
        PartialApplyError,
        public_mutation_error_message,
        vault_mutation_lock,
    )
    from _search.lexical_query import IndexNotFoundError, load_index
    from _staging import finalise_staged_body

    if context.dry_run:
        return no_effect_error(
            ContentIngestRequest,
            ErrorCode.INVALID_REQUEST,
            "content.ingest does not support dry-run",
        )
    router = load_router(context, ContentIngestRequest)
    if isinstance(router, Error):
        return router
    try:
        index = load_index(context.selected_brain.vault_root)
    except (IndexNotFoundError, OSError, ValueError) as exc:
        return no_effect_error(
            ContentIngestRequest,
            ErrorCode.CONFLICT,
            str(exc),
        )

    type_embeddings = doc_embeddings = metadata = None
    if request.mode is ContentClassifyMode.EMBEDDING:
        if not semantic_provider_ready(context):
            return semantic_unavailable(ContentIngestRequest, context)
        semantic_state = load_semantic_state(context, ContentIngestRequest)
        if isinstance(semantic_state, Error):
            return semantic_state
        _config, type_embeddings, doc_embeddings, metadata = semantic_state
        if type_embeddings is None:
            return no_effect_error(
                ContentIngestRequest,
                ErrorCode.CONFLICT,
                "semantic type embeddings are missing",
            )
    elif semantic_provider_ready(context):
        semantic_state = load_semantic_state(context, ContentIngestRequest)
        if isinstance(semantic_state, Error):
            return semantic_state
        _config, type_embeddings, doc_embeddings, metadata = semantic_state

    root = str(context.selected_brain.vault_root)
    try:
        with vault_mutation_lock(root):
            body, staged_handle = resolve_mutation_content(root, request.content)
            result = process.ingest_content(
                router,
                root,
                body,
                title=request.title,
                type_hint=request.type_key,
                index=index,
                type_embeddings=type_embeddings,
                type_embeddings_meta=metadata,
                doc_embeddings=doc_embeddings,
                doc_embeddings_meta=metadata,
                classification_mode=request.mode.value,
            )
            action = result.get("action_taken")
            staging_warning = None
            if action in {"created", "updated"}:
                staging_warning = finalise_staged_body(root, staged_handle)
    except MutationLockError as exc:
        return no_effect_error(
            ContentIngestRequest,
            ErrorCode.CONFLICT,
            public_mutation_error_message(exc),
            retryable=True,
        )
    except PartialApplyError as exc:
        message = public_mutation_error_message(exc)
        return Partial(
            request.COMMAND_ID,
            request.COMMAND_VERSION,
            CommandError(
                ErrorCode.CONFLICT,
                message,
                RequestErrorDetails(None, message),
            ),
            (
                CommittedEffect(
                    request.COMMAND_ID,
                    request.title or request.type_key or "content",
                ),
            ),
        )
    except ValueError as exc:
        return no_effect_error(
            ContentIngestRequest,
            ErrorCode.INVALID_REQUEST,
            str(exc),
        )

    if action == "error":
        return no_effect_error(
            ContentIngestRequest,
            ErrorCode.INVALID_REQUEST,
            result["message"],
        )
    payload = _payload(
        result,
        staged_handle_consumed=(
            staged_handle is not None
            and action in {"created", "updated"}
            and staging_warning is None
        ),
    )
    warnings = (
        (CommandWarning(WarningCode.FOLLOW_UP_REQUIRED, staging_warning),)
        if staging_warning
        else ()
    )
    effects = (
        (CommittedEffect(request.COMMAND_ID, payload.path),)
        if payload.path is not None
        and payload.action in {ContentIngestAction.CREATED, ContentIngestAction.UPDATED}
        else ()
    )
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        payload,
        committed_effects=effects,
        warnings=warnings,
    )


def _payload(result, *, staged_handle_consumed: bool) -> ContentIngestPayload:
    raw_classification = result.get("classification")
    raw_resolution = result.get("resolution")
    return ContentIngestPayload(
        action=ContentIngestAction(result["action_taken"]),
        path=result.get("path"),
        artefact_type=result.get("type"),
        title=result.get("title"),
        classification=(
            classification_payload(raw_classification)
            if raw_classification is not None
            else None
        ),
        resolution=(
            payload_from_result(raw_resolution)
            if raw_resolution is not None
            else None
        ),
        needs_decision=bool(result["needs_decision"]),
        message=result["message"],
        staged_handle_consumed=staged_handle_consumed,
    )


def decode(payload: Mapping[str, object]) -> ContentIngestRequest:
    allowed = {"content", "type_key", "title", "mode"}
    unexpected = sorted(set(payload) - allowed)
    if unexpected:
        raise ValueError(f"unexpected fields: {', '.join(unexpected)}")
    type_key = payload.get("type_key")
    title = payload.get("title")
    if type_key is not None and not isinstance(type_key, str):
        raise ValueError("type_key must be null or a string")
    if title is not None and not isinstance(title, str):
        raise ValueError("title must be null or a string")
    mode = payload.get("mode", ContentClassifyMode.AUTO.value)
    if not isinstance(mode, str):
        raise ValueError("mode must be a string")
    try:
        classification_mode = ContentClassifyMode(mode)
    except ValueError as exc:
        raise ValueError(
            "mode must be 'auto', 'embedding', 'bm25_only', or 'context_assembly'"
        ) from exc
    return ContentIngestRequest(
        content=decode_mutation_content(payload.get("content")),
        type_key=type_key,
        title=title,
        mode=classification_mode,
    )


def catalogue_entry():
    from ..catalogue import ApplicationEntry

    return ApplicationEntry(
        request_type=ContentIngestRequest,
        executor=execute,
        dependency_tier=DependencyTier.MANAGED,
        locality=Locality.SELECTED_BRAIN_LOCAL,
        required_providers=(),
        optional_providers=("semantic_retrieval",),
        authority=Authority.CONTRIBUTOR,
        effect_class=EffectClass.SELECTED_BRAIN_MUTATION,
        retry_class=RetryClass.RECEIPT_REQUIRED,
        projections=tuple(
            ProjectionEligibility(projection, True)
            for projection in (
                Projection.MCP,
                Projection.CLI,
                Projection.SCRIPT,
                Projection.PYTHON,
            )
        ),
    )


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(ContentIngestRequest, decode)
