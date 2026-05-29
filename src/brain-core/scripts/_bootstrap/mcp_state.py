#!/usr/bin/env python3
"""Launcher-safe MCP state, config-layout, and init-state helpers."""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from _bootstrap.runtime import find_launcher_python
from _bootstrap.workspace_binding import (
    WorkspaceBindingError,
    extract_workspace_binding,
    read_workspace_manifest,
    resolve_bound_brain_vault,
)
from _common import safe_write, safe_write_json


BRAIN_SERVER_NAME = "brain"
BRAIN_CORE_MARKER = ".brain-core/VERSION"
MCP_PYTHONPATH_REL = ".brain-core"
MCP_PROXY_MODULE = "brain_mcp.proxy"
MCP_SERVER_MODULE = "brain_mcp.server"

CLAUDE_PROJECT_CONFIG_FILE = ".mcp.json"
CLAUDE_USER_CONFIG_FILE = ".claude.json"
CLAUDE_LOCAL_SETTINGS_FILE = ".claude/settings.local.json"
CLAUDE_MD_FILE = "CLAUDE.md"
CLAUDE_LOCAL_MD_FILE = ".claude/CLAUDE.local.md"

CODEX_CONFIG_REL = ".codex/config.toml"
INIT_STATE_REL = ".brain/local/init-state.json"
INIT_STATE_VERSION = 1

CLAUDE_MD_BOOTSTRAP_VAULT = (
    "ALWAYS DO FIRST: Call MCP `brain_session`, else read `.brain-core/index.md` if it exists."
)
CLAUDE_MD_BOOTSTRAP_PROJECT = "ALWAYS DO FIRST: Call brain_session"


def is_vault_root(path: Path) -> bool:
    """Return whether the path looks like a Brain vault root."""
    return (path / BRAIN_CORE_MARKER).is_file()


