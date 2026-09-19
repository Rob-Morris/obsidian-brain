"""Canonical host-local MCP registration planning; no command or process effects."""

from __future__ import annotations

from enum import Enum
import json
import os
from pathlib import Path
import sys

from _bootstrap.paths import config_home


REGISTRATION_SCHEMA = "brain.mcp-registration/2"
LEDGER_VERSION = 2


def user_ledger_path(home: Path) -> Path:
    """Resolve machine ownership from trusted host layout, never a record path."""
    # Injected homes are used by embedders and isolated installations.
    root = config_home() if home == Path.home() else home / ".config"
    return root / "brain" / "mcp-registrations.json"


def registration_lock(home: Path):
    """Serialize the whole inspection/plan/apply interval across local Brains."""
    from _bootstrap.file_lock import exclusive_file_lock

    from _bootstrap.file_transaction import _refuse_symlink_path

    path = user_ledger_path(home).with_suffix(".lock")
    _refuse_symlink_path(path)
    return exclusive_file_lock(path, timeout=10, follow_symlinks=False)


def stable_server_config(cli_binary: Path) -> dict:
    """Project a generic user route without a Brain or rotating runtime pin."""
    if not cli_binary.is_absolute():
        raise ValueError("MCP bootstrap path must be absolute")
    return {"command": str(cli_binary), "args": ["mcp", "serve"], "env": {}}


def require_no_registered_integrations(vault: Path, target: Path) -> None:
    """A path-removal/move cannot orphan canonical integration intent."""
    from _bootstrap.file_transaction import FilePlan

    _, records = read_records(FilePlan(), vault, Path.home(), McpScope.PROJECT)
    if any(record["target_path"] == str(target) for record in records):
        raise ValueError(f"Remove registered MCP integrations for {target} before moving or unregistering it")


def require_launcher_capability(cli_binary: Path, *, supports_stdio: bool) -> None:
    """Refuse a CLI replacement that would strand a persisted user command."""
    if supports_stdio:
        return
    from _bootstrap.file_transaction import FilePlan

    plan = FilePlan()
    home = Path.home()
    _, records = read_records(plan, None, home, McpScope.USER)
    servers = [record["server_config"] for record in records]
    for client in _clients(McpClient.ALL, McpScope.USER):
        current = observed_server(plan, client, _config_path(client, McpScope.USER, None, home))
        if current is not None:
            servers.append(current)
    if any(server.get("command") == str(cli_binary.absolute()) and server.get("args") == ["mcp", "serve"] for server in servers):
        raise ValueError("CLI replacement would strand user MCP registrations; explicitly remove/migrate them first")

class McpClient(str, Enum):
    CLAUDE = "claude"
    CODEX = "codex"
    GROK = "grok"
    ALL = "all"


class McpScope(str, Enum):
    PROJECT = "project"
    LOCAL = "local"
    USER = "user"


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


def _ensure_hook(plan, target: Path, vault: Path, python: str, previous: str | None = None) -> tuple[Path, str]:
    from _bootstrap import mcp_state

    path = target / mcp_state.CLAUDE_LOCAL_SETTINGS_FILE
    settings = _json_object(plan, path)
    hooks = settings.get("hooks")
    if hooks is None:
        hooks = {}
        settings["hooks"] = hooks
    if not isinstance(hooks, dict):
        raise ValueError(f"hooks must be an object: {path}")
    command = mcp_state.build_session_hook_command(vault, target, python_path=python)
    for entry in hooks.get("SessionStart", []):
        for child in entry.get("hooks", []) if isinstance(entry, dict) else []:
            candidate = child.get("command") if isinstance(child, dict) else None
            if isinstance(candidate, str) and mcp_state.is_session_hook_command(candidate, vault, target) and candidate not in (previous, command):
                raise ValueError(f"Modified/legacy Brain hook requires explicit migration or recovery: {path}")
    kept = _without_owned_hook(hooks.get("SessionStart", []), previous)
    if command != previous:
        kept = _without_owned_hook(kept, command)
    child = {"type": "command", "command": command}
    if sys.platform == "win32":
        child["shell"] = "powershell"
    kept.append({"hooks": [child]})
    hooks["SessionStart"] = kept
    _write_json(plan, path, settings)
    return path, command


