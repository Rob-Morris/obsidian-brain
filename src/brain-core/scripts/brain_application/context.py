"""Supported trusted-context contracts for typed Python adapters."""

from _application.context import (
    Capability,
    CapabilitySnapshot,
    CapabilitySnapshotStore,
    Clock,
    DiagnosticReporter,
    InvocationContext,
    NullDiagnosticReporter,
    ProviderBindings,
    ProviderPort,
    SelectedBrain,
)
from _application.access_contracts import AuthorisationAccess
from _application.access_session import AuthorisationSession
from _application.consent import ConsentIdentity, ConsentPolicy, ConsentService, ConsentStateStore
from _application.receipts import (
    OutcomeReceipt,
    OutcomeReference,
    OwnedReceiptPort,
    OwnedReceiptLookup,
    AdmissionIntent,
    InvocationOutcome,
    ReceiptOwnership,
    ReceiptIntentConflict,
    ReceiptOwnershipError,
)
from _application.types import Authority, Availability, DependencyTier, SnapshotFreshness

__all__ = (
    "AuthorisationAccess",
    "AuthorisationSession",
    "ConsentIdentity",
    "ConsentPolicy",
    "ConsentService",
    "ConsentStateStore",
    "Authority",
    "Availability",
    "Capability",
    "CapabilitySnapshot",
    "CapabilitySnapshotStore",
    "Clock",
    "DependencyTier",
    "DiagnosticReporter",
    "InvocationContext",
    "NullDiagnosticReporter",
    "OutcomeReceipt",
    "OutcomeReference",
    "ProviderBindings",
    "ProviderPort",
    "OwnedReceiptPort",
    "OwnedReceiptLookup",
    "AdmissionIntent",
    "InvocationOutcome",
    "ReceiptOwnership",
    "ReceiptIntentConflict",
    "ReceiptOwnershipError",
    "SelectedBrain",
    "SnapshotFreshness",
)
