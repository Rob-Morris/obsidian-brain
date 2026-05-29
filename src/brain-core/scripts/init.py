#!/usr/bin/env python3
"""
init.py - Set up or remove Brain MCP server registrations for Claude and Codex.

Examples:
  1. Current directory:  python3 /vault/.brain-core/scripts/init.py
  2. Claude local only:  python3 /vault/.brain-core/scripts/init.py --client claude --local
  3. User default:       python3 /vault/.brain-core/scripts/init.py --client all --user
  4. Specific folder:    python3 /vault/.brain-core/scripts/init.py --project /path/to/project
  5. Explicit removal:   python3 /vault/.brain-core/scripts/init.py --remove --client all --project /path/to/project

Launcher-safe and dependency-light. Idempotent — safe to re-run. Never
clobbers non-brain MCP config.

Claude uses native project/local/user config surfaces.
Codex uses native project/user config surfaces only.
"""

import argparse
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, NoReturn, Optional, Tuple

# init.py is invoked from an ambient launcher Python at install/setup time.
# Keep imports launcher-safe and dependency-light so MCP registration and
# bootstrap-only folder binding remain available before any managed-runtime
# handoff has happened.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bootstrap.mcp_state as _mcp_state  # noqa: E402
from _bootstrap.mcp_state import (  # noqa: E402
    BRAIN_SERVER_NAME,
    CLAUDE_LOCAL_MD_FILE,
    CLAUDE_LOCAL_SETTINGS_FILE,
    CLAUDE_MD_FILE,
    CLAUDE_MD_BOOTSTRAP_PROJECT,
    CLAUDE_MD_BOOTSTRAP_VAULT,
    CLAUDE_PROJECT_CONFIG_FILE,
    CLAUDE_USER_CONFIG_FILE,
    CODEX_CONFIG_REL,
    INIT_STATE_REL,
    bootstrap_line_for_target,
    build_mcp_config,
    build_session_hook_command,
    config_targets_vault,
    configured_vault_root,
    is_vault_root as _is_vault_root,
    _load_init_state,
    matching_records,
    read_codex_server_config,
    record_init_target,
    remove_codex_server,
    remove_init_records as _remove_init_records,
    write_codex_config,
)
from _bootstrap.runtime import ensure_managed_runtime, find_launcher_python, required_modules_for_scope  # noqa: E402
from _bootstrap.workspace_binding import (  # noqa: E402
    WORKSPACE_MANIFEST_LEGACY_REL as WORKSPACE_MANIFEST_LEGACY_FILE,
    WORKSPACE_MANIFEST_REL as WORKSPACE_MANIFEST_FILE,
    WorkspaceBindingError,
    converge_workspace_binding,
    resolve_local_brain_alias,
)
from _common import safe_write, safe_write_json  # noqa: E402


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VENV_HELPER_REL = os.path.join(".brain-core", "scripts", "_common", "_venv.py")

DEFAULT_BRAIN_IGNORE_ENTRIES = (".brain/local/",)
CLAUDE_LOCAL_SETTINGS_IGNORE = ".claude/settings.local.json"
CLAUDE_LOCAL_MD_IGNORE = ".claude/CLAUDE.local.md"
CODEX_PROJECT_IGNORE_ENTRIES = (".codex/config.toml",)

SUPPORTED_CLIENTS = ("claude", "codex")
SUPPORTED_SCOPES = ("project", "local", "user")


class GitInspectionError(RuntimeError):
    """Raised when git metadata cannot be inspected reliably."""


class InitTransportError(RuntimeError):
    """Raised when MCP transport configuration cannot be applied cleanly."""


def _find_vault_root_from_script() -> Optional[Path]:
    """Walk up from this script's location to find a vault root."""
    current = Path(__file__).resolve().parent
    for _ in range(10):
        if _is_vault_root(current):
            return current
        current = current.parent
    return None


def find_vault_root(vault_arg: Optional[str] = None) -> Path:
    """Resolve vault root from argument, env var, or script location."""
    if vault_arg:
        path = Path(vault_arg).resolve()
        if _is_vault_root(path):
            return path
        fatal(f"Not a vault root: {path}")

    env_root = os.environ.get("BRAIN_VAULT_ROOT")
    if env_root:
        path = Path(env_root).resolve()
        if _is_vault_root(path):
            return path

    root = _find_vault_root_from_script()
    if root:
        return root

    fatal(
        "Could not find vault root.\n"
        "Run from inside a vault, use --vault, or set BRAIN_VAULT_ROOT."
    )


