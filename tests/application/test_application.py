"""Behavioural proofs for the mandatory selected-Brain admission boundary."""
from dataclasses import dataclass, replace
from typing import ClassVar

import pytest

from command_application import context_for, _Receipts
from _application.application import CommandApplication
from _application.catalogue import ApplicationCatalogue, ApplicationEntry, ALL_APPLICATION_PROJECTIONS
from _application.context import Capability, report_failure_safely
from _application.preparation import LIVE_QUERY, OperationPreparation, live_query, admit_owner
from _application.receipts import ExecutionState, ReceiptState
from _application.results import ErrorCode, Ok
from _application.types import Authority, Availability, DependencyTier, EffectClass, InitialAuthorisationClass, Locality, RetryClass


@dataclass(frozen=True)
class ProbePayload:
    value: str = "observed"


@dataclass(frozen=True)
class ProbeRequest:
    COMMAND_ID: ClassVar[str] = "test.probe"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = ProbePayload
    query: str = "value"


def _ok(*_args):
    return Ok("test.probe", 1, ProbePayload())


def _entry(executor=_ok, **changes):
    return replace(ApplicationEntry(ProbeRequest, executor, DependencyTier.PORTABLE,
        Locality.SELECTED_BRAIN_LOCAL, (), (), Authority.READER, EffectClass.NONE,
        InitialAuthorisationClass.OBSERVATION, RetryClass.SAFE, ALL_APPLICATION_PROJECTIONS,
        preparation=LIVE_QUERY), **changes)


def _setup(root, entry=None, *, receipts=None, **kwargs):
    entry = entry or _entry()
    catalogue = ApplicationCatalogue((entry,))
    context = context_for(root, catalogue=catalogue, receipts=receipts, invocation_id="inv-1", **kwargs)
    return CommandApplication(context, catalogue), context


def _effect_entry(executor=_ok):
    def execute(context, request):
        admit_owner(context, request, live_query)
        return executor(context, request)
    return _entry(execute, effect_class=EffectClass.SELECTED_BRAIN_MUTATION,
        initial_class=InitialAuthorisationClass.CONTENT, retry_class=RetryClass.RECEIPT_REQUIRED,
        preparation=OperationPreparation(live_query))


class _Diagnostics:
    def __init__(self):
        self.failures = []

    def report_failure(self, **failure):
        self.failures.append(failure)


class _FailingDiagnostics:
    def report_failure(self, **_failure):
        raise OSError("private diagnostic sink detail")


class _FaultReceipts(_Receipts):
    def __init__(self, *, begin=False, finalise=False, after_begin=None):
        super().__init__()
        self.fail_begin, self.fail_finalise, self.after_begin = begin, finalise, after_begin

    def begin(self, intent):
        if self.fail_begin:
            raise OSError("private receipt failure")
        created = super().begin(intent)
        if self.after_begin:
            self.after_begin()
        return created

    def finalise(self, outcome):
        if self.fail_finalise:
            raise OSError("private receipt failure")
        super().finalise(outcome)


@pytest.mark.parametrize("mutating", [False, True])
def test_durable_intent_depends_on_possible_effects_not_final_effects(tmp_path, mutating):
    receipts = _Receipts()
    def execute(context, request):
        if mutating:
            assert receipts.intents[context.invocation_id].command_id == request.COMMAND_ID
        else:
            assert not receipts.intents
        assert not receipts.outcomes
        return _ok()
    app, _ = _setup(tmp_path, (_effect_entry if mutating else _entry)(execute), receipts=receipts)
    assert app.invoke(ProbeRequest()).status == "ok"
    if mutating:
        outcome = receipts.outcomes["inv-1"]
        assert outcome.execution is ExecutionState.SUCCEEDED
        assert outcome.receipt.state is ReceiptState.NONE
    else:
        assert not receipts.outcomes


