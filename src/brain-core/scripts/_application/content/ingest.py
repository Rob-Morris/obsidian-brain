"""Typed managed ``content.ingest`` owner."""

from __future__ import annotations

from .._decoding import reject_unexpected

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
    load_lexical_index,
    load_router,
    load_semantic_state,
    semantic_provider_ready,
    semantic_unavailable,
)
from ..context import InvocationContext
from ..preparation import admit_owner
from ._preparation import INGESTION, plan_ingest, ingestion_binding
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
    COMMAND_VERSION: ClassVar[int] = 2
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


@dataclass(frozen=True, slots=True)
class _RetrievalState:
    index: object | None
    type_embeddings: object | None
    doc_embeddings: object | None
    metadata: object | None


@dataclass(frozen=True, slots=True)
class _IngestionOutcome:
    result: Mapping[str, object]
    action: str | None
    staged_handle: str | None
    staging_warning: str | None


def execute(context: InvocationContext, request: ContentIngestRequest):
    from _common import (
        MutationLockError,
        PartialApplyError,
        public_mutation_error_message,
    )

    if context.dry_run:
        return no_effect_error(
            ContentIngestRequest,
            ErrorCode.INVALID_REQUEST,
            "content.ingest does not support dry-run",
        )
    root = str(context.selected_brain.vault_root)
    try:
        outcome = _ingest_locked(root, request, context=context)
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
    return outcome if isinstance(outcome, Error) else _project_outcome(request, outcome)


def load_ingestion_inputs(context, request):
    """Resolve current router and retrieval state while the caller holds the guard."""
    from _lifecycle.derived_cache_state import load_fresh_compiled_router
    router = load_fresh_compiled_router(context.selected_brain.vault_root)
    if "error" in router:
        return no_effect_error(type(request), ErrorCode.CONFLICT, router["error"])
    retrieval = _prepare_retrieval_state(context, request, router)
    return retrieval if isinstance(retrieval, Error) else (router, retrieval)


def _prepare_retrieval_state(
    context: InvocationContext,
    request: ContentIngestRequest,
    router,
):
    import process
    from _search.lexical_query import IndexNotFoundError

    if request.type_key is not None and request.title is not None:
        exact = process.resolve_exact_content(
            router,
            context.selected_brain.vault_root,
            request.type_key,
            request.title,
        )
        if exact is not None and exact.get("action") == "update":
            return _RetrievalState(None, None, None, None)

    needs_classification_choice = (
        request.type_key is None
        and request.mode is ContentClassifyMode.CONTEXT_ASSEMBLY
    )
    index = None
    if not needs_classification_choice:
        try:
            index = load_lexical_index(context)
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
        semantic_state = load_semantic_state(
            context,
            ContentIngestRequest,
            router,
            selection="all" if request.type_key is None else "documents",
        )
        if isinstance(semantic_state, Error):
            return semantic_state
        _config, type_embeddings, doc_embeddings, metadata = semantic_state
        if type_embeddings is None:
            return no_effect_error(
                ContentIngestRequest,
                ErrorCode.CONFLICT,
                "semantic type embeddings are missing",
            )
    elif semantic_provider_ready(context) and not needs_classification_choice:
        semantic_state = load_semantic_state(
            context,
            ContentIngestRequest,
            router,
            selection=(
                "all"
                if request.type_key is None
                and request.mode is ContentClassifyMode.AUTO
                else "documents"
            ),
        )
        if isinstance(semantic_state, Error):
            return semantic_state
        _config, type_embeddings, doc_embeddings, metadata = semantic_state

    return _RetrievalState(index, type_embeddings, doc_embeddings, metadata)


def _ingest_locked(
    root: str,
    request: ContentIngestRequest,
    *, context: InvocationContext,
) -> _IngestionOutcome:
    import process
    from _common import vault_mutation_lock
    from _staging import finalise_staged_body

    with vault_mutation_lock(root):
        inputs = load_ingestion_inputs(context, request)
        if isinstance(inputs, Error):
            return inputs
        router, retrieval = inputs
        body, staged_handle = resolve_mutation_content(root, request.content, context=context)
        if context.admission is None:
            result = process.ingest_content(
                router, root, body, title=request.title, type_hint=request.type_key,
                index=retrieval.index, type_embeddings=retrieval.type_embeddings,
                type_embeddings_meta=retrieval.metadata, doc_embeddings=retrieval.doc_embeddings,
                doc_embeddings_meta=retrieval.metadata, classification_mode=request.mode.value)
        else:
            plan, frozen = plan_ingest(context, request, router, retrieval, body,
                                       frozen_inputs=context.admission.frozen_inputs)
            if plan["action_taken"] != "error":
                # The planner freezes time/key choices; admission must compare that
                # complete binding, not reconstruct it with a new naming choice.
                def binding(context, request, *, frozen_inputs=None):
                    return ingestion_binding(context, request, plan=plan, frozen_inputs=frozen)
                admit_owner(context, request, binding)
            result = process.apply_ingestion_plan(router, root, plan)
        action = result.get("action_taken")
        staging_warning = (
            finalise_staged_body(root, staged_handle)
            if action in {"created", "updated"}
            else None
        )
    return _IngestionOutcome(result, action, staged_handle, staging_warning)


def _project_outcome(
    request: ContentIngestRequest,
    outcome: _IngestionOutcome,
):
    if outcome.action == "error":
        return no_effect_error(
            ContentIngestRequest,
            ErrorCode.INVALID_REQUEST,
            outcome.result["message"],
        )
    payload = _payload(
        outcome.result,
        staged_handle_consumed=(
            outcome.staged_handle is not None
            and outcome.action in {"created", "updated"}
            and outcome.staging_warning is None
        ),
    )
    warnings = (
        (CommandWarning(WarningCode.FOLLOW_UP_REQUIRED, outcome.staging_warning),)
        if outcome.staging_warning
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
    reject_unexpected(payload, allowed)
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
    from ..catalogue import ALL_APPLICATION_PROJECTIONS, ApplicationEntry

    return ApplicationEntry(
        preparation=INGESTION,
        request_type=ContentIngestRequest,
        executor=execute,
        dependency_tier=DependencyTier.MANAGED,
        locality=Locality.SELECTED_BRAIN_LOCAL,
        required_providers=(),
        optional_providers=("semantic_retrieval",),
        authority=Authority.CONTRIBUTOR,
        effect_class=EffectClass.SELECTED_BRAIN_MUTATION,
        retry_class=RetryClass.RECEIPT_REQUIRED,
        projections=ALL_APPLICATION_PROJECTIONS,
    )
