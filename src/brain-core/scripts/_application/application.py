"""One enforced application boundary for permissions, consent and owned outcomes."""
from __future__ import annotations

from dataclasses import replace

from .access_contracts import CommandAuthorisationState
from .authorisation import invocation_outcome
from .catalogue import ApplicationCatalogue, ApplicationEntry
from .consent import ConsentError
from .context import InvocationContext, report_failure_safely
from .identity import command_identity
from .preparation import bind_operation, content_digest
from .receipts import AdmissionIntent, OutcomeReference, ReceiptIntentConflict
from .results import (
    AuthorityDeniedDetails, CapabilityUnavailableDetails, CommandArgument, CommandError,
    CommandNextAction, CommandResult, Error, ErrorCode, InstructionNextAction,
    InternalErrorDetails, Ok, OutcomeUnknownDetails, Partial, RequestErrorDetails,
)
from .types import Availability, EffectClass, InitialAuthorisationClass
from _lifecycle.derived_cache_state import RouterCacheUnavailable
from .results import router_cache_error


class CommandApplication:
    """Invoke typed requests through the installed owners and mandatory authorisation."""

    def __init__(self, context: InvocationContext, catalogue: ApplicationCatalogue | None = None):
        """Bind explicit trusted composition to one installed command catalogue."""
        if catalogue is None:
            from .registry import current_application_catalogue
            catalogue = current_application_catalogue()
        self._context, self._catalogue = context, catalogue

    def invoke(self, request: object) -> CommandResult:
        """Check permission before resources, admit under owner guards, then record outcome."""
        command_id, version, _result_type = command_identity(request)
        context = self._context
        try:
            entry = self._catalogue.resolve(request)
            if context.authorisation is None or context.access is None:
                raise RuntimeError("application invocation requires explicit authorisation composition")
            preflight = self._preflight(entry)
            if preflight is not None:
                return preflight
        except ConsentError as exc:
            return consent_error_result(context, command_id, version, exc)
        except Exception as exc:
            self._report("preflight", command_id, exc)
            return internal_error_result(context, command_id, version)

        control = entry.initial_class is InitialAuthorisationClass.CONTROL
        admission = None
        control_intent = None
        try:
            if control:
                from .access.prepare import AccessPrepareRequest, InspectOperation
                if context.operation_id is not None:
                    raise ConsentError("invalid_request", "Session controls cannot select a prepared operation.")
                pure_inspection = isinstance(request, AccessPrepareRequest) and isinstance(request.preparation, InspectOperation)
                if entry.effect_class is not EffectClass.NONE and not pure_inspection:
                    control_intent = self._begin_control(entry, request)
            else:
                admission = context.authorisation.invocation(entry, context)
                context = replace(context, admission=admission)
                context = replace(context, access=context.authorisation.bind(context))
                admission.admit_query(request)
            result = entry.executor(context, request)
            self._validate_result(entry, result)
        except RouterCacheUnavailable as exc:
            if control_intent is not None or (admission is not None and admission.entered):
                result = self._unknown_result(entry)
            else:
                result = Error(command_id, version, router_cache_error(exc.state))
        except ConsentError as exc:
            # The kernel uses this type only for known refusals, including a
            # positively cancelled reservation. Lost owner replies use I/O errors.
            result = consent_error_result(context, command_id, version, exc)
        except Exception as exc:
            self._report("execute", command_id, exc)
            uncertain = control_intent is not None or (
                admission is not None and (admission.entered or admission.intent_recorded))
            result = self._unknown_result(entry) if uncertain else internal_error_result(context, command_id, version)

        try:
            if admission is not None:
                admission.finalise(result)
            elif control_intent is not None:
                context.authorisation.receipts.finalise(invocation_outcome(entry, context, result))
        except Exception as exc:
            self._report("receipt.finalise", command_id, exc)
            if control_intent is not None or (admission is not None and admission.intent_recorded):
                return self._unknown_result(entry)
            return internal_error_result(context, command_id, version)
        return result

    def _preflight(self, entry):
        context = self._context
        service = context.authorisation.service
        service.check_permission(entry.command_id)
        if entry.initial_class is not InitialAuthorisationClass.CONTROL:
            denied = authority_denied_result(context, entry)
            if denied is not None:
                return denied
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
        return Error(entry.command_id, entry.command_version, CommandError(
            ErrorCode.CAPABILITY_UNAVAILABLE,
            "The command is installed but its required capability is unavailable.",
            CapabilityUnavailableDetails(entry.dependency_tier, context.dependency_tier,
                entry.locality, tuple(missing), context.capabilities.freshness, True),
            next_action=InstructionNextAction("Restore the named tier or provider, refresh capabilities, then invoke again.")))

    def _begin_control(self, entry, request):
        context = self._context
        session = context.authorisation
        digest = content_digest(bind_operation(request).request_json)
        reference = OutcomeReference(context.invocation_id)
        previous = session.receipts.read(reference)
        if previous.intent is not None:
            # A repeated trusted invocation is recovery, never a new control
            # effect. In particular an old clear-grants call must not revoke
            # grants created after its original completion.
            raise ConsentError("operation_entered", "This control invocation already has an admission intent; inspect its owned outcome.")
        intent = AdmissionIntent(reference, entry.command_id, entry.command_version,
            context.clock.now(), "initial", session.service.generation(), session.source,
            operation_digest=digest, request_id=context.invocation_id)
        try:
            created = session.receipts.begin(intent)
        except ReceiptIntentConflict as exc:
            raise ConsentError("operation_entered", "This control invocation already has an admission intent; inspect its owned outcome.") from exc
        if created is not True:
            raise ConsentError("operation_entered", "This control invocation already has an admission intent; inspect its owned outcome.")
        session.service.check_permission(entry.command_id)
        return intent

    def _validate_result(self, entry, result):
        if not isinstance(result, (Ok, Partial, Error)):
            raise TypeError("executor did not return a command result variant")
        if (result.command_id, result.command_version) != (entry.command_id, entry.command_version):
            raise ValueError("executor result identity does not match its catalogue entry")
        if isinstance(result, Ok) and not isinstance(result.result, entry.result_type):
            raise TypeError("executor ok payload does not match the request result type")
        if entry.effect_class is EffectClass.NONE:
            if isinstance(result, Ok) and result.committed_effects:
                raise ValueError("read-only command cannot report committed effects")
            if isinstance(result, Partial) or (isinstance(result, Error) and result.effects == "unknown"):
                raise ValueError("read-only command cannot report possible domain effects")

    def _unknown_result(self, entry):
        reference = OutcomeReference(self._context.invocation_id)
        return Error(entry.command_id, entry.command_version, CommandError(
            ErrorCode.COMMAND_OUTCOME_UNKNOWN,
            "Execution may have entered; inspect the owned invocation outcome before any further action.",
            OutcomeUnknownDetails(reference), next_action=CommandNextAction("invocation.read", (
                CommandArgument("invocation_id", reference.invocation_id),))),
            effects="none" if entry.effect_class is EffectClass.NONE else "unknown",
            outcome_reference=reference)

    def _report(self, phase, command_id, error):
        report_failure_safely(self._context, phase=phase, command_id=command_id, error=error)


