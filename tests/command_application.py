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


NOW = datetime.fromisoformat("2026-08-09T16:00:00+10:00")


class _Authority:
    def allows(self, **_kwargs):
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


def application_for(vault_root):
    receipts = _Receipts()
    context = InvocationContext(
        selected_brain=SelectedBrain("command-vault", vault_root.resolve()),
        profile="reader",
        authority=_Authority(),
        dependency_tier=DependencyTier.PORTABLE,
        capabilities=CapabilitySnapshot(
            "snapshot",
            SnapshotFreshness.FRESH,
            NOW,
        ),
        providers=ProviderBindings(),
        correlation_id="corr-read",
        invocation_id="inv-read",
        receipt_writer=receipts,
        receipt_reader=receipts,
        clock=_Clock(),
    )
    return CommandApplication(context, current_application_catalogue())
