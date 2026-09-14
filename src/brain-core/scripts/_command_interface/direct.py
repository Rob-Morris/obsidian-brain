"""Trusted discovery and context composition for direct local commands."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import threading
from typing import Callable
import uuid

from _application.availability import CapabilityRefresher
from _application.catalogue import ApplicationCatalogue
from _application.context import CapabilitySnapshot
from _application.registry import current_application_catalogue, current_request_resolver
from _application.access_session import AuthorisationSession
from _application.consent import ConsentError, ConsentIdentity, ConsentService
from _application.receipts import ReceiptOwnership
from _bootstrap.consent_state import MemoryStateStore
from .authorisation_config import resolve_authorisation_config, authorisation_config_signature
from .owner_authentication import OwnerAuthentication
from .consent_staging import ConsentContentPins
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
from .derived_snapshots import FileDerivedSnapshotStore
from .receipts import OwnedReceiptStore


_LOCAL_PROVIDERS = (
    "caller_filesystem",
    "document_renderer",
    "git_remote",
    "obsidian_cli",
    "semantic_retrieval",
    "semantic_runtime",
)


class DirectContextError(RuntimeError):
    """Trusted direct-command context could not be resolved safely."""


@dataclass(frozen=True, slots=True)
class DirectIdentity:
    config: dict
    profile: str
    principal: str
    allowed_tools: frozenset[str]
    resolved: object


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
    invocation_id: str | None = None,
    clock=None,
    owner_attachment=None,
    owner_unavailable_reason: str | None = None,
    transport_identity=None,
    owner_initialisation_allowed: bool = False,
    operation_id: str | None = None,
):
    """Resolve one fresh direct-process invocation context without hand-off."""

    return DirectContextComposer(
        vault_root=vault_root,
        catalogue=catalogue,
        operator_key=operator_key,
        workspace_dir=workspace_dir,
        clock=clock,
        owner_attachment=owner_attachment,
        owner_unavailable_reason=owner_unavailable_reason,
        transport_identity=transport_identity,
        owner_initialisation_allowed=owner_initialisation_allowed,
    ).compose(
        command_id=command_id,
        dry_run=dry_run,
        invocation_id=invocation_id,
        operation_id=operation_id,
    )


class DirectContextComposer:
    """Compose invocations while retaining safe process-scoped read state."""

    def __init__(
        self,
        *,
        vault_root: Path,
        catalogue: ApplicationCatalogue | None = None,
        operator_key: str | None = None,
        workspace_dir: Path | None = None,
        clock=None,
        derived_snapshots=None,
        session_mirror=None,
        owner_attachment=None,
        owner_unavailable_reason: str | None = None,
        transport_identity=None,
        owner_initialisation_allowed: bool = False,
    ):
        self._clock = clock or SystemClock()
        self._root = _require_vault(vault_root)
        if owner_attachment is not None and owner_unavailable_reason is not None:
            raise DirectContextError("an attached owner cannot also be unavailable")
        self._owner_attachment = owner_attachment
        self._owner_store = owner_attachment.connect(self._root) if owner_attachment is not None else None
        self.owner_unavailable_reason = owner_unavailable_reason
        self._closed = False
        self._context_kind = (transport_identity.kind if transport_identity is not None else
                              owner_attachment.kind if owner_attachment is not None else "standalone")
        self._context_id = (transport_identity.context_id if transport_identity is not None else
                            self._owner_store.identity.context_id if self._owner_store is not None else str(uuid.uuid4()))
        if self._owner_store is not None and (
                self._context_id != self._owner_store.identity.context_id
                or self._context_kind != owner_attachment.kind):
            raise DirectContextError("transport identity does not match its private owner")
        self._consent_store = self._owner_store if self._owner_store is not None else MemoryStateStore()
        self._owner_initialisation_allowed = owner_initialisation_allowed
        self._owner_auth = None
        self._catalogue = catalogue or current_application_catalogue()
        self._operator_key = operator_key
        self._workspace_dir = workspace_dir
        self._derived_snapshots = (
            derived_snapshots
            if derived_snapshots is not None
            else FileDerivedSnapshotStore(self._root)
        )
        self._session_mirror = session_mirror
        self._lock = threading.RLock()
        self._identity_signature = None
        self._identity: DirectIdentity | None = None
        self._brain_id_signature = None
        self._cached_brain_id: str | None = None
        self._capability_state: tuple[
            DirectIdentity,
            Path | None,
            DependencyTier,
            CapabilityRefresher,
            CapabilitySnapshot,
        ] | None = None

    @property
    def owner_store(self):
        """Private process-owned state for trusted authorisation composition."""
        if self._closed:
            raise DirectContextError("direct context composer is closed")
        return self._owner_store

    @property
    def owner_kind(self) -> str | None:
        return self._owner_attachment.kind if self._owner_attachment is not None else None

    def close(self) -> None:
        """Release this caller's channel without ending the proxy/job owner's lifetime."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        if self._owner_attachment is not None:
            self._owner_attachment.close()

    @property
    def catalogue(self) -> ApplicationCatalogue:
        return self._catalogue

    def identity(self) -> DirectIdentity:
        if self._closed:
            raise DirectContextError("direct context composer is closed")
        with self._lock:
            if self._owner_store is not None and self._owner_auth is None:
                self._owner_auth = OwnerAuthentication(self._owner_store, self._owner_store.identity,
                    brain_id=self._brain_id(), kind=self._context_kind)
            binding = (self._owner_auth.binding(required=not self._owner_initialisation_allowed)
                       if self._owner_auth is not None else None)
            signature = (authorisation_config_signature(self._root), binding)
            if self._identity is not None and signature == self._identity_signature:
                return self._identity
            try:
                identity = resolve_direct_identity(vault_root=self._root, catalogue=self._catalogue,
                    operator_key=self._operator_key, delegated_binding=binding)
            except brain_config.OperatorBindingRevoked:
                if self._owner_auth is not None:
                    self._owner_auth.revoke()
                raise
            if self._owner_auth is not None and binding is None:
                self._owner_auth.seed(identity.resolved.authentication_binding)
                self._owner_initialisation_allowed = False
                signature = (signature[0], identity.resolved.authentication_binding)
            self._identity, self._identity_signature = identity, signature
            return identity

    def _consent_identity(self, identity):
        return ConsentIdentity(self._brain_id(), str(self._root), identity.principal,
            self._context_id, self._context_kind, identity.resolved.permission_generation,
            f"{self._catalogue.interface_epoch}:{self._catalogue.fingerprint}")

    def _authorisation(self, identity):
        def refresh():
            current = self.identity()
            return self._consent_identity(current), current.resolved.policy
        service = ConsentService(self._consent_store, self._consent_identity(identity), identity.resolved.policy,
            {entry.command_id: entry.command_version for entry in self._catalogue.entries}, refresh=refresh)
        ownership = ReceiptOwnership(self._brain_id(), identity.principal, self._context_kind,
            None if self._context_kind == "standalone" else self._context_id)
        receipts = OwnedReceiptStore(self._root, self._clock, ownership=ownership)
        def content_for(namespace):
            if self._owner_store is None:
                raise ConsentError("context_unavailable", "Prepared content requires a private owner context.")
            owner = self._owner_store.identity
            return ConsentContentPins(Path(owner.private_directory), self._owner_store,
                namespace=namespace, coordination_path=Path(owner.coordination_path))
        return AuthorisationSession(service, self._catalogue, current_request_resolver(), receipts, content_for,
            configuration_provider=lambda: _access_configuration(self.identity().resolved),
            context_available=self._owner_store is not None,
            unavailable_reason=self.owner_unavailable_reason,
            source="host-request" if self._context_kind == "mcp-instance" else "cli-request")

    def compose(
        self,
        *,
        command_id: str | None = None,
        dry_run: bool = False,
        invocation_id: str | None = None,
        operation_id: str | None = None,
    ):
        """Compose fresh authority/capability state over cached immutable inputs."""

        identity = self.identity()
        merged = identity.config
        profile = identity.profile
        allowed_tools = identity.allowed_tools
        authorisation = self._authorisation(identity)
        workspace = _resolve_workspace(self._root, self._workspace_dir)
        tier = (
            DependencyTier.MANAGED
            if current_process_in_managed_runtime(self._root)
            else DependencyTier.PORTABLE
        )
        refresher, baseline = self._capability_refresher_for(
            identity,
            workspace,
            tier,
        )
        relevant_providers = _relevant_providers(command_id, self._catalogue)
        if relevant_providers and command_id in allowed_tools:
            snapshot = refresher.refresh(relevant_providers)
        else:
            snapshot = baseline
        states = tuple(
            (capability.name, capability.availability)
            for capability in snapshot.capabilities
        )
        if invocation_id is None:
            invocation_id = f"direct-{uuid.uuid4()}"
        elif (
            not isinstance(invocation_id, str)
            or not invocation_id.strip()
            or len(invocation_id) > 128
        ):
            raise DirectContextError("trusted invocation identity is invalid")
        receipt_store = authorisation.receipts
        return compose_local_context(
            vault_root=self._root,
            brain_id=self._brain_id(),
            profile=profile,
            dependency_tier=tier,
            provider_ids=_LOCAL_PROVIDERS,
            capability_states=states,
            snapshot_token=snapshot.token,
            snapshot_freshness=SnapshotFreshness.FRESH,
            snapshot_observed_at=snapshot.observed_at,
            correlation_id=invocation_id,
            invocation_id=invocation_id,
            receipt_store=receipt_store,
            authorisation=authorisation,
            operation_id=operation_id,
            workspace_dir=workspace,
            capability_snapshots=refresher,
            dry_run=dry_run,
            clock=self._clock,
            derived_snapshots=self._derived_snapshots,
            session_mirror=self._session_mirror,
        )

    def _brain_id(self) -> str:
        signature = _path_signature(Path(vault_registry.registry_path()))
        with self._lock:
            if (
                self._cached_brain_id is not None
                and signature == self._brain_id_signature
            ):
                return self._cached_brain_id
            brain_id = resolve_direct_brain_id(self._root)
            self._cached_brain_id = brain_id
            self._brain_id_signature = signature
            return brain_id

    def _capability_refresher_for(
        self,
        identity: DirectIdentity,
        workspace: Path | None,
        tier: DependencyTier,
    ) -> tuple[CapabilityRefresher, CapabilitySnapshot]:
        """Retain page snapshots only while their trusted inputs remain current."""

        with self._lock:
            state = self._capability_state
            if (
                state is not None
                and state[0] is identity
                and state[1] == workspace
                and state[2] is tier
            ):
                return state[3], state[4]
            refresher = _capability_refresher(
                self._root,
                workspace,
                identity.config,
                tier,
                self._clock,
            )
            baseline = refresher.refresh(())
            if identity is not self._identity:
                # A newer config generation won a concurrent identity refresh.
                # The older invocation may finish, but must not republish its
                # pagination store as the process-scoped current generation.
                return refresher, baseline
            self._capability_state = (
                identity,
                workspace,
                tier,
                refresher,
                baseline,
            )
            return refresher, baseline


