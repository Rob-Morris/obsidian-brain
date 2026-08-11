"""Typed ``artefact.migrate-naming`` owner."""

from __future__ import annotations

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
        public_mutation_error_message,
        vault_mutation_lock,
    )
    from _lifecycle.derived_cache_state import load_fresh_compiled_router
    import migrate_naming

    root = str(context.selected_brain.vault_root)
    router = load_fresh_compiled_router(root)
    if "error" in router:
        return no_effect_error(type(request), ErrorCode.CONFLICT, router["error"])
    try:
        with vault_mutation_lock(root):
            result = migrate_naming.migrate_vault(
                root, router=router, dry_run=context.dry_run
            )
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
        if payload.renamed and not payload.dry_run:
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
    if payload:
        raise ValueError(f"unexpected fields: {', '.join(sorted(payload))}")
    return ArtefactMigrateNamingRequest()


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
    return contributor_mutation_entry(ArtefactMigrateNamingRequest, execute)


def resolver_entry():
    from ..resolver import ResolverEntry

    return ResolverEntry(ArtefactMigrateNamingRequest, decode)
