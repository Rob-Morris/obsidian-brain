"""Typed launcher ownership for deterministic MCP client configuration."""

from __future__ import annotations

from dataclasses import dataclass
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
    ErrorCode,
    InstructionNextAction,
    Ok,
    Partial,
    RequestErrorDetails,
    WarningCode,
    no_effect_error,
)


class McpClient(str, Enum):
    CLAUDE = "claude"
    CODEX = "codex"
    GROK = "grok"
    ALL = "all"


class McpScope(str, Enum):
    PROJECT = "project"
    LOCAL = "local"
    USER = "user"


class McpConfigureAction(str, Enum):
    CONFIGURE = "configure"
    REMOVE = "remove"


class McpOperation(str, Enum):
    CONFIGURE = "configure"
    REMOVE = "remove"
    REPAIR = "repair"


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
            self.operation is McpOperation.REPAIR
            and self.status is McpMutationStatus.NOOP
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
    COMMAND_VERSION: ClassVar[int] = 2
    RESULT_TYPE: ClassVar[type] = McpMutationPayload

    client: McpClient = McpClient.ALL
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
    COMMAND_VERSION: ClassVar[int] = 2
    RESULT_TYPE: ClassVar[type] = McpMutationPayload


def _clients(client: McpClient, scope: McpScope) -> tuple[McpClient, ...]:
    if client is McpClient.ALL:
        return (
            (McpClient.CLAUDE,)
            if scope is McpScope.LOCAL
            else (McpClient.CLAUDE, McpClient.CODEX, McpClient.GROK)
        )
    return (client,)


def _json_object(plan, path: Path) -> dict:
    content = plan.read_text(path)
    if content is None:
        return {}
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"MCP JSON is malformed: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"MCP JSON must contain an object: {path}")
    return value


