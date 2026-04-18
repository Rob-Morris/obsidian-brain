#!/usr/bin/env python3
"""
migrate_to_0_27_6.py — Rewrite legacy MCP launch config to brain_mcp.

Repairs Brain-managed MCP registrations that still launch the deprecated
`.brain-core/mcp/` transport paths introduced before v0.26.0. The migration
rewrites only the Brain entry for configs that still point at this vault's
legacy transport, preserving unrelated config and updating `.brain/local/
init-state.json` so later removal still matches the repaired entry.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


VERSION = "0.27.6"

BRAIN_SERVER_NAME = "brain"
INIT_STATE_REL = os.path.join(".brain", "local", "init-state.json")
PACKAGE_ROOT_REL = os.path.join(".brain-core")
LEGACY_PROXY_REL = os.path.join(".brain-core", "mcp", "proxy.py")
LEGACY_SERVER_REL = os.path.join(".brain-core", "mcp", "server.py")


def _safe_write(path: Path, content: str) -> str:
    """Atomic file write: tmp -> fsync -> os.replace."""
    target = os.path.realpath(str(path))
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=os.path.basename(target) + ".",
        suffix=".tmp",
        dir=os.path.dirname(target) or ".",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
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


def _safe_write_json(path: Path, data: dict[str, Any]) -> str:
    return _safe_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def _read_json_safe(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _looks_like_path(value: str) -> bool:
    return (
        os.sep in value
        or value.endswith(".py")
        or value.startswith(".")
        or value.startswith("~")
    )


def _display_path(path: Path, vault_root: str) -> str:
    try:
        return os.path.relpath(str(path), vault_root)
    except ValueError:
        return str(path)


def _ensure_pythonpath(existing: Any, package_root: str) -> str:
    if not isinstance(existing, str) or not existing:
        return package_root
    parts = existing.split(os.pathsep)
    if package_root in parts:
        return existing
    return os.pathsep.join([package_root, existing])


def _legacy_paths(vault_root: str) -> tuple[str, str]:
    return (
        os.path.realpath(os.path.join(vault_root, LEGACY_PROXY_REL)),
        os.path.realpath(os.path.join(vault_root, LEGACY_SERVER_REL)),
    )


def _is_legacy_config(server_config: dict[str, Any], vault_root: str) -> bool:
    args = server_config.get("args")
    if not isinstance(args, list):
        return False
    legacy_proxy, legacy_server = _legacy_paths(vault_root)
    for value in args:
        if not isinstance(value, str) or not _looks_like_path(value):
            continue
        resolved = os.path.realpath(value)
        if resolved in {legacy_proxy, legacy_server}:
            return True
    return False


def _belongs_to_vault(server_config: dict[str, Any], vault_root: str) -> bool:
    env = server_config.get("env")
    if isinstance(env, dict):
        configured_root = env.get("BRAIN_VAULT_ROOT")
        if isinstance(configured_root, str) and configured_root:
            if os.path.realpath(configured_root) == os.path.realpath(vault_root):
                return True

    if not _is_legacy_config(server_config, vault_root):
        return False

    legacy_proxy, legacy_server = _legacy_paths(vault_root)
    args = server_config.get("args")
    if not isinstance(args, list):
        return False
    for value in args:
        if not isinstance(value, str) or not _looks_like_path(value):
            continue
        resolved = os.path.realpath(value)
        if resolved in {legacy_proxy, legacy_server}:
            return True
    return False


def _rewrite_server_config(
    server_config: dict[str, Any],
    vault_root: str,
    *,
    workspace_dir: str | None = None,
) -> dict[str, Any] | None:
    command = server_config.get("command")
    if not isinstance(command, str) or not command:
        return None

    env = server_config.get("env")
    rewritten_env = dict(env) if isinstance(env, dict) else {}
    rewritten_env["BRAIN_VAULT_ROOT"] = vault_root
    rewritten_env["PYTHONPATH"] = _ensure_pythonpath(
        rewritten_env.get("PYTHONPATH"),
        os.path.join(vault_root, PACKAGE_ROOT_REL),
    )
    if workspace_dir and not rewritten_env.get("BRAIN_WORKSPACE_DIR"):
        rewritten_env["BRAIN_WORKSPACE_DIR"] = workspace_dir

    return {
        "command": command,
        "args": ["-m", "brain_mcp.proxy", command, "brain_mcp.server"],
        "env": rewritten_env,
    }


def _parse_toml_sections(content: str) -> tuple[list[str], list[dict[str, Any]]]:
    preamble: list[str] = []
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

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


def _render_toml(preamble: list[str], sections: list[dict[str, Any]]) -> str:
    chunks: list[str] = []

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


def _find_section_index(sections: list[dict[str, Any]], name: str) -> int | None:
    for index, section in enumerate(sections):
        if section["name"] == name:
            return index
    return None


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


def _parse_toml_mapping(body_lines: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for raw_line in body_lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = _parse_toml_scalar(value)
    return result


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return json.dumps(value, ensure_ascii=False)


def _toml_body_lines(mapping: dict[str, Any]) -> list[str]:
    return [f"{key} = {_toml_value(value)}\n" for key, value in mapping.items()]


def _upsert_toml_section(
    sections: list[dict[str, Any]],
    name: str,
    body_lines: list[str],
) -> None:
    existing_index = _find_section_index(sections, name)
    if existing_index is not None:
        sections[existing_index]["body"] = body_lines
        return
    sections.append({"name": name, "header": f"[{name}]\n", "body": body_lines})


def _read_codex_server_config(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return None

    _, sections = _parse_toml_sections(content)
    main_index = _find_section_index(sections, "mcp_servers.brain")
    if main_index is None:
        return None
    main = _parse_toml_mapping(sections[main_index]["body"])
    env_index = _find_section_index(sections, "mcp_servers.brain.env")
    env = _parse_toml_mapping(sections[env_index]["body"]) if env_index is not None else {}
    if "command" not in main or "args" not in main:
        return None
    return {"command": main["command"], "args": main["args"], "env": env}


def _write_codex_server_config(path: Path, server_config: dict[str, Any]) -> None:
    try:
        content = path.read_text(encoding="utf-8") if path.is_file() else ""
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
    _safe_write(path, _render_toml(preamble, sections))


def _scan_json_config(
    path: Path,
    vault_root: str,
    *,
    workspace_dir: str | None = None,
) -> tuple[dict[str, Any], str] | None:
    data = _read_json_safe(path)
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        return None
    server_config = servers.get(BRAIN_SERVER_NAME)
    if not isinstance(server_config, dict):
        return None
    if not _belongs_to_vault(server_config, vault_root) or not _is_legacy_config(server_config, vault_root):
        return None

    rewritten = _rewrite_server_config(
        server_config,
        vault_root,
        workspace_dir=workspace_dir,
    )
    if rewritten is None or rewritten == server_config:
        return None

    servers[BRAIN_SERVER_NAME] = rewritten
    _safe_write_json(path, data)
    return rewritten, f"rewrote legacy Brain MCP entry in {_display_path(path, vault_root)}"


def _scan_codex_config(
    path: Path,
    vault_root: str,
    *,
    workspace_dir: str | None = None,
) -> tuple[dict[str, Any], str] | None:
    server_config = _read_codex_server_config(path)
    if server_config is None:
        return None
    if not _belongs_to_vault(server_config, vault_root) or not _is_legacy_config(server_config, vault_root):
        return None

    rewritten = _rewrite_server_config(
        server_config,
        vault_root,
        workspace_dir=workspace_dir,
    )
    if rewritten is None or rewritten == server_config:
        return None

    _write_codex_server_config(path, rewritten)
    return rewritten, f"rewrote legacy Brain MCP entry in {_display_path(path, vault_root)}"


def _state_path(vault_root: str) -> Path:
    return Path(vault_root) / INIT_STATE_REL


def _load_init_state(vault_root: str) -> dict[str, Any]:
    path = _state_path(vault_root)
    if not path.is_file():
        return {"version": 1, "records": []}
    data = _read_json_safe(path)
    records = data.get("records")
    if not isinstance(records, list):
        records = []
    return {"version": data.get("version", 1), "records": records}


def _save_init_state(vault_root: str, state: dict[str, Any]) -> None:
    _safe_write_json(_state_path(vault_root), state)


def _repair_init_state(
    vault_root: str,
    rewritten_by_path: dict[str, dict[str, Any]],
) -> list[str]:
    state = _load_init_state(vault_root)
    actions: list[str] = []
    changed = False

    for record in state["records"]:
        if not isinstance(record, dict):
            continue
        config_path = record.get("config_path")
        if not isinstance(config_path, str) or not config_path:
            continue

        rewritten = rewritten_by_path.get(os.path.realpath(config_path))
        if rewritten is None:
            server_config = record.get("server_config")
            if not isinstance(server_config, dict):
                continue
            target_path = record.get("target_path")
            workspace_dir = target_path if isinstance(target_path, str) and target_path else None
            if not _belongs_to_vault(server_config, vault_root) or not _is_legacy_config(server_config, vault_root):
                continue
            rewritten = _rewrite_server_config(
                server_config,
                vault_root,
                workspace_dir=workspace_dir,
            )
            if rewritten is None:
                continue

        if record.get("server_config") == rewritten:
            continue
        record["server_config"] = rewritten
        changed = True

    if changed:
        _save_init_state(vault_root, state)
        actions.append("updated recorded Brain MCP config in .brain/local/init-state.json")

    return actions


def migrate(vault_root: str) -> dict[str, Any]:
    """Repair legacy Brain MCP launch config for this vault."""
    vault_root = os.path.realpath(str(vault_root))
    actions: list[str] = []
    rewritten_by_path: dict[str, dict[str, Any]] = {}

    candidates: list[tuple[str, Path, str | None]] = [
        ("json", Path(vault_root) / ".mcp.json", vault_root),
        ("json", Path(vault_root) / ".claude" / "settings.local.json", vault_root),
        ("toml", Path(vault_root) / ".codex" / "config.toml", vault_root),
        ("json", Path.home() / ".claude.json", None),
        ("toml", Path.home() / ".codex" / "config.toml", None),
    ]

    state = _load_init_state(vault_root)
    for record in state["records"]:
        if not isinstance(record, dict):
            continue
        client = record.get("client")
        config_path = record.get("config_path")
        if client not in {"claude", "codex"} or not isinstance(config_path, str) or not config_path:
            continue
        target_path = record.get("target_path")
        workspace_dir = target_path if isinstance(target_path, str) and target_path else None
        kind = "toml" if client == "codex" else "json"
        candidates.append((kind, Path(config_path), workspace_dir))

    seen: set[tuple[str, str]] = set()
    for kind, path, workspace_dir in candidates:
        key = (kind, os.path.realpath(str(path)))
        if key in seen:
            continue
        seen.add(key)

        if kind == "json":
            result = _scan_json_config(path, vault_root, workspace_dir=workspace_dir)
        else:
            result = _scan_codex_config(path, vault_root, workspace_dir=workspace_dir)

        if result is None:
            continue

        rewritten, action = result
        rewritten_by_path[key[1]] = rewritten
        actions.append(action)

    actions.extend(_repair_init_state(vault_root, rewritten_by_path))

    if not actions:
        return {"status": "skipped", "actions": []}
    return {"status": "ok", "actions": actions}