def _without_owned_hook(entries, command):
    if not isinstance(entries, list):
        raise ValueError("SessionStart hooks must be an array")
    kept = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
            kept.append(entry)
            continue
        expected = {"type": "command", "command": command}
        if sys.platform == "win32":
            expected["shell"] = "powershell"
        for child in entry["hooks"]:
            if isinstance(child, dict) and command is not None and child.get("command") == command:
                if child != expected or set(entry) != {"hooks"}:
                    raise ValueError("Modified owned SessionStart hook preserved; resolve its ownership before repair/removal")
        children = [child for child in entry["hooks"] if not (
            isinstance(child, dict) and child.get("type") == "command"
            and command is not None and child.get("command") == command
        )]
        if children:
            kept.append({**entry, "hooks": children})
    return kept


def _remove_hook(plan, path: Path, vault: Path, target: Path, command: str | None) -> None:
    from _bootstrap import mcp_transport

    settings = _json_object(plan, path)
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return
    original = hooks.get("SessionStart", [])
    kept = _without_owned_hook(original, command)
    if kept == original:
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
    if plan.read_bytes(path) is None:
        return path, []
    data = _json_object(plan, path)
    if data.get("version") != LEDGER_VERSION:
        raise ValueError(f"MCP registration migration required: {path}")
    records = data.get("records")
    if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
        raise ValueError(f"MCP init-state records must be objects: {path}")
    return path, list(records)


def read_records(plan, vault: Path | None, home: Path, scope: McpScope):
    if scope is McpScope.USER:
        path = user_ledger_path(home)
        if plan.read_bytes(path) is None:
            return path, []
        data = _json_object(plan, path)
        if data.get("version") != LEDGER_VERSION or not isinstance(data.get("records"), list):
            raise ValueError(f"Invalid machine MCP ledger: {path}")
        records = data["records"]
    else:
        if vault is None:
            raise ValueError("Workspace registration requires a selected Brain")
        path, records = _init_records(plan, vault)
    identities = set()
    for record in records:
        if not isinstance(record, dict) or record.get("schema") != REGISTRATION_SCHEMA:
            raise ValueError(f"MCP registration migration required: {path}")
        client = McpClient(record.get("client"))
        native_scope = McpScope(record.get("scope"))
        if client is McpClient.ALL or (native_scope is McpScope.LOCAL and client is not McpClient.CLAUDE):
            raise ValueError(f"Invalid native client/scope in {path}")
        if (scope is McpScope.USER) != (native_scope is McpScope.USER):
            raise ValueError(f"MCP registration ownership migration required: {path}")
        enabled = record.get("transport_enabled", True)
        if not isinstance(enabled, bool) or (not enabled and (client is not McpClient.CLAUDE or native_scope is McpScope.USER)):
            raise ValueError(f"Invalid transport/bootstrap intent: {path}")
        target_value = record.get("target_path")
        if native_scope is McpScope.USER:
            if target_value is not None:
                raise ValueError(f"User registration cannot own a target: {path}")
            target = None
        else:
            if not isinstance(target_value, str) or not Path(target_value).is_absolute():
                raise ValueError(f"Invalid registered workspace path: {path}")
            target = Path(target_value)
        expected = _config_path(client, native_scope, target, home).absolute()
        if record.get("config_path") != str(expected) or not isinstance(record.get("server_config"), dict):
            raise ValueError(f"Invalid MCP ownership evidence: {path}")
        validate_server(record["server_config"], path)
        if client is McpClient.CLAUDE and target is not None:
            from _bootstrap.mcp_state import build_session_hook_command, bootstrap_line_for_target

            hook = record.get("hook_command")
            expected_hook = build_session_hook_command(vault, target, python_path=record["server_config"].get("command", ""))
            if hook is not None and hook != expected_hook:
                raise ValueError(f"Invalid owned hook evidence; migration/recovery required: {path}")
            if record.get("bootstrap_line") not in (None, bootstrap_line_for_target(target)):
                raise ValueError(f"Invalid owned bootstrap evidence: {path}")
        identity = _record_id(record)
        if identity in identities:
            raise ValueError(f"Duplicate MCP ownership claim: {path}")
        identities.add(identity)
    return path, list(records)


def validate_server(server: dict, path: Path) -> None:
    if (not isinstance(server.get("command"), str) or not server["command"].strip()
            or not isinstance(server.get("args"), list)
            or any(not isinstance(argument, str) for argument in server["args"])
            or not isinstance(server.get("env", {}), dict)
            or any(not isinstance(key, str) or not isinstance(value, str)
                   for key, value in server.get("env", {}).items())):
        raise ValueError(f"Invalid MCP command ownership evidence: {path}")