@pytest.mark.parametrize(("allowed", "initial", "code"), [
    ((), (), ErrorCode.AUTHORITY_DENIED),
    (("test.probe",), (), ErrorCode.AUTHORISATION_REQUIRED),
])
def test_denial_precedes_executor_and_intent(tmp_path, allowed, initial, code):
    def execute(*_args):
        pytest.fail("denied operation entered")
    app, context = _setup(tmp_path, _entry(execute), allowed_commands=allowed, initial_commands=initial)
    result = app.invoke(ProbeRequest())
    assert result.error.code is code
    assert result.effects == "none"
    assert not context.authorisation.receipts.intents


def test_missing_capability_precedes_intent_and_executor(tmp_path):
    entry = _entry(lambda *_: pytest.fail("unavailable operation entered"),
        dependency_tier=DependencyTier.MANAGED, required_providers=("document_renderer",))
    app, context = _setup(tmp_path, entry)
    result = app.invoke(ProbeRequest())
    assert result.error.code is ErrorCode.CAPABILITY_UNAVAILABLE
    assert result.error.details.missing == ("tier:managed", "provider:document_renderer")
    assert not context.authorisation.receipts.intents


def test_bound_unavailable_provider_remains_unavailable(tmp_path):
    class Provider:
        provider_id = "document_renderer"
    app, _ = _setup(tmp_path, _entry(required_providers=("document_renderer",)),
        providers=(Provider(),), capabilities=(Capability("document_renderer", Availability.UNAVAILABLE),))
    assert app.invoke(ProbeRequest()).error.details.missing == ("capability:document_renderer",)


def test_missing_explicit_composition_fails_closed(tmp_path):
    _, context = _setup(tmp_path)
    app = CommandApplication(replace(context, authorisation=None, access=None), context.authorisation.catalogue)
    assert app.invoke(ProbeRequest()).error.code is ErrorCode.INTERNAL_ERROR
    assert not context.authorisation.receipts.intents


def test_owner_guarded_success_cannot_bypass_admission(tmp_path):
    app, context = _setup(tmp_path, _entry(preparation=OperationPreparation(live_query)))
    assert app.invoke(ProbeRequest()).error.code is ErrorCode.INTERNAL_ERROR
    assert not context.authorisation.receipts.intents


@pytest.mark.parametrize("mutating", [False, True])
def test_entered_crash_requires_reconciliation_only_for_possible_effects(tmp_path, mutating):
    def execute(context, request):
        if mutating:
            admit_owner(context, request, live_query)
        raise OSError("private executor failure")
    entry = _entry(execute, **({"effect_class": EffectClass.SELECTED_BRAIN_MUTATION,
        "initial_class": InitialAuthorisationClass.CONTENT,
        "retry_class": RetryClass.RECEIPT_REQUIRED,
        "preparation": OperationPreparation(live_query)} if mutating else {}))
    app, context = _setup(tmp_path, entry)
    result = app.invoke(ProbeRequest())
    assert result.effects == ("unknown" if mutating else "none")
    assert not result.retryable
    if mutating:
        assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
        assert result.error.next_action.command_id == "invocation.read"
        assert result.outcome_reference.invocation_id == "inv-1"
        assert context.authorisation.receipts.outcomes["inv-1"].execution is ExecutionState.UNKNOWN
    else:
        assert result.error.code is ErrorCode.INTERNAL_ERROR
        assert result.error.details.correlation_id == context.correlation_id
        assert result.error.next_action is None
        assert result.outcome_reference is None
        assert not context.authorisation.receipts.intents
        assert not context.authorisation.receipts.outcomes


