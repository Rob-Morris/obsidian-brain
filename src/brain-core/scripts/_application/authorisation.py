"""Coordinate immutable preparation and guarded entry over application ports."""

from __future__ import annotations

from dataclasses import replace
import json
from typing import Callable, Protocol
import uuid

from .catalogue import ApplicationCatalogue, ApplicationEntry
from .consent import AdmissionProof, ConsentError, ConsentService
from .context import InvocationContext, report_failure_safely
from .preparation import OperationBinding, bind_operation, canonical_json, content_digest
from .receipts import (
    AdmissionIntent, ExecutionState, InvocationOutcome, OutcomeReceipt,
    OutcomeReference, OwnedReceiptPort, ReceiptIntentConflict, ReceiptState,
)
from .results import Error, ErrorCode, Ok, Partial
from .types import EffectClass


def _execution_binding(binding: OperationBinding, context: InvocationContext) -> OperationBinding:
    options = {"dry_run": context.dry_run,
               "workspace_dir": str(context.workspace_dir.resolve()) if context.workspace_dir else None}
    return replace(binding,
                   request_json=canonical_json({**json.loads(binding.request_json), "execution": options}),
                   review_json=canonical_json({**binding.review, "execution": options}))


def _check_context(service: ConsentService, context: InvocationContext) -> None:
    selected = context.selected_brain
    if (selected.brain_id != service.identity.brain_id
            or str(selected.vault_root.resolve()) != service.identity.vault_root):
        raise ValueError("authorisation owner does not belong to the selected Brain")


class PreparedContent(Protocol):
    """Private immutable bytes belonging to one preparation attempt."""

    def retain_content(self, source_key: str, content: bytes) -> dict: ...
    def read_pinned(self, source_key: str) -> bytes | None: ...
    def discard(self) -> None: ...


class _PreparationCapture:
    requires_binding = True
    frozen_inputs = None

    def __init__(self, content: PreparedContent):
        self.content = content

    def retain_content(self, source_key: str, content: bytes) -> dict:
        return self.content.retain_content(source_key, content)

    def read_pinned(self, source_key: str) -> bytes | None:
        return self.content.read_pinned(source_key)

    def admit(self, _binding) -> None:
        raise RuntimeError("a preparation planner attempted operation entry")


class PreparationCoordinator:
    """Resolve through installed owners; a descriptor never substitutes for authority."""

    def __init__(self, service: ConsentService, catalogue: ApplicationCatalogue,
                 content_for: Callable[[str], PreparedContent]):
        self.service = service
        self.catalogue = catalogue
        self.content_for = content_for

    def prepare(self, context: InvocationContext, request, *, request_id: str,
                validate_view: Callable[[dict], None] | None = None) -> dict:
        _check_context(self.service, context)
        entry = self.catalogue.resolve(request)
        self.service.check_permission(entry.command_id, request=True)
        if entry.preparation is None:
            raise ConsentError("unsupported_preparation", "This command has no installed preparation owner.")
        request_digest = content_digest(_execution_binding(bind_operation(request), context).request_json)
        prior = self.service.preparation_retry(request_id, request_digest)
        if prior is not None:
            return prior
        # Concurrent identical requests use independent temporary namespaces;
        # disposing a losing attempt cannot remove the winner's immutable inputs.
        namespace = "preparation-" + str(uuid.uuid4())
        content = self.content_for(namespace)
        capture = _PreparationCapture(content)
        try:
            binding = entry.preparation.prepare(replace(context, admission=capture), request)
            if (binding.command_id, binding.command_version) != (entry.command_id, entry.command_version):
                raise RuntimeError("preparation owner returned a different command contract")
            binding = _execution_binding(binding, context)
        except Exception:
            self._discard_after_failure(context, entry.command_id, content)
            raise
        try:
            view = self.service.prepare(binding, request_id=request_id, pin_namespace=namespace,
                                        validate_view=validate_view)
        except (ConsentError, ValueError):
            # These are known rejections. Uncertain owner-transport exceptions
            # retain the copy: a descriptor may already have committed remotely.
            self._discard_after_failure(context, entry.command_id, content)
            raise
        if view["state"] == "discarded":
            content.discard()
            return view
        try:
            retained = self.service.inspect(view["operation_id"])
        except ConsentError as exc:
            if exc.reason != "operation_missing":
                raise
            content.discard()
            return {"operation_id": view["operation_id"], "digest": view["digest"], "state": "discarded"}
        if retained["pin_namespace"] != namespace:
            content.discard()
        return view

    @staticmethod
    def _discard_after_failure(context, command_id, content):
        try:
            content.discard()
        except Exception as exc:
            report_failure_safely(context, phase="preparation-cleanup", command_id=command_id, error=exc)

    def discard(self, operation_ids: tuple[str, ...]) -> bool:
        """Close admission, release private inputs, then remove terminal descriptors."""
        changed = False
        for descriptor in self.service.begin_discard(operation_ids):
            operation_id = descriptor["operation_id"]
            if descriptor.get("pin_namespace"):
                self.content_for(descriptor["pin_namespace"]).discard()
            changed = self.service.reduce(operation_ids=(operation_id,)) or changed
        return changed