def _record_id(record: dict) -> tuple[object, ...]:
    return (
        record.get("client"),
        record.get("scope"),
        record.get("target_path"),
        record.get("config_path"),
    )


def _save_records(plan, path: Path, records: list[dict]) -> None:
    if records:
        _write_json(plan, path, {"version": LEDGER_VERSION, "records": records})
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
        "schema": REGISTRATION_SCHEMA,
        "client": client.value,
        "scope": scope.value,
        "target_path": str(target) if target is not None else None,
        "config_path": str(config_path),
        "server_name": "brain",
        "server_config": server_config,
        "transport_enabled": True,
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
    previous: dict | None = None,
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
                plan, target, vault, server["command"], (previous or {}).get("hook_command")
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


def plan_reverse_registration(plan: FilePlan, vault: Path, target: Path) -> None:
    if target == vault or target.is_relative_to(vault / "_Workspaces"):
        return
    from _bootstrap.workspace_binding import extract_workspace_binding, read_workspace_manifest

    binding = extract_workspace_binding(read_workspace_manifest(target))
    if binding is None:
        raise ValueError(f"Cannot recover unbound workspace: {target}")
    from _common._workspace import manifest_workspace_reference

    manifest = read_workspace_manifest(target)
    slug = manifest_workspace_reference(manifest).split("/", 1)[1]
    path = vault / ".brain/local/workspaces.json"
    data = _json_object(plan, path)
    workspaces = data.get("workspaces", {})
    if not isinstance(workspaces, dict):
        raise ValueError(f"Workspace registry requires explicit recovery: {path}")
    existing = workspaces.get(slug)
    if existing is not None and existing != {"path": str(target)}:
        raise ValueError(f"Conflicting reverse registration for {slug}: {path}")
    _write_json(plan, path, {**data, "workspaces": {**workspaces, slug: {"path": str(target)}}})


def _configure_plan(
    vault: Path | None,
    home: Path,
    target: Path | None,
    scope: McpScope,
    clients: tuple[McpClient, ...],
    server: dict,
    *,
    plan=None,
    repair: bool = False,
):
    from _bootstrap.file_transaction import FilePlan

    plan = plan or FilePlan()
    if target is not None:
        plan_target_admission(plan, vault, target)
    path, records = read_records(plan, vault, home, scope)
    if target is not None and not repair:
        plan_reverse_registration(plan, vault, target)
    for client in clients:
        config_path = _config_path(client, scope, target, home).absolute()
        owned = next((item for item in records if _record_id(item) == (
            client.value, scope.value, str(target) if target is not None else None, str(config_path)
        )), None)
        current = observed_server(plan, client, config_path)
        if repair and owned is None:
            if current is not None:
                raise ValueError(f"Unowned MCP entry requires explicit migration/admission: {config_path}")
            continue
        if repair and owned is not None and not owned.get("transport_enabled", True):
            if current is not None:
                raise ValueError(f"Removed transport has reappeared without installation intent: {config_path}")
            if _remaining_claude_route(plan, records, owned, target, home):
                _ensure_bootstrap(plan, target, local=scope is McpScope.LOCAL)
                _, command = _ensure_hook(plan, target, vault, server["command"], owned.get("hook_command"))
                records = [{**item, "server_config": server, "hook_command": command} if item is owned else item for item in records]
            else:
                from _bootstrap import mcp_state

                _remove_bootstrap(plan, target / (mcp_state.CLAUDE_LOCAL_MD_FILE if scope is McpScope.LOCAL else mcp_state.CLAUDE_MD_FILE),
                                  mcp_state.bootstrap_line_for_target(target))
                _remove_hook(plan, target / mcp_state.CLAUDE_LOCAL_SETTINGS_FILE, vault, target, owned.get("hook_command"))
                records = [item for item in records if item is not owned]
            continue
        if current is not None and (owned is None or current != owned["server_config"]):
            raise ValueError(f"MCP entry is unowned or modified; preserved: {config_path}")
        previous = owned
        if client is McpClient.CLAUDE and target is not None and previous is None:
            previous = next((item for item in records if item["client"] == "claude" and item["target_path"] == str(target)), None)
        record = _configure_client(plan, vault, home, target, scope, client, server, previous)
        identity = _record_id(record)
        records = [item for item in records if _record_id(item) != identity]
        records.append(record)
    records.sort(key=lambda item: tuple(str(part) for part in _record_id(item)))
    _save_records(plan, path, records)
    return plan


