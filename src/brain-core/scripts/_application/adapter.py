"""Shared dynamic adapter boundary for MCP, CLI and direct scripts."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from copy import deepcopy
import json
from typing import Mapping

from .application import (
    CommandApplication,
    authority_denied_result,
    consent_error_result,
    internal_error_result,
)
from .catalogue import ApplicationCatalogue
from .consent import ConsentError
from .delivery_failure import DeliveryFailure, NavigationDeliveryError
from .context import InvocationContext, report_failure_safely
from .projection import canonical_result_envelope, unavailable_delivery_envelope, disclosure_ended_envelope
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


@dataclass(frozen=True)
class AdapterProjection:
    result: CommandResult
    is_error: bool
    exit_code: int
    delivery_envelope: dict[str, object] | None = None
    result_session_credential: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.is_error != (not isinstance(self.result, Ok)):
            raise ValueError("adapter error flag must follow the structural result branch")
        if self.exit_code not in range(5):
            raise ValueError("adapter exit code must use the canonical 0-4 categories")

    @cached_property
    def _projected_envelope(self) -> dict[str, object]:
        if self.delivery_envelope is not None:
            return self.delivery_envelope
        from .result_navigation import build_view
        original = canonical_result_envelope(self.result)
        try:
            view = build_view(original)
            if view.details or len(view.pages) > 1:
                return unavailable_delivery_envelope(original, "Output requires a result-capable invocation context.")
            return view.page()
        except (ValueError, TypeError):
            return unavailable_delivery_envelope(original, "Output requires a result-capable invocation context.")

    @property
    def structured_content(self) -> dict[str, object]:
        return deepcopy(self._projected_envelope)

    @property
    def json_text(self) -> str:
        return json.dumps(self.structured_content, ensure_ascii=False, separators=(",", ":"))

    @property
    def concise_text(self) -> str:
        if self.delivery_envelope is not None and self.delivery_envelope.get("delivery", {}).get("reason") == "disclosure_ended":
            return f"{self.result.command_id}: {self.result.status} — source disclosure ended; business output withheld. Do not rerun a mutation."
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
        delivery = context.result_delivery
        ticket = None
        if delivery is not None and entry.retain_output and not command_id.startswith("result."):
            try:
                ticket = delivery.begin(context, command_id, request)
            except DeliveryFailure as exc:
                return project_delivery_failure(command_id, entry.command_version, exc)
            except ConsentError as exc:
                return project_adapter_result(consent_error_result(context, command_id, entry.command_version, exc))
            except Exception as exc:
                report_failure_safely(context, phase="result.admission", command_id=command_id, error=exc)
                return project_adapter_result(Error(command_id, entry.command_version,
                    CommandError(ErrorCode.CAPABILITY_UNAVAILABLE, "Private result storage is unavailable; no command was executed.")))
        result = CommandApplication(context, self.catalogue).invoke(request)
        if delivery is None:
            return project_adapter_result(result)
        projection = AdapterProjection(result, not isinstance(result, Ok), _exit_code(result))
        if isinstance(result, NavigationDeliveryError):
            failure = result.failure
            envelope = unavailable_delivery_envelope(canonical_result_envelope(result),
                failure.message, reason=failure.reason)
            envelope["delivery"]["result_id"] = result.result_id
            if failure.capacity_limits is not None:
                envelope["delivery"]["capacity_limits"] = failure.capacity_limits
        elif command_id == "result.read" and isinstance(result, Ok):
            envelope = deepcopy(result.result.envelope)
        elif command_id.startswith("result."):
            from .result_navigation import build_view
            envelope = build_view(canonical_result_envelope(result)).page()
        elif not entry.retain_output:
            envelope = project_adapter_result(result).structured_content
        else:
            try:
                envelope = delivery.finish(context, request, result, ticket)
            except DeliveryFailure as exc:
                if exc.reason == "disclosure_ended":
                    envelope = disclosure_ended_envelope(canonical_result_envelope(result))
                else:
                    envelope = unavailable_delivery_envelope(canonical_result_envelope(result),
                        exc.message + " Source outcome is unchanged. Do not rerun a mutation to recover output.",
                        reason=exc.reason)
                    if exc.capacity_limits is not None:
                        envelope["delivery"]["capacity_limits"] = exc.capacity_limits
                    if ticket is not None:
                        envelope["delivery"]["result_id"] = ticket["result_id"]
            except Exception as exc:
                report_failure_safely(context, phase="result.delivery", command_id=command_id, error=exc)
                envelope = unavailable_delivery_envelope(canonical_result_envelope(result),
                    "Command outcome is unchanged, but retained output is unavailable. Do not rerun a mutation to recover output.")
                if ticket is not None:
                    envelope["delivery"]["result_id"] = ticket["result_id"]
        return AdapterProjection(result, projection.is_error, projection.exit_code, envelope,
                                 delivery.credential)


def project_delivery_failure(command_id: str, command_version: int, failure: DeliveryFailure) -> AdapterProjection:
    """Project a known pre-execution delivery refusal consistently across transports."""
    message = failure.message + " No command was executed."
    result = Error(command_id, command_version, CommandError(ErrorCode.CAPABILITY_UNAVAILABLE, message))
    envelope = unavailable_delivery_envelope(canonical_result_envelope(result), message, reason=failure.reason)
    if failure.capacity_limits is not None:
        envelope["delivery"]["capacity_limits"] = failure.capacity_limits
    return AdapterProjection(result, True, _exit_code(result), envelope)


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
