"""Shared dynamic adapter boundary for MCP, CLI and direct scripts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .application import (
    CommandApplication,
    authority_denied_result,
    consent_error_result,
    internal_error_result,
)
from .catalogue import ApplicationCatalogue
from .consent import ConsentError
from .context import InvocationContext, report_failure_safely
from .projection import canonical_result_envelope, canonical_result_json
from .resolver import RequestResolutionError, RequestResolver, ResolutionErrorCode
from .results import (
    CommandError,
    CommandResult,
    Error,
    ErrorCode,
    Ok,
    Partial,
    RequestErrorDetails,
)


@dataclass(frozen=True, slots=True)
class AdapterRequestError(ValueError):
    code: ResolutionErrorCode
    message: str

    def __post_init__(self) -> None:
        if not self.message.strip():
            raise ValueError("adapter request error requires a message")

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class AdapterProjection:
    result: CommandResult
    is_error: bool
    exit_code: int

    def __post_init__(self) -> None:
        if self.is_error != (not isinstance(self.result, Ok)):
            raise ValueError("adapter error flag must follow the structural result branch")
        if self.exit_code not in range(5):
            raise ValueError("adapter exit code must use the canonical 0-4 categories")

    @property
    def structured_content(self) -> dict[str, object]:
        return canonical_result_envelope(self.result)

    @property
    def json_text(self) -> str:
        return canonical_result_json(self.result)

    @property
    def concise_text(self) -> str:
        return _concise_text(self.result)


@dataclass(frozen=True, slots=True)
class ApplicationAdapter:
    catalogue: ApplicationCatalogue
    resolver: RequestResolver

    def __post_init__(self) -> None:
        catalogue_entries = {
            entry.command_id: (entry.command_version, entry.request_type)
            for entry in self.catalogue.entries
        }
        resolver_entries = {
            entry.command_id: (entry.command_version, entry.request_type)
            for entry in self.resolver.entries
        }
        if catalogue_entries != resolver_entries:
            raise ValueError("adapter catalogue and resolver identities must match exactly")

    def invoke(
        self,
        context: InvocationContext,
        command_id: str,
        payload: Mapping[str, object],
    ) -> AdapterProjection:
        entry = next(
            (item for item in self.catalogue.entries if item.command_id == command_id),
            None,
        )
        if entry is not None:
            try:
                denied = authority_denied_result(context, entry)
            except ConsentError as exc:
                return project_adapter_result(consent_error_result(
                    context, entry.command_id, entry.command_version, exc))
            except Exception as exc:
                report_failure_safely(
                    context,
                    phase="authority.before-resolution",
                    command_id=entry.command_id,
                    error=exc,
                )
                return project_adapter_result(
                    internal_error_result(
                        context,
                        entry.command_id,
                        entry.command_version,
                    )
                )
            if denied is not None:
                return project_adapter_result(denied)
        if not isinstance(payload, Mapping):
            message = "command request payload must be an object"
            if entry is not None:
                return project_invalid_request(entry, message)
            raise AdapterRequestError(ResolutionErrorCode.INVALID_REQUEST, message)
        try:
            request = self.resolver.resolve(command_id, payload)
        except RequestResolutionError as exc:
            if entry is not None:
                return project_invalid_request(entry, str(exc))
            raise AdapterRequestError(exc.code, str(exc)) from exc
        except (TypeError, ValueError) as exc:
            if entry is not None:
                return project_invalid_request(entry, str(exc))
            raise AdapterRequestError(
                ResolutionErrorCode.INVALID_REQUEST,
                str(exc),
            ) from exc
        result = CommandApplication(context, self.catalogue).invoke(request)
        return project_adapter_result(result)


def project_adapter_result(result: CommandResult) -> AdapterProjection:
    """Project one canonical result without changing its semantic branch."""

    return AdapterProjection(
        result=result,
        is_error=not isinstance(result, Ok),
        exit_code=_exit_code(result),
    )


def project_invalid_request(entry, message: str) -> AdapterProjection:
    """Project a known command's semantic request failure structurally."""

    return project_adapter_result(
        Error(
            entry.command_id,
            entry.command_version,
            CommandError(
                ErrorCode.INVALID_REQUEST,
                message,
                RequestErrorDetails(None, message),
            ),
        )
    )


def parser_exit_code() -> int:
    return 2


def _exit_code(result: CommandResult) -> int:
    if isinstance(result, Ok):
        return 0
    if isinstance(result, Partial):
        return 1
    assert isinstance(result, Error)
    if result.error.code in {
        ErrorCode.INVALID_REQUEST,
        ErrorCode.NOT_FOUND,
        ErrorCode.CONFLICT,
    }:
        return 2
    if result.error.code in {
        ErrorCode.AUTHORITY_DENIED,
        ErrorCode.AUTHORISATION_REQUIRED,
        ErrorCode.CAPABILITY_UNAVAILABLE,
    }:
        return 3
    return 4


def _concise_text(result: CommandResult) -> str:
    if isinstance(result, Ok):
        return f"{result.command_id}: ok"
    if isinstance(result, Partial):
        return f"{result.command_id}: partial — {result.error.message}"
    return f"{result.command_id}: {result.error.code.value} — {result.error.message}"
