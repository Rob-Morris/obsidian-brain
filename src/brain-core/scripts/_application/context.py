"""Trusted invocation context composed by adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from .receipts import ReceiptReader, ReceiptWriter
from .types import (
    Authority,
    Availability,
    DependencyTier,
    EffectClass,
    SnapshotFreshness,
)


class Clock(Protocol):
    def now(self) -> datetime: ...


class AuthorityEvaluator(Protocol):
    def allows(
        self,
        *,
        command_id: str,
        required: Authority,
        effect: EffectClass,
    ) -> bool: ...


class ProviderPort(Protocol):
    @property
    def provider_id(self) -> str: ...


@dataclass(frozen=True, slots=True)
class ProviderBindings:
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
    name: str
    availability: Availability

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("capability name must be non-empty")


@dataclass(frozen=True, slots=True)
class CapabilitySnapshot:
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
    brain_id: str
    vault_root: Path

    def __post_init__(self) -> None:
        if not self.brain_id.strip():
            raise ValueError("selected Brain requires a non-empty brain_id")
        if not self.vault_root.is_absolute():
            raise ValueError("selected Brain vault_root must be absolute")


@dataclass(frozen=True, slots=True)
class InvocationContext:
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
    dry_run: bool = False

    def __post_init__(self) -> None:
        if not self.profile.strip():
            raise ValueError("invocation context requires an authenticated profile")
        if not self.correlation_id.strip() or not self.invocation_id.strip():
            raise ValueError("invocation context requires correlation and invocation identifiers")