def _access_configuration(resolved):
    """Project only authorisation settings; never operator registrations or keys."""
    from _application.access_contracts import (AccessConfiguration,
        AccessConfigurationDiagnostic, InitialAuthorisationOverride)
    defaults = resolved.config.get("defaults", {}).get("access", {})
    initial = defaults.get("initial", {"mode": "normal"})
    return AccessConfiguration(initial_mode=resolved.policy.initial_mode,
        initial_commands=tuple(initial["commands"]) if initial["mode"] == "explicit" else None,
        initial_source=resolved.initial_source,
        overrides=tuple(InitialAuthorisationOverride(command, enabled)
                        for command, enabled in sorted(defaults.get("overrides", {}).items())),
        overrides_source=resolved.overrides_source,
        request_policy=resolved.config.get("vault", {}).get("access", {}).get("request_policy", "allowed"),
        effective_request_policy=resolved.policy.request_policy,
        request_policy_source=resolved.request_policy_source,
        diagnostics=tuple(AccessConfigurationDiagnostic(item.code, item.setting, item.source, item.message)
                          for item in resolved.diagnostics), revision=resolved.revision)


def resolve_direct_identity(*, vault_root: Path, catalogue: ApplicationCatalogue,
                            operator_key: str | None, delegated_binding=None) -> DirectIdentity:
    """Resolve current config through its one authentication/policy owner."""
    resolved = resolve_authorisation_config(vault_root=vault_root, catalogue=catalogue,
        operator_key=operator_key, delegated_binding=delegated_binding)
    return DirectIdentity(resolved.config, resolved.profile, resolved.principal,
                          resolved.policy.permissions, resolved)


