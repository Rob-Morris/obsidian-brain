"""Typed machine-global Brain install, uninstall and upgrade owners."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import json
from pathlib import Path
import re
import shutil
from typing import ClassVar

from .context import LauncherContext
from .cutover import CutoverPreflight, CutoverPreflightError, preflight as cutover_preflight
from .contracts import (
    CapabilityUnavailableDetails,
    CommandError,
    CommittedEffect,
    Error,
    ErrorCode,
    Ok,
    Partial,
    RequestErrorDetails,
    no_effect_error,
)


_BRAIN_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class LifecycleStatus(str, Enum):
    NOOP = "noop"
    PLANNED = "planned"
    CHANGED = "changed"


class InstallMode(str, Enum):
    FRESH = "fresh"
    EXISTING_VAULT = "existing_vault"


class InstallMcpScope(str, Enum):
    PROJECT = "project"
    USER = "user"
    SKIP = "skip"


class InstallClient(str, Enum):
    CLAUDE = "claude"
    CODEX = "codex"
    ALL = "all"


class UpgradePolicy(str, Enum):
    AUTO = "auto"
    ENABLE = "enable"
    DISABLE = "disable"


@dataclass(frozen=True, slots=True)
class LifecycleStep:
    name: str
    status: LifecycleStatus
    message: str

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.message.strip():
            raise ValueError("lifecycle steps require a name and message")
        if not isinstance(self.status, LifecycleStatus):
            raise ValueError("lifecycle step status must be closed and typed")


@dataclass(frozen=True, slots=True)
class BrainInstallPayload:
    status: LifecycleStatus
    vault_root: str
    brain_id: str
    brain_core_version: str
    mode: InstallMode
    steps: tuple[LifecycleStep, ...]

    def __post_init__(self) -> None:
        _absolute_result_path(self.vault_root, "install vault_root")
        _brain_id(self.brain_id)
        if not self.brain_core_version.strip():
            raise ValueError("install result requires a Brain Core version")
        if not isinstance(self.mode, InstallMode):
            raise ValueError("install mode must be closed and typed")
        _steps(self.steps)


@dataclass(frozen=True, slots=True)
class BrainUninstallPayload:
    status: LifecycleStatus
    vault_root: str
    removed_paths: tuple[str, ...]
    removed_brain_ids: tuple[str, ...]
    steps: tuple[LifecycleStep, ...]

    def __post_init__(self) -> None:
        _absolute_result_path(self.vault_root, "uninstall vault_root")
        if any(not Path(path).is_absolute() for path in self.removed_paths):
            raise ValueError("uninstall removed paths must be absolute")
        if self.removed_paths != tuple(sorted(set(self.removed_paths))):
            raise ValueError("uninstall removed paths must be ordered and unique")
        for brain_id in self.removed_brain_ids:
            _brain_id(brain_id)
        if self.removed_brain_ids != tuple(sorted(set(self.removed_brain_ids))):
            raise ValueError("uninstall Brain IDs must be ordered and unique")
        _steps(self.steps)


@dataclass(frozen=True, slots=True)
class BrainUpgradePayload:
    status: LifecycleStatus
    vault_root: str
    old_version: str | None
    new_version: str
    files_added: int
    files_modified: int
    files_removed: int
    migrations: tuple[str, ...]
    preflight: CutoverPreflight
    cli_distribution_fingerprint: str | None
    post_commit_reconciliation: tuple[LifecycleStep, ...]

    def __post_init__(self) -> None:
        _absolute_result_path(self.vault_root, "upgrade vault_root")
        if self.old_version is not None and not self.old_version.strip():
            raise ValueError("upgrade old_version must be non-empty or null")
        if not self.new_version.strip():
            raise ValueError("upgrade new_version must be non-empty")
        if any(value < 0 for value in (self.files_added, self.files_modified, self.files_removed)):
            raise ValueError("upgrade file counts cannot be negative")
        if any(not item.strip() for item in self.migrations):
            raise ValueError("upgrade migration identifiers must be non-empty")
        if not isinstance(self.preflight, CutoverPreflight):
            raise ValueError("upgrade result requires a checked cutover preflight")
        if (
            self.cli_distribution_fingerprint is not None
            and not self.cli_distribution_fingerprint.startswith("sha256:")
        ):
            raise ValueError("upgrade CLI distribution fingerprint is invalid")
        _steps(self.post_commit_reconciliation)


@dataclass(frozen=True, slots=True)
class BrainInstallRequest:
    COMMAND_ID: ClassVar[str] = "brain.install"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = BrainInstallPayload

    vault_root: Path
    brain_id: str
    mcp_scope: InstallMcpScope = InstallMcpScope.PROJECT
    client: InstallClient = InstallClient.ALL

    def __post_init__(self) -> None:
        _absolute_request_path(self.vault_root, "vault_root")
        _brain_id(self.brain_id)
        if not isinstance(self.mcp_scope, InstallMcpScope):
            raise ValueError("install MCP scope must be closed and typed")
        if not isinstance(self.client, InstallClient):
            raise ValueError("install client must be closed and typed")


@dataclass(frozen=True, slots=True)
class BrainUninstallRequest:
    COMMAND_ID: ClassVar[str] = "brain.uninstall"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = BrainUninstallPayload


@dataclass(frozen=True, slots=True)
class BrainUpgradeRequest:
    COMMAND_ID: ClassVar[str] = "brain.upgrade"
    COMMAND_VERSION: ClassVar[int] = 2
    RESULT_TYPE: ClassVar[type] = BrainUpgradePayload

    force: bool = False
    definition_sync: UpgradePolicy = UpgradePolicy.AUTO
    dependency_sync: UpgradePolicy = UpgradePolicy.AUTO
    acknowledge_global_cli_cutover: bool = False
    excluded_stale_brain_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.force, bool):
            raise ValueError("upgrade force must be boolean")
        if not isinstance(self.definition_sync, UpgradePolicy):
            raise ValueError("definition sync policy must be closed and typed")
        if not isinstance(self.dependency_sync, UpgradePolicy):
            raise ValueError("dependency sync policy must be closed and typed")
        if not isinstance(self.acknowledge_global_cli_cutover, bool):
            raise ValueError("global CLI cutover acknowledgement must be boolean")
        if self.excluded_stale_brain_ids != tuple(
            sorted(set(self.excluded_stale_brain_ids))
        ):
            raise ValueError("stale Brain exclusions must be sorted and unique")
        for brain_id in self.excluded_stale_brain_ids:
            _brain_id(brain_id)


def _absolute_request_path(value: object, field: str) -> None:
    if not isinstance(value, Path) or not value.is_absolute():
        raise ValueError(f"{field} must be an absolute Path")


def _absolute_result_path(value: object, field: str) -> None:
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError(f"{field} must be absolute")


def _brain_id(value: object) -> None:
    if not isinstance(value, str) or not _BRAIN_ID.fullmatch(value):
        raise ValueError("brain_id must be a canonical lower-case slug")


def _steps(value: object) -> None:
    if not isinstance(value, tuple) or any(not isinstance(step, LifecycleStep) for step in value):
        raise ValueError("lifecycle result steps must be typed")


def _source(context: LauncherContext) -> tuple[Path, Path] | None:
    root = context.distribution_root
    if root is None:
        return None
    core = root / "src" / "brain-core"
    template = root / "template-vault"
    if not (core / "VERSION").is_file() or not template.is_dir():
        return None
    return root, core


def _source_version(core: Path) -> str:
    version = (core / "VERSION").read_text(encoding="utf-8").strip()
    if not version:
        raise ValueError("launcher distribution has an empty Brain Core version")
    return version


def _install_mode(vault: Path) -> InstallMode:
    return InstallMode.EXISTING_VAULT if vault.is_dir() and any(vault.iterdir()) else InstallMode.FRESH


def _raw_steps(raw_steps: list[dict]) -> tuple[LifecycleStep, ...]:
    status_map = {
        "noop": LifecycleStatus.NOOP,
        "planned": LifecycleStatus.PLANNED,
        "changed": LifecycleStatus.CHANGED,
    }
    return tuple(
        LifecycleStep(step["name"], status_map.get(step["status"], LifecycleStatus.NOOP), step["message"])
        for step in raw_steps
        if step.get("status") != "error"
    )


def _install_effects(request: BrainInstallRequest, raw_steps: list[dict]) -> tuple[CommittedEffect, ...]:
    effects = []
    for step in raw_steps:
        if step.get("status") != "changed":
            continue
        subject = step.get("venv_dir") or step.get("path") or request.vault_root
        effects.append(CommittedEffect(request.COMMAND_ID, f"{step['name']}:{subject}"))
    return tuple(effects)


def _install_preview(context: LauncherContext, request: BrainInstallRequest, source_root: Path, core: Path):
    from install import _destination_error
    import vault_registry

    vault = request.vault_root
    error = _destination_error(vault, source_root)
    if error is not None:
        return no_effect_error(type(request), ErrorCode.INVALID_REQUEST, error, "vault_root")
    if (vault / ".brain-core").exists():
        return no_effect_error(
            type(request),
            ErrorCode.CONFLICT,
            "Brain is already installed at this path; use brain.upgrade.",
            "vault_root",
        )
    try:
        registration = vault_registry.preview_register_action(
            vault,
            brain_id=request.brain_id,
        )
    except (
        vault_registry.RegistryReadError,
        vault_registry.RegistryConflictError,
        ValueError,
    ) as exc:
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))
    steps = [
        LifecycleStep(
            "vault_scaffold",
            LifecycleStatus.PLANNED,
            "Would install the Brain vault scaffold.",
        ),
        LifecycleStep(
            "machine_resolution_runtime",
            LifecycleStatus.PLANNED,
            "Would install the machine resolution runtime.",
        ),
        LifecycleStep(
            "vault_registry",
            LifecycleStatus.PLANNED if registration.changed else LifecycleStatus.NOOP,
            (
                f"Would register local Brain '{registration.brain_id}'."
                if registration.changed
                else "Brain registry entry already matches."
            ),
        ),
        LifecycleStep(
            "git_ignore",
            LifecycleStatus.PLANNED,
            "Would converge Brain-managed ignore rules.",
        ),
    ]
    if request.mcp_scope is not InstallMcpScope.SKIP:
        steps.extend(
            (
                LifecycleStep(
                    "managed_runtime",
                    LifecycleStatus.PLANNED,
                    "Would provision the managed runtime.",
                ),
                LifecycleStep(
                    "mcp_transport",
                    LifecycleStatus.PLANNED,
                    "Would configure the requested MCP clients.",
                ),
            )
        )
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        BrainInstallPayload(
            LifecycleStatus.PLANNED,
            str(vault),
            request.brain_id,
            _source_version(core),
            _install_mode(vault),
            tuple(steps),
        ),
    )


def execute_install(context: LauncherContext, request: BrainInstallRequest):
    source = _source(context)
    if source is None:
        return _distribution_unavailable(type(request))
    source_root, core = source
    if context.dry_run:
        try:
            return _install_preview(context, request, source_root, core)
        except (OSError, ValueError) as exc:
            return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))

    from install import install_vault_action

    mode = _install_mode(request.vault_root)
    result = install_vault_action(
        request.vault_root,
        source_root=source_root,
        launcher=context.launcher_python,
        mcp_scope=request.mcp_scope.value,
        client=request.client.value,
        brain_id=request.brain_id,
    )
    raw_steps = list(result.get("steps", []))
    effects = _install_effects(request, raw_steps)
    errors = [step for step in raw_steps if step.get("status") == "error"]
    if errors:
        message = errors[-1].get("message") or "Brain installation did not complete."
        error = CommandError(ErrorCode.CONFLICT, message, RequestErrorDetails(None, message))
        if effects:
            return Partial(request.COMMAND_ID, request.COMMAND_VERSION, error, effects)
        return Error(request.COMMAND_ID, request.COMMAND_VERSION, error)
    status = LifecycleStatus.CHANGED if effects else LifecycleStatus.NOOP
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        BrainInstallPayload(
            status,
            str(request.vault_root),
            request.brain_id,
            _source_version(core),
            mode,
            _raw_steps(raw_steps),
        ),
        effects,
    )


def _system_paths(vault: Path) -> tuple[Path, ...]:
    return tuple(
        path
        for path in (vault / ".brain-core", vault / ".brain", vault / ".venv")
        if path.exists() or path.is_symlink()
    )


def _preflight_uninstall(vault: Path) -> str | None:
    if not (vault / ".brain-core" / "VERSION").is_file():
        return "The selected path is not an installed Brain."
    for path in _system_paths(vault):
        if path.is_symlink():
            return f"Refusing to uninstall symlinked Brain system state: {path}"
        if not path.is_dir():
            return f"Brain system path is not a directory: {path}"
    return None


def _removal_requests():
    from . import mcp

    return (
        mcp.McpConfigureRequest(
            client=mcp.McpClient.ALL,
            scope=mcp.McpScope.PROJECT,
            action=mcp.McpConfigureAction.REMOVE,
        ),
        mcp.McpConfigureRequest(
            client=mcp.McpClient.CLAUDE,
            scope=mcp.McpScope.LOCAL,
            action=mcp.McpConfigureAction.REMOVE,
        ),
    )


def _mcp_cleanup(context: LauncherContext):
    from . import mcp

    assert context.current_vault is not None
    cleanup_context = replace(context, caller_dir=context.current_vault)
    results = []
    for request in _removal_requests():
        result = mcp.execute_configure(cleanup_context, request)
        results.append(result)
        if isinstance(result, (Error, Partial)):
            break
    return tuple(results)


def _map_effects(command_id: str, results) -> list[CommittedEffect]:
    effects: list[CommittedEffect] = []
    for result in results:
        for effect in getattr(result, "committed_effects", ()):
            effects.append(CommittedEffect(command_id, effect.subject))
    return effects


def execute_uninstall(context: LauncherContext, request: BrainUninstallRequest):
    vault = context.current_vault
    if vault is None:
        return no_effect_error(type(request), ErrorCode.NOT_FOUND, "No current Brain is selected.", "current_vault")
    error = _preflight_uninstall(vault)
    if error is not None:
        return no_effect_error(type(request), ErrorCode.CONFLICT, error)

    from .registry import BrainUnregisterRequest, execute_unregister

    paths = _system_paths(vault)
    cleanup_results = _mcp_cleanup(context)
    effects = _map_effects(request.COMMAND_ID, cleanup_results)
    failed_cleanup = next((result for result in cleanup_results if isinstance(result, (Error, Partial))), None)
    if failed_cleanup is not None:
        if effects:
            return Partial(request.COMMAND_ID, request.COMMAND_VERSION, failed_cleanup.error, tuple(effects))
        return Error(request.COMMAND_ID, request.COMMAND_VERSION, failed_cleanup.error)

    registry_result = execute_unregister(context, BrainUnregisterRequest(vault))
    effects.extend(_map_effects(request.COMMAND_ID, (registry_result,)))
    if isinstance(registry_result, (Error, Partial)):
        if effects:
            return Partial(request.COMMAND_ID, request.COMMAND_VERSION, registry_result.error, tuple(effects))
        return Error(request.COMMAND_ID, request.COMMAND_VERSION, registry_result.error)

    planned_paths = tuple(sorted(str(path) for path in paths))
    removed_ids = registry_result.result.removed_brain_ids
    if context.dry_run:
        steps = tuple(
            LifecycleStep("system_path", LifecycleStatus.PLANNED, f"Would remove {path}.")
            for path in planned_paths
        )
        return Ok(
            request.COMMAND_ID,
            request.COMMAND_VERSION,
            BrainUninstallPayload(
                LifecycleStatus.PLANNED,
                str(vault),
                planned_paths,
                removed_ids,
                steps,
            ),
        )

    removed: list[str] = []
    for path in paths:
        shutil.rmtree(path)
        removed.append(str(path))
        effects.append(CommittedEffect(request.COMMAND_ID, f"directory:{path}"))
    status = LifecycleStatus.CHANGED if effects else LifecycleStatus.NOOP
    steps = tuple(
        LifecycleStep("system_path", LifecycleStatus.CHANGED, f"Removed {path}.")
        for path in sorted(removed)
    )
    return Ok(
        request.COMMAND_ID,
        request.COMMAND_VERSION,
        BrainUninstallPayload(status, str(vault), tuple(sorted(removed)), removed_ids, steps),
        tuple(effects),
    )


def _policy(value: UpgradePolicy) -> bool | None:
    return {
        UpgradePolicy.AUTO: None,
        UpgradePolicy.ENABLE: True,
        UpgradePolicy.DISABLE: False,
    }[value]


def _distribution_unavailable(request_type):
    return Error(
        request_type.COMMAND_ID,
        request_type.COMMAND_VERSION,
        CommandError(
            ErrorCode.CAPABILITY_UNAVAILABLE,
            "The launcher has no complete trusted Brain distribution.",
            CapabilityUnavailableDetails(
                "machine_local", ("launcher_distribution",), True
            ),
        ),
    )


def _migration_ids(result: dict) -> tuple[str, ...]:
    values = []
    for key in ("precompile_patch_migrations", "migrations"):
        for item in result.get(key, []):
            if isinstance(item, dict):
                value = item.get("id") or item.get("version") or item.get("name")
            else:
                value = item
            if value is not None:
                values.append(str(value))
    return tuple(values)


def _load_upgrade(core: Path):
    import importlib.util

    path = core / "scripts" / "upgrade.py"
    spec = importlib.util.spec_from_file_location("_brain_launcher_upgrade", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load trusted Brain upgrader: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source_interface_contract(
    source_root: Path, core: Path
) -> tuple[str, str, int, int]:
    from _distribution import source_versions

    version = _source_version(core)
    cli_version = source_versions(source_root).cli_version
    catalogue = json.loads(
        (core / "command-catalogue.json").read_text(encoding="utf-8")
    )
    epoch = catalogue.get("interface_epoch")
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 1:
        raise ValueError("source command catalogue has an invalid interface epoch")
    protocol_text = (core / "brain_mcp" / "_interface_protocol.py").read_text(
        encoding="utf-8"
    )
    match = re.search(r"^PROXY_PROTOCOL = ([0-9]+)$", protocol_text, re.MULTILINE)
    if match is None:
        raise ValueError("source proxy protocol declaration is invalid")
    return version, cli_version, epoch, int(match.group(1))


def _checked_preflight(
    context: LauncherContext,
    request: BrainUpgradeRequest,
    source_root: Path,
    core: Path,
) -> CutoverPreflight:
    version, cli_version, epoch, protocol = _source_interface_contract(
        source_root, core
    )
    assert context.current_vault is not None
    return cutover_preflight(
        selected_vault=context.current_vault,
        source_brain_core_version=version,
        old_cli_version=context.cli_version,
        new_cli_version=cli_version,
        interface_epoch=epoch,
        proxy_protocol=protocol,
        acknowledge_global_cli_cutover=request.acknowledge_global_cli_cutover,
        excluded_stale_brain_ids=request.excluded_stale_brain_ids,
    )


def _reconciliation_steps(result: dict) -> tuple[LifecycleStep, ...]:
    steps = []
    for item in result.get("skill_reconciliation", ()):
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        steps.append(
            LifecycleStep(
                f"skill_override:{item['name']}",
                LifecycleStatus.CHANGED,
                f"Collapsed the clean tracked user override for {item['name']} "
                "to the matching upgraded core skill.",
            )
        )
    if isinstance(result.get("sync_error"), str):
        steps.append(
            LifecycleStep(
                "definition_sync",
                LifecycleStatus.CHANGED,
                result["sync_error"],
            )
        )
    for name, key in (
        ("managed_runtime", "central_runtime"),
        ("machine_resolution_runtime", "machine_resolution_runtime"),
        ("retrieval_assets", "retrieval_asset_repair"),
    ):
        value = result.get(key)
        if value is None:
            continue
        outcome = value.get("outcome") if isinstance(value, dict) else None
        if outcome in {"error", "partial", "unknown"}:
            status = LifecycleStatus.CHANGED
            message = value.get("message") or f"{name} reconciliation requires recovery."
        else:
            status = LifecycleStatus.CHANGED if outcome in {"updated", "changed", "ok"} else LifecycleStatus.NOOP
            message = (
                value.get("message")
                if isinstance(value, dict) and isinstance(value.get("message"), str)
                else f"{name} reconciliation completed."
            )
        steps.append(LifecycleStep(name, status, message))
    return tuple(steps)


def _reconciliation_failed(result: dict) -> bool:
    return isinstance(result.get("sync_error"), str) or any(
        isinstance(result.get(key), dict)
        and result[key].get("outcome") in {"error", "partial", "unknown"}
        for key in (
            "central_runtime",
            "machine_resolution_runtime",
            "retrieval_asset_repair",
        )
    )


def execute_upgrade(context: LauncherContext, request: BrainUpgradeRequest):
    source = _source(context)
    vault = context.current_vault
    if source is None:
        return _distribution_unavailable(type(request))
    if vault is None or not (vault / ".brain-core" / "VERSION").is_file():
        return no_effect_error(
            type(request),
            ErrorCode.NOT_FOUND,
            "No installed current Brain is selected.",
            "current_vault",
        )
    source_root, core = source
    try:
        preflight = _checked_preflight(context, request, source_root, core)
        upgrade_script = _load_upgrade(core)
    except (CutoverPreflightError, OSError, RuntimeError, ValueError) as exc:
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))

    installed_distribution = None

    def commit_cutover(_upgrade_result):
        nonlocal installed_distribution
        from _distribution import install_from_source

        installed_distribution = install_from_source(source_root, context.cli_binary)
        return {
            "cli_binary": str(installed_distribution.cli_binary),
            "distribution_root": str(installed_distribution.distribution_root),
            "manifest_fingerprint": installed_distribution.manifest_fingerprint,
        }

    result = upgrade_script.upgrade(
        str(vault),
        str(core),
        force=request.force,
        dry_run=context.dry_run,
        sync=_policy(request.definition_sync),
        sync_deps=_policy(request.dependency_sync),
        commit_callback=None if context.dry_run else commit_cutover,
    )
    status = result.get("status")
    if status == "error":
        commit = result.get("cutover_commit")
        if result.get("rollback_verified") is False or (
            isinstance(commit, dict)
            and commit.get("external_rollback_verified") is False
        ):
            raise RuntimeError(
                result.get("message") or "Brain/CLI cutover rollback could not be proven."
            )
        return no_effect_error(
            type(request),
            ErrorCode.CONFLICT,
            result.get("message") or "Brain upgrade was rolled back.",
        )
    if status not in {"ok", "skipped"}:
        raise RuntimeError(f"Brain upgrader returned an unknown status: {status!r}")
    if result.get("new_version") != preflight.source_brain_core_version:
        raise RuntimeError(
            "Brain upgrader returned a version that does not match cutover preflight"
        )
    if not context.dry_run and status == "ok" and installed_distribution is None:
        raise RuntimeError(
            "Brain upgrader did not execute the coordinated CLI commit callback"
        )
    lifecycle_status = (
        LifecycleStatus.PLANNED
        if context.dry_run and status == "ok"
        else LifecycleStatus.NOOP
        if status == "skipped"
        else LifecycleStatus.CHANGED
    )
    payload = BrainUpgradePayload(
        lifecycle_status,
        str(vault),
        result.get("old_version"),
        result.get("new_version") or _source_version(core),
        len(result.get("files_added", [])),
        len(result.get("files_modified", [])),
        len(result.get("files_removed", [])),
        _migration_ids(result),
        preflight,
        (
            installed_distribution.manifest_fingerprint
            if installed_distribution is not None
            else None
        ),
        _reconciliation_steps(result),
    )
    if context.dry_run or status == "skipped":
        effects = ()
    else:
        effects = (
            CommittedEffect(request.COMMAND_ID, f"upgrade:{vault}"),
            CommittedEffect(request.COMMAND_ID, f"file:{context.cli_binary}"),
            CommittedEffect(
                request.COMMAND_ID,
                f"directory:{installed_distribution.distribution_root}",
            ),
        )
    if _reconciliation_failed(result):
        message = "The Brain/CLI cutover committed, but post-commit reconciliation requires recovery."
        return Partial(
            request.COMMAND_ID,
            request.COMMAND_VERSION,
            CommandError(
                ErrorCode.CONFLICT,
                message,
                RequestErrorDetails(None, message),
            ),
            effects,
        )
    return Ok(request.COMMAND_ID, request.COMMAND_VERSION, payload, effects)


def install_owner():
    from .owners import LauncherOwner

    return LauncherOwner(
        BrainInstallRequest,
        BrainInstallPayload,
        "_launcher.lifecycle:install",
        execute_install,
    )


def uninstall_owner():
    from .owners import LauncherOwner

    return LauncherOwner(
        BrainUninstallRequest,
        BrainUninstallPayload,
        "_launcher.lifecycle:uninstall",
        execute_uninstall,
    )


def upgrade_owner():
    from .owners import LauncherOwner

    return LauncherOwner(
        BrainUpgradeRequest,
        BrainUpgradePayload,
        "_launcher.lifecycle:upgrade",
        execute_upgrade,
    )
