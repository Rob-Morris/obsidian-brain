"""Stdlib-only authoritative catalogue for machine-global launcher commands."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re


LAUNCHER_CATALOGUE_SCHEMA = "brain.launcher-catalogue/1"
_COMMAND_ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*\.[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_PROJECTIONS = ("cli", "launcher", "mcp", "python", "script")


@dataclass(frozen=True, slots=True)
class LauncherProjection:
    projection: str
    supported: bool
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.projection not in _PROJECTIONS:
            raise ValueError(f"unknown launcher projection: {self.projection}")
        if self.supported and self.reason is not None:
            raise ValueError("supported launcher projection cannot carry an exclusion reason")
        if not self.supported and (self.reason is None or not self.reason.strip()):
            raise ValueError("unsupported launcher projection requires a reason")


def _projection_contract() -> tuple[LauncherProjection, ...]:
    return (
        LauncherProjection("cli", True),
        LauncherProjection("launcher", True),
        LauncherProjection("mcp", False, "machine-global launcher authority is not selected-Brain MCP"),
        LauncherProjection("python", False, "launcher entries have no synthetic application executor"),
        LauncherProjection("script", False, "selected-Brain direct scripts do not own launcher lifecycle"),
    )


@dataclass(frozen=True, slots=True)
class LauncherEntry:
    command_id: str
    command_version: int
    owner_ref: str
    entry_point: tuple[str, ...]
    authority: str
    effect_class: str
    retry_class: str
    required_providers: tuple[str, ...] = ()
    projections: tuple[LauncherProjection, ...] = _projection_contract()
    owner: str = "launcher"
    dependency_tier: str = "bootstrap"
    locality: str = "machine_local"

    def __post_init__(self) -> None:
        if not _COMMAND_ID.fullmatch(self.command_id):
            raise ValueError(f"invalid launcher command identifier: {self.command_id}")
        if self.command_version < 1:
            raise ValueError("launcher command version must be positive")
        if self.owner != "launcher":
            raise ValueError("launcher entry owner must be launcher")
        if self.dependency_tier != "bootstrap" or self.locality != "machine_local":
            raise ValueError("launcher entries must remain bootstrap-tier and machine-local")
        if not self.owner_ref.strip() or not self.entry_point:
            raise ValueError("launcher entries require an owner reference and entry point")
        if any(not part.strip() for part in self.entry_point):
            raise ValueError("launcher entry-point parts must be non-empty")
        if self.authority not in {"reader", "operator"}:
            raise ValueError("launcher authority must be reader or operator")
        if self.effect_class not in {"none", "machine_mutation"}:
            raise ValueError("launcher effect class must be none or machine_mutation")
        if self.retry_class not in {"safe", "receipt_required"}:
            raise ValueError("launcher retry class is invalid")
        if self.effect_class == "none" and self.retry_class != "safe":
            raise ValueError("read-only launcher entry must be safely retryable")
        if self.effect_class != "none" and self.retry_class != "receipt_required":
            raise ValueError("mutating launcher entry requires an outcome receipt")
        if self.required_providers != tuple(sorted(set(self.required_providers))):
            raise ValueError("launcher provider names must be unique and sorted")
        projection_names = [projection.projection for projection in self.projections]
        if tuple(projection_names) != _PROJECTIONS:
            raise ValueError("launcher entry must describe every projection in canonical order")


@dataclass(frozen=True, slots=True)
class LauncherCatalogue:
    entries: tuple[LauncherEntry, ...]
    schema: str = LAUNCHER_CATALOGUE_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != LAUNCHER_CATALOGUE_SCHEMA:
            raise ValueError(f"unsupported launcher catalogue schema: {self.schema}")
        ids = [entry.command_id for entry in self.entries]
        if ids != sorted(ids):
            raise ValueError("launcher catalogue entries must be sorted by command_id")
        if len(ids) != len(set(ids)):
            raise ValueError("launcher catalogue command identifiers must be unique")
        owners = [entry.owner_ref for entry in self.entries]
        if len(owners) != len(set(owners)):
            raise ValueError("launcher catalogue owner references must be unique")

    @property
    def fingerprint(self) -> str:
        payload = [
            {
                "command_id": entry.command_id,
                "command_version": entry.command_version,
                "owner": entry.owner,
                "owner_ref": entry.owner_ref,
                "entry_point": entry.entry_point,
                "dependency_tier": entry.dependency_tier,
                "locality": entry.locality,
                "required_providers": entry.required_providers,
                "authority": entry.authority,
                "effect_class": entry.effect_class,
                "retry_class": entry.retry_class,
                "projections": tuple(
                    (projection.projection, projection.supported, projection.reason)
                    for projection in entry.projections
                ),
            }
            for entry in self.entries
        ]
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _read(command_id: str, owner_ref: str, *entry_point: str) -> LauncherEntry:
    return LauncherEntry(command_id, 1, owner_ref, entry_point, "reader", "none", "safe")


def _mutation(command_id: str, owner_ref: str, *entry_point: str) -> LauncherEntry:
    return LauncherEntry(
        command_id,
        1,
        owner_ref,
        entry_point,
        "operator",
        "machine_mutation",
        "receipt_required",
        required_providers=("caller_filesystem",),
    )


LAUNCHER_CATALOGUE = LauncherCatalogue(
    tuple(
        sorted(
            (
                _mutation("agent-skill.configure", "_launcher.agent_skill:configure", "brain", "agent-skill", "configure"),
                _mutation("brain.backfill", "_launcher.registry:backfill", "brain", "backfill"),
                _mutation("brain.clear-default", "_launcher.registry:clear_default", "brain", "clear-default"),
                _read("brain.doctor", "_launcher.doctor:doctor", "brain", "doctor"),
                _read("brain.get-default", "_launcher.registry:get_default", "brain", "get-default"),
                _mutation("brain.install", "cli:install", "brain", "install"),
                _read("brain.list", "_launcher.registry:list", "brain", "list"),
                _mutation("brain.prune", "_launcher.registry:prune", "brain", "prune"),
                _mutation("brain.register", "_launcher.registry:register", "brain", "register"),
                _read("brain.resolve", "_launcher.registry:resolve", "brain", "resolve"),
                _mutation("brain.set-default", "_launcher.registry:set_default", "brain", "set-default"),
                _mutation("brain.uninstall", "cli:uninstall", "brain", "uninstall"),
                _mutation("brain.unregister", "_launcher.registry:unregister", "brain", "unregister"),
                _mutation("brain.upgrade", "cli:upgrade", "brain", "upgrade"),
                _read("brain.version", "_launcher.version:version", "brain", "version"),
                _mutation("machine.migrate-legacy", "_launcher.machine:migrate_legacy", "brain", "machine", "migrate-legacy"),
                _mutation("machine.prune-runtimes", "_launcher.machine:prune_runtimes", "brain", "machine", "prune-runtimes"),
                _mutation("mcp.configure", "configure:mcp", "brain", "mcp", "configure"),
                _mutation("mcp.repair", "repair:mcp", "brain", "mcp", "repair"),
                LauncherEntry("operator.generate-key", 1, "_launcher.operator:generate_key", ("brain", "operator", "generate-key"), "operator", "none", "safe"),
                _mutation("runtime.repair", "repair:runtime", "brain", "runtime", "repair"),
                _read("runtime.resolve", "_launcher.managed_runtime:resolve", "brain", "runtime", "resolve"),
                _read("runtime.resolve-runnable", "_launcher.managed_runtime:resolve_runnable", "brain", "runtime", "resolve-runnable"),
            ),
            key=lambda entry: entry.command_id,
        )
    )
)
