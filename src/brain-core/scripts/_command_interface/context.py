"""Trusted local invocation-context composition outside ``_application``."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from _application.context import (
    Capability,
    CapabilitySnapshot,
    CapabilitySnapshotStore,
    InvocationContext,
    ProviderBindings,
    SelectedBrain,
)
from _application.projection import project_identity
from _application.receipts import ReceiptReader, ReceiptWriter
from _application.types import (
    Authority,
    Availability,
    DependencyTier,
    EffectClass,
    SnapshotFreshness,
    validate_command_id,
)


@dataclass(frozen=True, slots=True)
class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class BoundProvider:
    provider_id: str

    def __post_init__(self) -> None:
        if not self.provider_id.strip():
            raise ValueError("bound provider requires a non-empty provider_id")


@dataclass(frozen=True, slots=True)
class ProfileAuthority:
    """Enforce one authenticated profile's canonical command allow-list."""

    profile: str
    allowed_tools: frozenset[str]

    def __post_init__(self) -> None:
        if not self.profile.strip():
            raise ValueError("profile authority requires a profile name")
        try:
            for tool in self.allowed_tools:
                validate_command_id(tool)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "profile authority accepts only canonical Brain MCP tool names"
            ) from exc

    def allows(
        self,
        *,
        command_id: str,
        required: Authority,
        effect: EffectClass,
    ) -> bool:
        del required, effect
        return project_identity(command_id).mcp_tool in self.allowed_tools


def compose_local_context(
    *,
    vault_root: Path,
    brain_id: str,
    profile: str,
    allowed_tools: frozenset[str],
    dependency_tier: DependencyTier,
    provider_ids: tuple[str, ...],
    capability_states: tuple[tuple[str, Availability], ...],
    snapshot_token: str,
    snapshot_freshness: SnapshotFreshness,
    snapshot_observed_at: datetime,
    correlation_id: str,
    invocation_id: str,
    receipt_store: ReceiptReader | ReceiptWriter,
    workspace_dir: Path | None = None,
    capability_snapshots: CapabilitySnapshotStore | None = None,
    dry_run: bool = False,
    clock=None,
) -> InvocationContext:
    """Compose trusted state already resolved by a concrete local adapter."""

    if not vault_root.is_absolute() or vault_root.is_symlink():
        raise ValueError("local context vault_root must be a regular absolute path")
    root = vault_root.resolve()
    _validate_vault(root)
    if provider_ids != tuple(sorted(set(provider_ids))):
        raise ValueError("provider_ids must be sorted and unique")
    capability_names = tuple(name for name, _state in capability_states)
    if capability_names != tuple(sorted(set(capability_names))):
        raise ValueError("capability states must be sorted and unique")
    if any(not isinstance(state, Availability) for _name, state in capability_states):
        raise ValueError("capability states must use Availability")
    # Runtime-checkable protocols would enlarge the application port; validate
    # the concrete methods here instead.
    if not callable(getattr(receipt_store, "read", None)) or not callable(
        getattr(receipt_store, "write", None)
    ):
        raise ValueError("receipt_store must implement read and write")
    if workspace_dir is not None and not workspace_dir.is_absolute():
        raise ValueError("local context workspace_dir must be absolute")
    resolved_workspace = workspace_dir.resolve() if workspace_dir is not None else None
    return InvocationContext(
        selected_brain=SelectedBrain(brain_id, root),
        profile=profile,
        authority=ProfileAuthority(profile, allowed_tools),
        dependency_tier=dependency_tier,
        capabilities=CapabilitySnapshot(
            snapshot_token,
            snapshot_freshness,
            snapshot_observed_at,
            tuple(Capability(name, state) for name, state in capability_states),
        ),
        providers=ProviderBindings(tuple(BoundProvider(name) for name in provider_ids)),
        correlation_id=correlation_id,
        invocation_id=invocation_id,
        receipt_writer=receipt_store,
        receipt_reader=receipt_store,
        clock=clock or SystemClock(),
        dry_run=dry_run,
        workspace_dir=resolved_workspace,
        capability_snapshots=capability_snapshots,
    )


def _validate_vault(root: Path) -> None:
    if not root.is_absolute():
        raise ValueError("local context vault_root must be absolute")
    core = root / ".brain-core"
    version = core / "VERSION"
    if core.is_symlink() or version.is_symlink() or not version.is_file():
        raise ValueError("local context requires a regular installed Brain Core")
