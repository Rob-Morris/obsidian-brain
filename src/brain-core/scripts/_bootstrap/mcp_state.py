#!/usr/bin/env python3
"""Launcher-safe MCP state, config-layout, and init-state helpers."""

from __future__ import annotations

import json
import os
import shlex
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from _bootstrap.runtime import find_launcher_python
from _bootstrap.workspace_binding import (
    WorkspaceBindingError,
    extract_workspace_binding,
    read_workspace_manifest,
    resolve_local_brain_vault,
)
from _common import is_brain_vault, safe_write, safe_write_json, safe_write_via


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
GROK_CONFIG_REL = ".grok/config.toml"
GROK_RULE_REL = ".grok/rules/brain.md"
INIT_STATE_REL = ".brain/local/init-state.json"
INIT_STATE_VERSION = 1

CLAUDE_MD_BOOTSTRAP_VAULT = "ALWAYS DO FIRST: Call MCP `session_start`, else read `.brain-core/index.md` if it exists."
CLAUDE_MD_BOOTSTRAP_PROJECT = "ALWAYS DO FIRST: Call MCP `session_start`; if MCP is unavailable, run `brain session start --json` from this workspace."


@dataclass(frozen=True)
class BootstrapLineRelease:
    """One bootstrap line Brain wrote for a target kind, and the releases that wrote it."""

    line: str
    target: Literal["vault", "project"]
    first_version: str
    last_version: str | None  # None while the line is current


# Every whole line Brain has written as its agent bootstrap line, oldest first: MCP
# registration (`CLAUDE.md`, `.claude/CLAUDE.local.md`), workspace bootstrap (`CLAUDE.md`,
# `AGENTS.md`) and the template vault's `AGENTS.md`. The set is closed: Brain owns exactly
# these lines, and any other line, including an edited copy, belongs to the user. The looser
# variants that historical migrations also recognise are deliberately outside it.
# `target` is the kind a line was written for; `configure.py` also wrote the vault lines into
# workspace `AGENTS.md` (from 0.44.0) until that file got the workspace line.
# To change a current line, close its row and append the new one. Ownership records may then
# hold the closed line: `read_records` accepts any line in the set, and the next repair
# converges both the file and the record on the new line.
# Init-state records carry `bootstrap_line` from 0.27.0, so lines retired before then occur
# only in files.
BOOTSTRAP_LINE_HISTORY: tuple[BootstrapLineRelease, ...] = (
    BootstrapLineRelease('If brain MCP tools are available, call brain_read(resource="router") at session start.', "vault", "0.10.0", "0.23.6"),
    BootstrapLineRelease('If brain MCP tools are available, call brain_read(resource="router") at session start.', "project", "0.10.0", "0.23.6"),
    BootstrapLineRelease("ALWAYS DO FIRST: Call brain_session. Read [[.brain-core/index]]", "vault", "0.24.0", "0.24.12"),
    BootstrapLineRelease("ALWAYS DO FIRST: Call brain_session", "project", "0.24.0", "0.48.9"),
    BootstrapLineRelease("ALWAYS DO FIRST: Call MCP `brain_session`, else read `.brain-core/index.md` if it exists.", "vault", "0.25.1", "0.54.59"),
    BootstrapLineRelease("ALWAYS DO FIRST: Call MCP `brain_session`; if MCP is unavailable, run `brain session --json` from this workspace.", "project", "0.48.10", "0.54.59"),
    BootstrapLineRelease("ALWAYS DO FIRST: Call MCP `session.start`, else read `.brain-core/index.md` if it exists.", "vault", "0.55.0", "0.63.0"),
    BootstrapLineRelease("ALWAYS DO FIRST: Call MCP `session.start`; if MCP is unavailable, run `brain session start --json` from this workspace.", "project", "0.55.0", "0.63.0"),
    BootstrapLineRelease(CLAUDE_MD_BOOTSTRAP_VAULT, "vault", "0.64.0", None),
    BootstrapLineRelease(CLAUDE_MD_BOOTSTRAP_PROJECT, "project", "0.64.0", None),
)
BRAIN_BOOTSTRAP_LINES = frozenset(release.line for release in BOOTSTRAP_LINE_HISTORY)