def authority_denied_result(context: InvocationContext, entry: ApplicationEntry, **_unused):
    """Name the actual permission, policy, context or authorisation boundary."""
    access = context.access.command_access(entry.command_id)
    if access.state is CommandAuthorisationState.AUTHORISED and (
            context.operation_id is None or entry.initial_class is InitialAuthorisationClass.CONTROL):
        return None
    if context.operation_id is not None and access.boundary != "permissions":
        service = context.authorisation.service
        if service.state(entry.command_id, operation_id=context.operation_id) == "authorised":
            return None
        descriptor = service.inspect(context.operation_id)
        invocation_id = descriptor.get("invocation_id")
        if invocation_id:
            return Error(entry.command_id, entry.command_version,
                CommandError(ErrorCode.CONFLICT,
                    "This prepared operation has already entered; inspect its owned outcome.",
                    RequestErrorDetails("brain_operation", "operation_entered"),
                    next_action=CommandNextAction("invocation.read", (
                        CommandArgument("invocation_id", invocation_id),))))
    profile = context.authorisation.service.policy.ceiling_profile
    message = {
        "permissions": "Credential permissions deny this command; an authorised administrator must change permissions through CLI.",
        "policy": "Brain policy does not permit this authorisation request; inspect vault.read-config.",
        "context": "Exceptional authorisation requires an available MCP instance or an explicit brain session run job.",
        "authorisation": "The command is permitted but this operation requires explicit authorisation.",
    }[access.boundary]
    code = ErrorCode.AUTHORISATION_REQUIRED if access.requestable else ErrorCode.AUTHORITY_DENIED
    return Error(entry.command_id, entry.command_version, CommandError(code, message,
        AuthorityDeniedDetails(profile, entry.authority.value, access.boundary, access.requestable),
        next_action=access.next_action or (CommandNextAction("access.status", (
            CommandArgument("target_command_id", entry.command_id),)) if access.requestable else None)))


def consent_error_result(context, command_id, version, error: ConsentError):
    permission = error.reason in {"permission", "authorisation_required", "denied", "migration_required",
                                  "context_required", "context_unavailable", "context_changed", "foreign_context"}
    if permission:
        boundary = ("permissions" if error.reason == "permission" else "authorisation"
                    if error.reason == "authorisation_required" else "policy"
                    if error.reason in {"denied", "migration_required"} else "context")
        requestable = boundary == "authorisation" and context.authorisation.context_available
        details = AuthorityDeniedDetails(context.authorisation.service.policy.ceiling_profile,
            command_id, boundary, requestable, error.reason)
        code = ErrorCode.AUTHORISATION_REQUIRED if requestable else ErrorCode.AUTHORITY_DENIED
    else:
        code = ErrorCode.CONFLICT if error.reason in {
            "conflict", "stale_cursor", "stale_operation", "request_conflict", "operation_entered",
            "invalidated", "revoked", "contract_changed", "review_mismatch"} else ErrorCode.INVALID_REQUEST
        details = RequestErrorDetails(None, error.reason)
    return Error(command_id, version, CommandError(code, str(error), details,
        next_action=(CommandNextAction("invocation.read", (CommandArgument("invocation_id", context.invocation_id),))
                     if error.reason == "operation_entered" else
                     CommandNextAction("vault.read-config") if error.reason in {"migration_required", "denied"} else
                     CommandNextAction("access.status") if context.access is not None else None)))


def internal_error_result(context: InvocationContext, command_id: str, command_version: int) -> Error:
    """Return correlation-safe diagnostics for unexpected application/adapter failure."""
    return Error(command_id, command_version, CommandError(ErrorCode.INTERNAL_ERROR,
        "The command failed unexpectedly.", InternalErrorDetails(context.correlation_id)))
