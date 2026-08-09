"""Shared dynamic adapter boundary for MCP, CLI and direct scripts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .application import CommandApplication
from .catalogue import ApplicationCatalogue
from .context import InvocationContext
from .projection import canonical_result_envelope, canonical_result_json
from .resolver import RequestResolutionError, RequestResolver, ResolutionErrorCode
from .results import CommandResult, Error, ErrorCode, Ok, Partial


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
    structured_content: dict[str, object]
    json_text: str
    concise_text: str
    is_error: bool
    exit_code: int

    def __post_init__(self) -> None:
        if not self.concise_text.strip() or not self.json_text:
            raise ValueError("adapter projection requires JSON and concise text")
        if self.is_error != (not isinstance(self.result, Ok)):
            raise ValueError("adapter error flag must follow the structural result branch")
        if self.exit_code not in range(5):
            raise ValueError("adapter exit code must use the canonical 0-4 categories")


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
        if not isinstance(payload, Mapping):
            raise AdapterRequestError(
                ResolutionErrorCode.INVALID_REQUEST,
                "command request payload must be an object",
            )
        try:
            request = self.resolver.resolve(command_id, payload)
        except RequestResolutionError as exc:
            raise AdapterRequestError(exc.code, str(exc)) from exc
        except (TypeError, ValueError) as exc:
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
        structured_content=canonical_result_envelope(result),
        json_text=canonical_result_json(result),
        concise_text=_concise_text(result),
        is_error=not isinstance(result, Ok),
        exit_code=_exit_code(result),
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