def test_spent_prepared_read_requires_new_preparation_not_missing_receipt(tmp_path):
    from _application.authorisation import PreparationCoordinator
    from _application.consent import ConsentScope

    entry = _entry()
    app, context = _setup(tmp_path, entry, initial_commands=())
    service = context.authorisation.service
    descriptor = PreparationCoordinator(
        service, context.authorisation.catalogue, context.authorisation.content_for,
    ).prepare(context, ProbeRequest(), request_id="prepare-read")
    service.request(
        request_id="allow-read", scope=ConsentScope.OPERATION,
        operation_id=descriptor["operation_id"], digest=descriptor["digest"],
        review=descriptor["review"],
    )
    selected = replace(context, operation_id=descriptor["operation_id"])
    selected = replace(selected, access=selected.authorisation.bind(selected))
    first = CommandApplication(selected, selected.authorisation.catalogue).invoke(ProbeRequest())
    assert first.status == "ok", first

    replay = replace(selected, invocation_id="inv-read-replay")
    replay = replace(replay, access=replay.authorisation.bind(replay))
    result = CommandApplication(replay, replay.authorisation.catalogue).invoke(ProbeRequest())

    assert result.error.code is ErrorCode.CONFLICT
    assert result.error.message == (
        "This prepared observation has already been consumed; prepare and authorise a new operation."
    )
    assert result.error.details.reason == "operation_consumed"
    assert result.error.next_action is None
    assert not replay.authorisation.receipts.intents
    assert not replay.authorisation.receipts.outcomes


def test_failed_intent_prevents_executor_entry(tmp_path):
    receipts = _FaultReceipts(begin=True)
    app, _ = _setup(tmp_path, _effect_entry(lambda *_: pytest.fail("entry without durable intent")), receipts=receipts)
    result = app.invoke(ProbeRequest())
    assert result.error.code is ErrorCode.INTERNAL_ERROR
    assert not receipts.intents and not receipts.outcomes


@pytest.mark.parametrize("basis", ["initial", "blanket"])
@pytest.mark.parametrize("elapsed_seconds", [0, 1])
def test_duplicate_invocation_cannot_reenter_with_durable_intent(tmp_path, basis, elapsed_seconds):
    from datetime import timedelta
    from _application.consent import ConsentScope
    from _application.receipts import OutcomeReference, ReceiptOwnership
    from _command_interface.receipts import OwnedReceiptStore

    (tmp_path / ".brain-core").mkdir()
    (tmp_path / ".brain-core" / "VERSION").write_text("0.68.0\n")
    entered = []
    def execute(*_args):
        entered.append(True)
        return Ok("test.probe", 1, ProbePayload(str(len(entered))))
    app, context = _setup(tmp_path, _effect_entry(execute),
        initial_commands=() if basis == "blanket" else ("test.probe",))
    class Clock:
        value = context.clock.now()

        def now(self):
            return self.value

    clock = Clock()
    context = replace(context, clock=clock)
    service = context.authorisation.service
    receipts = OwnedReceiptStore(tmp_path, context.clock, ReceiptOwnership(
        service.identity.brain_id, service.identity.principal, service.identity.kind,
        service.identity.context_id))
    session = replace(context.authorisation, receipts=receipts)
    context = replace(context, authorisation=session, receipt_reader=receipts)
    context = replace(context, access=session.bind(context))
    app = CommandApplication(context, session.catalogue)
    if basis == "blanket":
        service.request(scope=ConsentScope.COMMAND, command_id="test.probe",
            review=service.command_review("test.probe"), request_id="allow-probe")
    assert app.invoke(ProbeRequest()).result.value == "1"
    original = receipts.read(OutcomeReference(context.invocation_id))
    clock.value += timedelta(seconds=elapsed_seconds)
    duplicate = app.invoke(ProbeRequest())
    assert duplicate.error.code is ErrorCode.CONFLICT
    assert duplicate.error.next_action.command_id == "invocation.read"
    assert entered == [True]
    assert receipts.read(OutcomeReference(context.invocation_id)) == original