def _write_json(plan, path: Path, value: dict, *, delete_empty: bool = False) -> None:
    if delete_empty and not value:
        plan.delete(path)
    else:
        plan.write_text(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def _config_path(client: McpClient, scope: McpScope, target: Path | None, home: Path) -> Path:
    from _bootstrap import mcp_state

    if client is McpClient.CLAUDE:
        if scope is McpScope.USER:
            return home / mcp_state.CLAUDE_USER_CONFIG_FILE
        if scope is McpScope.LOCAL:
            assert target is not None
            return target / mcp_state.CLAUDE_LOCAL_SETTINGS_FILE
        assert target is not None
        return target / mcp_state.CLAUDE_PROJECT_CONFIG_FILE
    if client is McpClient.GROK:
        return (home if scope is McpScope.USER else target) / mcp_state.GROK_CONFIG_REL
    if scope is McpScope.USER:
        return home / mcp_state.CODEX_CONFIG_REL
    assert target is not None
    return target / mcp_state.CODEX_CONFIG_REL


def _upsert_json_server(plan, path: Path, server_config: dict) -> None:
    from _bootstrap.mcp_state import BRAIN_SERVER_NAME

    data = _json_object(plan, path)
    servers = data.get("mcpServers")
    if servers is None:
        servers = {}
        data["mcpServers"] = servers
    if not isinstance(servers, dict):
        raise ValueError(f"mcpServers must be an object: {path}")
    servers[BRAIN_SERVER_NAME] = server_config
    _write_json(plan, path, data)


def _remove_json_server(plan, path: Path, server_config: dict) -> bool:
    from _bootstrap.mcp_state import BRAIN_SERVER_NAME

    data = _json_object(plan, path)
    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or servers.get(BRAIN_SERVER_NAME) != server_config:
        return False
    del servers[BRAIN_SERVER_NAME]
    if not servers:
        data.pop("mcpServers", None)
    _write_json(plan, path, data, delete_empty=True)
    return True


def _ensure_bootstrap(plan, target: Path, *, local: bool) -> tuple[Path, str]:
    from _bootstrap import mcp_state

    line = mcp_state.bootstrap_line_for_target(target)
    path = target / (mcp_state.CLAUDE_LOCAL_MD_FILE if local else mcp_state.CLAUDE_MD_FILE)
    existing = plan.read_text(path) or ""
    if line not in existing.splitlines():
        separator = "" if not existing else "\n" if existing.endswith("\n") else "\n\n"
        plan.write_text(path, f"{existing}{separator}{line}\n")
    return path, line


def _remove_bootstrap(plan, path: Path, line: str) -> None:
    content = plan.read_text(path)
    if content is None:
        return
    lines = content.splitlines()
    if not any(item.strip() == line for item in lines):
        return
    kept = [item for item in lines if item.strip() != line]
    while kept and not kept[-1].strip():
        kept.pop()
    if kept:
        plan.write_text(path, "\n".join(kept) + "\n")
    else:
        plan.delete(path)


def _ensure_hook(plan, target: Path, vault: Path, python: str) -> tuple[Path, str]:
    from _bootstrap import mcp_state, mcp_transport

    path = target / mcp_state.CLAUDE_LOCAL_SETTINGS_FILE
    settings = _json_object(plan, path)
    hooks = settings.get("hooks")
    if hooks is None:
        hooks = {}
        settings["hooks"] = hooks
    if not isinstance(hooks, dict):
        raise ValueError(f"hooks must be an object: {path}")
    command = mcp_state.build_session_hook_command(vault, target, python_path=python)
    kept, _ = mcp_transport._strip_brain_session_hooks(
        hooks.get("SessionStart", []), vault, target, extra_valid=(command,)
    )
    child = {"type": "command", "command": command}
    if sys.platform == "win32":
        child["shell"] = "powershell"
    kept.append({"hooks": [child]})
    hooks["SessionStart"] = kept
    _write_json(plan, path, settings)
    return path, command


def _remove_hook(plan, path: Path, vault: Path, target: Path, command: str | None) -> None:
    from _bootstrap import mcp_transport

    settings = _json_object(plan, path)
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return
    extra = (command,) if isinstance(command, str) else ()
    kept, changed = mcp_transport._strip_brain_session_hooks(
        hooks.get("SessionStart", []), vault, target, extra_valid=extra
    )
    if not changed:
        return
    if kept:
        hooks["SessionStart"] = kept
    else:
        hooks.pop("SessionStart", None)
    if not hooks:
        settings.pop("hooks", None)
    _write_json(plan, path, settings, delete_empty=True)


def _init_records(plan, vault: Path) -> tuple[Path, list[dict]]:
    from _bootstrap.mcp_state import INIT_STATE_REL, INIT_STATE_VERSION

    path = vault / INIT_STATE_REL
    data = _json_object(plan, path)
    if not data:
        return path, []
    if data.get("version", INIT_STATE_VERSION) != INIT_STATE_VERSION:
        raise ValueError(f"Unsupported MCP init-state version: {path}")
    records = data.get("records")
    if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
        raise ValueError(f"MCP init-state records must be objects: {path}")
    return path, list(records)


def _record_id(record: dict) -> tuple[object, ...]:
    return (
        record.get("client"),
        record.get("scope"),
        record.get("target_path"),
        record.get("config_path"),
    )


def _save_records(plan, path: Path, records: list[dict]) -> None:
    if records:
        _write_json(plan, path, {"version": 1, "records": records})
    else:
        plan.delete(path)


def _record_for(
    client: McpClient,
    scope: McpScope,
    target: Path | None,
    config_path: Path,
    server_config: dict,
    *,
    bootstrap_path: Path | None = None,
    bootstrap_line: str | None = None,
    hook_path: Path | None = None,
    hook_command: str | None = None,
) -> dict:
    record = {
        "client": client.value,
        "scope": scope.value,
        "target_path": str(target) if target is not None else None,
        "config_path": str(config_path),
        "server_name": "brain",
        "server_config": server_config,
        "method": f"{config_path} (transactional direct)",
    }
    for key, value in (
        ("bootstrap_path", bootstrap_path),
        ("bootstrap_line", bootstrap_line),
        ("hook_path", hook_path),
        ("hook_command", hook_command),
    ):
        if value is not None:
            record[key] = str(value)
    return record


def _configure_client(
    plan,
    vault: Path,
    home: Path,
    target: Path | None,
    scope: McpScope,
    client: McpClient,
    server: dict,
) -> dict:
    from _bootstrap import mcp_state

    config_path = _config_path(client, scope, target, home).absolute()
    bootstrap_path = bootstrap_line = hook_path = hook_command = None
    if client is McpClient.CLAUDE:
        _upsert_json_server(plan, config_path, server)
        if target is not None:
            bootstrap_path, bootstrap_line = _ensure_bootstrap(
                plan, target, local=scope is McpScope.LOCAL
            )
            hook_path, hook_command = _ensure_hook(
                plan, target, vault, server["command"]
            )
    elif client is McpClient.GROK:
        from _bootstrap import grok_mcp

        config_path, bootstrap_path = grok_mcp.plan_configure(
            plan, home if scope is McpScope.USER else target, server
        )
    else:
        content = plan.read_text(config_path) or ""
        _validate_toml(content, config_path)
        plan.write_text(config_path, mcp_state.render_toml_config(content, server))
    return _record_for(
        client,
        scope,
        target,
        config_path,
        server,
        bootstrap_path=bootstrap_path,
        bootstrap_line=bootstrap_line,
        hook_path=hook_path,
        hook_command=hook_command,
    )


def _configure_plan(
    vault: Path,
    home: Path,
    target: Path | None,
    scope: McpScope,
    clients: tuple[McpClient, ...],
    server: dict,
):
    from _bootstrap.file_transaction import FilePlan

    plan = FilePlan()
    path, records = _init_records(plan, vault)
    for client in clients:
        record = _configure_client(plan, vault, home, target, scope, client, server)
        identity = _record_id(record)
        records = [item for item in records if _record_id(item) != identity]
        records.append(record)
    records.sort(key=lambda item: tuple(str(part) for part in _record_id(item)))
    _save_records(plan, path, records)
    return plan


def _remove_plan(vault: Path, home: Path, target: Path | None, scope: McpScope, clients: tuple[McpClient, ...]):
    from _bootstrap import mcp_state
    from _bootstrap.file_transaction import FilePlan

    plan = FilePlan()
    init_path, records = _init_records(plan, vault)
    wanted = {client.value for client in clients}
    expected_paths = {
        client.value: _config_path(client, scope, target, home).absolute()
        for client in clients
    }
    retained: list[dict] = []
    for record in records:
        matches = (
            record.get("client") in wanted
            and record.get("scope") == scope.value
            and record.get("target_path") == (str(target) if target is not None else None)
            and record.get("config_path") == str(expected_paths[record["client"]])
        )
        if not matches:
            retained.append(record)
            continue
        client = McpClient(record["client"])
        server = record.get("server_config")
        if not isinstance(server, dict):
            raise ValueError("Recorded MCP server configuration is invalid")
        config_path = expected_paths[client.value]
        removed = False
        if client is McpClient.CLAUDE:
            removed = _remove_json_server(plan, config_path, server)
            if removed and target is not None:
                bootstrap_path = target / (
                    mcp_state.CLAUDE_LOCAL_MD_FILE
                    if scope is McpScope.LOCAL
                    else mcp_state.CLAUDE_MD_FILE
                )
                _remove_bootstrap(
                    plan,
                    bootstrap_path,
                    mcp_state.bootstrap_line_for_target(target),
                )
                _remove_hook(
                    plan,
                    target / mcp_state.CLAUDE_LOCAL_SETTINGS_FILE,
                    vault,
                    target,
                    None,
                )
        elif client is McpClient.GROK:
            from _bootstrap import grok_mcp

            removed = grok_mcp.plan_remove(
                plan, home if scope is McpScope.USER else target, server
            )
            if not removed:
                retained.append(record)
            continue
        else:
            content = plan.read_text(config_path)
            if content is not None:
                _validate_toml(content, config_path)
                rendered = mcp_state.render_toml_without_server(content, server)
                if rendered is not None:
                    removed = True
                    if rendered:
                        plan.write_text(config_path, rendered)
                    else:
                        plan.delete(config_path)
        if not removed and plan.read_bytes(config_path) is not None:
            retained.append(record)
    _save_records(plan, init_path, retained)
    return plan


def _validate_target(vault: Path, target: Path | None) -> None:
    if target is None or target == vault:
        return
    from _bootstrap.workspace_binding import WorkspaceBindingError, resolve_brain_target

    try:
        resolved = resolve_brain_target(
            workspace_env=str(target),
            vault_root_env=None,
            start_dir=target,
        )
    except WorkspaceBindingError as exc:
        raise ValueError(str(exc)) from exc
    if Path(resolved.vault_root).resolve() != vault:
        raise ValueError(
            f"Workspace {target} is not bound to the selected Brain {vault}."
        )


def _runtime_python(vault: Path) -> str:
    from _bootstrap import diagnostics

    state = diagnostics.inspect_runtime(vault)
    if not state["healthy"]:
        raise RuntimeError(state["message"])
    return state["python"]


def _validate_toml(content: str, path: Path) -> None:
    if not content:
        return
    import tomllib

    try:
        tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"MCP TOML is malformed: {path}: {exc}") from exc


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
        apply_file_changes(changes)
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
    vault = context.current_vault
    if vault is None or not (vault / ".brain-core" / "VERSION").is_file():
        return no_effect_error(
            type(request),
            ErrorCode.NOT_FOUND,
            "No installed current Brain is selected.",
            "current_vault",
        )
    target = None if request.scope is McpScope.USER else context.caller_dir.resolve()
    clients = _clients(request.client, request.scope)
    try:
        if request.action is McpConfigureAction.CONFIGURE:
            _validate_target(vault, target)
            from _bootstrap.mcp_state import build_mcp_config

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


