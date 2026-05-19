#!/usr/bin/env python3
"""Launcher-safe bootstrap diagnostics."""

from __future__ import annotations

import json
import os
import importlib.util
import sys
from pathlib import Path
from typing import Any

import init
from _common import resolve_vault_venv_python
from _bootstrap.runtime import probe_python, required_modules_for_scope
from _repair_common import attach_repair_guidance


ISSUE_RUNTIME_MISSING = "runtime-missing"
ISSUE_RUNTIME_UNUSABLE = "runtime-unusable"
ISSUE_MANAGED_RUNTIME_DEPENDENCIES_MISSING = "managed-runtime-dependencies-missing"


def _runtime_issue_message(issue: str) -> str:
    messages = {
        ISSUE_RUNTIME_MISSING: "Central managed runtime is missing for this vault.",
        ISSUE_RUNTIME_UNUSABLE: "Central managed runtime is present but unusable.",
        ISSUE_MANAGED_RUNTIME_DEPENDENCIES_MISSING: "Central managed runtime is present but missing required baseline packages.",
    }
    return messages.get(issue, "Central managed runtime is unhealthy.")


def _runtime_state_from_probe(python_path: str, probe: dict) -> dict:
    """Normalise runtime probe results into one structured state payload."""
    missing = list(probe.get("missing", []))
    if not probe.get("compatible"):
        return {
            "healthy": False,
            "python": python_path,
            "issues": [ISSUE_RUNTIME_UNUSABLE],
            "missing_modules": missing,
            "message": _runtime_issue_message(ISSUE_RUNTIME_UNUSABLE),
        }
    if missing:
        module_list = ", ".join(sorted(missing))
        return {
            "healthy": False,
            "python": python_path,
            "issues": [ISSUE_MANAGED_RUNTIME_DEPENDENCIES_MISSING],
            "missing_modules": missing,
            "message": _runtime_issue_message(
                ISSUE_MANAGED_RUNTIME_DEPENDENCIES_MISSING
            ).rstrip(".")
            + f": {module_list}.",
        }
    return {
        "healthy": True,
        "python": python_path,
        "issues": [],
        "missing_modules": [],
        "message": "Central managed runtime is ready for packageful Brain work.",
    }


def inspect_runtime(vault_root: Path) -> dict:
    """Inspect the central managed runtime for this vault."""
    managed_python = resolve_vault_venv_python(vault_root)
    runtime_modules = required_modules_for_scope("runtime")
    managed_python_str = os.path.realpath(str(managed_python))
    if not managed_python.is_file():
        return {
            "healthy": False,
            "python": managed_python_str,
            "issues": [ISSUE_RUNTIME_MISSING],
            "missing_modules": list(runtime_modules),
            "message": _runtime_issue_message(ISSUE_RUNTIME_MISSING),
        }

    if os.path.realpath(sys.executable) == managed_python_str:
        probe = {
            "compatible": sys.version_info >= (3, 12),
            "missing": [
                name for name in runtime_modules if importlib.util.find_spec(name) is None
            ],
        }
        return _runtime_state_from_probe(managed_python_str, probe)

    probe = probe_python(managed_python_str, modules=runtime_modules)
    return _runtime_state_from_probe(managed_python_str, probe)


def _read_json_safe(path: Path) -> tuple[dict | None, str | None]:
    if not path.is_file():
        return None, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return None, str(exc)
    return data if isinstance(data, dict) else None, None


def inspect_registry(vault_root: Path) -> dict:
    """Inspect .brain/local/workspaces.json without mutating it."""
    path = vault_root / ".brain" / "local" / "workspaces.json"
    if not path.is_file():
        return {
            "path": path,
            "healthy": True,
            "present": False,
            "message": "No linked workspace registry is present for this vault.",
            "canonical": {"workspaces": {}},
        }

    raw, error = _read_json_safe(path)
    if error:
        return {
            "path": path,
            "healthy": False,
            "present": True,
            "message": "Registry JSON is unreadable.",
            "canonical": {"workspaces": {}},
            "backup_required": True,
        }
    if raw is None:
        return {
            "path": path,
            "healthy": False,
            "present": True,
            "message": "Registry payload is not a JSON object.",
            "canonical": {"workspaces": {}},
            "backup_required": True,
        }

    workspaces = raw.get("workspaces", {})
    if not isinstance(workspaces, dict):
        return {
            "path": path,
            "healthy": False,
            "present": True,
            "message": "Registry `workspaces` payload is not an object.",
            "canonical": {"workspaces": {}},
            "backup_required": True,
        }

    canonical: dict[str, Any] = {}
    invalid_slugs: list[str] = []
    for slug, entry in workspaces.items():
        if isinstance(entry, str):
            canonical[slug] = {"path": entry}
        elif isinstance(entry, dict) and isinstance(entry.get("path"), str):
            canonical[slug] = entry
        else:
            invalid_slugs.append(slug)

    if invalid_slugs:
        return {
            "path": path,
            "healthy": False,
            "present": True,
            "message": (
                "Registry contains invalid linked-workspace entries: "
                + ", ".join(sorted(invalid_slugs))
            ),
            "canonical": {"workspaces": canonical},
            "backup_required": True,
        }

    canonical_payload = {"workspaces": canonical}
    if raw != canonical_payload:
        return {
            "path": path,
            "healthy": False,
            "present": True,
            "message": "Registry needs canonical JSON normalisation.",
            "canonical": canonical_payload,
            "backup_required": False,
        }

    return {
        "path": path,
        "healthy": True,
        "present": True,
        "message": "Registry is healthy.",
        "canonical": canonical_payload,
    }


