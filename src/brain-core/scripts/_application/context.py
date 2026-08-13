"""Trusted invocation context composed by adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sys
import traceback
from typing import Protocol

from .access_contracts import AccessController
from .receipts import ReceiptReader, ReceiptWriter
from .types import (
    Authority,
    Availability,
    DependencyTier,
    EffectClass,
    SnapshotFreshness,
)


class Clock(Protocol):
    """Provide a trusted timezone-aware wall clock to application services."""

    def now(self) -> datetime: ...


class AuthorityEvaluator(Protocol):
    """Evaluate immutable ceiling and current principal-bound command grants."""

    def allows(
        self,
        *,
        command_id: str,
        required: Authority,
        effect: EffectClass,
    ) -> bool: ...

    def ceiling_allows(self, command_id: str) -> bool: ...

    def consume(self, command_id: str) -> bool: ...


class DiagnosticReporter(Protocol):
    """Receive best-effort internal diagnostics without changing command results."""

    def report_failure(
        self,
        *,
        phase: str,
        command_id: str,
        correlation_id: str,
        error: BaseException,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class NullDiagnosticReporter:
    def report_failure(
        self,
        *,
        phase: str,
        command_id: str,
        correlation_id: str,
        error: BaseException,
    ) -> None:
        del phase, command_id, correlation_id, error


def report_failure_safely(
    context: "InvocationContext",
    *,
    phase: str,
    command_id: str,
    error: BaseException,
) -> None:
    """Report an invocation failure without letting diagnostics alter its outcome."""

    try:
        context.diagnostics.report_failure(
            phase=phase,
            command_id=command_id,
            correlation_id=context.correlation_id,
            error=error,
        )
    except Exception as reporter_error:
        try:
            sys.__stderr__.write(
                "Brain command diagnostic reporter failed while handling "
                f"{phase} for {command_id}:\n"
            )
            traceback.print_exception(reporter_error, file=sys.__stderr__)
        except Exception:
            # Diagnostic fallback must never change the command outcome.
            pass


class ProviderPort(Protocol):
    """Identify one adapter-composed provider available to command executors."""

    @property
    def provider_id(self) -> str: ...


class CapabilitySnapshotStore(Protocol):
    """Read or refresh immutable provider-availability snapshots."""

    def refresh(
        self,
        provider_ids: tuple[str, ...],
        *,
        previous_token: str | None = None,
    ) -> "CapabilitySnapshot": ...

    def read(self, token: str) -> "CapabilitySnapshot | None": ...


@dataclass(frozen=True, slots=True)
class ProviderBindings:
    """Immutable provider lookup keyed by unique provider identity."""

    providers: tuple[ProviderPort, ...] = ()

    def __post_init__(self) -> None:
        names = [provider.provider_id for provider in self.providers]
        if any(not name.strip() for name in names):
            raise ValueError("provider_id must be non-empty")
        if len(names) != len(set(names)):
            raise ValueError("provider bindings cannot contain duplicate provider_id values")

    def get(self, provider_id: str) -> ProviderPort | None:
        return next(
            (provider for provider in self.providers if provider.provider_id == provider_id),
            None,
        )

    def require(self, provider_id: str) -> ProviderPort:
        provider = self.get(provider_id)
        if provider is None:
            raise KeyError(f"provider is not bound: {provider_id}")
        return provider


@dataclass(frozen=True, slots=True)
class Capability:
    """Availability of one named capability in a trusted snapshot."""

    name: str
    availability: Availability

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("capability name must be non-empty")


@dataclass(frozen=True, slots=True)
class CapabilitySnapshot:
    """Point-in-time capability evidence used for one invocation boundary."""

    token: str
    freshness: SnapshotFreshness
    observed_at: datetime
    capabilities: tuple[Capability, ...] = ()

    def __post_init__(self) -> None:
        if not self.token.strip():
            raise ValueError("capability snapshot token must be non-empty")
        if self.observed_at.tzinfo is None:
            raise ValueError("capability snapshot observed_at must be timezone-aware")
        names = [capability.name for capability in self.capabilities]
        if len(names) != len(set(names)):
            raise ValueError("capability snapshot cannot contain duplicate names")

    def availability_of(self, name: str) -> Availability:
        capability = next(
            (item for item in self.capabilities if item.name == name),
            None,
        )
        return Availability.UNKNOWN if capability is None else capability.availability


@dataclass(frozen=True, slots=True)
class SelectedBrain:
    """Trusted identity and absolute vault root selected by an adapter."""

    brain_id: str
    vault_root: Path

    def __post_init__(self) -> None:
        if not self.brain_id.strip():
            raise ValueError("selected Brain requires a non-empty brain_id")
        if not self.vault_root.is_absolute():
            raise ValueError("selected Brain vault_root must be absolute")


@dataclass(frozen=True, slots=True)
class InvocationContext:
    """Trusted, immutable execution facts composed outside semantic requests."""

    selected_brain: SelectedBrain
    profile: str
    authority: AuthorityEvaluator
    dependency_tier: DependencyTier
    capabilities: CapabilitySnapshot
    providers: ProviderBindings
    correlation_id: str
    invocation_id: str
    receipt_writer: ReceiptWriter
    receipt_reader: ReceiptReader
    clock: Clock
    access: AccessController | None = None
    dry_run: bool = False
    workspace_dir: Path | None = None
    capability_snapshots: CapabilitySnapshotStore | None = None
    diagnostics: DiagnosticReporter = NullDiagnosticReporter()

    def __post_init__(self) -> None:
        if not self.profile.strip():
            raise ValueError("invocation context requires an authenticated profile")
        if not self.correlation_id.strip() or not self.invocation_id.strip():
            raise ValueError("invocation context requires correlation and invocation identifiers")
        if self.workspace_dir is not None and not self.workspace_dir.is_absolute():
            raise ValueError("invocation context workspace_dir must be absolute")
