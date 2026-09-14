"""Typed ``artefact.migrate-naming`` owner."""

from __future__ import annotations

from .._decoding import decode_empty
from dataclasses import dataclass
from typing import ClassVar, Mapping

from .._artefact_transition import PathChange
from .._mutation_support import contributor_mutation_entry, no_effect_error
from ..context import InvocationContext
from ..receipts import CommittedEffect
from ..results import CommandError, ErrorCode, Ok, Partial, RequestErrorDetails


@dataclass(frozen=True, slots=True)
class NamingMigration:
    path: PathChange
    links_updated: int


@dataclass(frozen=True, slots=True)
class NamingMigrationError:
    file: str
    target: str
    message: str
    partial_apply: bool


@dataclass(frozen=True, slots=True)
class ArtefactMigrateNamingPayload:
    dry_run: bool
    renamed: int
    skipped: int
    details: tuple[NamingMigration, ...]
    errors: tuple[NamingMigrationError, ...]


@dataclass(frozen=True, slots=True)
class ArtefactMigrateNamingRequest:
    COMMAND_ID: ClassVar[str] = "artefact.migrate-naming"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = ArtefactMigrateNamingPayload


def execute(context: InvocationContext, request: ArtefactMigrateNamingRequest):
    from _common import (
        MutationLockError,
        PartialApplyError,
        public_mutation_error_message,
        vault_mutation_lock,
    )
    from _lifecycle.derived_cache_state import require_fresh_compiled_router
    import migrate_naming

    root = str(context.selected_brain.vault_root)
    try:
        with vault_mutation_lock(root):
            from ..preparation import admit_owner

            router = require_fresh_compiled_router(root)
            plan = migrate_naming.plan_naming_migration(root, router=router, dry_run=context.dry_run)
            if plan.movement is not None:
                admit_owner(context, request, migration_binding, plan=plan, router=router)
            result = migrate_naming.apply_naming_migration(root, plan)
            if not context.dry_run and (result.get("renamed") or any(
                item.get("partial_apply") for item in result.get("errors", ())
            )):
                from .._transition_indexes import reconcile_transition_indexes
                reconcile_transition_indexes(context)
    except PartialApplyError as exc:
        from .._transition_indexes import transition_error
        return Partial(request.COMMAND_ID, request.COMMAND_VERSION,
                       transition_error(exc), (CommittedEffect(request.COMMAND_ID, "vault"),))
    except MutationLockError as exc:
        return no_effect_error(
            type(request),
            ErrorCode.CONFLICT,
            public_mutation_error_message(exc),
            retryable=True,
        )

    if result.get("error"):
        return no_effect_error(type(request), ErrorCode.CONFLICT, result["error"])
    payload = _payload(result)
    if payload.errors:
        message = "; ".join(item.message for item in payload.errors)
        if (payload.renamed or any(item.partial_apply for item in payload.errors)) and not payload.dry_run:
            return Partial(
                request.COMMAND_ID,
                request.COMMAND_VERSION,
                CommandError(
                    ErrorCode.CONFLICT,
                    message,
                    RequestErrorDetails(None, message),
                ),
                (CommittedEffect(request.COMMAND_ID, "vault"),),
            )
        return no_effect_error(type(request), ErrorCode.CONFLICT, message)
    effects = (
        (CommittedEffect(request.COMMAND_ID, "vault"),)
        if payload.renamed and not payload.dry_run
        else ()
    )
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        payload,
        committed_effects=effects,
    )

def decode(payload: Mapping[str, object]) -> ArtefactMigrateNamingRequest:
    return decode_empty(payload, ArtefactMigrateNamingRequest)


def _payload(result: dict) -> ArtefactMigrateNamingPayload:
    return ArtefactMigrateNamingPayload(
        dry_run=bool(result["dry_run"]),
        renamed=result["renamed"],
        skipped=result["skipped"],
        details=tuple(
            NamingMigration(
                PathChange(item["source"], item["dest"]),
                item["links_updated"],
            )
            for item in result.get("details") or ()
        ),
        errors=tuple(
            NamingMigrationError(
                item["file"],
                item["target"],
                item["error"],
                bool(item.get("partial_apply")),
            )
            for item in result.get("errors") or ()
        ),
    )


def catalogue_entry():
    from ..catalogue import exclude_projection
    from ..types import Projection

    from dataclasses import replace
    from ..preparation import OperationPreparation

    return replace(exclude_projection(
        contributor_mutation_entry(ArtefactMigrateNamingRequest, execute),
        Projection.MCP,
        "Vault-wide naming migration is reserved for deliberate CLI or "
        "direct-script administration.",
    ), preparation=OperationPreparation(prepare))


def migration_binding(context, request, *, plan, router, frozen_inputs=None):
    from ..preparation_transition import transition_binding

    if plan.movement is None:
        raise ValueError("Naming migration preflight found conflicts; resolve them before preparing")
    return transition_binding(context, request, plan=plan.movement, router=router,
                              frozen_inputs=frozen_inputs)


def prepare(context, request, *, frozen_inputs=None):
    from _common import vault_mutation_lock
    from _lifecycle.derived_cache_state import require_fresh_compiled_router
    import migrate_naming

    root = str(context.selected_brain.vault_root)
    with vault_mutation_lock(root):
        router = require_fresh_compiled_router(root)
        plan = migrate_naming.plan_naming_migration(root, router=router, dry_run=context.dry_run)
        return migration_binding(context, request, plan=plan, router=router, frozen_inputs=frozen_inputs)
