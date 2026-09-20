#!/usr/bin/env python3
"""Launcher-safe MCP transport helpers shared by setup/configure/repair."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from _bootstrap.mcp_state import (
    BRAIN_SERVER_NAME,
    CLAUDE_LOCAL_MD_FILE,
    CLAUDE_LOCAL_SETTINGS_FILE,
    CLAUDE_MD_BOOTSTRAP_PROJECT,
    CLAUDE_MD_BOOTSTRAP_VAULT,
    CLAUDE_MD_FILE,
    CLAUDE_PROJECT_CONFIG_FILE,
    CLAUDE_USER_CONFIG_FILE,
    CODEX_CONFIG_REL,
    INIT_STATE_REL,
    bootstrap_line_for_target,
    migrate_bootstrap_text,
    build_mcp_config,
    build_session_hook_command,
    configured_vault_root,
    is_session_hook_command,
    matching_records,
    read_toml_server_config,
    record_init_target,
    remove_toml_server,
    remove_init_records as _remove_init_records,
    session_hook_python,
    write_toml_config,
)
from _bootstrap.runtime import target_managed_python
from _bootstrap.workspace_binding import (
    WorkspaceBindingError,
    converge_workspace_binding,
    resolve_local_brain_alias,
)
from _bootstrap.workspace_scaffold import GitInspectionError, ensure_brain_ignore_rules
from _common import join_argv, safe_write, safe_write_json


SUPPORTED_CLIENTS = ("claude", "codex", "grok")
SUPPORTED_SCOPES = ("project", "local", "user")


class InitTransportError(RuntimeError):
    """Raised when MCP transport configuration cannot be applied cleanly."""


def info(msg: str) -> None:
    print(f"  {msg}", file=sys.stderr)


def header(msg: str) -> None:
    print(f"\n{'-' * 60}", file=sys.stderr)
    print(f"  {msg}", file=sys.stderr)
    print(f"{'-' * 60}", file=sys.stderr)


def fatal(msg: str):
    print(f"Error: {msg}", file=sys.stderr)
    raise SystemExit(1)


def _resolve_managed_python(vault_root: Path) -> str:
    try:
        return str(target_managed_python(vault_root))
    except RuntimeError as exc:
        raise InitTransportError(str(exc)) from exc


def _resolve_clients_or_error(client_arg: str, scope: str) -> Tuple[List[str], List[str]]:
    warnings: List[str] = []

    if client_arg == "all":
        clients = list(SUPPORTED_CLIENTS)
    else:
        clients = [client_arg]

    if scope != "local":
        return clients, warnings

    if client_arg in {"codex", "grok"}:
        raise InitTransportError(
            f"{client_arg.title()} does not support local scope.\n"
            "Use --client claude --local, or choose project/user scope."
        )

    if client_arg == "all":
        warnings.append(
            "Codex and Grok have no supported local scope. Applying Claude local setup only."
        )
        return ["claude"], warnings

    return clients, warnings


def _claude_config_path(scope: str, target_dir: Optional[Path]) -> Path:
    if scope == "user":
        return Path.home() / CLAUDE_USER_CONFIG_FILE
    if scope == "local":
        return (target_dir or Path.cwd()) / CLAUDE_LOCAL_SETTINGS_FILE
    return (target_dir or Path.cwd()) / CLAUDE_PROJECT_CONFIG_FILE


def _codex_config_path(scope: str, target_dir: Optional[Path]) -> Path:
    if scope == "local":
        fatal("Codex does not support local scope.")
    if scope == "user":
        return Path.home() / CODEX_CONFIG_REL
    return (target_dir or Path.cwd()) / CODEX_CONFIG_REL


def _has_claude_cli() -> bool:
    return shutil.which("claude") is not None


def _register_claude_via_cli(
    server_config: Dict[str, Any],
    scope: str,
    target_dir: Optional[Path],
) -> bool:
    config_json = json.dumps(server_config)
    cmd = [
        "claude",
        "mcp",
        "add-json",
        BRAIN_SERVER_NAME,
        config_json,
        "--scope",
        scope,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(target_dir) if target_dir else None,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        info(f"claude CLI unavailable ({exc}), falling back to direct file edit")
        return False

    if result.returncode == 0:
        return True

    info(f"claude mcp add-json exited {result.returncode}, falling back to direct file edit")
    if result.stderr.strip():
        info(f"  stderr: {result.stderr.strip()}")
    return False


def _read_json_safe(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _delete_file_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _remove_empty_parent_dirs(path: Path, stop_at: Path) -> None:
    current = path.parent
    stop = stop_at.resolve()
    while current.exists() and current != stop:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _config_cleanup_stop(path: Path) -> Path:
    parent = path.parent
    if parent.name in (".claude", ".codex"):
        return parent.parent
    return parent


def _cleanup_json_file(path: Path, data: Dict[str, Any], stop_at: Path) -> None:
    if data:
        safe_write_json(path, data)
        return
    _delete_file_if_exists(path)
    _remove_empty_parent_dirs(path, stop_at)


def _upsert_mcp_server(json_path: Path, server_config: Dict[str, Any]) -> None:
    existing = _read_json_safe(json_path)
    if "mcpServers" not in existing or not isinstance(existing["mcpServers"], dict):
        existing["mcpServers"] = {}
    existing["mcpServers"][BRAIN_SERVER_NAME] = server_config
    safe_write_json(json_path, existing)
    info(f"Wrote {BRAIN_SERVER_NAME} -> {json_path}")


def _remove_json_server(json_path: Path, server_config: Dict[str, Any]) -> bool:
    data = _read_json_safe(json_path)
    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or BRAIN_SERVER_NAME not in servers:
        info(f"No recorded {BRAIN_SERVER_NAME} entry found in {json_path}")
        return False
    if servers[BRAIN_SERVER_NAME] != server_config:
        info(f"Skipping {json_path}: current {BRAIN_SERVER_NAME} entry does not match recorded config")
        return False

    del servers[BRAIN_SERVER_NAME]
    if not servers:
        data.pop("mcpServers", None)

    _cleanup_json_file(json_path, data, _config_cleanup_stop(json_path))
    info(f"Removed {BRAIN_SERVER_NAME} from {json_path}")
    return True


def write_project_mcp_json(server_config: Dict[str, Any], target_dir: Path) -> None:
    _upsert_mcp_server(target_dir / CLAUDE_PROJECT_CONFIG_FILE, server_config)


def write_local_settings_json(server_config: Dict[str, Any], target_dir: Path) -> None:
    _upsert_mcp_server(target_dir / CLAUDE_LOCAL_SETTINGS_FILE, server_config)


def write_user_claude_json(server_config: Dict[str, Any]) -> None:
    _upsert_mcp_server(Path.home() / CLAUDE_USER_CONFIG_FILE, server_config)


def _claude_project_approval_state(target_dir: Path) -> Dict[str, Any]:
    data = _read_json_safe(Path.home() / CLAUDE_USER_CONFIG_FILE)

    projects = data.get("projects")
    project_entry = projects.get(str(target_dir.resolve())) if isinstance(projects, dict) else {}
    if not isinstance(project_entry, dict):
        project_entry = {}

    enabled = project_entry.get("enabledMcpjsonServers", [])
    disabled = project_entry.get("disabledMcpjsonServers", [])
    enabled = enabled if isinstance(enabled, list) else []
    disabled = disabled if isinstance(disabled, list) else []

    user_servers = data.get("mcpServers")
    user_server = user_servers.get(BRAIN_SERVER_NAME) if isinstance(user_servers, dict) else None

    user_scope_vault_root = None
    resolved_user_root = configured_vault_root(user_server)
    if resolved_user_root is not None:
        user_scope_vault_root = str(resolved_user_root)

    return {
        "approved": BRAIN_SERVER_NAME in enabled,
        "disabled": BRAIN_SERVER_NAME in disabled,
        "has_user_scope_server": isinstance(user_server, dict),
        "user_scope_vault_root": user_scope_vault_root,
    }


def claude_project_followup_notes(target_dir: Path) -> List[str]:
    state = _claude_project_approval_state(target_dir)
    if state["approved"]:
        return []

    notes: List[str] = []
    if state["disabled"]:
        notes.append(
            f'Claude currently has project-scoped ".mcp.json" server "{BRAIN_SERVER_NAME}" '
            f"disabled for {target_dir}."
        )
        notes.append(
            f'Open Claude Code in {target_dir} and re-enable "{BRAIN_SERVER_NAME}" via /mcp.'
        )
    else:
        notes.append(
            f'Claude has not approved project-scoped ".mcp.json" server "{BRAIN_SERVER_NAME}" '
            f"for {target_dir} yet."
        )
        notes.append(
            f'Open Claude Code in {target_dir} and run /mcp to approve "{BRAIN_SERVER_NAME}".'
        )

    if state["has_user_scope_server"]:
        source = state["user_scope_vault_root"] or str(Path.home() / CLAUDE_USER_CONFIG_FILE)
        notes.append(
            'Until you approve it, Claude may route `mcp__brain__*` calls to the '
            f'user-scoped "{BRAIN_SERVER_NAME}" from ~/.claude.json ({source}).'
        )

    notes.append("`claude mcp list` runs health checks, but it does not confirm project approval.")
    notes.append(
        f'Advanced: add "{BRAIN_SERVER_NAME}" to projects["{target_dir}"].enabledMcpjsonServers '
        "in ~/.claude.json by hand if you prefer not to use /mcp."
    )
    return notes


def ensure_claude_md(target_dir: Path, local: bool = False) -> Path:
    bootstrap = bootstrap_line_for_target(target_dir)
    rel_path = CLAUDE_LOCAL_MD_FILE if local else CLAUDE_MD_FILE
    claude_md = target_dir / rel_path

    try:
        existing = claude_md.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing = ""

    if not existing:
        safe_write(claude_md, f"{bootstrap}\n")
        info(f"Created {rel_path} with brain bootstrap")
        return claude_md

    updated = migrate_bootstrap_text(existing)
    if updated != existing:
        safe_write(claude_md, updated)
        existing = updated

    if bootstrap in existing:
        info(f"{rel_path} already has bootstrap line")
        return claude_md

    separator = "\n" if existing.endswith("\n") else "\n\n"
    safe_write(claude_md, f"{existing}{separator}{bootstrap}\n")
    info(f"Appended brain bootstrap to {rel_path}")
    return claude_md


def _remove_bootstrap_line(path: Path, bootstrap: str) -> bool:
    if not path.is_file():
        return False

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise InitTransportError(f"failed to read {path}: {exc}") from exc

    matched = any(line.strip() == bootstrap for line in lines)
    if not matched:
        return False
    kept = [line for line in lines if line.strip() != bootstrap]
    while kept and not kept[-1].strip():
        kept.pop()

    try:
        if kept:
            safe_write(path, "\n".join(kept) + "\n")
        else:
            _delete_file_if_exists(path)
    except OSError as exc:
        raise InitTransportError(f"failed to update {path}: {exc}") from exc
    return True


def cleanup_claude_bootstrap(
    target_dir: Path,
    *,
    local: bool = False,
    bootstrap_line: Optional[str] = None,
) -> bool:
    rel_path = CLAUDE_LOCAL_MD_FILE if local else CLAUDE_MD_FILE
    path = target_dir / rel_path
    if bootstrap_line is not None:
        return _remove_bootstrap_line(path, bootstrap_line)
    lines = (
        bootstrap_line_for_target(target_dir),
        "ALWAYS DO FIRST: Call MCP `brain_session`, else read `.brain-core/index.md` if it exists.",
        "ALWAYS DO FIRST: Call MCP `brain_session`; if MCP is unavailable, run `brain session --json` from this workspace.",
    )
    changed = False
    for line in lines:
        changed = _remove_bootstrap_line(path, line) or changed
    return changed


def _converge_workspace_manifest(
    target_dir: Path,
    *,
    vault_root: Path | None = None,
    brain_id: str | None = None,
    allow_rebind: bool = False,
):
    resolved_brain = brain_id
    if resolved_brain is None:
        if vault_root is None:
            raise WorkspaceBindingError(
                "Workspace binding now requires an explicit Brain identity.\n"
                "Pass vault_root or brain_id when converging the workspace manifest."
            )
        resolved_brain = resolve_local_brain_alias(vault_root)

    return converge_workspace_binding(
        target_dir,
        brain=resolved_brain,
        allow_rebind=allow_rebind,
    )


def ensure_session_start_hook(
    target_dir: Path,
    vault_root: Path,
    *,
    python_path: str | None = None,
) -> Path:
    settings_path = target_dir / CLAUDE_LOCAL_SETTINGS_FILE
    settings = _read_json_safe(settings_path)

    if "hooks" not in settings or not isinstance(settings["hooks"], dict):
        settings["hooks"] = {}

    hook_command = build_session_hook_command(vault_root, target_dir, python_path=python_path)
    session_entries = settings["hooks"].get("SessionStart", [])
    kept_entries, changed = _strip_brain_session_hooks(session_entries, vault_root, target_dir)

    if changed:
        settings["hooks"]["SessionStart"] = kept_entries
    else:
        settings["hooks"].setdefault("SessionStart", kept_entries)

    new_hook = {
        "type": "command",
        "command": hook_command,
    }
    if sys.platform == "win32":
        new_hook["shell"] = "powershell"
    new_entry = {"hooks": [new_hook]}
    settings["hooks"].setdefault("SessionStart", []).append(new_entry)
    safe_write_json(settings_path, settings)
    info("Added SessionStart hook for session.start")
    return settings_path


def _strip_brain_session_hooks(
    session_entries: Any,
    vault_root: Path,
    target_dir: Path,
    *,
    extra_valid: tuple[str, ...] = (),
) -> tuple[list[Any], bool]:
    """Return SessionStart entries with Brain hooks removed."""
    kept_entries: list[Any] = []
    changed = False
    valid_commands = set(extra_valid)

    if not isinstance(session_entries, list):
        return kept_entries, True

    for entry in session_entries:
        if not isinstance(entry, dict):
            kept_entries.append(entry)
            continue

        hook_items = entry.get("hooks", [])
        if not isinstance(hook_items, list):
            kept_entries.append(entry)
            continue

        kept_hooks = []
        for hook in hook_items:
            command = hook.get("command") if isinstance(hook, dict) else None
            if command in valid_commands or is_session_hook_command(command, vault_root, target_dir):
                changed = True
                continue
            kept_hooks.append(hook)

        if kept_hooks:
            new_entry = dict(entry)
            new_entry["hooks"] = kept_hooks
            kept_entries.append(new_entry)
        elif hook_items:
            changed = True

    return kept_entries, changed


def _remove_session_start_hook(
    settings_path: Path,
    vault_root: Path,
    target_dir: Path,
    recorded_command: Optional[str] = None,
    python_path: str | None = None,
) -> None:
    settings = _read_json_safe(settings_path)
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return

    fresh_command = build_session_hook_command(vault_root, target_dir, python_path=python_path)
    valid_commands = [fresh_command]
    if recorded_command:
        valid_commands.append(recorded_command)
    session_entries = hooks.get("SessionStart", [])
    kept_entries, changed = _strip_brain_session_hooks(
        session_entries,
        vault_root,
        target_dir,
        extra_valid=tuple(valid_commands),
    )

    if not changed:
        return

    if kept_entries:
        hooks["SessionStart"] = kept_entries
    else:
        hooks.pop("SessionStart", None)

    if not hooks:
        settings.pop("hooks", None)

    _cleanup_json_file(settings_path, settings, target_dir)


def _warn_if_user_scope_exists(client: str, scope: str, server_config: Dict[str, Any]) -> None:
    if scope == "user":
        return

    if client == "claude":
        current = _read_json_safe(Path.home() / CLAUDE_USER_CONFIG_FILE)
        servers = current.get("mcpServers", {})
        if isinstance(servers, dict) and BRAIN_SERVER_NAME in servers:
            if scope == "project":
                info(
                    f'Note: "{BRAIN_SERVER_NAME}" is already registered globally '
                    f"({Path.home() / CLAUDE_USER_CONFIG_FILE}). Claude only prefers the "
                    "project .mcp.json entry after you approve it via /mcp."
                )
            else:
                info(
                    f'Note: "{BRAIN_SERVER_NAME}" is already registered globally '
                    f"({Path.home() / CLAUDE_USER_CONFIG_FILE}). Local Claude config will take priority."
                )
        return

    if client != "codex":
        return

    current_codex = read_toml_server_config(Path.home() / CODEX_CONFIG_REL)
    if current_codex:
        info(
            f'Note: "{BRAIN_SERVER_NAME}" is already registered globally '
            f"({Path.home() / CODEX_CONFIG_REL}). Project Codex config will take priority "
            "once this project is trusted and its project-scoped `brain` MCP is enabled."
        )


def register_claude(
    vault_root: Path,
    server_config: Dict[str, Any],
    scope: str,
    target_dir: Optional[Path],
) -> Dict[str, Any]:
    config_path = _claude_config_path(scope, target_dir)
    method = ""

    if _has_claude_cli():
        info("Found `claude` CLI")
        if _register_claude_via_cli(server_config, scope, target_dir):
            method = "claude CLI"

    if not method:
        if scope == "user":
            write_user_claude_json(server_config)
            method = "~/.claude.json (direct)"
        elif scope == "local":
            write_local_settings_json(server_config, target_dir or Path.cwd())
            method = f"{config_path} (direct)"
        else:
            write_project_mcp_json(server_config, target_dir or Path.cwd())
            method = f"{config_path} (direct)"

    record: Dict[str, Any] = {
        "client": "claude",
        "scope": scope,
        "target_path": str(target_dir) if target_dir else None,
        "config_path": str(config_path),
        "server_name": BRAIN_SERVER_NAME,
        "server_config": server_config,
    }

    if target_dir:
        hook_python = session_hook_python(server_config)
        bootstrap_path = ensure_claude_md(target_dir, local=scope == "local")
        hook_path = ensure_session_start_hook(target_dir, vault_root, python_path=hook_python)
        record["bootstrap_path"] = str(bootstrap_path)
        record["bootstrap_line"] = bootstrap_line_for_target(target_dir)
        record["hook_path"] = str(hook_path)
        record["hook_command"] = build_session_hook_command(vault_root, target_dir, python_path=hook_python)

    record["method"] = method
    return record


def register_grok(
    server_config: Dict[str, Any], scope: str, target_dir: Optional[Path]
) -> Dict[str, Any]:
    """Install native Grok transport and bootstrap as one preflighted mutation."""
    from _bootstrap import grok_mcp
    from _bootstrap.file_transaction import FilePlan, apply_file_changes

    if scope not in {"user", "project"}:
        raise InitTransportError("Grok supports project and user MCP scopes only")
    root = Path.home() if scope == "user" else (target_dir or Path.cwd())
    plan = FilePlan()
    config_path, rule_path = grok_mcp.plan_configure(plan, root, server_config)
    apply_file_changes(plan.changes())
    return {
        "client": "grok",
        "scope": scope,
        "target_path": str(target_dir) if target_dir else None,
        "config_path": str(config_path),
        "server_name": BRAIN_SERVER_NAME,
        "server_config": server_config,
        "bootstrap_path": str(rule_path),
        "method": f"{config_path} (transactional direct)",
    }


def register_codex(
    server_config: Dict[str, Any],
    scope: str,
    target_dir: Optional[Path],
) -> Dict[str, Any]:
    config_path = _codex_config_path(scope, target_dir)
    write_toml_config(server_config, config_path)
    info(f"Wrote {BRAIN_SERVER_NAME} -> {config_path}")
    return {
        "client": "codex",
        "scope": scope,
        "target_path": str(target_dir) if target_dir else None,
        "config_path": str(config_path),
        "server_name": BRAIN_SERVER_NAME,
        "server_config": server_config,
        "method": f"{config_path} (direct)",
    }


def _remove_toml_server(config_path: Path, server_config: Dict[str, Any]) -> bool:
    current = read_toml_server_config(config_path)
    if current is None:
        info(f"No recorded {BRAIN_SERVER_NAME} entry found in {config_path}")
        return False
    if current != server_config:
        info(f"Skipping {config_path}: current {BRAIN_SERVER_NAME} entry does not match recorded config")
        return False
    if not remove_toml_server(config_path, server_config):
        return False
    _remove_empty_parent_dirs(config_path, _config_cleanup_stop(config_path))
    info(f"Removed {BRAIN_SERVER_NAME} from {config_path}")
    return True


def _remove_record(vault_root: Path, record: Dict[str, Any]) -> bool:
    client = record["client"]
    scope = record["scope"]
    config_path = Path(record["config_path"])
    server_config = record["server_config"]
    removed = False

    if client == "claude":
        removed = _remove_json_server(config_path, server_config)
        target_path = record.get("target_path")
        if removed and target_path:
            target_dir = Path(target_path)
            try:
                cleanup_claude_bootstrap(
                    target_dir,
                    local=scope == "local",
                    bootstrap_line=record.get("bootstrap_line"),
                )
            except InitTransportError as exc:
                info(f"Warning: could not clean Claude bootstrap for {target_dir}: {exc}")
            _remove_session_start_hook(
                Path(record.get("hook_path", target_dir / CLAUDE_LOCAL_SETTINGS_FILE)),
                vault_root,
                target_dir,
                recorded_command=record.get("hook_command"),
                python_path=session_hook_python(server_config),
            )
        return removed

    if client == "grok":
        from _bootstrap import grok_mcp
        from _bootstrap.file_transaction import FilePlan, apply_file_changes

        plan = FilePlan()
        root = config_path.parent.parent
        try:
            removed = grok_mcp.plan_remove(plan, root, server_config)
            apply_file_changes(plan.changes())
        except (OSError, ValueError, RuntimeError) as exc:
            surviving = getattr(exc, "surviving_paths", ())
            detail = (
                f"; surviving files: {', '.join(map(str, surviving))}"
                if surviving
                else ""
            )
            raise InitTransportError(str(exc) + detail) from exc
        return removed

    if client == "codex":
        return _remove_toml_server(config_path, server_config)

    info(f"Unknown client in init state: {client}")
    return False


def _scope_configure_flags(scope: str, target_dir: Optional[Path]) -> List[str]:
    if scope == "user":
        return ["--user"]
    assert target_dir is not None
    flags = ["--workspace", str(target_dir)]
    if scope == "local":
        flags.append("--local")
    return flags


def _is_project_scope(scope: str, target_dir: Optional[Path]) -> bool:
    return scope == "project" and target_dir is not None


def mcp_followup_notes(clients: List[str], scope: str, target_dir: Optional[Path]) -> List[str]:
    """Return shared post-registration verification guidance."""
    project_scope = _is_project_scope(scope, target_dir)
    notes: List[str] = []
    if "claude" in clients:
        if project_scope:
            notes.append("Claude:   open Claude Code in this directory and use /mcp to approve `brain` if prompted")
            notes.append(
                "Verify:   ask Claude to call `session_start` and confirm `environment.vault_root`"
            )
        else:
            notes.append("Verify:   claude mcp list")
    if "codex" in clients:
        if project_scope:
            notes.append(
                "Codex:    trust this project and ensure the project-scoped `brain` MCP is enabled if prompted"
            )
            notes.append(
                "Verify:   ask Codex to call `session_start` and confirm `environment.vault_root`"
            )
            notes.append("Health:   codex mcp list")
        else:
            notes.append("Verify:   codex mcp list")
    if "grok" in clients:
        if project_scope:
            notes.append(
                "Grok: open this directory and review its folder-trust prompt to load project MCP and rules"
            )
        notes.append("Verify: grok inspect; grok mcp doctor brain")
        notes.append(
            "Bootstrap: ask Grok to call `session_start` and confirm `environment.vault_root`"
        )
    return notes


def _scope_label(scope: str, target_dir: Optional[Path]) -> str:
    if scope == "user":
        return "user (all projects)"
    return f"{scope} ({target_dir})"


def delegate_machine_command(
    command: str, request: dict, *, vault_root: Path | None = None,
    target_dir: Path | None = None, dry_run: bool = False,
) -> dict:
    """Use the installed compatible machine writer; never fall back to legacy writes."""
    import os

    binary = shutil.which("brain")
    if binary is None:
        candidate = Path.home() / ".local/bin" / ("brain.cmd" if sys.platform == "win32" else "brain")
        if candidate.is_file():
            binary = str(candidate)
    if binary is None:
        raise InitTransportError("Install the parity-capable Brain CLI before MCP registration changes.")
    environment = dict(os.environ)
    for key in ("BRAIN_CLI_BUNDLE", "BRAIN_CLI_DISTRIBUTION_ROOT", "PYTHONPATH", "PYTHONHOME", "BRAIN_VAULT_ROOT", "BRAIN_WORKSPACE_DIR"):
        environment.pop(key, None)
    probe = subprocess.run([binary, "command", "describe", f"mcp.{command}", "--json"],
                           capture_output=True, text=True, env=environment, timeout=30)
    try:
        description = json.loads(probe.stdout)
        version = description.get("command_version", description.get("payload", {}).get("command_version", 0))
    except (ValueError, AttributeError) as exc:
        raise InitTransportError("Installed CLI cannot describe the MCP writer; upgrade/reinstall it.") from exc
    if probe.returncode or version < (1 if command == "migrate" else 3):
        raise InitTransportError("Installed CLI predates MCP lifecycle parity; upgrade/reinstall it before writing registrations.")
    argv = [binary, "mcp", command, "--request-json", json.dumps(request), "--json"]
    if vault_root is not None:
        argv.extend(("--vault", str(vault_root)))
    if dry_run:
        argv.append("--dry-run")
    completed = subprocess.run(argv, cwd=target_dir, capture_output=True, text=True,
                               env=environment, timeout=300)
    try:
        result = json.loads(completed.stdout)
    except ValueError as exc:
        raise InitTransportError(f"MCP writer returned no valid receipt: {completed.stderr}") from exc
    if completed.returncode or result.get("status") != "ok":
        error = InitTransportError(f"MCP {command} incomplete: {json.dumps(result)}")
        error.committed_effects = result.get("committed_effects", [])
        raise error
    return result


def apply_mcp_transport_action(
    vault_root: Path,
    *,
    client_arg: str,
    scope: str,
    target_dir: Optional[Path],
    remove: bool,
    vault_self: bool = False,
) -> Dict[str, Any]:
    """Project explicit transport intent through the canonical registration owner."""
    from _bootstrap import mcp_registration as registration
    from _bootstrap.file_transaction import apply_file_changes

    clients, warnings = _resolve_clients_or_error(client_arg, scope)
    from _bootstrap import machine_cli
    managed_approvals = machine_cli.approvals_present()
    if scope == "user" or managed_approvals:
        request = {"client": client_arg, "scope": scope, "action": "remove" if remove else "configure"}
        if managed_approvals:
            try:
                envelope = machine_cli.invoke("mcp.configure", request, vault=vault_root if scope != "user" else None, target=target_dir)
            except (OSError, ValueError, RuntimeError) as exc:
                raise InitTransportError(str(exc)) from exc
            if envelope["status"] != "ok":
                error = InitTransportError("MCP/approval configuration incomplete: " + json.dumps(envelope))
                error.committed_effects = envelope.get("committed_effects", [])
                raise error
        else:
            envelope = delegate_machine_command("configure", request,
                                                 vault_root=vault_root if scope != "user" else None, target_dir=target_dir)
        return {
            "action": "remove" if remove else "configure", "status": envelope["result"]["status"],
            "scope": scope, "scope_label": _scope_label(scope, target_dir),
            "target_dir": target_dir, "clients": clients, "warnings": warnings,
            "committed_effects": envelope.get("committed_effects", []),
            "verification_notes": mcp_followup_notes(clients, scope, target_dir),
        }
    native_scope = registration.McpScope(scope)
    native_clients = tuple(registration.McpClient(client) for client in clients)
    target = target_dir.resolve() if target_dir is not None else vault_root.resolve()
    try:
        with registration.registration_lock(Path.home()):
            if remove:
                plan = registration._remove_plan(vault_root, Path.home(), target, native_scope, native_clients)
            else:
                registration._validate_target(vault_root, target)
                python = _resolve_managed_python(vault_root)
                server = build_mcp_config(python, vault_root, workspace_dir=target)
                plan = registration._configure_plan(vault_root, Path.home(), target, native_scope, native_clients, server)
            plan.validate()
            changes = plan.changes()
            apply_file_changes(changes, before_write=plan.validate_dependencies)
    except (OSError, ValueError, RuntimeError) as exc:
        error = InitTransportError(str(exc))
        error.surviving_paths = getattr(exc, "surviving_paths", ())
        error.committed_effects = [{"kind": "mcp.configure", "subject": f"file:{path}"} for path in error.surviving_paths]
        raise error from exc
    return {
        "action": "remove" if remove else "configure", "status": "changed" if changes else "noop",
        "scope": scope, "scope_label": _scope_label(scope, target), "target_dir": target,
        "clients": clients, "warnings": warnings,
        "committed_effects": [{"kind": "mcp.configure", "subject": f"file:{change.path}"} for change in changes],
        "verification_notes": mcp_followup_notes(clients, scope, target),
        "remove_command": join_argv(["brain", "mcp", "configure", "--vault", str(vault_root),
            "--workspace", str(target), "--request-json", json.dumps({"client": client_arg, "scope": scope, "action": "remove"})]),
    }


def _confirm_removal(scope_label: str, clients: List[str]) -> bool:
    client_label = ", ".join(clients)
    print(file=sys.stderr)
    print(f"Remove recorded Brain MCP registration for {client_label} at {scope_label}? [y/N]: ", end="", file=sys.stderr)
    response = input().strip()
    return response.lower() == "y"
