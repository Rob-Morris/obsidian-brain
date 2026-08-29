"""Shared selected-Brain application harness for command-owner tests."""

from __future__ import annotations

from datetime import datetime

from _application.application import CommandApplication
from _application.context import (
    CapabilitySnapshot,
    InvocationContext,
    ProviderBindings,
    SelectedBrain,
)
from _application.registry import current_application_catalogue
from _application.types import DependencyTier, SnapshotFreshness
from _command_interface.context import SynchronousSessionMirror


NOW = datetime.fromisoformat("2026-08-09T16:00:00+10:00")


class _Authority:
    def allows(self, **_kwargs):
        return True

    def ceiling_allows(self, _command_id):
        return True

    def consume(self, _command_id):
        return True


class _Receipts:
    def __init__(self):
        self.values = {}

    def write(self, receipt):
        self.values[receipt.reference.invocation_id] = receipt

    def read(self, reference):
        return self.values.get(reference.invocation_id)


class _Clock:
    def now(self):
        return NOW


def application_for(
    vault_root,
    *,
    dependency_tier=DependencyTier.PORTABLE,
    workspace_dir=None,
    capabilities=(),
    providers=(),
    authority=None,
    dry_run=False,
):
    receipts = _Receipts()
    context = InvocationContext(
        selected_brain=SelectedBrain("command-vault", vault_root.resolve()),
        profile="reader",
        authority=authority or _Authority(),
        dependency_tier=dependency_tier,
        capabilities=CapabilitySnapshot(
            "snapshot",
            SnapshotFreshness.FRESH,
            NOW,
            tuple(capabilities),
        ),
        providers=ProviderBindings(tuple(providers)),
        correlation_id="corr-read",
        invocation_id="inv-read",
        receipt_writer=receipts,
        receipt_reader=receipts,
        clock=_Clock(),
        dry_run=dry_run,
        workspace_dir=workspace_dir,
        session_mirror=SynchronousSessionMirror(vault_root.resolve()),
    )
    return CommandApplication(context, current_application_catalogue())
