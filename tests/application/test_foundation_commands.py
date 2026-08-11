"""Real owners for command discovery and invocation-outcome queries."""

from __future__ import annotations

from datetime import datetime

from _application.application import CommandApplication
from _application.context import (
    Capability,
    CapabilitySnapshot,
    InvocationContext,
    ProviderBindings,
    SelectedBrain,
)
from _application.foundation import build_application_catalogue, build_request_resolver
from _application.receipts import (
    OutcomeReceipt,
    OutcomeReference,
    ReceiptLookupState,
    ReceiptState,
)
from _application.requests import (
    CatalogueCursor,
    CommandDescribeRequest,
    CommandListRequest,
    InvocationReadRequest,
)
from _application.results import ErrorCode
from _application.types import (
    Availability,
    Authority,
    DependencyTier,
    EffectClass,
    Locality,
    Projection,
    CommandOwner,
    SnapshotFreshness,
)


NOW = datetime.fromisoformat("2026-08-09T15:00:00+10:00")


class _Authority:
    def allows(self, **_kwargs):
        return True


class _Receipts:
    def __init__(self, receipts=()):
        self.receipts = {
            receipt.reference.invocation_id: receipt for receipt in receipts
        }

    def write(self, receipt):
        self.receipts[receipt.reference.invocation_id] = receipt

    def read(self, reference):
        return self.receipts.get(reference.invocation_id)


class _Clock:
    def now(self):
        return NOW


def _context(tmp_path, receipts=None):
    receipts = receipts or _Receipts()
    return InvocationContext(
        selected_brain=SelectedBrain("test", tmp_path.resolve()),
        profile="reader",
        authority=_Authority(),
        dependency_tier=DependencyTier.PORTABLE,
        capabilities=CapabilitySnapshot(
            "snapshot",
            SnapshotFreshness.FRESH,
            NOW,
        ),
        providers=ProviderBindings(),
        correlation_id="corr-foundation",
        invocation_id="inv-query",
        receipt_writer=receipts,
        receipt_reader=receipts,
        clock=_Clock(),
    )


def _application(tmp_path, receipts=None):
    return CommandApplication(
        _context(tmp_path, receipts),
        build_application_catalogue(),
    )


def test_foundational_catalogue_has_one_real_owner_for_each_required_command():
    catalogue = build_application_catalogue()

    assert [entry.command_id for entry in catalogue.entries] == [
        "command.describe",
        "command.list",
        "invocation.read",
    ]
    assert len({entry.executor for entry in catalogue.entries}) == 3


def test_command_list_filters_and_paginates_the_bound_catalogue(tmp_path):
    application = _application(tmp_path)

    first = application.invoke(CommandListRequest(page_size=2))
    second = application.invoke(
        CommandListRequest(cursor=first.result.next_cursor, page_size=2)
    )
    filtered = application.invoke(
        CommandListRequest(
            domain="command",
            authority=Authority.READER,
            dependency_tier=DependencyTier.PORTABLE,
            locality=Locality.SELECTED_BRAIN_LOCAL,
            effect_class=EffectClass.NONE,
            projection=Projection.MCP,
        )
    )

    assert first.result.command_ids == ("command.describe", "command.list")
    assert first.result.catalogue_schema == "brain.command-catalogue/1"
    assert first.result.catalogue_fingerprint == build_application_catalogue().fingerprint
    assert first.result.next_cursor == CatalogueCursor("snapshot", "command.list")
    assert second.result.command_ids == ("invocation.read",)
    assert second.result.next_cursor is None
    assert filtered.result.command_ids == ("command.describe", "command.list")
    assert all(entry.owner is CommandOwner.APPLICATION for entry in filtered.result.entries)
    assert filtered.result.snapshot_token == "snapshot"


def test_command_list_owner_and_availability_filters_are_honest(tmp_path):
    application = _application(tmp_path)

    launcher = application.invoke(CommandListRequest(owner=CommandOwner.LAUNCHER))
    available = application.invoke(
        CommandListRequest(availability=Availability.AVAILABLE)
    )
    summary_match = application.invoke(CommandListRequest(query="resources"))

    assert launcher.result.entries == ()
    assert summary_match.result.command_ids == ("command.list",)
    assert available.result.command_ids == (
        "command.describe",
        "command.list",
        "invocation.read",
    )


