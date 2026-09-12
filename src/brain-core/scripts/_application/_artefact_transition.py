"""Shared typed mechanics for destructive artefact transitions."""

from __future__ import annotations

from ._decoding import reject_unexpected

from dataclasses import dataclass
from typing import Callable, Mapping

from ._mutation_support import mutation_entry, no_effect_error
from .types import Authority
from .context import InvocationContext
from .receipts import CommittedEffect
from .results import (
    CommandError,
    CommandWarning,
    ErrorCode,
    Ok,
    Partial,
    RequestErrorDetails,
    WarningCode,
)


@dataclass(frozen=True, slots=True)
class PathChange:
    old_path: str
    new_path: str


@dataclass(frozen=True, slots=True)
class UninspectedArchiveCandidate:
    path: str
    reason: str


@dataclass(frozen=True, slots=True)
class ArtefactRenamePayload:
    old_path: str
    new_path: str
    links_updated: int


@dataclass(frozen=True, slots=True)
class ArtefactConvertPayload:
    old_path: str
    new_path: str
    type: str
    links_updated: int
    attachment_scope_moved: PathChange | None
    orphaned_attachment_scopes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ArtefactArchivePayload:
    old_path: str
    new_path: str
    links_updated: int
    archived: tuple[PathChange, ...]


@dataclass(frozen=True, slots=True)
class ArtefactUnarchivePayload:
    old_path: str
    new_path: str
    links_updated: int
    restored: tuple[PathChange, ...]
    uninspected: tuple[UninspectedArchiveCandidate, ...]


@dataclass(frozen=True, slots=True)
class ArtefactDeletePayload:
    path: str
    deleted: tuple[str, ...]
    links_replaced: int
    orphaned_attachment_scopes: tuple[str, ...]


def validate_string(command_id: str, field: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{command_id} {field} must be a non-empty string")


def validate_recursive(command_id: str, recursive: bool) -> None:
    if not isinstance(recursive, bool):
        raise ValueError(f"{command_id} recursive must be a boolean")


def decode_required_strings(
    payload: Mapping[str, object],
    request_type,
    fields: tuple[str, ...],
):
    reject_unexpected(payload, set(fields))
    for field in fields:
        if field not in payload:
            raise ValueError(f"{field} is required")
        if not isinstance(payload[field], str):
            raise ValueError(f"{field} must be a string")
    return request_type(*(payload[field] for field in fields))


def decode_path_recursive(payload: Mapping[str, object], request_type):
    reject_unexpected(payload, {"path", "recursive"})
    if "path" not in payload:
        raise ValueError("path is required")
    path = payload["path"]
    recursive = payload.get("recursive", False)
    if not isinstance(path, str):
        raise ValueError("path must be a string")
    if not isinstance(recursive, bool):
        raise ValueError("recursive must be a boolean")
    return request_type(path, recursive)


def execute_transition(
    context: InvocationContext,
    request,
    *,
    operation: Callable[[str, dict], dict],
    payload_builder: Callable[[dict], object],
    effect_subject: Callable[[object], str | None],
):
    from _common import (
        MutationLockError,
        ParentChainError,
        PartialApplyError,
        parent_chain_error_message,
        public_mutation_error_message,
        vault_mutation_lock,
    )
    from _lifecycle.derived_cache_state import load_fresh_compiled_router

    if context.dry_run:
        return no_effect_error(
            type(request),
            ErrorCode.INVALID_REQUEST,
            f"{request.COMMAND_ID} does not support dry-run",
        )
    vault_root = str(context.selected_brain.vault_root)
    router = load_fresh_compiled_router(vault_root)
    if "error" in router:
        return no_effect_error(type(request), ErrorCode.CONFLICT, router["error"])

    try:
        with vault_mutation_lock(vault_root):
            partial_error = None
            try:
                raw_result = operation(vault_root, router)
            except PartialApplyError as exc:
                partial_error = exc
            if request.COMMAND_ID in {
                "artefact.archive",
                "artefact.unarchive",
                "artefact.delete",
            }:
                from _portable.router_maintenance import maintain_router
                from _portable.lexical_maintenance import maintain_lexical_index

                try:
                    router_result = maintain_router(
                        vault_root, dry_run=False, force=True
                    )
                    maintain_lexical_index(vault_root, dry_run=False, force=True)
                    if router_result.status == "partial":
                        raise RuntimeError("Session mirror refresh failed")
                except (OSError, RuntimeError, ValueError) as exc:
                    raise PartialApplyError(
                        (
                            public_mutation_error_message(partial_error) + " "
                            if partial_error
                            else ""
                        )
                        + "Artefact mutation committed effects, but derived index refresh failed; "
                        "run runtime.refresh-router and retrieval.refresh-lexical."
                    ) from exc
                finally:
                    if context.derived_snapshots is not None:
                        context.derived_snapshots.invalidate()
            if partial_error is not None:
                raise partial_error
    except MutationLockError as exc:
        return no_effect_error(
            type(request),
            ErrorCode.CONFLICT,
            public_mutation_error_message(exc),
            retryable=True,
        )
    except ParentChainError as exc:
        message = parent_chain_error_message(exc)
        return no_effect_error(type(request), ErrorCode.INVALID_REQUEST, message)
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
            (CommittedEffect(request.COMMAND_ID, _request_subject(request)),),
        )
    except FileNotFoundError as exc:
        return no_effect_error(type(request), ErrorCode.NOT_FOUND, str(exc))
    except FileExistsError as exc:
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))
    except ValueError as exc:
        return no_effect_error(type(request), ErrorCode.INVALID_REQUEST, str(exc))

    payload = payload_builder(raw_result)
    warnings = _warnings(payload)
    subject = effect_subject(payload)
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        payload,
        committed_effects=(
            ()
            if subject is None
            else (CommittedEffect(request.COMMAND_ID, subject),)
        ),
        warnings=warnings,
    )


def path_changes(values) -> tuple[PathChange, ...]:
    return tuple(PathChange(item["old_path"], item["new_path"]) for item in values)


def catalogue_entry(
    request_type,
    executor,
    *,
    authority: Authority = Authority.CONTRIBUTOR,
):
    return mutation_entry(request_type, executor, authority)


def _request_subject(request) -> str:
    for field in ("path", "source"):
        value = getattr(request, field, None)
        if isinstance(value, str) and value:
            return value
    return request.COMMAND_ID


def _warnings(payload) -> tuple[CommandWarning, ...]:
    messages = []
    orphaned = getattr(payload, "orphaned_attachment_scopes", ())
    if orphaned:
        messages.append(
            "Preserved orphaned attachment scope(s): " + ", ".join(orphaned)
        )
    uninspected = getattr(payload, "uninspected", ())
    if uninspected:
        messages.append(
            "Recursive restore could not inspect archived candidate(s): "
            + "; ".join(f"{item.path}: {item.reason}" for item in uninspected)
        )
    return tuple(
        CommandWarning(WarningCode.FOLLOW_UP_REQUIRED, message)
        for message in messages
    )