def initialise_attached_job_owner(*, vault_root, owner_attachment, transport_identity,
                                   operator_key=None, workspace_dir=None) -> None:
    """Selected-Brain bootstrap authenticates before the supervisor launches its root."""
    if transport_identity.kind != "cli-job" or owner_attachment.kind != "cli-job":
        raise DirectContextError("job initialisation requires a private job attachment")
    composer = DirectContextComposer(vault_root=vault_root, operator_key=operator_key,
        workspace_dir=workspace_dir, owner_attachment=owner_attachment,
        transport_identity=transport_identity, owner_initialisation_allowed=True)
    try:
        composer._authorisation(composer.identity()).service.generation()
    finally:
        composer.close()


def _path_signature(path: Path):
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        return ("error", type(exc).__name__, getattr(exc, "errno", None))
    return (stat.st_dev, stat.st_ino, stat.st_mode, stat.st_size, stat.st_mtime_ns)


def _require_vault(candidate: Path) -> Path:
    if candidate.is_symlink():
        raise DirectContextError("selected Brain vault cannot be a symlink")
    root = candidate.resolve()
    core = root / ".brain-core"
    version = core / "VERSION"
    if core.is_symlink() or version.is_symlink() or not is_brain_vault(root):
        raise DirectContextError(f"not an installed Brain vault: {candidate}")
    return root


def resolve_direct_brain_id(root: Path) -> str:
    """Resolve the canonical registry identity or stable selected-path identity."""
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
    from _bootstrap.workspace_binding import resolve_brain_target

    try:
        target = resolve_brain_target(
            workspace_env=str(workspace),
            vault_root_env=str(root),
            start_dir=workspace,
        )
    except Exception as exc:
        raise DirectContextError(f"direct workspace cannot be resolved: {exc}") from exc
    target_vault = Path(target.vault_root).resolve()
    if (
        target.source == "vault_self"
        and target.workspace_dir is None
        and workspace == root
        and target_vault == root
    ):
        return workspace
    if (
        target.workspace_dir is None
        or Path(target.workspace_dir).resolve() != workspace
        or target_vault != root
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
        "git_remote": lambda: (
            Availability.AVAILABLE
            if shutil.which("git") is not None
            else Availability.UNAVAILABLE
        ),
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