def is_owned_bootstrap_line(value: Any) -> bool:
    """Whether ownership evidence names a line Brain itself has written; an edited copy is the user's."""
    return isinstance(value, str) and value in BRAIN_BOOTSTRAP_LINES


def _text_lines(content: str) -> list[str]:
    """Split on ``\n`` only, keeping each line's own ending, so joining restores every byte."""
    return re.findall(r"[^\n]*\n|[^\n]+\Z", content)


def _brain_line(item: str) -> bool:
    return item.rstrip("\r\n").strip() in BRAIN_BOOTSTRAP_LINES


def converge_bootstrap_text(content: str, line: str) -> str:
    """Bring a file's Brain bootstrap line to ``line``, keeping every other byte.

    The first line in the closed set becomes ``line`` in place, keeping its
    indentation and line ending; later ones are dropped. Any other line,
    including an edited copy, is kept. A file holding no Brain line gets
    ``line`` appended.
    """
    kept: list[str] = []
    found = False
    for item in _text_lines(content):
        if not _brain_line(item):
            kept.append(item)
        elif not found:
            text = item.rstrip("\r\n")
            indent = text[: len(text) - len(text.lstrip())]
            kept.append(indent + line + item[len(text):])
            found = True
    if found:
        return "".join(kept)
    if not content:
        return f"{line}\n"
    separator = "\n" if content.endswith("\n") else "\n\n"
    return f"{content}{separator}{line}\n"


BOOTSTRAP_FILE_UNCHANGED = "unchanged"
BOOTSTRAP_FILE_CREATED = "created"
BOOTSTRAP_FILE_APPENDED = "appended"
BOOTSTRAP_FILE_UPDATED = "updated"


def converge_bootstrap_file(path: Path, line: str, *, before_write=None) -> str:
    """Converge the file at ``path`` on ``line`` and say what changed.

    Reads and writes bytes, so ``converge_bootstrap_text``'s promise to keep
    every other byte (line endings included) holds on disk. Returns one of
    the ``BOOTSTRAP_FILE_*`` outcomes; ``before_write`` runs once, before a
    write. ``OSError`` and ``UnicodeDecodeError`` propagate for the caller
    to classify.
    """
    try:
        existing = path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        existing = ""
    updated = converge_bootstrap_text(existing, line)
    if updated == existing:
        return BOOTSTRAP_FILE_UNCHANGED
    if before_write is not None:
        before_write()
    safe_write_via(path, lambda handle: handle.write(updated.encode("utf-8")))
    if not existing:
        return BOOTSTRAP_FILE_CREATED
    if updated.startswith(existing):
        return BOOTSTRAP_FILE_APPENDED
    return BOOTSTRAP_FILE_UPDATED


def remove_bootstrap_text(content: str) -> str:
    """Remove every Brain bootstrap line, keeping every other byte.

    Blank lines left at the end of the file (the separator Brain added before
    its line) are dropped too, so an empty result means nothing else remained.
    """
    lines = _text_lines(content)
    kept = [item for item in lines if not _brain_line(item)]
    if len(kept) == len(lines):
        return content
    while kept and not kept[-1].strip():
        kept.pop()
    return "".join(kept)


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
    """Resolve the effective target Brain vault for a persisted MCP config.

    The authoritative local binding route is the user-home vault registry via
    ``resolve_local_brain_vault()``.
    """
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
    return resolve_local_brain_vault(binding["brain"])


def config_targets_vault(server_config: Any, vault_root: Path) -> bool:
    """Return whether a Brain server config resolves to the given target vault."""
    configured_root = resolved_target_vault_root(server_config)
    if configured_root is None:
        return False
    return configured_root == vault_root.resolve()