def test_concurrent_control_invocations_require_one_atomic_intent_claim(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from _application.receipts import ReceiptOwnership
    from _command_interface.receipts import OwnedReceiptStore

    (tmp_path / ".brain-core").mkdir()
    (tmp_path / ".brain-core" / "VERSION").write_text("0.68.0\n")
    entered = []
    def execute(*_args):
        entered.append(True)
        return _ok()
    entry = _entry(execute, initial_class=InitialAuthorisationClass.CONTROL,
        effect_class=EffectClass.SELECTED_BRAIN_MUTATION, preparation=None)
    app, context = _setup(tmp_path, entry)
    identity = context.authorisation.service.identity
    barrier = Barrier(2)
    class SimultaneousReceipts(OwnedReceiptStore):
        def read(self, reference):
            lookup = super().read(reference)
            barrier.wait(timeout=10)
            return lookup
    receipts = SimultaneousReceipts(tmp_path, context.clock,
        ReceiptOwnership(identity.brain_id, identity.principal, identity.kind, identity.context_id))
    session = replace(context.authorisation, receipts=receipts)
    context = replace(context, authorisation=session, receipt_reader=receipts)
    context = replace(context, access=session.bind(context))
    app = CommandApplication(context, session.catalogue)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: app.invoke(ProbeRequest()), range(2)))
    assert [result.status for result in results].count("ok") == 1
    assert [result.error.code for result in results if result.status == "error"] == [ErrorCode.CONFLICT]
    assert entered == [True]


def test_receipt_port_without_positive_claim_cannot_authorise_entry(tmp_path):
    class OldPort(_Receipts):
        def begin(self, intent):
            super().begin(intent)
    app, context = _setup(tmp_path, _effect_entry(lambda *_: pytest.fail("entry without positive claim")),
        receipts=OldPort())
    assert app.invoke(ProbeRequest()).error.code is ErrorCode.CONFLICT
    assert not context.authorisation.receipts.outcomes


@pytest.mark.parametrize("mutating", [False, True])
def test_completion_storage_failure_is_only_load_bearing_for_possible_effects(tmp_path, mutating):
    def execute(context, request):
        if mutating:
            admit_owner(context, request, live_query)
        return _ok()
    entry = _entry(execute, **({"effect_class": EffectClass.SELECTED_BRAIN_MUTATION,
        "initial_class": InitialAuthorisationClass.CONTENT,
        "retry_class": RetryClass.RECEIPT_REQUIRED,
        "preparation": OperationPreparation(live_query)} if mutating else {}))
    receipts = _FaultReceipts(finalise=True)
    app, _ = _setup(tmp_path, entry, receipts=receipts)
    result = app.invoke(ProbeRequest())
    if mutating:
        assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
        assert result.effects == "unknown"
        assert "inv-1" in receipts.intents and not receipts.outcomes
    else:
        assert result.status == "ok"
        assert not receipts.intents and not receipts.outcomes


def test_revocation_after_durable_intent_wins_before_entry(tmp_path):
    receipts = _FaultReceipts()
    app, context = _setup(tmp_path, _effect_entry(lambda *_: pytest.fail("revoked operation entered")), receipts=receipts)
    receipts.after_begin = lambda: context.authorisation.service.reduce(commands=("test.probe",))
    result = app.invoke(ProbeRequest())
    assert result.status == "error"
    assert result.effects == "none"
    assert receipts.outcomes["inv-1"].execution is ExecutionState.FAILED


def test_wrong_read_payload_returns_internal_error_without_effect_receipt(tmp_path):
    app, context = _setup(tmp_path, _entry(lambda *_: Ok("test.probe", 1, "wrong payload")))
    assert app.invoke(ProbeRequest()).error.code is ErrorCode.INTERNAL_ERROR
    assert not context.authorisation.receipts.outcomes


def test_diagnostics_identify_phase_and_preserve_private_details_outside_envelope(tmp_path):
    _, context = _setup(tmp_path)
    diagnostics = _Diagnostics()
    context = replace(context, diagnostics=diagnostics)
    result = CommandApplication(context, ApplicationCatalogue(())).invoke(ProbeRequest())
    assert result.error.code is ErrorCode.INTERNAL_ERROR
    assert result.error.details.correlation_id == context.correlation_id
    assert diagnostics.failures[0]["phase"] == "preflight"
    assert diagnostics.failures[0]["command_id"] == "test.probe"
    assert diagnostics.failures[0]["correlation_id"] == context.correlation_id


