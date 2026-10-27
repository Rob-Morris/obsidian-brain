"""Typed application projection for portable lexical-index maintenance."""

from __future__ import annotations

from .types import InitialAuthorisationClass

from dataclasses import dataclass, replace
from enum import Enum

from ._mutation_support import maintainer_mutation_entry, no_effect_error
from .context import InvocationContext
from ._managed_preparation import MAINTENANCE, maintenance_binding
from .preparation import admit_owner
from .receipts import CommittedEffect
from .results import CommandError, ErrorCode, Ok, Partial, RequestErrorDetails


class LexicalMaintenanceStatus(str, Enum):
    NOOP = "noop"
    PLANNED = "planned"
    CHANGED = "changed"


@dataclass(frozen=True, slots=True)
class LexicalMaintenancePayload:
    status: LexicalMaintenanceStatus
    reason: str
    dry_run: bool
    forced: bool
    document_count: int | None
    term_count: int | None
    sidecars_removed: tuple[str, ...]


def execute_lexical_maintenance(
    context: InvocationContext,
    request,
    *,
    force: bool,
):
    from _common import (
        MutationLockError,
        public_mutation_error_message,
        vault_mutation_lock,
    )
    from _lifecycle.retrieval_errors import (
        RetrievalPersistenceError,
        UnreadableRetrievalSourceError,
    )
    from _portable.lexical_maintenance import maintain_lexical_index

    from _lifecycle.retrieval_assets import _asset_signatures
    from _common._source_access import SourceAccessError
    root = context.selected_brain.vault_root
    paths = (".brain/local/retrieval-index.json", ".brain/local/embeddings-meta.json",
             ".brain/local/type-embeddings.npy", ".brain/local/doc-embeddings.npy")
    try:
        with vault_mutation_lock(root):
            admit_owner(context, request, maintenance_binding)
            from _bootstrap.owned_paths import owned_output_paths
            try:
                owned_output_paths(root, paths)
                before = _asset_signatures(root, paths)
            except OSError as exc:
                return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))
            try:
                result = maintain_lexical_index(
                    root,
                    dry_run=context.dry_run,
                    force=force,
                )
            except (OSError, SourceAccessError, RetrievalPersistenceError) as exc:
                after = _asset_signatures(root, paths)
                changed = tuple(path for path in paths if before[path] != after[path])
                if changed:
                    message = "Lexical maintenance published derived state but follow-up work failed. Inspect the receipt before running retrieval.refresh-lexical again."
                    return Partial(request.COMMAND_ID, request.COMMAND_VERSION,
                        CommandError(ErrorCode.CONFLICT, message, RequestErrorDetails(None, message)),
                        tuple(CommittedEffect(request.COMMAND_ID, path) for path in changed))
                return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))
    except MutationLockError as exc:
        return no_effect_error(
            type(request),
            ErrorCode.CONFLICT,
            public_mutation_error_message(exc),
            retryable=True,
        )

    status = LexicalMaintenanceStatus(result.status)
    payload = LexicalMaintenancePayload(
        status,
        result.reason,
        result.dry_run,
        result.forced,
        result.document_count,
        result.term_count,
        result.sidecars_removed,
    )
    effects = (
        tuple(CommittedEffect(request.COMMAND_ID, path) for path in
              (".brain/local/retrieval-index.json", *result.sidecars_removed))
        if status is LexicalMaintenanceStatus.CHANGED
        else ()
    )
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        payload,
        committed_effects=effects,
    )


def catalogue_entry(request_type, executor):
    return replace(maintainer_mutation_entry(request_type, executor), initial_class=InitialAuthorisationClass.OBSERVATION, preparation=MAINTENANCE)