def bootstrap_line_for_target(target_dir: Path) -> str:
    """Return the expected Brain bootstrap line for the target directory."""
    return CLAUDE_MD_BOOTSTRAP_VAULT if is_brain_vault(target_dir) else CLAUDE_MD_BOOTSTRAP_PROJECT


def _resolve_session_launcher() -> str:
    """Resolve the persisted launcher path used in the SessionStart hook."""
    return find_launcher_python(prefer_path_binaries=True) or sys.executable


def session_hook_python(server_config: dict) -> str:
    """Return the Python executable that should launch the SessionStart hook."""
    command = server_config.get("command")
    if not isinstance(command, str) or not command:
        raise ValueError("Brain MCP server config is missing a string command")
    return command


def _join_hook_command(args: list[str]) -> str:
    """Return a shell command fragment for the platform writing the hook."""
    if sys.platform == "win32":
        return "& " + " ".join(_quote_powershell_arg(arg) for arg in args)
    return " ".join(shlex.quote(arg) for arg in args)


def _quote_powershell_arg(value: str) -> str:
    """Return a single-quoted PowerShell argument token."""
    return "'" + value.replace("'", "''") + "'"


def _session_hook_argv(vault_root: Path, target_dir: Path) -> list[str]:
    """Return the stable SessionStart argv payload after the Python launcher."""
    return [
        str(vault_root / ".brain-core" / "scripts" / "session.py"),
        "--vault",
        str(vault_root),
        "--workspace-dir",
        str(target_dir),
        "--json",
    ]


def build_session_hook_command(
    vault_root: Path,
    target_dir: Path,
    *,
    python_path: str | None = None,
) -> str:
    """Build the persisted SessionStart hook command for a target directory."""
    launcher = python_path or _resolve_session_launcher()
    command = _join_hook_command([launcher, *_session_hook_argv(vault_root, target_dir)])
    if sys.platform == "win32":
        return f"Write-Output 'session.start called:'; {command}"
    return f"echo session.start called: && {command}"


def _hook_command_contains_arg(command: str, arg: str) -> bool:
    return arg in command or shlex.quote(arg) in command or _quote_powershell_arg(arg) in command


def is_session_hook_command(command: Any, vault_root: Path, target_dir: Path) -> bool:
    """Return whether a command is Brain's SessionStart hook for this target.

    Older installs persisted a different echo prefix and a launcher Python
    instead of the managed runtime. Match the stable argv payload rather than
    the whole shell string so repair can replace those stale hooks exactly once.
    """
    if not isinstance(command, str):
        return False
    return all(_hook_command_contains_arg(command, part) for part in _session_hook_argv(vault_root, target_dir))


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
        )
        if is_header:
            name = (
                stripped[2:-2].strip()
                if stripped.startswith("[[")
                else stripped[1:-1].strip()
            )
            current = {"name": name, "header": line, "body": []}
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
        key = key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else json.dumps(key)
        if isinstance(value, dict):
            fields = [line.rstrip() for line in _toml_body_lines(value)]
            body.append(f"{key} = {{ {', '.join(fields)} }}\n")
        elif isinstance(value, str):
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


def read_toml_server_config(config_path: Path) -> Optional[Dict[str, Any]]:
    """Read the Brain MCP entry from a client TOML config file."""
    if not config_path.is_file():
        return None

    try:
        content = config_path.read_text(encoding="utf-8")
    except OSError:
        return None

    import tomllib
    try:
        main = tomllib.loads(content).get("mcp_servers", {}).get("brain", {})
    except (tomllib.TOMLDecodeError, AttributeError):
        return None

    if "command" not in main or "args" not in main:
        return None

    return {
        "command": main["command"],
        "args": main["args"],
        "env": main.get("env", {}),
    }