def _expected_project_server_config(vault_root: Path) -> dict:
    venv_python = str(resolve_vault_venv_python(vault_root))
    return init.build_mcp_config(venv_python, vault_root, workspace_dir=vault_root)


def _record_matches(record: dict, *, client: str, config_path: Path, server_config: dict) -> bool:
    return (
        record.get("client") == client
        and record.get("scope") == "project"
        and record.get("target_path") == str(config_path.parent.parent if client == "codex" else config_path.parent)
        and record.get("config_path") == str(config_path)
        and record.get("server_config") == server_config
    )


def _has_hook_entry(settings: dict, command: str) -> bool:
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return False
    session_start = hooks.get("SessionStart")
    if not isinstance(session_start, list):
        return False
    for entry in session_start:
        if not isinstance(entry, dict):
            continue
        child_hooks = entry.get("hooks")
        if not isinstance(child_hooks, list):
            continue
        for child in child_hooks:
            if not isinstance(child, dict):
                continue
            if child.get("command") == command:
                return True
    return False


def _current_vault_project_records(vault_root: Path) -> list[dict]:
    return init.matching_records(
        vault_root,
        ["claude", "codex"],
        "project",
        vault_root,
    )


def _has_project_record(records: list[dict], client: str) -> bool:
    return any(record.get("client") == client for record in records)


def _read_claude_project_server(config_path: Path) -> dict | None:
    payload, _ = _read_json_safe(config_path)
    servers = payload.get("mcpServers", {}) if isinstance(payload, dict) else {}
    if not isinstance(servers, dict):
        return None
    server = servers.get(init.BRAIN_SERVER_NAME)
    return server if isinstance(server, dict) else None


def inspect_mcp(vault_root: Path) -> dict:
    """Inspect current-vault project MCP state without mutating user scope."""
    server_config = _expected_project_server_config(vault_root)
    claude_config_path = vault_root / init.CLAUDE_PROJECT_CONFIG_FILE
    codex_config_path = vault_root / init.CODEX_CONFIG_REL
    claude_settings_path = vault_root / init.CLAUDE_LOCAL_SETTINGS_FILE
    claude_md_path = vault_root / init.CLAUDE_MD_FILE
    expected_hook = init.build_session_hook_command(vault_root, vault_root)
    expected_bootstrap = init.bootstrap_line_for_target(vault_root)

    claude_server = _read_claude_project_server(claude_config_path)
    codex_server = init.read_codex_server_config(codex_config_path)
    claude_config_ok = claude_server == server_config
    codex_config_ok = codex_server == server_config

    try:
        claude_md_text = claude_md_path.read_text(encoding="utf-8")
    except OSError:
        claude_md_text = ""
    bootstrap_ok = expected_bootstrap in claude_md_text

    settings, _ = _read_json_safe(claude_settings_path)
    hook_ok = _has_hook_entry(settings or {}, expected_hook)

    records = _current_vault_project_records(vault_root)
    claude_record_ok = any(
        _record_matches(record, client="claude", config_path=claude_config_path, server_config=server_config)
        for record in records
    )
    codex_record_ok = any(
        _record_matches(record, client="codex", config_path=codex_config_path, server_config=server_config)
        for record in records
    )
    claude_present = claude_server is not None or _has_project_record(records, "claude")
    codex_present = codex_server is not None or _has_project_record(records, "codex")

    return {
        "server_config": server_config,
        "claude": {
            "config_path": claude_config_path,
            "present": claude_present,
            "healthy": claude_config_ok and bootstrap_ok and hook_ok and claude_record_ok,
            "config_ok": claude_config_ok,
            "bootstrap_ok": bootstrap_ok,
            "hook_ok": hook_ok,
            "record_ok": claude_record_ok,
        },
        "codex": {
            "config_path": codex_config_path,
            "present": codex_present,
            "healthy": codex_config_ok and codex_record_ok,
            "config_ok": codex_config_ok,
            "record_ok": codex_record_ok,
        },
    }


def local_mcp_state_present(vault_root: Path) -> bool:
    return any(
        (vault_root / rel).exists()
        for rel in (
            init.CLAUDE_PROJECT_CONFIG_FILE,
            init.CODEX_CONFIG_REL,
            init.INIT_STATE_REL,
        )
    )


def collect_bootstrap_check_findings(vault_root: str | Path) -> list[dict]:
    """Return launcher-safe repair-oriented compliance findings."""
    vault_root = Path(vault_root)
    findings: list[dict] = []

    registry = inspect_registry(vault_root)
    if registry["present"] and not registry["healthy"]:
        finding = {
            "check": "workspace_registry",
            "severity": "warning",
            "file": str(registry["path"].relative_to(vault_root)),
            "message": registry["message"],
        }
        findings.append(attach_repair_guidance(finding, vault_root, "registry"))

    if local_mcp_state_present(vault_root):
        runtime = inspect_runtime(vault_root)
        if not runtime["healthy"]:
            for issue in runtime["issues"]:
                finding = {
                    "check": f"runtime:{issue}",
                    "severity": "warning",
                    "file": None,
                    "message": _runtime_issue_message(issue),
                }
                findings.append(attach_repair_guidance(finding, vault_root, "runtime"))

        mcp = inspect_mcp(vault_root)
        unhealthy = [
            client
            for client in ("claude", "codex")
            if mcp[client]["present"] and not mcp[client]["healthy"]
        ]
        if unhealthy:
            finding = {
                "check": "mcp_registration",
                "severity": "warning",
                "file": None,
                "message": "Current-vault MCP registration state is drifted or incomplete.",
            }
            findings.append(attach_repair_guidance(finding, vault_root, "mcp"))

    return findings
