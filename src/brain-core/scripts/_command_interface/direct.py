"""Trusted discovery and context composition for direct local commands."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import os
from pathlib import Path
from typing import Callable
import uuid

from _application.availability import CapabilityRefresher
from _application.catalogue import ApplicationCatalogue
from _application.projection import project_identity
from _application.registry import current_application_catalogue
from _application.types import (
    Availability,
    DependencyTier,
    SnapshotFreshness,
)
from _bootstrap.runtime import current_process_in_managed_runtime
from _common import is_brain_vault
import config as brain_config
import vault_registry

from .context import SystemClock, compose_local_context
from .receipts import FileReceiptStore


_LOCAL_PROVIDERS = (
    "caller_filesystem",
    "document_renderer",
    "obsidian_cli",
    "semantic_retrieval",
    "semantic_runtime",
)


class DirectContextError(RuntimeError):
    """Trusted direct-command context could not be resolved safely."""


def resolve_direct_vault(
    vault_arg: str | None,
    *,
    start_dir: Path | None = None,
    script_path: Path | None = None,
) -> Path:
    """Resolve explicit, environment, cwd or owning-script Brain without exiting."""

    if vault_arg:
        candidate = Path(vault_arg).expanduser()
        if not candidate.is_absolute():
            candidate = (start_dir or Path.cwd()) / candidate
        return _require_vault(candidate)
    env_root = os.environ.get("BRAIN_VAULT_ROOT")
    if env_root:
        return _require_vault(Path(env_root).expanduser())
    for origin in (start_dir or Path.cwd(), script_path or Path(__file__)):
        current = origin.resolve()
        if current.is_file():
            current = current.parent
        for candidate in (current, *current.parents):
            if is_brain_vault(candidate):
                return _require_vault(candidate)
    raise DirectContextError(
        "no Brain vault could be resolved; pass --vault or set BRAIN_VAULT_ROOT"
    )


def compose_direct_context(
    *,
    vault_root: Path,
    command_id: str | None = None,
    catalogue: ApplicationCatalogue | None = None,
    operator_key: str | None = None,
    workspace_dir: Path | None = None,
    dry_run: bool = False,
    clock=None,
):
    """Resolve one fresh direct-process invocation context without hand-off."""

    clock = clock or SystemClock()
    root = _require_vault(vault_root)
    catalogue = catalogue or current_application_catalogue()
    merged = brain_config.load_config(
        str(root),
        additional_valid_tools=frozenset(
            project_identity(entry.command_id).mcp_tool
            for entry in catalogue.entries
        ),
    )
    try:
        profile, _operator_id = brain_config.authenticate_operator(operator_key, merged)
    except ValueError as exc:
        raise DirectContextError(str(exc)) from exc
    allowed_tools = _profile_tools(merged, profile)
    workspace = _resolve_workspace(root, workspace_dir)
    tier = (
        DependencyTier.MANAGED
        if current_process_in_managed_runtime(root)
        else DependencyTier.PORTABLE
    )
    observed_at = clock.now()
    if not isinstance(observed_at, datetime) or observed_at.tzinfo is None:
        raise DirectContextError("direct command clock must be timezone-aware")
    refresher = _capability_refresher(root, workspace, merged, tier, clock)
    relevant_providers = _relevant_providers(command_id, catalogue)
    if relevant_providers:
        snapshot = refresher.refresh(relevant_providers)
        states = tuple(
            (capability.name, capability.availability)
            for capability in snapshot.capabilities
        )
        snapshot_token = snapshot.token
        observed_at = snapshot.observed_at
    else:
        states = ()
        snapshot_token = _snapshot_token(states, observed_at)
    invocation_id = f"direct-{uuid.uuid4()}"
    receipt_store = FileReceiptStore(root, clock)
    return compose_local_context(
        vault_root=root,
        brain_id=_brain_id(root),
        profile=profile,
        allowed_tools=allowed_tools,
        dependency_tier=tier,
        provider_ids=_LOCAL_PROVIDERS,
        capability_states=states,
        snapshot_token=snapshot_token,
        snapshot_freshness=SnapshotFreshness.FRESH,
        snapshot_observed_at=observed_at,
        correlation_id=invocation_id,
        invocation_id=invocation_id,
        receipt_store=receipt_store,
        workspace_dir=workspace,
        capability_snapshots=refresher,
        dry_run=dry_run,
        clock=clock,
    )


def _require_vault(candidate: Path) -> Path:
    if candidate.is_symlink():
        raise DirectContextError("selected Brain vault cannot be a symlink")
    root = candidate.resolve()
    core = root / ".brain-core"
    version = core / "VERSION"
    if core.is_symlink() or version.is_symlink() or not is_brain_vault(root):
        raise DirectContextError(f"not an installed Brain vault: {candidate}")
    return root


def _profile_tools(config: dict, profile: str) -> frozenset[str]:
    profiles = config.get("vault", {}).get("profiles", {})
    definition = profiles.get(profile) if isinstance(profiles, dict) else None
    allowed = definition.get("allow") if isinstance(definition, dict) else None
    if not isinstance(allowed, list) or any(not isinstance(item, str) for item in allowed):
        raise DirectContextError(f"profile '{profile}' has an invalid allow-list")
    return frozenset(allowed)


def _brain_id(root: Path) -> str:
    try:
        entries = vault_registry.load_registry_entries()
    except vault_registry.RegistryReadError as exc:
        raise DirectContextError(str(exc)) from exc
    matches = sorted(
        entry.brain_id
        for entry in entries.values()
        if entry.kind == vault_registry.TYPE_LOCAL
        and Path(entry.value).resolve() == root
    )
    if len(matches) > 1:
        raise DirectContextError("selected Brain path has multiple local registry identities")
    if matches:
        return matches[0]
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]
    return f"local-path-{digest}"


def _resolve_workspace(root: Path, workspace_dir: Path | None) -> Path | None:
    if workspace_dir is None:
        return None
    if not workspace_dir.is_absolute() or workspace_dir.is_symlink():
        raise DirectContextError("direct workspace must be a regular absolute path")
    workspace = workspace_dir.resolve()
    from _bootstrap.workspace_binding import resolve_startup_target

    try:
        target = resolve_startup_target(
            workspace_env=str(workspace),
            vault_root_env=str(root),
            start_dir=workspace,
        )
    except Exception as exc:
        raise DirectContextError(f"direct workspace cannot be resolved: {exc}") from exc
    if (
        target.workspace_dir is None
        or Path(target.workspace_dir).resolve() != workspace
        or Path(target.vault_root).resolve() != root
    ):
        raise DirectContextError("direct workspace is not bound to the selected Brain")
    return workspace


@dataclass(frozen=True, slots=True)
class _Probe:
    provider_id: str
    operation: Callable[[], Availability]
    timeout_seconds: float = 1.5

    def probe(self) -> Availability:
        return self.operation()


@dataclass(frozen=True, slots=True)
class _TokenFactory:
    def next_token(self, previous_token: str | None) -> str:
        del previous_token
        return f"direct:{uuid.uuid4()}"


def _capability_refresher(
    root: Path,
    workspace: Path | None,
    config: dict,
    tier: DependencyTier,
    clock,
) -> CapabilityRefresher:
    operations = {
        "caller_filesystem": lambda: (
            Availability.AVAILABLE if workspace is not None else Availability.UNAVAILABLE
        ),
        "document_renderer": lambda: _managed_provider_availability(tier),
        "obsidian_cli": _probe_obsidian_cli,
        "semantic_retrieval": lambda: _probe_semantic_retrieval(root, config),
        "semantic_runtime": lambda: _managed_provider_availability(tier),
    }
    return CapabilityRefresher(
        probes=tuple(_Probe(name, operations[name]) for name in _LOCAL_PROVIDERS),
        clock=clock,
        token_factory=_TokenFactory(),
    )


def _relevant_providers(
    command_id: str | None,
    catalogue: ApplicationCatalogue,
) -> tuple[str, ...]:
    if command_id is None or command_id in {"command.list", "command.describe"}:
        return ()
    entry = next(
        (
            candidate
            for candidate in catalogue.entries
            if candidate.command_id == command_id
        ),
        None,
    )
    if entry is None:
        raise DirectContextError(f"unknown direct command: {command_id}")
    return tuple(sorted({*entry.required_providers, *entry.optional_providers}))


def _probe_obsidian_cli() -> Availability:
    try:
        import obsidian_cli

        return (
            Availability.AVAILABLE
            if obsidian_cli.check_available()
            else Availability.UNAVAILABLE
        )
    except Exception:
        return Availability.UNKNOWN


def _probe_semantic_retrieval(root: Path, config: dict) -> Availability:
    try:
        from _search.mode import mode_available

        available, _message = mode_available(str(root), "semantic", config=config)
        return Availability.AVAILABLE if available else Availability.UNAVAILABLE
    except Exception:
        return Availability.UNKNOWN


def _managed_provider_availability(tier: DependencyTier) -> Availability:
    return (
        Availability.AVAILABLE
        if tier is DependencyTier.MANAGED
        else Availability.UNAVAILABLE
    )


def _snapshot_token(
    states: tuple[tuple[str, Availability], ...],
    observed_at: datetime,
) -> str:
    payload = "|".join(
        [observed_at.isoformat(), *(f"{name}:{state.value}" for name, state in states)]
    )
    return "direct:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def direct_script_command_ids(
    catalogue: ApplicationCatalogue | None = None,
) -> tuple[str, ...]:
    """Return only application commands explicitly eligible for direct script use."""

    from _application.types import Projection

    catalogue = catalogue or current_application_catalogue()
    return tuple(
        entry.command_id
        for entry in catalogue.entries
        if Projection.SCRIPT in entry.eligible_projections
    )