def test_diagnostic_reporter_failure_uses_sanitised_fallback(tmp_path, capfd):
    _, context = _setup(tmp_path)
    context = replace(context, diagnostics=_FailingDiagnostics())
    report_failure_safely(context, phase="execute", command_id="test.probe", error=RuntimeError("private original error"))
    fallback = capfd.readouterr().err
    assert "diagnostic reporter failed" in fallback
    assert "private" not in fallback and "Traceback" not in fallback


def test_reporter_failure_does_not_obscure_unknown_outcome(tmp_path, capfd):
    app, context = _setup(tmp_path, _effect_entry(lambda *_: (_ for _ in ()).throw(OSError("private crash"))))
    context = replace(context, diagnostics=_FailingDiagnostics())
    result = CommandApplication(context, context.authorisation.catalogue).invoke(ProbeRequest())
    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
    assert "diagnostic reporter failed" in capfd.readouterr().err


def test_catalogue_rejects_launcher_locality_and_duplicates():
    with pytest.raises(ValueError, match="launcher manifest"):
        _entry(locality=Locality.MACHINE_LOCAL)
    with pytest.raises(ValueError, match="unique"):
        ApplicationCatalogue((_entry(), _entry()))


def test_catalogue_fingerprint_excludes_executor_identity():
    assert ApplicationCatalogue((_entry(),)).fingerprint == ApplicationCatalogue((_entry(lambda *_: _ok()),)).fingerprint


@pytest.mark.parametrize("terminal", [False, True])
def test_control_intent_prevents_replay_against_later_state(tmp_path, terminal):
    from _application.access.reduce import AccessReduceRequest, ClearGrants
    from _application.consent import ConsentScope
    from _application.receipts import AdmissionIntent, OutcomeReference
    from _application.preparation import bind_operation, content_digest

    context = context_for(tmp_path, invocation_id="clear-original", initial_commands=())
    service = context.authorisation.service
    request = AccessReduceRequest(ClearGrants())
    app = CommandApplication(context)
    if terminal:
        assert app.invoke(request).result.changed is False
    else:
        context.authorisation.receipts.begin(AdmissionIntent(OutcomeReference("clear-original"),
            request.COMMAND_ID, request.COMMAND_VERSION, context.clock.now(), "initial",
            service.generation(), "host-request", operation_digest=content_digest(bind_operation(request).request_json),
            request_id="clear-original"))
    review = service.command_review("artefact.delete")
    service.request(scope=ConsentScope.COMMAND, command_id="artefact.delete", review=review, request_id="grant-later")
    result = app.invoke(request)
    assert result.error.code is ErrorCode.CONFLICT
    assert result.error.next_action.command_id == "invocation.read"
    assert result.error.next_action.arguments[0].value == "clear-original"
    assert service.state("artefact.delete") == "authorised"


@pytest.mark.parametrize("policy", ["denied", "migration_required"])
def test_policy_denials_route_to_source_specific_configuration_inspection(tmp_path, policy):
    from _application.access.request import AccessRequestRequest, CommandConsent
    from _application.adapter import ApplicationAdapter
    from _application.registry import current_application_catalogue, current_request_resolver
    context = context_for(tmp_path, initial_commands=(), request_policy=policy)
    ordinary = ApplicationAdapter(current_application_catalogue(), current_request_resolver()).invoke(
        context, "artefact.delete", {})
    assert ordinary.result.error.next_action.command_id == "vault.read-config"
    assert ordinary.exit_code == 3
    request = AccessRequestRequest(CommandConsent("artefact.delete",
        context.authorisation.service.command_review("artefact.delete")))
    result = CommandApplication(context).invoke(request)
    assert result.error.next_action.command_id == "vault.read-config"
    assert result.effects == "none"
    assert not context.authorisation.service.inventory("grants")["items"]