def build_mcp_config(
    python_path: str,
    vault_root: Path,
    workspace_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Build the shared MCP server config shape used by Claude and Codex."""
    pythonpath = str(vault_root / MCP_PYTHONPATH_REL)
    env = {
        "PYTHONPATH": pythonpath,
    }
    if workspace_dir is not None:
        env["BRAIN_WORKSPACE_DIR"] = str(workspace_dir)
    return {
        "command": python_path,
        "args": ["-m", MCP_PROXY_MODULE, python_path, MCP_SERVER_MODULE],
        "env": env,
    }


def configured_vault_root(server_config: Any) -> Optional[Path]:
    """Return the legacy configured vault root from a Brain server config, if present."""
    if not isinstance(server_config, dict):
        return None
    env = server_config.get("env")
    if not isinstance(env, dict):
        return None
    vault_root = env.get("BRAIN_VAULT_ROOT")
    if not isinstance(vault_root, str) or not vault_root:
        return None
    try:
        return Path(vault_root).resolve()
    except OSError:
        return None


def configured_workspace_dir(server_config: Any) -> Optional[Path]:
    """Return the explicit configured workspace directory, if present."""
    if not isinstance(server_config, dict):
        return None
    env = server_config.get("env")
    if not isinstance(env, dict):
        return None
    workspace_dir = env.get("BRAIN_WORKSPACE_DIR")
    if not isinstance(workspace_dir, str) or not workspace_dir:
        return None
    try:
        return Path(workspace_dir).resolve()
    except OSError:
        return None


def resolved_target_vault_root(server_config: Any) -> Optional[Path]:
    """Resolve the effective target Brain vault for a persisted MCP config."""
    legacy_root = configured_vault_root(server_config)
    if legacy_root is not None:
        return legacy_root

    workspace_dir = configured_workspace_dir(server_config)
    if workspace_dir is None:
        return None

    try:
        manifest = read_workspace_manifest(workspace_dir)
    except WorkspaceBindingError:
        return None
    binding = extract_workspace_binding(manifest)
    if binding is None:
        return None
    return resolve_bound_brain_vault(binding["brain"])


def config_targets_vault(server_config: Any, vault_root: Path) -> bool:
    """Return whether a Brain server config resolves to the given target vault."""
    configured_root = resolved_target_vault_root(server_config)
    if configured_root is None:
        return False
    return configured_root == vault_root.resolve()


def bootstrap_line_for_target(target_dir: Path) -> str:
    """Return the expected Brain bootstrap line for the target directory."""
    return CLAUDE_MD_BOOTSTRAP_VAULT if is_vault_root(target_dir) else CLAUDE_MD_BOOTSTRAP_PROJECT


def _resolve_session_launcher() -> str:
    """Resolve the persisted launcher path used in the SessionStart hook."""
    return find_launcher_python(prefer_path_binaries=True) or sys.executable


def build_session_hook_command(vault_root: Path, target_dir: Path) -> str:
    """Build the persisted SessionStart hook command for a target directory."""
    session_script = str(vault_root / ".brain-core" / "scripts" / "session.py")
    launcher = _resolve_session_launcher()
    return (
        "echo 'brain_session called:' "
        f"&& {shlex.quote(launcher)} {shlex.quote(session_script)} "
        f"--vault {shlex.quote(str(vault_root))} "
        f"--workspace-dir {shlex.quote(str(target_dir))} --json"
    )


def _state_path(vault_root: Path) -> Path:
    return vault_root / INIT_STATE_REL


def _load_init_state(vault_root: Path) -> Dict[str, Any]:
    path = _state_path(vault_root)
    if not path.is_file():
        return {"version": INIT_STATE_VERSION, "records": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"version": INIT_STATE_VERSION, "records": []}

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
        safe_write_json(path, {"version": INIT_STATE_VERSION, "records": records})
        return
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _record_identity(record: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        record.get("client"),
        record.get("scope"),
        record.get("target_path"),
        record.get("config_path"),
    )


def record_init_target(vault_root: Path, record: Dict[str, Any]) -> None:
    """Upsert one init-state record for the given vault/target pair."""
    state = _load_init_state(vault_root)
    records = []
    record_id = _record_identity(record)
    for existing in state["records"]:
        if _record_identity(existing) != record_id:
            records.append(existing)
    records.append(record)
    state["records"] = records
    _save_init_state(vault_root, state)


def remove_init_records(vault_root: Path, removed_records: List[Dict[str, Any]]) -> None:
    """Remove init-state entries matching the provided records."""
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


def matching_records(
    vault_root: Path,
    clients: List[str],
    scope: str,
    target_dir: Optional[Path],
) -> List[Dict[str, Any]]:
    """Return init-state records matching the given vault/client/scope target."""
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
        section_name = section["name"]
        if section_name == "mcp_servers.brain" or section_name.startswith("mcp_servers.brain."):
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


def _toml_body_lines(mapping: Dict[str, Any]) -> List[str]:
    body: List[str] = []
    for key, value in mapping.items():
        if isinstance(value, str):
            body.append(f'{key} = {json.dumps(value)}\n')
        elif isinstance(value, bool):
            body.append(f"{key} = {'true' if value else 'false'}\n")
        elif isinstance(value, list):
            body.append(f"{key} = {json.dumps(value)}\n")
        else:
            body.append(f"{key} = {value}\n")
    return body


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
    """Read the Brain MCP entry from a Codex TOML config file."""
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
    """Write the Brain MCP entry into a Codex TOML config file."""
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
    safe_write(config_path, _render_toml(preamble, sections))


def remove_codex_server(config_path: Path, server_config: Dict[str, Any]) -> bool:
    """Remove the Brain MCP entry from a Codex TOML config file when it matches."""
    current = read_codex_server_config(config_path)
    if current is None or current != server_config:
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
        safe_write(config_path, rendered)
        return True

    try:
        config_path.unlink()
    except FileNotFoundError:
        return True
    return True
