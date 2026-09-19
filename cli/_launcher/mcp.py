"""Typed launcher ownership for deterministic MCP client configuration."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import nullcontext
from enum import Enum
import json
from pathlib import Path
import sys
from typing import ClassVar

from .context import LauncherContext
from .contracts import (
    CommandError,
    CommandWarning,
    CapabilityUnavailableDetails,
    CommittedEffect,
    Error,
    ErrorCode,
    InstructionNextAction,
    Ok,
    OutcomeReference,
    OutcomeUnknownDetails,
    Partial,
    RequestErrorDetails,
    WarningCode,
    no_effect_error,
)


from _bootstrap.mcp_registration import (
    McpClient, McpScope,
    _clients,
    _json_object,
    _write_json,
    _config_path,
    _upsert_json_server,
    _remove_json_server,
    _ensure_bootstrap,
    _remove_bootstrap,
    _ensure_hook,
    _remove_hook,
    _init_records,
    _record_id,
    _save_records,
    _record_for,
    _configure_client,
    _configure_plan,
    _remove_plan,
    _validate_target,
    _runtime_python,
    _validate_toml,
)
from _bootstrap import mcp_registration as registration


class McpConfigureAction(str, Enum):
    CONFIGURE = "configure"
    REMOVE = "remove"


class McpOperation(str, Enum):
    CONFIGURE = "configure"
    REMOVE = "remove"
    REPAIR = "repair"
    MIGRATE = "migrate"


class RepairBreadth(str, Enum):
    WORKSPACE = "workspace"
    BRAIN = "brain"
    MACHINE = "machine"


class McpMutationStatus(str, Enum):
    NOOP = "noop"
    PLANNED = "planned"
    CHANGED = "changed"


@dataclass(frozen=True, slots=True)
class McpFileStep:
    path: str
    status: McpMutationStatus

    def __post_init__(self) -> None:
        if not Path(self.path).is_absolute():
            raise ValueError("MCP file-step paths must be absolute")
        if not isinstance(self.status, McpMutationStatus):
            raise ValueError("MCP file-step status must be closed and typed")


@dataclass(frozen=True, slots=True)
class McpMutationPayload:
    operation: McpOperation
    scope: McpScope
    target_dir: str | None
    clients: tuple[McpClient, ...]
    status: McpMutationStatus
    files: tuple[McpFileStep, ...]
    targets: tuple[str, ...] = ()
    breadth: RepairBreadth = RepairBreadth.WORKSPACE
    runtimes: tuple[McpFileStep, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.operation, McpOperation):
            raise ValueError("MCP operation must be closed and typed")
        if not isinstance(self.scope, McpScope):
            raise ValueError("MCP scope must be closed and typed")
        if self.scope is McpScope.USER:
            if self.target_dir is not None:
                raise ValueError("user-scope MCP results cannot carry a target directory")
        elif self.target_dir is None or not Path(self.target_dir).is_absolute():
            raise ValueError("workspace-scope MCP results require an absolute target")
        if not self.clients and not (
            self.operation in (McpOperation.REPAIR, McpOperation.MIGRATE)
            and (self.status is McpMutationStatus.NOOP or self.runtimes)
        ):
            raise ValueError("MCP mutation results require concrete clients")
        if any(client is McpClient.ALL for client in self.clients):
            raise ValueError("MCP results require concrete clients")
        if tuple(client.value for client in self.clients) != tuple(
            sorted({client.value for client in self.clients})
        ):
            raise ValueError("MCP result clients must be ordered and unique")
        if not isinstance(self.status, McpMutationStatus):
            raise ValueError("MCP mutation status must be closed and typed")
        if any(not isinstance(step, McpFileStep) for step in self.files):
            raise ValueError("MCP file steps must be typed")


@dataclass(frozen=True, slots=True)
class McpConfigureRequest:
    COMMAND_ID: ClassVar[str] = "mcp.configure"
    COMMAND_VERSION: ClassVar[int] = 3
    RESULT_TYPE: ClassVar[type] = McpMutationPayload

    client: McpClient
    scope: McpScope = McpScope.PROJECT
    action: McpConfigureAction = McpConfigureAction.CONFIGURE

    def __post_init__(self) -> None:
        if not isinstance(self.client, McpClient):
            raise ValueError("MCP client must be closed and typed")
        if not isinstance(self.scope, McpScope):
            raise ValueError("MCP scope must be closed and typed")
        if not isinstance(self.action, McpConfigureAction):
            raise ValueError("MCP configure action must be closed and typed")
        if self.scope is McpScope.LOCAL and self.client in (
            McpClient.CODEX,
            McpClient.GROK,
        ):
            raise ValueError(
                f"{self.client.value.title()} does not support local MCP scope"
            )


@dataclass(frozen=True, slots=True)
class McpRepairRequest:
    COMMAND_ID: ClassVar[str] = "mcp.repair"
    COMMAND_VERSION: ClassVar[int] = 3
    RESULT_TYPE: ClassVar[type] = McpMutationPayload

    client: McpClient = McpClient.ALL
    scope: McpScope = McpScope.PROJECT
    breadth: RepairBreadth = RepairBreadth.WORKSPACE

    def __post_init__(self) -> None:
        McpConfigureRequest(client=self.client, scope=self.scope)
        if not isinstance(self.breadth, RepairBreadth):
            raise ValueError("Repair breadth must be workspace, brain or machine")
        if self.breadth is not RepairBreadth.WORKSPACE and (self.client is not McpClient.ALL or self.scope is not McpScope.PROJECT):
            raise ValueError("Brain/machine breadth composes all registered client/scopes; omit scope/client filters")


@dataclass(frozen=True, slots=True)
class McpMigrateRequest:
    COMMAND_ID: ClassVar[str] = "mcp.migrate"
    COMMAND_VERSION: ClassVar[int] = 1
    RESULT_TYPE: ClassVar[type] = McpMutationPayload


def _warnings(client: McpClient, scope: McpScope) -> tuple[CommandWarning, ...]:
    if client is McpClient.ALL and scope is McpScope.LOCAL:
        return (
            CommandWarning(
                WarningCode.DEGRADED_CAPABILITY,
                "Codex and Grok have no local MCP scope; only Claude was selected.",
            ),
        )
    return ()


def _payload(
    operation: McpOperation,
    scope: McpScope,
    target: Path | None,
    clients: tuple[McpClient, ...],
    changes,
    *,
    dry_run: bool,
) -> McpMutationPayload:
    status = (
        McpMutationStatus.NOOP
        if not changes
        else McpMutationStatus.PLANNED
        if dry_run
        else McpMutationStatus.CHANGED
    )
    step_status = (
        McpMutationStatus.PLANNED if dry_run else McpMutationStatus.CHANGED
    )
    return McpMutationPayload(
        operation,
        scope,
        str(target) if target is not None else None,
        clients,
        status,
        tuple(McpFileStep(str(change.path), step_status) for change in changes),
    )


def _apply(
    request,
    context: LauncherContext,
    operation: McpOperation,
    scope: McpScope,
    target: Path | None,
    clients: tuple[McpClient, ...],
    plan,
    warnings=(),
):
    from _bootstrap.file_transaction import FileTransactionError, apply_file_changes

    changes = plan.changes()
    payload = _payload(
        operation, scope, target, clients, changes, dry_run=context.dry_run
    )
    if context.dry_run or not changes:
        return Ok(request.COMMAND_ID, request.COMMAND_VERSION, payload, warnings=warnings)
    try:
        plan.validate()
        apply_file_changes(changes, before_write=plan.validate_dependencies)
    except FileTransactionError as exc:
        effects = tuple(
            CommittedEffect(
                request.COMMAND_ID,
                f"{'directory' if path.is_dir() else 'file'}:{path}",
            )
            for path in exc.surviving_paths
        )
        if effects:
            return Partial(
                request.COMMAND_ID,
                request.COMMAND_VERSION,
                CommandError(
                    ErrorCode.CONFLICT,
                    str(exc),
                    RequestErrorDetails(None, str(exc)),
                ),
                effects,
                warnings,
            )
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))
    effects = tuple(
        CommittedEffect(request.COMMAND_ID, f"file:{change.path}")
        for change in changes
    )
    return Ok(request.COMMAND_ID, request.COMMAND_VERSION, payload, effects, warnings)


def execute_configure(context: LauncherContext, request: McpConfigureRequest):
    try:
        with nullcontext() if context.dry_run else registration.registration_lock(context.home_dir):
            return _execute_configure(context, request)
    except (OSError, RuntimeError, ValueError) as exc:
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))


def _execute_configure(context: LauncherContext, request: McpConfigureRequest):
    vault = context.current_vault
    if request.scope is not McpScope.USER and (vault is None or not (vault / ".brain-core" / "VERSION").is_file()):
        return no_effect_error(
            type(request),
            ErrorCode.NOT_FOUND,
            "No installed current Brain is selected.",
            "current_vault",
        )
    target = None if request.scope is McpScope.USER else (context.workspace_dir or context.caller_dir).resolve()
    clients = _clients(request.client, request.scope)
    try:
        if request.action is McpConfigureAction.CONFIGURE:
            _validate_target(vault, target)
            from _bootstrap.mcp_state import build_mcp_config

            if request.scope is McpScope.USER:
                _verify_stable_launcher(context)
                server = registration.stable_server_config(context.cli_binary)
            else:
                python = _runtime_python(vault)
                server = build_mcp_config(python, vault, workspace_dir=target)
            plan = _configure_plan(
                vault, context.home_dir, target, request.scope, clients, server
            )
            operation = McpOperation.CONFIGURE
        else:
            plan = _remove_plan(
                vault, context.home_dir, target, request.scope, clients
            )
            operation = McpOperation.REMOVE
    except RuntimeError as exc:
        return _runtime_error(type(request), str(exc))
    except (OSError, ValueError) as exc:
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))
    return _apply(
        request,
        context,
        operation,
        request.scope,
        target,
        clients,
        plan,
        _warnings(request.client, request.scope),
    )


def _runtime_error(request_type, message: str):
    from .contracts import Error

    return Error(
        request_type.COMMAND_ID,
        request_type.COMMAND_VERSION,
        CommandError(
            ErrorCode.CAPABILITY_UNAVAILABLE,
            message,
            CapabilityUnavailableDetails(
                "machine_local", ("managed_runtime:mcp",), True
            ),
            InstructionNextAction(
                "Run `brain runtime repair`, then invoke this command again."
            ),
        ),
    )


def _verify_stable_launcher(context):
    from _distribution import verify_distribution, validate_bootstrap_python

    root = context.cli_binary.parent.parent / "lib/brain-cli" / context.cli_version
    manifest = verify_distribution(root)
    if manifest.get("cli_version") != context.cli_version or not (root / "cli/_mcp_stdio.py").is_file():
        raise ValueError("Install the parity-capable machine CLI before changing user registrations")
    source = root / "cli" / ("brain.cmd" if context.cli_binary.suffix.lower() == ".cmd" else "brain")
    if context.cli_binary.is_symlink() or context.cli_binary.read_bytes() != source.read_bytes():
        raise ValueError("Installed CLI bootloader does not match its checked distribution; reinstall the CLI")
    validate_bootstrap_python(Path((root / ".bootstrap-python").read_text(encoding="utf-8").strip()))


def execute_repair(context: LauncherContext, request: McpRepairRequest):
    from _bootstrap.file_transaction import FilePlan
    from _bootstrap.mcp_state import build_mcp_config

    if request.breadth is not RepairBreadth.WORKSPACE:
        return _execute_composed_repair(context, request)

    vault = context.current_vault
    target = None if request.scope is McpScope.USER else (context.workspace_dir or context.caller_dir).resolve()
    if target is not None and (vault is None or not (vault / ".brain-core" / "VERSION").is_file()):
        return no_effect_error(type(request), ErrorCode.NOT_FOUND, "No installed current Brain is selected.", "current_vault")
    try:
        with nullcontext() if context.dry_run else registration.registration_lock(context.home_dir):
            _validate_target(vault, target)
            plan = FilePlan()
            _, records = registration.read_records(plan, vault, context.home_dir, request.scope)
            selected = _clients(request.client, request.scope)
            clients = tuple(client for client in selected if any(
                record["client"] == client.value and record["scope"] == request.scope.value
                and record["target_path"] == (str(target) if target else None)
                for record in records
            ))
            # Inspect all selected slots: an unrecorded slot is not an empty healthy target.
            for client in selected:
                path = registration._config_path(client, request.scope, target, context.home_dir)
                if client not in clients and registration.observed_server(plan, client, path) is not None:
                    raise ValueError(f"Unowned MCP entry requires explicit migration/admission: {path}")
            if clients:
                if request.scope is McpScope.USER:
                    _verify_stable_launcher(context)
                server = (
                    registration.stable_server_config(context.cli_binary)
                    if request.scope is McpScope.USER
                    else build_mcp_config(_runtime_python(vault), vault, workspace_dir=target)
                )
                _configure_plan(vault, context.home_dir, target, request.scope, clients, server, plan=plan, repair=True)
            return _apply(request, context, McpOperation.REPAIR, request.scope, target, clients, plan,
                          _warnings(request.client, request.scope))
    except RuntimeError as exc:
        return _runtime_error(type(request), str(exc))
    except (OSError, ValueError) as exc:
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))


def _execute_composed_repair(context, request):
    from _bootstrap import mcp_inventory
    from _bootstrap import runtime as bootstrap_runtime
    from _bootstrap.file_transaction import FilePlan
    from dataclasses import replace

    effects = []
    try:
        with nullcontext() if context.dry_run else registration.registration_lock(context.home_dir):
            plan = FilePlan()
            _verify_stable_launcher(context)
            if request.breadth is RepairBreadth.MACHINE:
                vaults = mcp_inventory.local_brains(plan)
            elif context.current_vault is not None:
                vaults = (context.current_vault,)
            else:
                raise ValueError("Brain repair requires a selected Brain")
            runtime_work = {}
            runtimes = {}
            for vault in vaults:
                plan.observe_directory(vault)
                plan.read_bytes(vault / ".brain-core/VERSION")
                plan.read_bytes(vault / ".brain-core/brain_mcp/requirements.txt")
                plan.read_bytes(vault / ".brain-core/brain_mcp/requirements-semantic.txt")
                plan.read_bytes(vault / ".brain-core/scripts/_common/_venv.py")
                contract = bootstrap_runtime.target_runtime_contract(vault)
                arguments = dict(required_modules=("mcp",), dependency_owner="MCP repair",
                                 full_conformance=True, runtime_contract=contract,
                                 launcher_python=str(context.launcher_python) if context.launcher_python else None)
                preview = bootstrap_runtime.bootstrap_managed_runtime(vault, dry_run=True, **arguments)
                if preview["status"] == "error" or not preview.get("managed_python"):
                    raise ValueError(preview.get("message") or f"Cannot plan managed runtime for {vault}")
                runtimes[vault] = Path(preview["managed_python"])
                runtime_work.setdefault(preview["runtime_dir"], (vault, arguments, preview))
            clients, targets = mcp_inventory.plan_repair(plan, vaults, context.home_dir, context.cli_binary, runtimes=runtimes)
            runtime_steps = []
            plan.validate()
            for directory, (vault, arguments, preview) in runtime_work.items():
                summary = preview if context.dry_run else bootstrap_runtime.bootstrap_managed_runtime(vault, **arguments)
                if summary["effect_outcome"] not in ("none", "committed", "partial"):
                    reference = OutcomeReference(context.invocation_id)
                    recovery_paths = tuple(sorted({directory, *(effect.subject.split(":", 1)[1] for effect in effects)}))
                    return Error(request.COMMAND_ID, request.COMMAND_VERSION,
                                 CommandError(ErrorCode.COMMAND_OUTCOME_UNKNOWN,
                                              summary.get("message") or "Managed-runtime repair effects are uncertain; inspect the recovery paths before retrying",
                                              OutcomeUnknownDetails(reference, recovery_paths)),
                                 effects="unknown", outcome_reference=reference)
                if summary["effect_outcome"] in ("committed", "partial"):
                    effects.append(CommittedEffect(request.COMMAND_ID, f"managed-runtime:{summary['runtime_dir']}"))
                if summary["status"] == "error":
                    raise ValueError(summary.get("message") or f"Managed runtime repair failed: {vault}")
                if summary["managed_python"] != str(runtimes[vault]):
                    raise ValueError(f"Runtime identity changed after admission: {vault}; rerun repair")
                state = (McpMutationStatus.PLANNED if summary["status"] == "planned" else
                         McpMutationStatus.CHANGED if summary["effect_outcome"] == "committed" else McpMutationStatus.NOOP)
                runtime_steps.append(McpFileStep(directory, state))
                plan.validate()
            result = _apply(request, context, McpOperation.REPAIR, McpScope.USER, None, clients, plan)
            if isinstance(result, Ok):
                status = result.result.status
                if any(step.status is not McpMutationStatus.NOOP for step in runtime_steps):
                    status = McpMutationStatus.PLANNED if context.dry_run else McpMutationStatus.CHANGED
                result = replace(result, committed_effects=(*effects, *result.committed_effects),
                                 result=replace(result.result, targets=targets, breadth=request.breadth,
                                                runtimes=tuple(runtime_steps), status=status))
            elif effects:
                result = Partial(request.COMMAND_ID, request.COMMAND_VERSION, result.error,
                                 (*effects, *getattr(result, "committed_effects", ())))
            return result
    except (RuntimeError, OSError, ValueError) as exc:
        if effects:
            return Partial(request.COMMAND_ID, request.COMMAND_VERSION,
                           CommandError(ErrorCode.CONFLICT, str(exc), RequestErrorDetails(None, str(exc))), tuple(effects))
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))


def _read_codex(path: Path) -> dict:
    from _bootstrap.mcp_state import read_toml_server_config

    return read_toml_server_config(path) or {}


def configure_owner():
    from .owners import LauncherOwner

    return LauncherOwner(McpConfigureRequest, McpMutationPayload, "_launcher.mcp:configure", execute_configure)


def execute_migrate(context: LauncherContext, request: McpMigrateRequest):
    from _bootstrap import mcp_migration
    from _bootstrap.file_transaction import FileTransactionError

    committed = ()
    try:
        with nullcontext() if context.dry_run else registration.registration_lock(context.home_dir):
            _verify_stable_launcher(context)
            if not context.dry_run:
                committed = mcp_migration.resume_migration(context.home_dir, context.current_vault)
                committed = (*committed, *mcp_migration.verify_transition(context.home_dir, context.current_vault))
            plan = mcp_migration.migration_plan(context.home_dir, context.cli_binary, context.current_vault)
            payload = _payload(McpOperation.MIGRATE, McpScope.USER, None, _clients(McpClient.ALL, McpScope.USER), plan.changes(), dry_run=context.dry_run)
            if not context.dry_run:
                committed = (*committed, *mcp_migration.apply_migration(plan, context.home_dir))
                committed = (*committed, *mcp_migration.verify_transition(context.home_dir, context.current_vault))
            effects = tuple(CommittedEffect(request.COMMAND_ID, f"file:{path}") for path in sorted(set(committed), key=str))
            if effects:
                from dataclasses import replace

                payload = replace(payload, status=McpMutationStatus.CHANGED,
                                  files=tuple(McpFileStep(str(path), McpMutationStatus.CHANGED) for path in sorted(set(committed), key=str)))
            return Ok(request.COMMAND_ID, request.COMMAND_VERSION, payload, effects)
    except (OSError, ValueError, RuntimeError) as exc:
        surviving = {*committed, *getattr(exc, "surviving_paths", ())}
        if surviving:
            return Partial(request.COMMAND_ID, request.COMMAND_VERSION,
                           CommandError(ErrorCode.CONFLICT, str(exc), RequestErrorDetails(None, str(exc))),
                           tuple(CommittedEffect(request.COMMAND_ID, f"file:{path}") for path in sorted(surviving, key=str)))
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))


def migrate_owner():
    from .owners import LauncherOwner

    return LauncherOwner(McpMigrateRequest, McpMutationPayload, "_launcher.mcp:migrate", execute_migrate)


def repair_owner():
    from .owners import LauncherOwner

    return LauncherOwner(McpRepairRequest, McpMutationPayload, "_launcher.mcp:repair", execute_repair)