def execute_repair(context: LauncherContext, request: McpRepairRequest):
    vault = context.current_vault
    if vault is None or not (vault / ".brain-core" / "VERSION").is_file():
        return no_effect_error(
            type(request),
            ErrorCode.NOT_FOUND,
            "No installed current Brain is selected.",
            "current_vault",
        )
    target = context.caller_dir.resolve()
    try:
        _validate_target(vault, target)
        python = _runtime_python(vault)
        from _bootstrap.mcp_state import (
            BRAIN_SERVER_NAME,
            CLAUDE_PROJECT_CONFIG_FILE,
            CODEX_CONFIG_REL,
            GROK_CONFIG_REL,
            build_mcp_config,
            render_toml_without_server,
        )
        from _bootstrap.file_transaction import FilePlan

        probe = FilePlan()
        claude_data = _json_object(probe, target / CLAUDE_PROJECT_CONFIG_FILE)
        claude_servers = claude_data.get("mcpServers")
        claude_present = (
            isinstance(claude_servers, dict)
            and BRAIN_SERVER_NAME in claude_servers
        )
        codex_content = probe.read_text(target / CODEX_CONFIG_REL)
        if codex_content is not None:
            _validate_toml(codex_content, target / CODEX_CONFIG_REL)
        current_codex = _read_codex(target / CODEX_CONFIG_REL)
        codex_present = (
            codex_content is not None
            and bool(current_codex)
            and render_toml_without_server(codex_content, current_codex) is not None
        )
        from _bootstrap.grok_mcp import read_server

        grok_content = probe.read_text(target / GROK_CONFIG_REL)
        grok_present = (
            grok_content is not None and read_server(grok_content) is not None
        )
        _, records = _init_records(probe, vault)
        recorded = {
            item.get("client")
            for item in records
            if item.get("scope") == "project" and item.get("target_path") == str(target)
        }
        clients = tuple(
            client
            for client, present in (
                (McpClient.CLAUDE, claude_present or "claude" in recorded),
                (McpClient.CODEX, codex_present or "codex" in recorded),
                (McpClient.GROK, grok_present or "grok" in recorded),
            )
            if present
        )
        if not clients:
            return Ok(
                request.COMMAND_ID,
                request.COMMAND_VERSION,
                McpMutationPayload(
                    McpOperation.REPAIR,
                    McpScope.PROJECT,
                    str(target),
                    clients,
                    McpMutationStatus.NOOP,
                    (),
                ),
            )
        server = build_mcp_config(python, vault, workspace_dir=target)
        plan = _configure_plan(
            vault,
            context.home_dir,
            target,
            McpScope.PROJECT,
            clients,
            server,
        )
    except RuntimeError as exc:
        return _runtime_error(type(request), str(exc))
    except (OSError, ValueError) as exc:
        return no_effect_error(type(request), ErrorCode.CONFLICT, str(exc))
    return _apply(
        request,
        context,
        McpOperation.REPAIR,
        McpScope.PROJECT,
        target,
        clients,
        plan,
    )


def _read_codex(path: Path) -> dict:
    from _bootstrap.mcp_state import read_toml_server_config

    return read_toml_server_config(path) or {}


def configure_owner():
    from .owners import LauncherOwner

    return LauncherOwner(McpConfigureRequest, McpMutationPayload, "_launcher.mcp:configure", execute_configure)


def repair_owner():
    from .owners import LauncherOwner

    return LauncherOwner(McpRepairRequest, McpMutationPayload, "_launcher.mcp:repair", execute_repair)
