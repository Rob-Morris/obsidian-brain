"""Shared direct-Python invocation boundary for selected-Brain commands."""

from __future__ import annotations

from .catalogue import ApplicationCatalogue, ApplicationEntry
from .context import InvocationContext
from .receipts import OutcomeReceipt, OutcomeReference, ReceiptState
from .requests import CommandRequest, command_identity
from .results import (
    AuthorityDeniedDetails,
    CapabilityUnavailableDetails,
    CommandArgument,
    CommandError,
    CommandNextAction,
    Error,
    ErrorCode,
    InstructionNextAction,
    InternalErrorDetails,
    Ok,
    OutcomeUnknownDetails,
    Partial,
    CommandResult,
)
from .types import Availability, EffectClass


class CommandApplication:
    """Invoke typed requests with explicit trusted context and one catalogue."""

    def __init__(
        self,
        context: InvocationContext,
        catalogue: ApplicationCatalogue,
    ) -> None:
        self._context = context
        self._catalogue = catalogue

    def invoke(self, request: CommandRequest) -> CommandResult:
        command_id, command_version, _result_type = command_identity(request)
        try:
            entry = self._catalogue.resolve(request)
        except Exception:
            return self._internal_error(command_id, command_version)

        try:
            preflight = self._preflight(entry)
        except Exception:
            preflight = self._internal_error(command_id, command_version)
        if preflight is not None:
            try:
                self._record(entry, preflight)
            except Exception:
                return self._internal_error(command_id, command_version)
            return preflight

        try:
            result = entry.executor(self._context, request)
            self._validate_result(entry, result)
        except Exception:
            result = self._execution_failure(entry)

        try:
            self._record(entry, result)
        except Exception:
            if entry.effect_class is EffectClass.NONE:
                return self._internal_error(command_id, command_version)
            return self._unknown_result(entry)
        return result

    def _preflight(self, entry: ApplicationEntry) -> Error | None:
        context = self._context
        if not context.authority.allows(
            command_id=entry.command_id,
            required=entry.authority,
            effect=entry.effect_class,
        ):
            return Error(
                entry.command_id,
                entry.command_version,
                CommandError(
                    ErrorCode.AUTHORITY_DENIED,
                    "The authenticated profile does not permit this command.",
                    AuthorityDeniedDetails(context.profile, entry.authority.value),
                ),
            )

        missing = []
        if not context.dependency_tier.supports(entry.dependency_tier):
            missing.append(f"tier:{entry.dependency_tier.name.lower()}")
        for provider_id in entry.required_providers:
            if context.providers.get(provider_id) is None:
                missing.append(f"provider:{provider_id}")
            elif context.capabilities.availability_of(provider_id) is not Availability.AVAILABLE:
                missing.append(f"capability:{provider_id}")
        if not missing:
            return None

        details = CapabilityUnavailableDetails(
            required_tier=entry.dependency_tier,
            current_tier=context.dependency_tier,
            locality=entry.locality,
            missing=tuple(missing),
            snapshot_freshness=context.capabilities.freshness,
            recoverable=True,
        )
        return Error(
            entry.command_id,
            entry.command_version,
            CommandError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                "The command is installed but its required capability is unavailable.",
                details,
                next_action=InstructionNextAction(
                    "Restore the named tier or provider, refresh capabilities, then invoke the command again."
                ),
            ),
        )

    def _validate_result(
        self,
        entry: ApplicationEntry,
        result: CommandResult,
    ) -> None:
        if not isinstance(result, (Ok, Partial, Error)):
            raise TypeError("executor did not return a command result variant")
        if (result.command_id, result.command_version) != (
            entry.command_id,
            entry.command_version,
        ):
            raise ValueError("executor result identity does not match its catalogue entry")
        if isinstance(result, Ok) and not isinstance(result.result, entry.result_type):
            raise TypeError("executor ok payload does not match the request result type")
        if entry.effect_class is EffectClass.NONE:
            if isinstance(result, Ok) and result.committed_effects:
                raise ValueError("read-only command cannot report committed effects")
            if isinstance(result, (Partial,)):
                raise ValueError("read-only command cannot return partial effects")
            if isinstance(result, Error) and result.effects == "unknown":
                raise ValueError("read-only command cannot have unknown effects")

    def _execution_failure(self, entry: ApplicationEntry) -> Error:
        if entry.effect_class is not EffectClass.NONE:
            return self._unknown_result(entry)
        return self._internal_error(entry.command_id, entry.command_version)

    def _unknown_result(self, entry: ApplicationEntry) -> Error:
        reference = OutcomeReference(self._context.invocation_id)
        return Error(
            entry.command_id,
            entry.command_version,
            CommandError(
                ErrorCode.COMMAND_OUTCOME_UNKNOWN,
                "The command may have produced effects; query the invocation outcome.",
                OutcomeUnknownDetails(reference),
                next_action=CommandNextAction(
                    "invocation.read",
                    (CommandArgument("invocation_id", reference.invocation_id),),
                ),
            ),
            effects="unknown",
            outcome_reference=reference,
        )

    def _record(self, entry: ApplicationEntry, result: CommandResult) -> None:
        if isinstance(result, Partial):
            state = ReceiptState.KNOWN_PARTIAL
            effects = result.committed_effects
        elif isinstance(result, Error) and result.effects == "unknown":
            state = ReceiptState.UNKNOWN
            effects = ()
        elif isinstance(result, Ok) and entry.effect_class is not EffectClass.NONE:
            state = ReceiptState.COMMITTED
            effects = result.committed_effects
        else:
            state = ReceiptState.NONE
            effects = ()
        receipt = OutcomeReceipt(
            reference=OutcomeReference(self._context.invocation_id),
            command_id=entry.command_id,
            command_version=entry.command_version,
            state=state,
            recorded_at=self._context.clock.now(),
            committed_effects=effects,
        )
        self._context.receipt_writer.write(receipt)

    def _internal_error(self, command_id: str, command_version: int) -> Error:
        return Error(
            command_id,
            command_version,
            CommandError(
                ErrorCode.INTERNAL_ERROR,
                "The command failed unexpectedly.",
                InternalErrorDetails(self._context.correlation_id),
            ),
        )