# Brain Lab probes use this name across older installed Brain versions.
read_codex_server_config = read_toml_server_config


def write_toml_config(server_config: Dict[str, Any], config_path: Path) -> None:
    """Write the Brain MCP entry into a client TOML config file."""
    try:
        content = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
    except OSError:
        content = ""

    safe_write(config_path, render_toml_config(content, server_config))


def render_toml_config(content: str, server_config: Dict[str, Any]) -> str:
    """Update transport while preserving client-owned policy in the same table."""
    import tomllib
    from copy import deepcopy

    original = tomllib.loads(content)
    observed = original.get("mcp_servers", {}).get("brain", {})
    transport = {"command": observed.get("command"), "args": observed.get("args"),
                 "env": observed.get("env", {})}
    if transport == server_config:
        return content
    preamble, sections = _parse_toml_sections(content)
    main_index = _find_section_index(sections, "mcp_servers.brain")
    main = tomllib.loads("".join(sections[main_index]["body"])) if main_index is not None else {}
    main.pop("env", None)
    _upsert_toml_section(
        sections,
        "mcp_servers.brain",
        _toml_body_lines(
            {
                **main,
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
    rendered = _render_toml(preamble, sections)
    expected = deepcopy(original)
    expected.setdefault("mcp_servers", {}).setdefault("brain", {}).update(server_config)
    if tomllib.loads(rendered) != expected:
        raise ValueError("Brain TOML transport cannot be updated without changing client settings")
    return rendered


def render_toml_without_server(
    content: str, server_config: Dict[str, Any]
) -> str | None:
    """Render removal of an exactly matching Brain entry, or return unchanged intent."""
    import tomllib

    observed = tomllib.loads(content).get("mcp_servers", {}).get("brain", {})
    transport = {"command": observed.get("command"), "args": observed.get("args"), "env": observed.get("env", {})}
    if transport != server_config:
        return None
    client_policy = set(observed) - {"command", "args", "env"}
    if client_policy:
        raise ValueError(
            "Codex Brain transport has client-owned policy/settings; remove managed approvals "
            "and explicitly relocate or remove remaining client settings before removing transport"
        )
    preamble, sections = _parse_toml_sections(content)
    main_index = _find_section_index(sections, "mcp_servers.brain")
    if main_index is None:
        return None
    kept_sections = [
        section
        for section in sections
        if not (
            section["name"] == "mcp_servers.brain"
            or section["name"].startswith("mcp_servers.brain.")
        )
    ]
    return _render_toml(preamble, kept_sections)


def remove_toml_server(config_path: Path, server_config: Dict[str, Any]) -> bool:
    """Remove the Brain MCP entry from a client TOML config file when it matches."""
    current = read_toml_server_config(config_path)
    if current is None or current != server_config:
        return False

    try:
        content = config_path.read_text(encoding="utf-8")
    except OSError:
        return False

    rendered = render_toml_without_server(content, server_config)
    if rendered is None:
        return False
    if rendered:
        safe_write(config_path, rendered)
        return True

    try:
        config_path.unlink()
    except FileNotFoundError:
        return True
    return True


def migrate_bootstrap_text(content: str) -> str:
    """Replace only complete Brain-authored bootstrap lines from the dotted epoch.

    Historical: only the frozen 0.64.0 migration uses this. Current writers
    converge through ``converge_bootstrap_text``.
    """
    current = (
        CLAUDE_MD_BOOTSTRAP_VAULT,
        CLAUDE_MD_BOOTSTRAP_PROJECT,
        "ALWAYS DO FIRST: Call MCP `session_start`.",
    )
    replacements = {
        line.replace("session_start", "session.start"): line for line in current
    }
    return "".join(
        replacements.get(line.rstrip("\r\n"), line.rstrip("\r\n"))
        + line[len(line.rstrip("\r\n")) :]
        for line in content.splitlines(keepends=True)
    )
