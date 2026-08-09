"""Behavioural tests for the shared selected-Brain invocation boundary."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path

from _application.application import CommandApplication
from _application.catalogue import ApplicationCatalogue, ApplicationEntry
from _application.context import (
    Capability,
    CapabilitySnapshot,
    InvocationContext,
    ProviderBindings,
    SelectedBrain,
)
from _application.receipts import ReceiptState
from _application.requests import CommandListPayload, CommandListRequest
from _application.results import Error, ErrorCode, Ok
from _application.types import (
    Authority,
    Availability,
    DependencyTier,
    EffectClass,
    Locality,
    Projection,
    RetryClass,
    SnapshotFreshness,
)


NOW = datetime.fromisoformat("2026-08-09T09:30:00+10:00")


class _Authority:
    def __init__(self, allowed=True, fail=False):
        self.allowed = allowed
        self.fail = fail

    def allows(self, **_kwargs):
        if self.fail:
            raise RuntimeError("authority backend unavailable")
        return self.allowed


class _Receipts:
    def __init__(self, fail=False):
        self.fail = fail
        self.written = []

    def write(self, receipt):
        if self.fail:
            raise OSError("receipt store unavailable")
        self.written.append(receipt)

    def read(self, _reference):
        return None


class _Clock:
    def now(self):
        return NOW


class _Provider:
    def __init__(self, provider_id):
        self.provider_id = provider_id


def _context(
    tmp_path: Path,
    *,
    authority=None,
    receipts=None,
    tier=DependencyTier.PORTABLE,
    providers=(),
    capabilities=(),
):
    receipts = receipts or _Receipts()
    return InvocationContext(
        selected_brain=SelectedBrain("test", tmp_path.resolve()),
        profile="reader",
        authority=authority or _Authority(),
        dependency_tier=tier,
        capabilities=CapabilitySnapshot(
            "snapshot",
            SnapshotFreshness.FRESH,
            NOW,
            tuple(capabilities),
        ),
        providers=ProviderBindings(tuple(providers)),
        correlation_id="corr-1",
        invocation_id="inv-1",
        receipt_writer=receipts,
        receipt_reader=receipts,
        clock=_Clock(),
    )


def _entry(executor, **changes):
    entry = ApplicationEntry(
        request_type=CommandListRequest,
        executor=executor,
        dependency_tier=DependencyTier.PORTABLE,
        locality=Locality.SELECTED_BRAIN_LOCAL,
        required_providers=(),
        optional_providers=(),
        authority=Authority.READER,
        effect_class=EffectClass.NONE,
        retry_class=RetryClass.SAFE,
        projections=(Projection.MCP, Projection.CLI, Projection.SCRIPT, Projection.PYTHON),
    )
    return replace(entry, **changes)


def _invoke(tmp_path, entry, *, context=None):
    context = context or _context(tmp_path)
    catalogue = ApplicationCatalogue((entry,))
    return CommandApplication(context, catalogue).invoke(CommandListRequest())


def test_success_flows_through_one_executor_and_records_no_effects(tmp_path):
    calls = []
    receipts = _Receipts()

    def execute(context, request):
        calls.append((context, request))
        return Ok("command.list", 1, CommandListPayload(("artefact.read",)))

    result = _invoke(
        tmp_path,
        _entry(execute),
        context=_context(tmp_path, receipts=receipts),
    )

    assert result.status == "ok"
    assert len(calls) == 1
    assert len(receipts.written) == 1
    assert receipts.written[0].state is ReceiptState.NONE


def test_authority_denial_occurs_before_executor_or_effects(tmp_path):
    calls = []
    receipts = _Receipts()

    def execute(_context, _request):
        calls.append(True)
        raise AssertionError("executor must not run")

    result = _invoke(
        tmp_path,
        _entry(execute),
        context=_context(tmp_path, authority=_Authority(allowed=False), receipts=receipts),
    )

    assert isinstance(result, Error)
    assert result.error.code is ErrorCode.AUTHORITY_DENIED
    assert result.effects == "none"
    assert calls == []
    assert receipts.written[0].state is ReceiptState.NONE


def test_missing_tier_and_provider_return_canonical_unavailable_before_executor(tmp_path):
    calls = []

    def execute(_context, _request):
        calls.append(True)
        raise AssertionError("executor must not run")

    entry = _entry(
        execute,
        dependency_tier=DependencyTier.MANAGED,
        required_providers=("document_renderer",),
    )
    result = _invoke(
        tmp_path,
        entry,
        context=_context(tmp_path, tier=DependencyTier.PORTABLE),
    )

    assert result.error.code is ErrorCode.CAPABILITY_UNAVAILABLE
    assert result.error.details.missing == (
        "tier:managed",
        "provider:document_renderer",
    )
    assert calls == []


def test_bound_but_unavailable_provider_is_not_silently_provisioned(tmp_path):
    entry = _entry(
        lambda *_args: Ok("command.list", 1, CommandListPayload(())),
        required_providers=("document_renderer",),
    )
    result = _invoke(
        tmp_path,
        entry,
        context=_context(
            tmp_path,
            providers=(_Provider("document_renderer"),),
            capabilities=(Capability("document_renderer", Availability.UNAVAILABLE),),
        ),
    )

    assert result.error.code is ErrorCode.CAPABILITY_UNAVAILABLE
    assert result.error.details.missing == ("capability:document_renderer",)


def test_port_failure_and_bad_read_result_map_to_stable_internal_error(tmp_path):
    authority_failure = _invoke(
        tmp_path,
        _entry(lambda *_args: Ok("command.list", 1, CommandListPayload(()))),
        context=_context(tmp_path, authority=_Authority(fail=True)),
    )
    wrong_payload = _invoke(
        tmp_path,
        _entry(lambda *_args: Ok("command.list", 1, "wrong payload")),
    )

    for result in (authority_failure, wrong_payload):
        assert result.error.code is ErrorCode.INTERNAL_ERROR
        assert result.effects == "none"
        assert result.error.details.correlation_id == "corr-1"


def test_mutation_executor_failure_is_unknown_non_retryable_and_receipted(tmp_path):
    receipts = _Receipts()
    entry = _entry(
        lambda *_args: (_ for _ in ()).throw(OSError("crashed after possible write")),
        effect_class=EffectClass.SELECTED_BRAIN_MUTATION,
        retry_class=RetryClass.RECEIPT_REQUIRED,
        authority=Authority.CONTRIBUTOR,
    )
    context = _context(tmp_path, receipts=receipts)
    context = replace(context, profile="contributor", authority=_Authority())

    result = _invoke(tmp_path, entry, context=context)

    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
    assert result.effects == "unknown"
    assert result.retryable is False
    assert result.outcome_reference.invocation_id == "inv-1"
    assert result.error.next_action.command_id == "invocation.read"
    assert result.error.next_action.arguments[0].value == "inv-1"
    assert receipts.written[0].state is ReceiptState.UNKNOWN


def test_receipt_failure_after_mutation_success_returns_unknown(tmp_path):
    entry = _entry(
        lambda *_args: Ok("command.list", 1, CommandListPayload(())),
        effect_class=EffectClass.SELECTED_BRAIN_MUTATION,
        retry_class=RetryClass.RECEIPT_REQUIRED,
        authority=Authority.CONTRIBUTOR,
    )
    context = _context(tmp_path, receipts=_Receipts(fail=True))
    context = replace(context, profile="contributor", authority=_Authority())

    result = _invoke(tmp_path, entry, context=context)

    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
    assert result.effects == "unknown"


def test_catalogue_rejects_launcher_locality_duplicates_and_unordered_entries():
    executor = lambda *_args: Ok("command.list", 1, CommandListPayload(()))
    entry = _entry(executor)

    try:
        ApplicationEntry(
            request_type=CommandListRequest,
            executor=executor,
            dependency_tier=DependencyTier.BOOTSTRAP,
            locality=Locality.MACHINE_LOCAL,
            required_providers=(),
            optional_providers=(),
            authority=Authority.READER,
            effect_class=EffectClass.NONE,
            retry_class=RetryClass.SAFE,
            projections=(Projection.CLI,),
        )
    except ValueError as exc:
        assert "launcher manifest" in str(exc)
    else:
        raise AssertionError("machine-local application entry was accepted")

    try:
        ApplicationCatalogue((entry, entry))
    except ValueError as exc:
        assert "unique" in str(exc)
    else:
        raise AssertionError("duplicate application entry was accepted")


def test_catalogue_fingerprint_excludes_executor_identity_and_dynamic_availability():
    first = ApplicationCatalogue(
        (_entry(lambda *_args: Ok("command.list", 1, CommandListPayload(()))),)
    )
    second = ApplicationCatalogue(
        (_entry(lambda *_args: Ok("command.list", 1, CommandListPayload(("different",)))),)
    )

    assert first.fingerprint == second.fingerprint
    assert first.fingerprint.startswith("sha256:")
