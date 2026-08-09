"""Dynamic stdlib launcher adapter over the typed launcher invocation owner."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Mapping

from launcher_catalogue import LauncherCatalogue

from .context import LauncherContext
from .contracts import Error, ErrorCode, Ok, Partial
from .invocation import (
    LauncherInvocation,
    authority_denied_result,
    internal_error_result,
)
from .owners import LauncherOwners
from .projection import canonical_wire_value, resolve_request


@dataclass(frozen=True, slots=True)
class LauncherRequestError(ValueError):
    message: str

    def __post_init__(self) -> None:
        if not self.message.strip():
            raise ValueError("launcher adapter request error requires a message")

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class LauncherAdapterProjection:
    result: object
    structured_content: dict[str, object]
    json_text: str
    concise_text: str
    is_error: bool
    exit_code: int

    def __post_init__(self) -> None:
        if not self.concise_text.strip() or not self.json_text:
            raise ValueError("launcher adapter projection requires JSON and concise text")
        if self.is_error != (not isinstance(self.result, Ok)):
            raise ValueError("launcher adapter error flag must follow the result branch")
        if self.exit_code not in range(5):
            raise ValueError("launcher adapter exit code must use categories 0-4")


@dataclass(frozen=True, slots=True)
class LauncherAdapter:
    catalogue: LauncherCatalogue
    owners: LauncherOwners

    def __post_init__(self) -> None:
        catalogue_entries = {
            entry.command_id: (entry.command_version, entry.owner_ref)
            for entry in self.catalogue.entries
        }
        owner_entries = {
            owner.command_id: (owner.command_version, owner.owner_ref)
            for owner in self.owners.entries
        }
        if catalogue_entries != owner_entries:
            raise ValueError("launcher adapter catalogue and owners must match exactly")

    def invoke(
        self,
        context: LauncherContext,
        command_id: str,
        payload: Mapping[str, object],
    ) -> LauncherAdapterProjection:
        entry = next(
            (item for item in self.catalogue.entries if item.command_id == command_id),
            None,
        )
        owner = next(
            (item for item in self.owners.entries if item.command_id == command_id),
            None,
        )
        if entry is None or owner is None:
            raise LauncherRequestError("command is not owned by the launcher catalogue")
        try:
            denied = authority_denied_result(context, entry)
        except Exception:
            return project_launcher_result(
                internal_error_result(context, command_id, entry.command_version)
            )
        if denied is not None:
            return project_launcher_result(denied)
        if not isinstance(payload, Mapping):
            raise LauncherRequestError("launcher command payload must be an object")
        try:
            request = resolve_request(owner.request_type, payload)
        except (TypeError, ValueError) as exc:
            raise LauncherRequestError(str(exc)) from exc
        result = LauncherInvocation(context, self.catalogue, self.owners).invoke(request)
        return project_launcher_result(result)


def project_launcher_result(result) -> LauncherAdapterProjection:
    """Project the launcher result into the shared structural wire vocabulary."""

    envelope = _result_envelope(result)
    return LauncherAdapterProjection(
        result=result,
        structured_content=envelope,
        json_text=json.dumps(envelope, ensure_ascii=False, separators=(",", ":")),
        concise_text=_concise_text(result),
        is_error=not isinstance(result, Ok),
        exit_code=_exit_code(result),
    )


def _result_envelope(result) -> dict[str, object]:
    if not isinstance(result, (Ok, Partial, Error)):
        raise TypeError("launcher result projection requires a structural result")
    envelope: dict[str, object] = {
        "schema": result.schema,
        "command": result.command_id,
        "command_version": result.command_version,
        "status": result.status,
        "warnings": canonical_wire_value(result.warnings),
    }
    if isinstance(result, Ok):
        envelope["result"] = canonical_wire_value(result.result)
        envelope["committed_effects"] = canonical_wire_value(result.committed_effects)
        return envelope
    if isinstance(result, Partial):
        envelope["result"] = {
            "committed_effects": canonical_wire_value(result.committed_effects)
        }
        envelope["error"] = _error_value(result.error, effects="known")
        return envelope
    envelope["result"] = None
    envelope["error"] = _error_value(
        result.error,
        effects=result.effects,
        retryable=result.retryable,
        outcome_reference=result.outcome_reference,
    )
    return envelope


def _error_value(
    error,
    *,
    effects: str,
    retryable: bool = False,
    outcome_reference=None,
) -> dict[str, object]:
    value = {
        "code": error.code.value,
        "message": error.message,
        "details": canonical_wire_value(error.details),
        "next_action": canonical_wire_value(error.next_action),
        "effects": effects,
        "retryable": retryable,
    }
    if outcome_reference is not None:
        value["outcome_reference"] = canonical_wire_value(outcome_reference)
    return value


def _exit_code(result) -> int:
    if isinstance(result, Ok):
        return 0
    if isinstance(result, Partial):
        return 1
    if result.error.code in {
        ErrorCode.INVALID_REQUEST,
        ErrorCode.NOT_FOUND,
        ErrorCode.CONFLICT,
    }:
        return 2
    if result.error.code in {
        ErrorCode.AUTHORITY_DENIED,
        ErrorCode.CAPABILITY_UNAVAILABLE,
    }:
        return 3
    return 4


def _concise_text(result) -> str:
    if isinstance(result, Ok):
        return f"{result.command_id}: ok"
    if isinstance(result, Partial):
        return f"{result.command_id}: partial — {result.error.message}"
    return f"{result.command_id}: {result.error.code.value} — {result.error.message}"