class _Snapshots:
    def __init__(self):
        self.calls = []
        self.values = {}

    def refresh(self, provider_ids, *, previous_token=None):
        self.calls.append((provider_ids, previous_token))
        snapshot = CapabilitySnapshot(
            "refreshed",
            SnapshotFreshness.FRESH,
            NOW,
            tuple(Capability(provider_id, Availability.AVAILABLE) for provider_id in provider_ids),
        )
        self.values[snapshot.token] = snapshot
        return snapshot

    def read(self, token):
        return self.values.get(token)


def test_command_list_refreshes_once_and_pagination_reuses_the_snapshot(tmp_path):
    snapshots = _Snapshots()
    context = _context(tmp_path)
    context = InvocationContext(
        selected_brain=context.selected_brain,
        profile=context.profile,
        authority=context.authority,
        dependency_tier=context.dependency_tier,
        capabilities=context.capabilities,
        providers=context.providers,
        correlation_id=context.correlation_id,
        invocation_id=context.invocation_id,
        receipt_writer=context.receipt_writer,
        receipt_reader=context.receipt_reader,
        clock=context.clock,
        capability_snapshots=snapshots,
    )
    application = CommandApplication(context, build_application_catalogue())

    first = application.invoke(CommandListRequest(refresh=True, page_size=2))
    second = application.invoke(
        CommandListRequest(cursor=first.result.next_cursor, page_size=2)
    )

    assert snapshots.calls == [((), "snapshot")]
    assert first.result.snapshot_token == second.result.snapshot_token == "refreshed"
    assert second.result.command_ids == ("invocation.read",)


def test_command_list_rejects_cursor_outside_the_filtered_view(tmp_path):
    result = _application(tmp_path).invoke(
        CommandListRequest(
            domain="command",
            cursor=CatalogueCursor("snapshot", "invocation.read"),
        )
    )

    assert result.error.code is ErrorCode.INVALID_REQUEST
    assert result.effects == "none"
    assert result.error.details.field == "cursor"


def test_command_describe_returns_installed_identity_or_not_found(tmp_path):
    application = _application(tmp_path)

    found = application.invoke(CommandDescribeRequest("invocation.read"))
    missing = application.invoke(CommandDescribeRequest("artefact.read"))

    assert found.result.command_id == "invocation.read"
    assert found.result.catalogue_schema == "brain.command-catalogue/1"
    assert found.result.catalogue_fingerprint == build_application_catalogue().fingerprint
    assert found.result.command_version == 2
    assert found.result.owner.value == "application"
    assert found.result.request_schema_json
    assert found.result.result_schema_json
    assert found.result.examples[0].mcp_tool == "invocation.read"
    assert missing.error.code is ErrorCode.NOT_FOUND


def test_invocation_read_returns_receipt_or_explicit_still_unknown(tmp_path):
    reference = OutcomeReference("inv-target")
    receipt = OutcomeReceipt(
        reference,
        "artefact.create",
        1,
        ReceiptState.COMMITTED,
        NOW,
    )
    application = _application(tmp_path, _Receipts((receipt,)))

    found = application.invoke(InvocationReadRequest(reference.invocation_id))
    unknown_reference = OutcomeReference("inv-absent")
    unknown = application.invoke(InvocationReadRequest(unknown_reference.invocation_id))

    assert found.result.state is ReceiptLookupState.FOUND
    assert found.result.receipt == receipt
    assert unknown.result.state is ReceiptLookupState.STILL_UNKNOWN
    assert unknown.result.receipt is None


def test_foundational_transport_resolver_is_strict_and_binds_installed_versions():
    resolver = build_request_resolver()

    listed = resolver.resolve(
        "command.list",
        {
            "dependency_tier": "portable",
            "effect_class": "none",
            "page_size": 25,
        },
    )
    described = resolver.resolve(
        "command.describe",
        {"target_command_id": "command.list"},
    )
    outcome = resolver.resolve(
        "invocation.read",
        {"invocation_id": "inv-target"},
    )

    assert listed.dependency_tier is DependencyTier.PORTABLE
    assert listed.effect_class is EffectClass.NONE
    assert listed.page_size == 25
    assert described.target_command_id == "command.list"
    assert outcome.reference.invocation_id == "inv-target"
