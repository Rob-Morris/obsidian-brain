#!/usr/bin/env python3
"""
init.py - Set up or remove Brain MCP server registrations for Claude and Codex.

Examples:
  1. Current directory:  python3 /vault/.brain-core/scripts/init.py
  2. Claude local only:  python3 /vault/.brain-core/scripts/init.py --client claude --local
  3. User default:       python3 /vault/.brain-core/scripts/init.py --client all --user
  4. Specific folder:    python3 /vault/.brain-core/scripts/init.py --project /path/to/project
  5. Explicit removal:   python3 /vault/.brain-core/scripts/init.py --remove --client all --project /path/to/project

Self-contained - no imports from _common (may run before deps are installed).
Idempotent - safe to re-run. Never clobbers non-brain MCP config.

Claude uses native project/local/user config surfaces.
Codex uses native project/user config surfaces only.
"""

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BRAIN_SERVER_NAME = "brain"
BRAIN_CORE_MARKER = os.path.join(".brain-core", "VERSION")
MCP_PYTHONPATH_REL = os.path.join(".brain-core")
MCP_PROXY_MODULE = "brain_mcp.proxy"
MCP_SERVER_MODULE = "brain_mcp.server"
VENV_PYTHON_REL = os.path.join(".venv", "bin", "python")

CLAUDE_PROJECT_CONFIG_FILE = ".mcp.json"
CLAUDE_USER_CONFIG_FILE = ".claude.json"
CLAUDE_LOCAL_SETTINGS_FILE = os.path.join(".claude", "settings.local.json")
CLAUDE_MD_FILE = "CLAUDE.md"
CLAUDE_LOCAL_MD_FILE = os.path.join(".claude", "CLAUDE.local.md")

CODEX_CONFIG_REL = os.path.join(".codex", "config.toml")

INIT_STATE_REL = os.path.join(".brain", "local", "init-state.json")
INIT_STATE_VERSION = 1

CLAUDE_MD_BOOTSTRAP_VAULT = (
    "ALWAYS DO FIRST: Call MCP `brain_session`, else read `.brain-core/index.md` if it exists."
)
CLAUDE_MD_BOOTSTRAP_PROJECT = "ALWAYS DO FIRST: Call brain_session"

SUPPORTED_CLIENTS = ("claude", "codex")
SUPPORTED_SCOPES = ("project", "local", "user")


# ---------------------------------------------------------------------------
# Vault root discovery (self-contained, no _common import)
# ---------------------------------------------------------------------------

def _is_vault_root(path: Path) -> bool:
    return (path / BRAIN_CORE_MARKER).is_file()


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

def find_python(vault_root: Path) -> str:
    """Find a Python with the mcp package available."""
    venv_python = vault_root / VENV_PYTHON_REL
    if venv_python.is_file() and _python_has_mcp(str(venv_python)):
        return str(venv_python)

    if _python_has_mcp(sys.executable):
        return sys.executable

    for candidate in ["python3.13", "python3.12", "python3.11", "python3.10", "python3"]:
        path = shutil.which(candidate)
        if path and _python_has_mcp(path):
            return path

    fatal(
        "No Python with the 'mcp' package found.\n"
        f"Run: cd {vault_root} && make install\n"
        "Or:  pip install 'mcp>=1.0.0' --break-system-packages"
    )