class InvocationAuthorisation:
    """One command's admission, durable attribution and final consent consumption."""

    def __init__(self, service: ConsentService, entry: ApplicationEntry,
                 context: InvocationContext, receipts: OwnedReceiptPort, *,
                 operation_id: str | None = None,
                 content_for: Callable[[str], PreparedContent], source: str | None = None):
        _check_context(service, context)
        self.service, self.entry, self.context = service, entry, context
        self.receipts = receipts
        self.operation_id = operation_id
        self.source = source or ("host-request" if service.identity.kind == "mcp-instance" else "cli-request")
        self._proof: AdmissionProof | None = None
        self._intent: AdmissionIntent | None = None
        self._finalised = False
        self._content = None
        self.frozen_inputs = None
        self.requires_binding = operation_id is not None
        self.service.check_permission(entry.command_id)
        if operation_id is not None:
            descriptor = service.inspect(operation_id)
            binding = OperationBinding.from_wire(descriptor["binding"])
            if (binding.command_id, binding.command_version) != (entry.command_id, entry.command_version):
                raise ConsentError("stale_operation", "Prepared selector belongs to a different command contract.")
            if not descriptor["valid"]:
                raise ConsentError("stale_operation", "Prepared selector has been invalidated; prepare again.")
            self.frozen_inputs = binding.frozen_inputs
            if descriptor.get("pin_namespace"):
                self._content = content_for(descriptor["pin_namespace"])

    @property
    def entered(self) -> bool:
        return self._proof is not None

    @property
    def intent_recorded(self) -> bool:
        """A missing local proof after intent can still mean the remote owner entered."""
        return self._intent is not None

    def retain_content(self, _source_key: str, _content: bytes) -> dict:
        raise ConsentError("stale_operation", "Execution cannot extend immutable prepared inputs; prepare again.")

    def read_pinned(self, source_key: str) -> bytes | None:
        return self._content.read_pinned(source_key) if self._content is not None else None

    def admit(self, binding: OperationBinding | None) -> None:
        if self._proof is not None or self._intent is not None or self._finalised:
            raise RuntimeError("an invocation owner attempted admission more than once")

        if binding is not None:
            binding = _execution_binding(binding, self.context)

        def record_intent(proof: AdmissionProof) -> None:
            intent = AdmissionIntent(
                OutcomeReference(proof.invocation_id), proof.command_id, proof.command_version,
                self.context.clock.now(), proof.basis, proof.generation,
                self.source,
                grant_id=proof.grant_id, operation_id=proof.operation_id,
                operation_digest=binding.digest if binding is not None else None,
                request_id=proof.request_id,
            )
            try:
                created = self.receipts.begin(intent)
            except ReceiptIntentConflict as exc:
                raise ConsentError("operation_entered", "This invocation already has an admission intent; inspect its owned outcome.") from exc
            if created is not True:
                raise ConsentError("operation_entered", "This invocation already has an admission intent; inspect its owned outcome.")
            self._intent = intent

        self._proof = self.service.admit(
            self.entry.command_id, self.entry.command_version, self.context.invocation_id,
            operation_id=self.operation_id, binding=binding, before_enter=record_intent,
        )

    def admit_query(self, request) -> None:
        """Enter a live query before dispatch; exact reads admit their opened result."""
        preparation = self.entry.preparation
        if preparation is not None and not preparation.owner_guarded:
            binding = (preparation.prepare(self.context, request, frozen_inputs=self.frozen_inputs)
                       if self.requires_binding else None)
            self.admit(binding)

    def finalise(self, result: Ok | Partial | Error | None) -> None:
        """Persist completion and spend entered consent, including observations."""
        if self._finalised:
            raise RuntimeError("invocation finalisation was attempted more than once")
        if not self.entered and isinstance(result, (Ok, Partial)):
            raise RuntimeError("successful owner bypassed operation admission")
        if self._intent is None:
            self._finalised = True
            return
        try:
            self.receipts.finalise(self._outcome(result))
        finally:
            self._finalised = True
            if self._proof is not None:
                try:
                    self.service.finish(self._proof)
                except Exception as exc:
                    # Entry already prevents replay; a closed owner cannot revive it.
                    report_failure_safely(self.context, phase="consent-finalisation",
                                          command_id=self.entry.command_id, error=exc)

    def _outcome(self, result) -> InvocationOutcome:
        return invocation_outcome(self.entry, self.context, result)


def invocation_outcome(entry, context, result) -> InvocationOutcome:
    """Describe execution and actual effects independently for ordinary and control calls."""
    effects = ()
    if isinstance(result, Ok):
        execution = ExecutionState.SUCCEEDED
        effects = result.committed_effects
        state = ReceiptState.COMMITTED if effects else ReceiptState.NONE
    elif isinstance(result, Partial):
        execution, state, effects = ExecutionState.PARTIAL, ReceiptState.KNOWN_PARTIAL, result.committed_effects
    elif result is None or (isinstance(result, Error) and result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN):
        execution = ExecutionState.UNKNOWN
        state = ReceiptState.NONE if entry.effect_class is EffectClass.NONE else ReceiptState.UNKNOWN
    elif isinstance(result, Error) and result.effects == "unknown":
        execution, state = ExecutionState.UNKNOWN, ReceiptState.UNKNOWN
    else:
        execution, state = ExecutionState.FAILED, ReceiptState.NONE
    receipt = OutcomeReceipt(OutcomeReference(context.invocation_id),
                             entry.command_id, entry.command_version,
                             state, context.clock.now(), effects)
    return InvocationOutcome(receipt, execution)