# ---------------------------------------------------------------------------
# Python / dependency detection
# ---------------------------------------------------------------------------

def _load_venv_helper(vault_root: Path):
    """Load the `_venv` helper from the vault, or return None when missing.

    `_venv` is part of `_common` and could in principle be imported normally,
    but this script is also packaged into the vault and may be invoked from
    a different vault than the one its source tree was copied from (e.g.
    when registering a project scope from outside the vault). Loading from
    the vault under inspection guarantees the path rule matches *that*
    vault's `.brain-core/` rather than whichever copy happens to be on
    `sys.path`. Returns None for partially-installed vaults so callers can
    fall through to legacy discovery.
    """
    helper = vault_root / VENV_HELPER_REL
    if not helper.is_file():
        return None
    spec = importlib.util.spec_from_file_location("_brain_venv", str(helper))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _resolve_managed_python(vault_root: Path) -> str:
    """Ensure and return the canonical managed-runtime Python for MCP bindings."""
    launcher = find_launcher_python()
    if not launcher:
        raise InitTransportError(
            "No compatible Python 3.12+ launcher found.\n"
            "Install Python 3.12+ with your preferred package manager and rerun init.py."
        )

    try:
        summary = ensure_managed_runtime(
            vault_root,
            required_modules=required_modules_for_scope("mcp"),
            dependency_owner="init.py",
            launcher_python=launcher,
        )
    except RuntimeError as exc:
        raise InitTransportError(str(exc)) from exc

    if not summary["managed_runtime_ready"] or not summary["managed_python"]:
        raise InitTransportError(
            "Could not provision the canonical managed runtime for MCP registration.\n"
            f"Run: {launcher} {vault_root / '.brain-core' / 'scripts' / 'repair.py'} runtime --vault {vault_root}"
        )
    return summary["managed_python"]


def find_python(vault_root: Path) -> str:
    try:
        return _resolve_managed_python(vault_root)
    except InitTransportError as exc:
        fatal(str(exc))


def _scope_from_args(args: argparse.Namespace) -> Tuple[str, Optional[Path], str]:
    if args.user:
        return "user", None, "user (all projects)"

    target_dir = Path(args.project).resolve() if args.project else Path.cwd().resolve()
    if not target_dir.is_dir():
        fatal(f"Not a directory: {target_dir}")

    scope = "local" if args.local else "project"
    return scope, target_dir, f"{scope} ({target_dir})"


def _resolve_clients_or_error(client_arg: str, scope: str) -> Tuple[List[str], List[str]]:
    warnings: List[str] = []

    if client_arg == "all":
        clients = list(SUPPORTED_CLIENTS)
    else:
        clients = [client_arg]

    if scope != "local":
        return clients, warnings

    if client_arg == "codex":
        raise InitTransportError(
            "Codex does not support local scope.\n"
            "Use --client claude --local, or choose project/user scope for Codex."
        )

    if client_arg == "all":
        warnings.append(
            "Codex has no supported local scope. Applying Claude local setup only."
        )
        return ["claude"], warnings

    return clients, warnings


def _resolve_clients(client_arg: str, scope: str) -> Tuple[List[str], List[str]]:
    try:
        return _resolve_clients_or_error(client_arg, scope)
    except InitTransportError as exc:
        fatal(str(exc))


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


# ---------------------------------------------------------------------------
# Claude CLI registration
# ---------------------------------------------------------------------------

def _has_claude_cli() -> bool:
    return shutil.which("claude") is not None


def _register_claude_via_cli(
    server_config: Dict[str, Any],
    scope: str,
    target_dir: Optional[Path],
) -> bool:
    """Register via `claude mcp add-json`. Returns True on success."""
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


# ---------------------------------------------------------------------------
# Direct file editing helpers
# ---------------------------------------------------------------------------