def _python_has_mcp(python_path: str) -> bool:
    """Check if a Python interpreter has the mcp package."""
    try:
        result = subprocess.run(
            [python_path, "-c", "import mcp; print('ok')"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0 and "ok" in result.stdout
    except (OSError, subprocess.TimeoutExpired):
        return False


# ---------------------------------------------------------------------------
# MCP configuration
# ---------------------------------------------------------------------------

def build_mcp_config(python_path: str, vault_root: Path) -> Dict[str, Any]:
    """Build the MCP server config shared by Claude and Codex."""
    pythonpath = str(vault_root / MCP_PYTHONPATH_REL)
    return {
        "command": python_path,
        "args": ["-m", MCP_PROXY_MODULE, python_path, MCP_SERVER_MODULE],
        "env": {
            "BRAIN_VAULT_ROOT": str(vault_root),
            "PYTHONPATH": pythonpath,
        },
    }


def _scope_from_args(args: argparse.Namespace) -> Tuple[str, Optional[Path], str]:
    if args.user:
        return "user", None, "user (all projects)"

    target_dir = Path(args.project).resolve() if args.project else Path.cwd().resolve()
    if not target_dir.is_dir():
        fatal(f"Not a directory: {target_dir}")

    scope = "local" if args.local else "project"
    return scope, target_dir, f"{scope} ({target_dir})"


def _resolve_clients(client_arg: str, scope: str) -> Tuple[List[str], List[str]]:
    warnings: List[str] = []

    if client_arg == "all":
        clients = list(SUPPORTED_CLIENTS)
    else:
        clients = [client_arg]

    if scope != "local":
        return clients, warnings

    if client_arg == "codex":
        fatal(
            "Codex does not support local scope.\n"
            "Use --client claude --local, or choose project/user scope for Codex."
        )

    if client_arg == "all":
        warnings.append(
            "Codex has no supported local scope. Applying Claude local setup only."
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


def _bootstrap_line_for_target(target_dir: Path) -> str:
    return CLAUDE_MD_BOOTSTRAP_VAULT if _is_vault_root(target_dir) else CLAUDE_MD_BOOTSTRAP_PROJECT


def _build_session_hook_command(vault_root: Path) -> str:
    session_script = str(vault_root / ".brain-core" / "scripts" / "session.py")
    return (
        "echo 'brain_session called:' "
        f"&& python3 {shlex.quote(session_script)} "
        f"--vault {shlex.quote(str(vault_root))} --json"
    )


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


def _safe_write(path: Path, content: str) -> str:
    """Atomic file write: tmp -> fsync -> os.replace. Returns resolved path."""
    target = os.path.realpath(str(path))
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    tmp_path = f"{target}.{os.getpid()}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return target


def _safe_write_json(path: Path, data: Dict[str, Any]) -> str:
    """Atomic JSON write."""
    content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    return _safe_write(path, content)


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
        _safe_write_json(path, data)
        return
    _delete_file_if_exists(path)
    _remove_empty_parent_dirs(path, stop_at)


def _upsert_mcp_server(json_path: Path, server_config: Dict[str, Any]) -> None:
    """Read-merge-write brain server config into a JSON file."""
    existing = _read_json_safe(json_path)
    if "mcpServers" not in existing or not isinstance(existing["mcpServers"], dict):
        existing["mcpServers"] = {}
    existing["mcpServers"][BRAIN_SERVER_NAME] = server_config
    _safe_write_json(json_path, existing)
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


# ---------------------------------------------------------------------------
# Minimal TOML editing for Codex config
# ---------------------------------------------------------------------------

def _parse_toml_sections(content: str) -> Tuple[List[str], List[Dict[str, Any]]]:
    preamble: List[str] = []
    sections: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    for line in content.splitlines(keepends=True):
        stripped = line.strip()
        is_header = (
            stripped.startswith("[")
            and stripped.endswith("]")
            and not stripped.startswith("[[")
        )
        if is_header:
            current = {"name": stripped[1:-1].strip(), "header": line, "body": []}
            sections.append(current)
            continue
        if current is None:
            preamble.append(line)
        else:
            current["body"].append(line)

    return preamble, sections


def _render_toml(preamble: List[str], sections: List[Dict[str, Any]]) -> str:
    chunks: List[str] = []

    preamble_text = "".join(preamble).rstrip("\n")
    if preamble_text:
        chunks.append(preamble_text)

    for section in sections:
        body = "".join(section["body"]).rstrip("\n")
        chunk = section["header"].rstrip("\n")
        if body:
            chunk = f"{chunk}\n{body}"
        chunks.append(chunk)

    if not chunks:
        return ""
    return "\n\n".join(chunks).rstrip() + "\n"


def _find_section_index(sections: List[Dict[str, Any]], name: str) -> Optional[int]:
    for index, section in enumerate(sections):
        if section["name"] == name:
            return index
    return None


def _brain_subtree_indexes(sections: List[Dict[str, Any]]) -> List[int]:
    indexes: List[int] = []
    for index, section in enumerate(sections):
        name = section["name"]
        if name == "mcp_servers.brain" or name.startswith("mcp_servers.brain."):
            indexes.append(index)
    return indexes


def _upsert_toml_section(
    sections: List[Dict[str, Any]],
    name: str,
    body_lines: List[str],
) -> None:
    existing_index = _find_section_index(sections, name)
    if existing_index is not None:
        sections[existing_index]["body"] = body_lines
        return

    insert_at = len(sections)
    subtree_indexes = _brain_subtree_indexes(sections)

    if name == "mcp_servers.brain":
        if subtree_indexes:
            insert_at = subtree_indexes[0]
    elif name == "mcp_servers.brain.env":
        tool_indexes = [
            index
            for index, section in enumerate(sections)
            if section["name"].startswith("mcp_servers.brain.tools.")
        ]
        if tool_indexes:
            insert_at = tool_indexes[0]
        else:
            main_index = _find_section_index(sections, "mcp_servers.brain")
            if main_index is not None:
                insert_at = main_index + 1
            elif subtree_indexes:
                insert_at = subtree_indexes[0]

    sections.insert(
        insert_at,
        {"name": name, "header": f"[{name}]\n", "body": body_lines},
    )


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return json.dumps(value, ensure_ascii=False)


def _toml_body_lines(mapping: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    for key, value in mapping.items():
        lines.append(f"{key} = {_toml_value(value)}\n")
    return lines


def _parse_toml_scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return value
    if value.startswith('"') and value.endswith('"'):
        return json.loads(value)
    if value.startswith("[") and value.endswith("]"):
        return json.loads(value)
    if value == "true":
        return True
    if value == "false":
        return False
    return value


def _parse_toml_mapping(body_lines: List[str]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for raw_line in body_lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = _parse_toml_scalar(value)
    return result


def read_codex_server_config(config_path: Path) -> Optional[Dict[str, Any]]:
    if not config_path.is_file():
        return None

    try:
        content = config_path.read_text(encoding="utf-8")
    except OSError:
        return None

    _, sections = _parse_toml_sections(content)
    main_index = _find_section_index(sections, "mcp_servers.brain")
    if main_index is None:
        return None

    main = _parse_toml_mapping(sections[main_index]["body"])
    env: Dict[str, Any] = {}
    env_index = _find_section_index(sections, "mcp_servers.brain.env")
    if env_index is not None:
        env = _parse_toml_mapping(sections[env_index]["body"])

    if "command" not in main or "args" not in main:
        return None

    return {
        "command": main["command"],
        "args": main["args"],
        "env": env,
    }


def write_codex_config(server_config: Dict[str, Any], config_path: Path) -> None:
    try:
        content = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
    except OSError:
        content = ""

    preamble, sections = _parse_toml_sections(content)
    _upsert_toml_section(
        sections,
        "mcp_servers.brain",
        _toml_body_lines(
            {
                "command": server_config["command"],
                "args": server_config["args"],
            }
        ),
    )
    _upsert_toml_section(
        sections,
        "mcp_servers.brain.env",
        _toml_body_lines(server_config["env"]),
    )

    _safe_write(config_path, _render_toml(preamble, sections))
    info(f"Wrote {BRAIN_SERVER_NAME} -> {config_path}")


def _remove_codex_server(config_path: Path, server_config: Dict[str, Any]) -> bool:
    current = read_codex_server_config(config_path)
    if current is None:
        info(f"No recorded {BRAIN_SERVER_NAME} entry found in {config_path}")
        return False
    if current != server_config:
        info(f"Skipping {config_path}: current {BRAIN_SERVER_NAME} entry does not match recorded config")
        return False

    try:
        content = config_path.read_text(encoding="utf-8")
    except OSError:
        return False

    preamble, sections = _parse_toml_sections(content)
    kept_sections = [
        section
        for section in sections
        if not (
            section["name"] == "mcp_servers.brain"
            or section["name"].startswith("mcp_servers.brain.")
        )
    ]

    rendered = _render_toml(preamble, kept_sections)
    if rendered:
        _safe_write(config_path, rendered)
    else:
        _delete_file_if_exists(config_path)
        _remove_empty_parent_dirs(config_path, _config_cleanup_stop(config_path))

    info(f"Removed {BRAIN_SERVER_NAME} from {config_path}")
    return True


# ---------------------------------------------------------------------------
# Claude bootstrap + hook helpers
# ---------------------------------------------------------------------------

def ensure_claude_md(target_dir: Path, local: bool = False) -> Path:
    """Ensure CLAUDE.md (or .claude/CLAUDE.local.md) has the brain bootstrap line."""
    bootstrap = _bootstrap_line_for_target(target_dir)
    rel_path = CLAUDE_LOCAL_MD_FILE if local else CLAUDE_MD_FILE
    claude_md = target_dir / rel_path
    content = ""

    if claude_md.is_file():
        with open(claude_md, "r", encoding="utf-8") as handle:
            content = handle.read()
        if bootstrap in content:
            info(f"{rel_path} already has bootstrap line")
            return claude_md
        separator = "\n" if content.endswith("\n") else "\n\n"
        with open(claude_md, "a", encoding="utf-8") as handle:
            handle.write(f"{separator}{bootstrap}\n")
        info(f"Appended brain bootstrap to {rel_path}")
        return claude_md

    _safe_write(claude_md, f"{bootstrap}\n")
    info(f"Created {rel_path} with brain bootstrap")
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
        _safe_write(path, "\n".join(kept) + "\n")
    else:
        _delete_file_if_exists(path)


def ensure_session_start_hook(target_dir: Path, vault_root: Path) -> Path:
    """Add a SessionStart hook that calls session.py automatically."""
    settings_path = target_dir / CLAUDE_LOCAL_SETTINGS_FILE
    settings = _read_json_safe(settings_path)

    if "hooks" not in settings or not isinstance(settings["hooks"], dict):
        settings["hooks"] = {}

    hook_command = _build_session_hook_command(vault_root)
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
    _safe_write_json(settings_path, settings)
    info("Added SessionStart hook for brain_session")
    return settings_path


def _remove_session_start_hook(settings_path: Path, vault_root: Path, target_dir: Path) -> None:
    settings = _read_json_safe(settings_path)
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return

    hook_command = _build_session_hook_command(vault_root)
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
            if isinstance(hook, dict) and hook.get("command") == hook_command:
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
# Init state bookkeeping
# ---------------------------------------------------------------------------

def _state_path(vault_root: Path) -> Path:
    return vault_root / INIT_STATE_REL


def _load_init_state(vault_root: Path) -> Dict[str, Any]:
    path = _state_path(vault_root)
    if not path.is_file():
        return {"version": INIT_STATE_VERSION, "records": []}

    data = _read_json_safe(path)
    records = data.get("records")
    if not isinstance(records, list):
        records = []
    return {
        "version": data.get("version", INIT_STATE_VERSION),
        "records": records,
    }


def _save_init_state(vault_root: Path, state: Dict[str, Any]) -> None:
    path = _state_path(vault_root)
    records = state.get("records", [])
    if records:
        _safe_write_json(path, {"version": INIT_STATE_VERSION, "records": records})
        return
    _delete_file_if_exists(path)


def _record_identity(record: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        record.get("client"),
        record.get("scope"),
        record.get("target_path"),
        record.get("config_path"),
    )


def _record_init_target(vault_root: Path, record: Dict[str, Any]) -> None:
    state = _load_init_state(vault_root)
    records = []
    record_id = _record_identity(record)
    for existing in state["records"]:
        if _record_identity(existing) != record_id:
            records.append(existing)
    records.append(record)
    state["records"] = records
    _save_init_state(vault_root, state)


def _remove_init_records(vault_root: Path, removed_records: List[Dict[str, Any]]) -> None:
    if not removed_records:
        return
    removed_ids = {_record_identity(record) for record in removed_records}
    state = _load_init_state(vault_root)
    state["records"] = [
        record
        for record in state["records"]
        if _record_identity(record) not in removed_ids
    ]
    _save_init_state(vault_root, state)


def _matching_records(
    vault_root: Path,
    clients: List[str],
    scope: str,
    target_dir: Optional[Path],
) -> List[Dict[str, Any]]:
    state = _load_init_state(vault_root)
    expected_target = str(target_dir) if target_dir else None
    matches: List[Dict[str, Any]] = []
    for record in state["records"]:
        if record.get("client") not in clients:
            continue
        if record.get("scope") != scope:
            continue
        if record.get("target_path") != expected_target:
            continue
        matches.append(record)
    return matches


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
            info(
                f'Note: "{BRAIN_SERVER_NAME}" is already registered globally '
                f"({Path.home() / CLAUDE_USER_CONFIG_FILE}). Project or local Claude config will take priority."
            )
        return

    current_codex = read_codex_server_config(Path.home() / CODEX_CONFIG_REL)
    if current_codex:
        info(
            f'Note: "{BRAIN_SERVER_NAME}" is already registered globally '
            f"({Path.home() / CODEX_CONFIG_REL}). Project Codex config will take priority."
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
        record["bootstrap_line"] = _bootstrap_line_for_target(target_dir)
        record["hook_path"] = str(hook_path)
        record["hook_command"] = _build_session_hook_command(vault_root)

    record["method"] = method
    return record


def register_codex(
    server_config: Dict[str, Any],
    scope: str,
    target_dir: Optional[Path],
) -> Dict[str, Any]:
    config_path = _codex_config_path(scope, target_dir)
    write_codex_config(server_config, config_path)
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
            _remove_bootstrap_line(
                Path(record.get("bootstrap_path", target_dir / CLAUDE_MD_FILE)),
                record.get("bootstrap_line", _bootstrap_line_for_target(target_dir)),
            )
            _remove_session_start_hook(
                Path(record.get("hook_path", target_dir / CLAUDE_LOCAL_SETTINGS_FILE)),
                vault_root,
                target_dir,
            )
        return removed

    if client == "codex":
        return _remove_codex_server(config_path, server_config)

    info(f"Unknown client in init state: {client}")
    return False


def _confirm_removal(scope_label: str, clients: List[str]) -> None:
    client_label = ", ".join(clients)
    print(file=sys.stderr)
    print(f"Remove recorded Brain MCP registration for {client_label} at {scope_label}? [y/N]: ", end="", file=sys.stderr)
    response = input().strip()
    if response.lower() != "y":
        print(file=sys.stderr)
        info("Removal cancelled. No changes made.")
        sys.exit(0)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def info(msg: str) -> None:
    print(f"  {msg}", file=sys.stderr)


def fatal(msg: str) -> None:
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(1)


def header(msg: str) -> None:
    print(f"\n{'-' * 60}", file=sys.stderr)
    print(f"  {msg}", file=sys.stderr)
    print(f"{'-' * 60}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
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
        "--remove",
        action="store_true",
        help="Remove only recorded Brain-managed entries for the requested scope",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip the confirmation prompt for --remove",
    )
    args = parser.parse_args()

    if args.user and (args.local or args.project):
        fatal("--user cannot be combined with --local or --project")

    scope, target_dir, scope_label = _scope_from_args(args)
    clients, warnings = _resolve_clients(args.client, scope)

    vault_root = find_vault_root(args.vault)
    header(f"Brain vault: {vault_root}")
    info(f"Scope:   {scope_label}")
    info(f"Client:  {', '.join(clients)}")

    for warning in warnings:
        info(f"Warning: {warning}")

    if args.remove:
        if not args.force:
            _confirm_removal(scope_label, clients)

        header("Removing MCP registrations")
        matching = _matching_records(vault_root, clients, scope, target_dir)
        if not matching:
            info("No recorded Brain-managed entries matched this request.")
            print(file=sys.stderr)
            return

        removed_records: List[Dict[str, Any]] = []
        for record in matching:
            info(f"Removing {record['client']} from {record['config_path']}")
            if _remove_record(vault_root, record):
                removed_records.append(record)

        _remove_init_records(vault_root, removed_records)

        header("Done")
        info(f"Removed:  {len(removed_records)} recorded registration(s)")
        info(f"Retained: {len(matching) - len(removed_records)} record(s)")
        print(file=sys.stderr)
        return

    python_path = find_python(vault_root)
    info(f"Python:  {python_path}")
    server_config = build_mcp_config(python_path, vault_root)

    for client in clients:
        _warn_if_user_scope_exists(client, scope, server_config)

    results: List[Dict[str, Any]] = []
    for client in clients:
        header(f"Registering {client} MCP server")
        if client == "claude":
            record = register_claude(vault_root, server_config, scope, target_dir)
        else:
            record = register_codex(server_config, scope, target_dir)
        _record_init_target(vault_root, record)
        results.append(record)

    header("Done")
    info(f"Vault:    {vault_root}")
    info(f"Scope:    {scope_label}")
    info(f"Clients:  {', '.join(clients)}")
    for result in results:
        info(
            f"{result['client'].title()}: {result['method']}"
        )
    print(file=sys.stderr)
    if any(result["client"] == "claude" for result in results):
        info("Verify:   claude mcp list")
    if any(result["client"] == "codex" for result in results):
        info("Verify:   codex mcp list")
    info(
        "Remove:   "
        f"python3 {shlex.quote(str(vault_root / '.brain-core' / 'scripts' / 'init.py'))} "
        f"--vault {shlex.quote(str(vault_root))} --client {args.client} "
        f"{'--user' if scope == 'user' else ('--local' if scope == 'local' else '--project ' + shlex.quote(str(target_dir)))} "
        "--remove"
    )
    print(file=sys.stderr)


if __name__ == "__main__":
    main()
