"""Trusted local invocation-context composition outside ``_application``."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from _application.context import (
    Capability,
    CapabilitySnapshot,
    CapabilitySnapshotStore,
    InvocationContext,
    DiagnosticReporter,
    ProviderBindings,
    SelectedBrain,
)
from _application.access_session import AuthorisationSession
from _application.receipts import OwnedReceiptPort
from _application.types import (
    Availability,
    DependencyTier,
    SnapshotFreshness,
)
from _common import _operational_log


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
class SynchronousSessionMirror:
    """Persist a session mirror before returning to direct CLI/script callers."""

    vault_root: Path

    def publish(self, model: Mapping[str, object]) -> None:
        import session

        session.persist_session_markdown(model, self.vault_root)


@dataclass(frozen=True, slots=True)
class OperationalDiagnosticReporter:
    """Submit command failures to the best-effort content-free operational log."""

    vault_root: Path
    process: str = "script"

    def report_failure(
        self,
        *,
        phase: str,
        command_id: str,
        correlation_id: str,
        error: BaseException,
    ) -> None:
        fields = {
            "phase": phase,
            "command_id": command_id,
            "correlation_id": correlation_id,
            "error_class": _operational_log.classify_error(error),
            "exception_type": type(error).__name__,
        }
        logger = _operational_log.current_logger()
        if logger is not None:
            logger.record("command.failed", **fields)
        else:
            _operational_log.append_record(
                self.vault_root, self.process, "command.failed", **fields
            )


def compose_local_context(
    *,
    vault_root: Path,
    brain_id: str,
    profile: str,
    dependency_tier: DependencyTier,
    provider_ids: tuple[str, ...],
    capability_states: tuple[tuple[str, Availability], ...],
    snapshot_token: str,
    snapshot_freshness: SnapshotFreshness,
    snapshot_observed_at: datetime,
    correlation_id: str,
    invocation_id: str,
    receipt_store: OwnedReceiptPort,
    authorisation: AuthorisationSession,
    operation_id: str | None = None,
    workspace_dir: Path | None = None,
    capability_snapshots: CapabilitySnapshotStore | None = None,
    dry_run: bool = False,
    clock=None,
    diagnostics: DiagnosticReporter | None = None,
    derived_snapshots=None,
    session_mirror=None,
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
    if any(not callable(getattr(receipt_store, method, None))
           for method in ("read", "begin", "finalise")):
        raise ValueError("receipt_store must implement owned read, admission intent and completion")
    if workspace_dir is not None and not workspace_dir.is_absolute():
        raise ValueError("local context workspace_dir must be absolute")
    resolved_workspace = workspace_dir.resolve() if workspace_dir is not None else None
    context = InvocationContext(
        selected_brain=SelectedBrain(brain_id, root),
        profile=profile,
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
        receipt_reader=receipt_store,
        clock=clock or SystemClock(),
        authorisation=authorisation,
        operation_id=operation_id,
        dry_run=dry_run,
        workspace_dir=resolved_workspace,
        capability_snapshots=capability_snapshots,
        diagnostics=(
            diagnostics
            if diagnostics is not None
            else OperationalDiagnosticReporter(root)
        ),
        derived_snapshots=derived_snapshots,
        session_mirror=(
            session_mirror
            if session_mirror is not None
            else SynchronousSessionMirror(root)
        ),
    )

    return replace(context, access=authorisation.bind(context))


def _validate_vault(root: Path) -> None:
    if not root.is_absolute():
        raise ValueError("local context vault_root must be absolute")
    core = root / ".brain-core"
    version = core / "VERSION"
    if core.is_symlink() or version.is_symlink() or not version.is_file():
        raise ValueError("local context requires a regular installed Brain Core")