def _read_json_safe(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


# `safe_write` / `safe_write_json` come from `_common` (imported at module
# top). The previously-duplicated bodies here drifted from upgrade.py's
# copy (init.py was text-only, upgrade.py supports bytes); using the
# shared helpers keeps the kernel's behaviour single-sourced.


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
    """Read-merge-write brain server config into a JSON file."""
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
    """Write or merge brain into .mcp.json."""
    _upsert_mcp_server(target_dir / CLAUDE_PROJECT_CONFIG_FILE, server_config)


def write_local_settings_json(server_config: Dict[str, Any], target_dir: Path) -> None:
    """Write brain into .claude/settings.local.json (local scope)."""
    _upsert_mcp_server(target_dir / CLAUDE_LOCAL_SETTINGS_FILE, server_config)


def write_user_claude_json(server_config: Dict[str, Any]) -> None:
    """Write brain into ~/.claude.json (user scope)."""
    _upsert_mcp_server(Path.home() / CLAUDE_USER_CONFIG_FILE, server_config)


def _claude_project_approval_state(target_dir: Path) -> Dict[str, Any]:
    """Read Claude's per-project trust state for `.mcp.json` servers."""
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
    """Return user-facing follow-up notes for Claude project-scope installs."""
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


def _remove_codex_server(config_path: Path, server_config: Dict[str, Any]) -> bool:
    current = read_codex_server_config(config_path)
    if current is None:
        info(f"No recorded {BRAIN_SERVER_NAME} entry found in {config_path}")
        return False
    if current != server_config:
        info(f"Skipping {config_path}: current {BRAIN_SERVER_NAME} entry does not match recorded config")
        return False
    if not remove_codex_server(config_path, server_config):
        return False
    _remove_empty_parent_dirs(config_path, _config_cleanup_stop(config_path))
    info(f"Removed {BRAIN_SERVER_NAME} from {config_path}")
    return True


# ---------------------------------------------------------------------------
# Claude bootstrap + hook helpers
# ---------------------------------------------------------------------------

def ensure_claude_md(target_dir: Path, local: bool = False) -> Path:
    """Ensure CLAUDE.md (or .claude/CLAUDE.local.md) has the brain bootstrap line."""
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

    if bootstrap in existing:
        info(f"{rel_path} already has bootstrap line")
        return claude_md

    separator = "\n" if existing.endswith("\n") else "\n\n"
    safe_write(claude_md, f"{existing}{separator}{bootstrap}\n")
    info(f"Appended brain bootstrap to {rel_path}")
    return claude_md


def _remove_bootstrap_line(path: Path, bootstrap: str) -> None:
    if not path.is_file():
        return

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    kept = [line for line in lines if line.strip() != bootstrap]
    while kept and not kept[-1].strip():
        kept.pop()

    if kept == lines:
        return

    if kept:
        safe_write(path, "\n".join(kept) + "\n")
    else:
        _delete_file_if_exists(path)


def cleanup_claude_bootstrap(
    target_dir: Path,
    *,
    local: bool = False,
    bootstrap_line: Optional[str] = None,
) -> None:
    rel_path = CLAUDE_LOCAL_MD_FILE if local else CLAUDE_MD_FILE
    line = bootstrap_line if bootstrap_line is not None else bootstrap_line_for_target(target_dir)
    _remove_bootstrap_line(target_dir / rel_path, line)


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


def ensure_workspace_manifest(
    target_dir: Path,
    *,
    vault_root: Path | None = None,
    brain_id: str | None = None,
    allow_rebind: bool = False,
) -> Path:
    """Converge the canonical workspace binding manifest for a target."""
    try:
        result = _converge_workspace_manifest(
            target_dir,
            vault_root=vault_root,
            brain_id=brain_id,
            allow_rebind=allow_rebind,
        )
    except WorkspaceBindingError as exc:
        fatal(str(exc))

    info(result.message)
    return result.manifest_path

def _run_git_rev_parse(target_dir: Path, *args: str, error_label: str) -> Optional[Path]:
    if shutil.which("git") is None:
        return None
    if not (target_dir / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(target_dir), "rev-parse", *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitInspectionError(f"failed to inspect {error_label}: {exc}") from exc
    if result.returncode != 0:
        raise GitInspectionError(result.stderr.strip() or f"git rev-parse {' '.join(args)} failed")
    resolved = result.stdout.strip()
    return Path(resolved).resolve() if resolved else None


def _git_repo_root(target_dir: Path) -> Optional[Path]:
    return _run_git_rev_parse(
        target_dir,
        "--show-toplevel",
        error_label="git repository root",
    )


def _git_dir(target_dir: Path) -> Optional[Path]:
    return _run_git_rev_parse(
        target_dir,
        "--path-format=absolute",
        "--git-dir",
        error_label="git directory",
    )


def _brain_ignore_entries(
    scope: str,
    clients: List[str],
    *,
    skip_mcp: bool,
) -> List[str]:
    entries = list(DEFAULT_BRAIN_IGNORE_ENTRIES)
    if skip_mcp:
        return entries
    if "claude" in clients:
        entries.append(CLAUDE_LOCAL_SETTINGS_IGNORE)
        if scope == "local":
            entries.append(CLAUDE_LOCAL_MD_IGNORE)
    if "codex" in clients and scope == "project":
        entries.extend(CODEX_PROJECT_IGNORE_ENTRIES)
    return entries


def ensure_brain_ignore_rules(
    target_dir: Path,
    scope: str,
    clients: List[str],
    *,
    skip_mcp: bool,
) -> None:
    try:
        repo_root = _git_repo_root(target_dir)
    except GitInspectionError as exc:
        info(f"Warning: skipped Brain ignore-rule installation: {exc}")
        return
    if repo_root is None or repo_root != target_dir.resolve():
        return

    gitignore_path = target_dir / ".gitignore"
    destination = gitignore_path if gitignore_path.exists() else None
    if destination is None:
        try:
            git_dir = _git_dir(target_dir)
        except GitInspectionError as exc:
            info(f"Warning: skipped Brain ignore-rule installation: {exc}")
            return
        if git_dir is None:
            return
        destination = git_dir / "info" / "exclude"

    entries = _brain_ignore_entries(scope, clients, skip_mcp=skip_mcp)
    try:
        existing = destination.read_text(encoding="utf-8") if destination.is_file() else ""
    except OSError as exc:
        raise GitInspectionError(
            f"failed to read ignore destination {destination}: {exc}"
        ) from exc

    existing_entries = {line.strip() for line in existing.splitlines() if line.strip()}
    missing = [entry for entry in entries if entry not in existing_entries]
    if not missing:
        info(f"{destination.relative_to(target_dir) if destination.is_relative_to(target_dir) else destination} already covers Brain local state")
        return

    prefix = ""
    if existing and not existing.endswith("\n"):
        prefix += "\n"
    if existing_entries:
        prefix += "\n"

    comment = "# Brain local state\n"
    if "# Brain local state" in existing_entries:
        comment = ""

    block = prefix + comment + "".join(f"{entry}\n" for entry in missing)
    destination.parent.mkdir(parents=True, exist_ok=True)
    safe_write(destination, existing + block)
    info(f"Updated {destination}")


def _ensure_brain_ignore_rules_or_fatal(
    target_dir: Path,
    scope: str,
    clients: List[str],
    *,
    skip_mcp: bool,
) -> None:
    try:
        ensure_brain_ignore_rules(target_dir, scope, clients, skip_mcp=skip_mcp)
    except GitInspectionError as exc:
        fatal(f"Failed to install Brain ignore rules: {exc}")


def _scope_cli_flags(scope: str, target_dir: Optional[Path]) -> List[str]:
    if scope == "user":
        return ["--user"]
    if scope == "local":
        return ["--local"]
    return ["--project", str(target_dir)]


# ---------------------------------------------------------------------------
# SessionStart hook
# ---------------------------------------------------------------------------

def ensure_session_start_hook(target_dir: Path, vault_root: Path) -> Path:
    """Add a SessionStart hook that calls session.py automatically."""
    settings_path = target_dir / CLAUDE_LOCAL_SETTINGS_FILE
    settings = _read_json_safe(settings_path)

    if "hooks" not in settings or not isinstance(settings["hooks"], dict):
        settings["hooks"] = {}

    hook_command = build_session_hook_command(vault_root, target_dir)
    for entry in settings["hooks"].get("SessionStart", []):
        if not isinstance(entry, dict):
            continue
        for hook in entry.get("hooks", []):
            if isinstance(hook, dict) and hook.get("command") == hook_command:
                info("SessionStart hook already configured")
                return settings_path

    new_entry = {
        "hooks": [
            {
                "type": "command",
                "command": hook_command,
            }
        ]
    }
    settings["hooks"].setdefault("SessionStart", []).append(new_entry)
    safe_write_json(settings_path, settings)
    info("Added SessionStart hook for brain_session")
    return settings_path


def _remove_session_start_hook(
    settings_path: Path,
    vault_root: Path,
    target_dir: Path,
    recorded_command: Optional[str] = None,
) -> None:
    settings = _read_json_safe(settings_path)
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return

    # Match either the freshly-computed command or the one recorded at install
    # time. The launcher path inside the command is environment-dependent (PATH
    # ordering, sys.executable, etc.), so write-time and remove-time may resolve
    # different launchers; the recorded value is the authoritative match when
    # init-state.json has one.
    fresh_command = build_session_hook_command(vault_root, target_dir)
    valid_commands = {fresh_command}
    if recorded_command:
        valid_commands.add(recorded_command)

    changed = False
    session_entries = hooks.get("SessionStart", [])
    kept_entries = []

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
            if isinstance(hook, dict) and hook.get("command") in valid_commands:
                changed = True
                continue
            kept_hooks.append(hook)

        if kept_hooks:
            new_entry = dict(entry)
            new_entry["hooks"] = kept_hooks
            kept_entries.append(new_entry)
        elif hook_items:
            changed = True

    if not changed:
        return

    if kept_entries:
        hooks["SessionStart"] = kept_entries
    else:
        hooks.pop("SessionStart", None)

    if not hooks:
        settings.pop("hooks", None)

    _cleanup_json_file(settings_path, settings, target_dir)


# ---------------------------------------------------------------------------
# Registration / removal dispatch
# ---------------------------------------------------------------------------

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

    current_codex = read_codex_server_config(Path.home() / CODEX_CONFIG_REL)
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
        bootstrap_path = ensure_claude_md(target_dir, local=scope == "local")
        hook_path = ensure_session_start_hook(target_dir, vault_root)
        record["bootstrap_path"] = str(bootstrap_path)
        record["bootstrap_line"] = bootstrap_line_for_target(target_dir)
        record["hook_path"] = str(hook_path)
        record["hook_command"] = build_session_hook_command(vault_root, target_dir)

    record["method"] = method
    return record


def register_codex(
    server_config: Dict[str, Any],
    scope: str,
    target_dir: Optional[Path],
) -> Dict[str, Any]:
    config_path = _codex_config_path(scope, target_dir)
    write_codex_config(server_config, config_path)
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
            cleanup_claude_bootstrap(
                target_dir,
                local=scope == "local",
                bootstrap_line=record.get("bootstrap_line"),
            )
            _remove_session_start_hook(
                Path(record.get("hook_path", target_dir / CLAUDE_LOCAL_SETTINGS_FILE)),
                vault_root,
                target_dir,
                recorded_command=record.get("hook_command"),
            )
        return removed

    if client == "codex":
        return _remove_codex_server(config_path, server_config)

    info(f"Unknown client in init state: {client}")
    return False


def _scope_label(scope: str, target_dir: Optional[Path]) -> str:
    if scope == "user":
        return "user (all projects)"
    return f"{scope} ({target_dir})"


def apply_mcp_transport_action(
    vault_root: Path,
    *,
    client_arg: str,
    scope: str,
    target_dir: Optional[Path],
    remove: bool,
) -> Dict[str, Any]:
    clients, warnings = _resolve_clients_or_error(client_arg, scope)
    scope_label = _scope_label(scope, target_dir)

    if remove:
        matching = matching_records(vault_root, clients, scope, target_dir)
        if not matching:
            return {
                "action": "remove",
                "status": "noop",
                "scope": scope,
                "scope_label": scope_label,
                "target_dir": target_dir,
                "clients": clients,
                "warnings": warnings,
                "matching_count": 0,
                "removed_count": 0,
                "retained_count": 0,
                "removed_records": [],
            }

        removed_records: List[Dict[str, Any]] = []
        for record in matching:
            info(f"Removing {record['client']} from {record['config_path']}")
            if _remove_record(vault_root, record):
                removed_records.append(record)

        _remove_init_records(vault_root, removed_records)
        return {
            "action": "remove",
            "status": "changed",
            "scope": scope,
            "scope_label": scope_label,
            "target_dir": target_dir,
            "clients": clients,
            "warnings": warnings,
            "matching_count": len(matching),
            "removed_count": len(removed_records),
            "retained_count": len(matching) - len(removed_records),
            "removed_records": removed_records,
        }

    try:
        python_path = _resolve_managed_python(vault_root)
        server_config = build_mcp_config(python_path, vault_root, workspace_dir=target_dir)

        for client in clients:
            _warn_if_user_scope_exists(client, scope, server_config)

        results: List[Dict[str, Any]] = []
        for client in clients:
            header(f"Registering {client} MCP server")
            if client == "claude":
                record = register_claude(vault_root, server_config, scope, target_dir)
            else:
                record = register_codex(server_config, scope, target_dir)
            record_init_target(vault_root, record)
            results.append(record)

        if target_dir:
            header("Workspace manifest")
            binding = _converge_workspace_manifest(target_dir, vault_root=vault_root)
            info(binding.message)
            header("Git ignore rules")
            ensure_brain_ignore_rules(target_dir, scope, clients, skip_mcp=False)
    except (WorkspaceBindingError, GitInspectionError, OSError) as exc:
        raise InitTransportError(str(exc)) from exc

    has_claude = any(result["client"] == "claude" for result in results)
    has_codex = any(result["client"] == "codex" for result in results)
    project_scope = scope == "project" and target_dir is not None

    claude_notes: List[str] = []
    if project_scope and has_claude:
        claude_notes = claude_project_followup_notes(target_dir)

    verification_notes: List[str] = []
    if has_claude:
        if project_scope:
            verification_notes.append("Claude:   open Claude Code in this directory and use /mcp to approve `brain` if prompted")
            verification_notes.append("Verify:   ask Claude to call `brain_session` and confirm `environment.vault_root`")
        else:
            verification_notes.append("Verify:   claude mcp list")
    if has_codex:
        if project_scope:
            verification_notes.append(
                "Codex:    trust this project and ensure the project-scoped `brain` MCP is enabled if prompted"
            )
            verification_notes.append("Verify:   ask Codex to call `brain_session` and confirm `environment.vault_root`")
            verification_notes.append("Health:   codex mcp list")
        else:
            verification_notes.append("Verify:   codex mcp list")

    remove_args = [
        "python3",
        str(vault_root / '.brain-core' / 'scripts' / 'init.py'),
        "--vault",
        str(vault_root),
        "--client",
        client_arg,
        *_scope_cli_flags(scope, target_dir),
        "--remove",
    ]

    return {
        "action": "configure",
        "status": "changed",
        "scope": scope,
        "scope_label": scope_label,
        "target_dir": target_dir,
        "clients": clients,
        "warnings": warnings,
        "python_path": python_path,
        "results": results,
        "claude_project_notes": claude_notes,
        "verification_notes": verification_notes,
        "remove_command": shlex.join(remove_args),
    }


def _confirm_removal(scope_label: str, clients: List[str]) -> bool:
    client_label = ", ".join(clients)
    print(file=sys.stderr)
    print(f"Remove recorded Brain MCP registration for {client_label} at {scope_label}? [y/N]: ", end="", file=sys.stderr)
    response = input().strip()
    return response.lower() == "y"


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def info(msg: str) -> None:
    print(f"  {msg}", file=sys.stderr)


def fatal(msg: str) -> NoReturn:
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(1)


def header(msg: str) -> None:
    print(f"\n{'-' * 60}", file=sys.stderr)
    print(f"  {msg}", file=sys.stderr)
    print(f"{'-' * 60}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Set up or remove Brain MCP server registrations for Claude and Codex.",
        epilog=(
            "Examples:\n"
            "  cd /my/project && python3 /vault/.brain-core/scripts/init.py\n"
            "      Configure Claude and Codex for the current directory\n\n"
            "  python3 /vault/.brain-core/scripts/init.py --client claude --local\n"
            "      Configure Claude local scope only (.claude/settings.local.json)\n\n"
            "  python3 /vault/.brain-core/scripts/init.py --client all --user\n"
            "      Register as the default brain for all supported clients\n\n"
            "  python3 /vault/.brain-core/scripts/init.py --client codex --project /my/project\n"
            "      Configure Codex for a specific project without cd-ing into it\n\n"
            "  python3 /vault/.brain-core/scripts/init.py --skip-mcp --project /my/project\n"
            "      Scaffold Brain folder bootstrap only, without writing MCP/client config\n\n"
            "  python3 /vault/.brain-core/scripts/init.py --remove --client all --project /my/project\n"
            "      Remove only recorded Brain-managed project registrations for that folder\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--vault",
        help="Path to Brain vault (default: auto-detect from script location)",
    )
    parser.add_argument(
        "--client",
        choices=("claude", "codex", "all"),
        default="all",
        help="Which client config to write (default: all)",
    )
    parser.add_argument(
        "--user",
        action="store_true",
        help="Register as default brain for all projects (user scope)",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Use Claude local scope (.claude/settings.local.json). Unsupported for Codex.",
    )
    parser.add_argument(
        "--project",
        help="Target folder to configure (default: current directory)",
    )
    parser.add_argument(
        "--skip-mcp",
        action="store_true",
        help="Scaffold folder bootstrap only; skip runtime discovery and MCP/client registration",
    )
    parser.add_argument(
        "--remove",
        action="store_true",
        help="Remove only recorded Brain-managed entries for the requested scope",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip the confirmation prompt for --remove",
    )
    parser.add_argument(
        "--cleanup-bootstrap",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)

    if args.user and (args.local or args.project):
        fatal("--user cannot be combined with --local or --project")
    if args.skip_mcp and args.user:
        fatal("--skip-mcp cannot be combined with --user")
    if args.skip_mcp and args.remove:
        fatal("--skip-mcp cannot be combined with --remove")

    scope, target_dir, scope_label = _scope_from_args(args)
    clients, warnings = _resolve_clients(args.client, scope)

    vault_root = find_vault_root(args.vault)
    header(f"Brain vault: {vault_root}")
    info(f"Scope:   {scope_label}")
    info(f"Client:  {', '.join(clients)}")

    for warning in warnings:
        info(f"Warning: {warning}")

    if args.cleanup_bootstrap:
        if target_dir is None:
            fatal("--cleanup-bootstrap requires a target directory")
        header("Cleaning Claude bootstrap")
        cleanup_claude_bootstrap(target_dir, local=scope == "local")
        header("Done")
        info(f"Target:   {target_dir}")
        print(file=sys.stderr)
        return

    if args.skip_mcp:
        header("Folder bootstrap")
        if target_dir is None:
            fatal("--skip-mcp requires a target directory")

        ensure_workspace_manifest(target_dir, vault_root=vault_root)
        _ensure_brain_ignore_rules_or_fatal(target_dir, scope, clients, skip_mcp=True)

        header("Done")
        info(f"Vault:    {vault_root}")
        info(f"Target:   {target_dir}")
        info("MCP:      skipped (--skip-mcp)")
        print(file=sys.stderr)
        return

    if args.remove:
        header("Removing MCP registrations")
        if not args.force and not _confirm_removal(scope_label, clients):
            print(file=sys.stderr)
            info("Removal cancelled. No changes made.")
            print(file=sys.stderr)
            return
    try:
        result = apply_mcp_transport_action(
            vault_root,
            client_arg=args.client,
            scope=scope,
            target_dir=target_dir,
            remove=args.remove,
        )
    except InitTransportError as exc:
        fatal(str(exc))

    if args.remove:
        if result["status"] == "noop":
            info("No recorded Brain-managed entries matched this request.")
        header("Done")
        info(f"Removed:  {result['removed_count']} recorded registration(s)")
        info(f"Retained: {result['retained_count']} record(s)")
        print(file=sys.stderr)
        return

    header("Done")
    info(f"Vault:    {vault_root}")
    info(f"Scope:    {scope_label}")
    info(f"Clients:  {', '.join(clients)}")
    info(f"Python:   {result['python_path']}")
    for registration in result["results"]:
        info(f"{registration['client'].title()}: {registration['method']}")
    print(file=sys.stderr)

    if result["claude_project_notes"]:
        header("Claude project approval")
        for note in result["claude_project_notes"]:
            info(note)
        print(file=sys.stderr)

    for note in result["verification_notes"]:
        info(note)
    info(f"Remove:   {result['remove_command']}")
    print(file=sys.stderr)


if __name__ == "__main__":
    main()
