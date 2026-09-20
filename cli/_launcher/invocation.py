"""Canonical invocation boundary for machine-global launcher owners."""

from __future__ import annotations

from launcher_catalogue import LauncherCatalogue

from .context import LauncherContext, report_failure_safely
from .contracts import (
    AuthorityDeniedDetails,
    CapabilityUnavailableDetails,
    CommandError,
    Error,
    ErrorCode,
    InstructionNextAction,
    InternalErrorDetails,
    Ok,
    OutcomeReceipt,
    OutcomeReference,
    OutcomeUnknownDetails,
    Partial,
    ReceiptState,
)
from .owners import LauncherOwners


class LauncherInvocation:
    """Invoke one typed launcher request without selected-Brain application code."""

    def __init__(
        self,
        context: LauncherContext,
        catalogue: LauncherCatalogue,
        owners: LauncherOwners,
    ) -> None:
        self._context = context
        self._catalogue = catalogue
        self._owners = owners

    def invoke(self, request: object):
        try:
            owner = self._owners.resolve(request)
        except Exception as exc:
            raise TypeError("request has no launcher owner") from exc
        entry = next(
            (item for item in self._catalogue.entries if item.command_id == owner.command_id),
            None,
        )
        if entry is None or (
            entry.owner_ref,
            entry.command_version,
        ) != (
            owner.owner_ref,
            owner.command_version,
        ):
            return self._internal_error(owner.command_id, owner.command_version)
        try:
            preflight = self._preflight(entry)
        except Exception as exc:
            self._report(entry.command_id, "preflight", exc)
            preflight = self._internal_error(owner.command_id, owner.command_version)
        if preflight is not None:
            try:
                self._record(entry, preflight)
            except Exception as exc:
                self._report(entry.command_id, "receipt.preflight", exc)
                return self._internal_error(owner.command_id, owner.command_version)
            return preflight
        try:
            if entry.approval_transition is not None:
                from .approval_lifecycle import invoke
                result = invoke(self._context, request, entry.approval_transition, owner.executor)
            else:
                result = owner.executor(self._context, request)
            self._validate_result(entry, owner, result)
        except Exception as exc:
            self._report(entry.command_id, "execute", exc)
            result = self._execution_failure(entry)
        try:
            self._record(entry, result)
        except Exception as exc:
            self._report(entry.command_id, "receipt.finalise", exc)
            return (
                self._internal_error(entry.command_id, entry.command_version)
                if entry.effect_class == "none"
                else self._unknown_result(entry)
            )
        return result

    def _report(self, command_id: str, phase: str, error: BaseException) -> None:
        report_failure_safely(
            self._context, phase=phase, command_id=command_id, error=error
        )

    def _preflight(self, entry):
        context = self._context
        denied = authority_denied_result(context, entry)
        if denied is not None:
            return denied
        missing = []
        for provider_id in entry.required_providers:
            provider = context.providers.get(provider_id)
            if provider is None:
                missing.append(f"provider:{provider_id}")
            elif not provider.available:
                missing.append(f"capability:{provider_id}")
        if not missing:
            return None
        return Error(
            entry.command_id,
            entry.command_version,
            CommandError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                "The launcher command is installed but its provider is unavailable.",
                CapabilityUnavailableDetails("machine_local", tuple(missing), True),
                InstructionNextAction(
                    "Restore the named local provider, then invoke the command again."
                ),
            ),
        )

    @staticmethod
    def _validate_result(entry, owner, result) -> None:
        if not isinstance(result, (Ok, Partial, Error)):
            raise TypeError("launcher executor returned a non-result")
        if (result.command_id, result.command_version) != (
            entry.command_id,
            entry.command_version,
        ):
            raise ValueError("launcher result identity does not match its catalogue entry")
        if isinstance(result, Ok) and not isinstance(result.result, owner.result_type):
            raise TypeError("launcher result payload does not match its owner")
        if entry.effect_class == "none":
            if isinstance(result, Ok) and result.committed_effects:
                raise ValueError("read-only launcher command cannot report effects")
            if isinstance(result, Partial):
                raise ValueError("read-only launcher command cannot be partial")
            if isinstance(result, Error) and result.effects == "unknown":
                raise ValueError("read-only launcher command cannot have unknown effects")

    def _execution_failure(self, entry):
        if entry.effect_class == "none":
            return self._internal_error(entry.command_id, entry.command_version)
        return self._unknown_result(entry)

    def _unknown_result(self, entry):
        reference = OutcomeReference(self._context.invocation_id)
        return Error(
            entry.command_id,
            entry.command_version,
            CommandError(
                ErrorCode.COMMAND_OUTCOME_UNKNOWN,
                "The launcher command may have produced machine-local effects.",
                OutcomeUnknownDetails(reference),
                InstructionNextAction(
                    "Inspect the recorded launcher outcome before attempting the mutation again."
                ),
            ),
            effects="unknown",
            outcome_reference=reference,
        )

    def _record(self, entry, result) -> None:
        error = getattr(result, "error", None)
        details = getattr(error, "details", None)
        recovery_paths = tuple(getattr(details, "recovery_paths", ()))
        if isinstance(result, Partial):
            state = ReceiptState.KNOWN_PARTIAL
            effects = result.committed_effects
        elif isinstance(result, Error) and result.effects == "unknown":
            state = ReceiptState.UNKNOWN
            effects = ()
        elif isinstance(result, Ok) and entry.effect_class != "none":
            state = ReceiptState.COMMITTED
            effects = result.committed_effects
        else:
            state = ReceiptState.NONE
            effects = ()
        self._context.receipt_writer.write(
            OutcomeReceipt(
                OutcomeReference(self._context.invocation_id),
                entry.command_id,
                entry.command_version,
                state,
                self._context.clock.now(),
                effects,
                recovery_paths,
            )
        )

    def _internal_error(self, command_id: str, command_version: int):
        return internal_error_result(
            self._context,
            command_id,
            command_version,
        )


def authority_denied_result(context: LauncherContext, entry) -> Error | None:
    """Return the launcher-owned denial before any caller payload is decoded."""

    if context.authority.allows(
        command_id=entry.command_id,
        required=entry.authority,
        effect=entry.effect_class,
    ):
        return None
    return Error(
        entry.command_id,
        entry.command_version,
        CommandError(
            ErrorCode.AUTHORITY_DENIED,
            "The authenticated launcher profile does not permit this command.",
            AuthorityDeniedDetails(context.profile, entry.authority),
        ),
    )


def internal_error_result(
    context: LauncherContext,
    command_id: str,
    command_version: int,
) -> Error:
    """Map unexpected launcher adapter failures without exposing details."""

    return Error(
        command_id,
        command_version,
        CommandError(
            ErrorCode.INTERNAL_ERROR,
            "The launcher command failed unexpectedly.",
            InternalErrorDetails(context.correlation_id),
        ),
    )
