"""Trusted machine-global launcher invocation context."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from .contracts import ReceiptWriter


class AuthorityEvaluator(Protocol):
    def allows(self, *, command_id: str, required: str, effect: str) -> bool: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class ProviderPort(Protocol):
    @property
    def provider_id(self) -> str: ...

    @property
    def available(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class ProviderBindings:
    providers: tuple[ProviderPort, ...] = ()

    def __post_init__(self) -> None:
        names = [provider.provider_id for provider in self.providers]
        if any(not name.strip() for name in names):
            raise ValueError("launcher provider IDs must be non-empty")
        if len(names) != len(set(names)):
            raise ValueError("launcher providers must be unique")

    def get(self, provider_id: str) -> ProviderPort | None:
        return next(
            (provider for provider in self.providers if provider.provider_id == provider_id),
            None,
        )


@dataclass(frozen=True, slots=True)
class LauncherContext:
    profile: str
    authority: AuthorityEvaluator
    providers: ProviderBindings
    correlation_id: str
    invocation_id: str
    receipt_writer: ReceiptWriter
    clock: Clock
    caller_dir: Path
    home_dir: Path
    cli_version: str
    cli_binary: Path
    launcher_python: Path | None = None
    current_vault: Path | None = None
    distribution_root: Path | None = None
    operator_key: str | None = field(default=None, repr=False)
    dry_run: bool = False

    def __post_init__(self) -> None:
        if not self.profile.strip() or not self.correlation_id.strip():
            raise ValueError("launcher context requires profile and correlation identity")
        if not self.invocation_id.strip() or not self.cli_version.strip():
            raise ValueError("launcher context requires invocation and CLI identity")
        if not self.caller_dir.is_absolute():
            raise ValueError("launcher caller_dir must be absolute")
        if not self.home_dir.is_absolute():
            raise ValueError("launcher home_dir must be absolute")
        if not self.cli_binary.is_absolute():
            raise ValueError("launcher cli_binary must be absolute")
        if self.launcher_python is not None and not self.launcher_python.is_absolute():
            raise ValueError("launcher_python must be absolute")
        if self.current_vault is not None and not self.current_vault.is_absolute():
            raise ValueError("launcher current_vault must be absolute")
        if self.distribution_root is not None and not self.distribution_root.is_absolute():
            raise ValueError("launcher distribution_root must be absolute")
        if self.operator_key is not None and not self.operator_key.strip():
            raise ValueError("launcher operator_key must be non-empty when supplied")
        if not isinstance(self.dry_run, bool):
            raise ValueError("launcher dry_run must be a boolean")
