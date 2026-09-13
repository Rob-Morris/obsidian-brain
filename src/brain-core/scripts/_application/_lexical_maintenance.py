"""Typed application projection for portable lexical-index maintenance."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from ._mutation_support import maintainer_mutation_entry, no_effect_error
from .context import InvocationContext
from ._managed_preparation import MAINTENANCE, maintenance_binding
from .preparation import admit_owner
from .receipts import CommittedEffect
from .results import ErrorCode, Ok


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

    root = context.selected_brain.vault_root
    try:
        with vault_mutation_lock(root):
            admit_owner(context, request, maintenance_binding)
            result = maintain_lexical_index(
                root,
                dry_run=context.dry_run,
                force=force,
            )
    except MutationLockError as exc:
        return no_effect_error(
            type(request),
            ErrorCode.CONFLICT,
            public_mutation_error_message(exc),
            retryable=True,
        )
    except (UnreadableRetrievalSourceError, RetrievalPersistenceError) as exc:
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))

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
        (CommittedEffect(request.COMMAND_ID, ".brain/local/retrieval-index.json"),)
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
    return replace(maintainer_mutation_entry(request_type, executor), preparation=MAINTENANCE)
