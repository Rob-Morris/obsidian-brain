"""Pure contracts for typed application requests, context, results and receipts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

from _application.context import (
    Capability,
    CapabilitySnapshot,
    InvocationContext,
    ProviderBindings,
    SelectedBrain,
)
from _application.receipts import (
    CommittedEffect,
    OutcomeReceipt,
    OutcomeReference,
    ReceiptState,
)
from _application.requests import (
    CommandDescribeRequest,
    CommandDescriptionPayload,
    CommandListPayload,
    CommandListRequest,
    InvocationReadPayload,
    InvocationReadRequest,
    command_identity,
)
from _application.results import (
    CapabilityUnavailableDetails,
    CommandError,
    Error,
    ErrorCode,
    Ok,
    OutcomeUnknownDetails,
    Partial,
)
from _application.types import (
    Authority,
    Availability,
    DependencyTier,
    EffectClass,
    Locality,
    Projection,
    SnapshotFreshness,
)


NOW = datetime.fromisoformat("2026-08-09T09:30:00+10:00")


@dataclass(frozen=True)
class _Provider:
    provider_id: str


class _Authority:
    def allows(self, *, command_id, required, effect):
        return required is Authority.READER

    def ceiling_allows(self, _command_id):
        return True

    def consume(self, _command_id):
        return True


class _Receipts:
    def __init__(self):
        self.receipts = {}

    def write(self, receipt):
        self.receipts[receipt.reference.invocation_id] = receipt

    def read(self, reference):
        return self.receipts.get(reference.invocation_id)


class _Clock:
    def now(self):
        return NOW


def test_request_type_owns_identity_version_and_result_type():
    cases = [
        (CommandListRequest(), "command.list", CommandListPayload),
        (CommandDescribeRequest("artefact.read"), "command.describe", CommandDescriptionPayload),
        (InvocationReadRequest("inv-1"), "invocation.read", InvocationReadPayload),
    ]

    for request, command_id, result_type in cases:
        expected_version = 2 if command_id.startswith("command.") or command_id == "invocation.read" else 1
        assert command_identity(request) == (command_id, expected_version, result_type)
        assert "command_id" not in request.__dataclass_fields__


def test_dynamic_identity_reads_the_concrete_request_contract():
    class Lookalike:
        COMMAND_ID = "command.list"
        COMMAND_VERSION = 1
        RESULT_TYPE = CommandListPayload

    assert command_identity(Lookalike()) == ("command.list", 1, CommandListPayload)


def test_dynamic_identity_rejects_types_without_a_request_contract():
    with pytest.raises(TypeError, match="does not own identity"):
        command_identity(object())


@pytest.mark.parametrize(
    "command_id",
    ("artefact.-read", "artefact.read-", "artefact.read--archived"),
)
def test_command_identifiers_reject_non_canonical_hyphenation(command_id):
    with pytest.raises(ValueError, match="canonical"):
        CommandDescribeRequest(command_id)


def test_list_request_validates_bounded_pagination():
    request = CommandListRequest(
        query="artefact",
        authority=Authority.READER,
        dependency_tier=DependencyTier.PORTABLE,
        locality=Locality.SELECTED_BRAIN_LOCAL,
        effect_class=EffectClass.NONE,
        projection=Projection.MCP,
        page_size=500,
    )

    assert request.page_size == 500
    with pytest.raises(ValueError, match="between 1 and 500"):
        CommandListRequest(page_size=0)
    with pytest.raises(ValueError, match="canonical noun"):
        CommandListRequest(domain="-artefact")
    with pytest.raises(ValueError, match="Authority"):
        CommandListRequest(authority="reader")
    with pytest.raises(ValueError, match="non-empty"):
        CommandListRequest(query="")


def test_result_variants_have_one_structurally_valid_shape():
    ok = Ok(
        "command.list",
        2,
        CommandListPayload(
            "brain.command-catalogue/1",
            "sha256:test",
            (),
            "snapshot",
            SnapshotFreshness.FRESH,
        ),
    )
    partial = Partial(
        "artefact.delete",
        1,
        CommandError(ErrorCode.CONFLICT, "Index refresh failed after deletion."),
        (CommittedEffect("artefact_deleted", "idea/example"),),
    )
    error = Error(
        "artefact.read",
        1,
        CommandError(ErrorCode.NOT_FOUND, "Artefact was not found."),
    )

    assert (ok.schema, ok.status) == ("brain.command-result/1", "ok")
    assert (partial.schema, partial.status) == ("brain.command-result/1", "partial")
    assert (error.schema, error.status, error.effects) == (
        "brain.command-result/1",
        "error",
        "none",
    )
    assert not hasattr(ok, "error")
    assert not hasattr(partial, "result")
    assert not hasattr(error, "result")


def test_partial_and_unknown_effect_invariants_fail_closed():
    command_error = CommandError(ErrorCode.CONFLICT, "Mutation did not complete.")
    with pytest.raises(ValueError, match="enumerate committed effects"):
        Partial("artefact.delete", 1, command_error, ())
    with pytest.raises(ValueError, match="requires an outcome reference"):
        Error("artefact.delete", 1, command_error, effects="unknown")
    with pytest.raises(ValueError, match="cannot be retryable"):
        reference = OutcomeReference("inv-2")
        Error(
            "artefact.delete",
            1,
            CommandError(
                ErrorCode.COMMAND_OUTCOME_UNKNOWN,
                "Mutation outcome is unknown.",
                OutcomeUnknownDetails(reference),
            ),
            effects="unknown",
            outcome_reference=reference,
            retryable=True,
        )
    with pytest.raises(ValueError, match="cannot carry"):
        Error(
            "artefact.delete",
            1,
            command_error,
            outcome_reference=OutcomeReference("inv-2"),
        )


def test_unknown_effect_requires_matching_typed_error_and_reference():
    reference = OutcomeReference("inv-unknown")
    result = Error(
        "artefact.delete",
        1,
        CommandError(
            ErrorCode.COMMAND_OUTCOME_UNKNOWN,
            "Mutation outcome is unknown.",
            OutcomeUnknownDetails(reference),
        ),
        effects="unknown",
        outcome_reference=reference,
    )

    assert result.retryable is False
    with pytest.raises(ValueError, match="command_outcome_unknown"):
        Error(
            "artefact.delete",
            1,
            CommandError(ErrorCode.INTERNAL_ERROR, "Connection failed."),
            effects="unknown",
            outcome_reference=reference,
        )


def test_capability_unavailable_details_are_typed_and_actionable():
    details = CapabilityUnavailableDetails(
        required_tier=DependencyTier.MANAGED,
        current_tier=DependencyTier.PORTABLE,
        locality=Locality.SELECTED_BRAIN_LOCAL,
        missing=("document_renderer",),
        snapshot_freshness=SnapshotFreshness.FRESH,
        recoverable=True,
    )

    assert details.missing == ("document_renderer",)
    with pytest.raises(ValueError, match="must name"):
        CapabilityUnavailableDetails(
            DependencyTier.MANAGED,
            DependencyTier.PORTABLE,
            Locality.SELECTED_BRAIN_LOCAL,
            (),
            SnapshotFreshness.FRESH,
            True,
        )


def test_outcome_receipt_invariants_distinguish_partial_and_unknown():
    reference = OutcomeReference("inv-3")
    partial = OutcomeReceipt(
        reference,
        "artefact.delete",
        1,
        ReceiptState.KNOWN_PARTIAL,
        NOW,
        (CommittedEffect("artefact_deleted", "idea/example"),),
    )
    unknown = OutcomeReceipt(
        reference,
        "artefact.delete",
        1,
        ReceiptState.UNKNOWN,
        NOW,
    )

    assert partial.committed_effects
    assert unknown.committed_effects == ()
    with pytest.raises(ValueError, match="enumerate"):
        OutcomeReceipt(reference, "artefact.delete", 1, ReceiptState.KNOWN_PARTIAL, NOW)


def test_context_contains_explicit_trusted_state_and_no_environment_resolution(tmp_path):
    receipts = _Receipts()
    snapshot = CapabilitySnapshot(
        token="snapshot-1",
        freshness=SnapshotFreshness.FRESH,
        observed_at=NOW,
        capabilities=(Capability("document_renderer", Availability.UNAVAILABLE),),
    )
    providers = ProviderBindings((_Provider("caller_filesystem"),))
    context = InvocationContext(
        selected_brain=SelectedBrain("test-brain", Path(tmp_path).resolve()),
        profile="reader",
        authority=_Authority(),
        dependency_tier=DependencyTier.PORTABLE,
        capabilities=snapshot,
        providers=providers,
        correlation_id="corr-1",
        invocation_id="inv-1",
        receipt_writer=receipts,
        receipt_reader=receipts,
        clock=_Clock(),
    )

    assert context.selected_brain.vault_root == Path(tmp_path).resolve()
    assert context.capabilities.availability_of("document_renderer") is Availability.UNAVAILABLE
    assert context.capabilities.availability_of("unobserved") is Availability.UNKNOWN
    assert context.providers.require("caller_filesystem").provider_id == "caller_filesystem"
    with pytest.raises(KeyError, match="not bound"):
        context.providers.require("missing")


def test_dependency_tiers_are_ordered_without_conflating_locality():
    assert DependencyTier.MANAGED.supports(DependencyTier.PORTABLE)
    assert DependencyTier.PORTABLE.supports(DependencyTier.BOOTSTRAP)
    assert not DependencyTier.BOOTSTRAP.supports(DependencyTier.PORTABLE)
    assert {item.value for item in Locality} == {
        "selected_brain_local",
        "caller_local",
        "machine_local",
    }