def plan_target_admission(plan, vault: Path, target: Path) -> None:
    """Bind project mutations to the observed directory, route and Brain identity."""
    import vault_registry

    plan.observe_directory(target)
    for path in (vault / ".brain-core/VERSION", target / ".brain/local/workspace.yaml",
                 target / ".brain/workspace.yaml", Path(vault_registry.registry_path())):
        plan.read_bytes(path)
    _validate_target(vault, target)


def observed_server(plan, client: McpClient, path: Path) -> dict | None:
    """Inspect the exact native slot, including user-added transport options."""
    if client is McpClient.CLAUDE:
        document = _json_object(plan, path)
        servers = document.get("mcpServers", {})
        if not isinstance(servers, dict):
            raise ValueError(f"mcpServers must be an object: {path}")
        server = servers.get("brain")
    else:
        import tomllib

        content = plan.read_text(path) or ""
        _validate_toml(content, path)
        servers = tomllib.loads(content).get("mcp_servers", {})
        if not isinstance(servers, dict):
            raise ValueError(f"mcp_servers must be a table: {path}")
        server = servers.get("brain")
    if server is not None and not isinstance(server, dict):
        raise ValueError(f"Brain MCP entry must be an object: {path}")
    # Native TOML omits an empty env table; it has the same transport meaning.
    if server is not None:
        server = {**server, "env": server.get("env", {})}
    return server


def _remaining_claude_route(plan, records, removed, target: Path, home: Path) -> bool:
    for item in records:
        if (item is not removed and item["client"] == "claude" and item["target_path"] == str(target)
                and item.get("transport_enabled", True)
                and observed_server(plan, McpClient.CLAUDE, _config_path(McpClient.CLAUDE, McpScope(item["scope"]), target, home)) == item["server_config"]):
            return True
    _, shared = read_records(plan, None, home, McpScope.USER)
    return any(item["client"] == "claude" and
               observed_server(plan, McpClient.CLAUDE, _config_path(McpClient.CLAUDE, McpScope.USER, None, home)) == item["server_config"]
               for item in shared)


def _remove_plan(vault: Path, home: Path, target: Path | None, scope: McpScope, clients: tuple[McpClient, ...], *, plan=None, preserve_shared_routes=True):
    from _bootstrap import mcp_state
    from _bootstrap.file_transaction import FilePlan

    plan = plan or FilePlan()
    init_path, records = read_records(plan, vault, home, scope)
    wanted = {client.value for client in clients}
    expected_paths = {
        client.value: _config_path(client, scope, target, home).absolute()
        for client in clients
    }
    for client in clients:
        current = observed_server(plan, client, expected_paths[client.value])
        owned = next((record for record in records if record.get("client") == client.value
                      and record.get("scope") == scope.value
                      and record.get("target_path") == (str(target) if target is not None else None)), None)
        if current is not None and (owned is None or current != owned["server_config"]):
            raise ValueError(f"MCP removal refused: unowned or modified entry preserved: {expected_paths[client.value]}")
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
        removed = observed_server(plan, client, config_path) is None
        if client is McpClient.CLAUDE:
            removed = _remove_json_server(plan, config_path, server) or removed
            remaining_route = target is not None and preserve_shared_routes and _remaining_claude_route(plan, records, record, target, home)
            if remaining_route:
                retained.append({**record, "transport_enabled": False})
                continue
            if target is not None and (not remaining_route or not preserve_shared_routes):
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
                    record.get("hook_command"),
                )
        elif client is McpClient.GROK:
            from _bootstrap import grok_mcp

            rule = (home if scope is McpScope.USER else target) / mcp_state.GROK_RULE_REL
            if plan.read_text(rule) not in (None, grok_mcp.RULE_CONTENT):
                raise ValueError(f"Modified Grok bootstrap rule preserved; resolve ownership before removal: {rule}")
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
    from _bootstrap.runtime import target_managed_python

    return str(target_managed_python(vault))


def _validate_toml(content: str, path: Path) -> None:
    if not content:
        return
    import tomllib

    try:
        tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"MCP TOML is malformed: {path}: {exc}") from exc
