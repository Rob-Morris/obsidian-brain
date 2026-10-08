#!/usr/bin/env python3
"""Launcher-safe bootstrap diagnostics."""

from __future__ import annotations

import json
import os
import importlib.util
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from _bootstrap.mcp_state import (
    BRAIN_SERVER_NAME,
    CLAUDE_LOCAL_SETTINGS_FILE,
    CLAUDE_MD_FILE,
    CLAUDE_PROJECT_CONFIG_FILE,
    CODEX_CONFIG_REL,
    GROK_CONFIG_REL,
    GROK_RULE_REL,
    INIT_STATE_REL,
    bootstrap_line_for_target,
    build_mcp_config,
    build_session_hook_command,
    configured_vault_root,
    is_session_hook_command,
    matching_records,
    read_toml_server_config,
    session_hook_python,
)
from _common import join_argv, resolve_vault_venv_python, same_executable_path
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
    probe_error = probe.get("probe_error")
    if not probe.get("compatible"):
        message = (
            f"Central managed runtime is present but could not be probed: {probe_error}."
            if probe_error
            else _runtime_issue_message(ISSUE_RUNTIME_UNUSABLE)
        )
        return {
            "healthy": False,
            "python": python_path,
            "issues": [ISSUE_RUNTIME_UNUSABLE],
            "missing_modules": missing,
            "message": message,
            "probe_error": probe_error,
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
            "probe_error": probe_error,
        }
    return {
        "healthy": True,
        "python": python_path,
        "issues": [],
        "missing_modules": [],
        "message": "Central managed runtime is ready for packageful Brain work.",
        "probe_error": probe_error,
    }


def inspect_runtime(vault_root: Path) -> dict:
    """Inspect the central managed runtime for this vault."""
    managed_python = resolve_vault_venv_python(vault_root)
    runtime_modules = required_modules_for_scope("runtime")
    managed_python_str = str(managed_python)
    if not managed_python.is_file():
        return {
            "healthy": False,
            "python": managed_python_str,
            "issues": [ISSUE_RUNTIME_MISSING],
            "missing_modules": list(runtime_modules),
            "message": _runtime_issue_message(ISSUE_RUNTIME_MISSING),
        }

    if same_executable_path(sys.executable, managed_python):
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
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        return None, str(exc)
    return data if isinstance(data, dict) else None, None


class RegistryCondition(str, Enum):
    """What a linked workspace registry file holds, and whether an automatic rebuild may touch it."""

    ABSENT = "absent"
    HEALTHY = "healthy"
    # Every row is kept; only the stored form changes.
    NORMALISABLE = "normalisable"
    # Every row it drops names no usable folder (``workspace_registry.salvage_row``).
    INVALID_ROWS = "invalid_rows"
    # No row can be read from it, so a rebuild would lose every row it held.
    UNPARSEABLE = "unparseable"
    # The file could not be read at all; nothing may rewrite it.
    UNREADABLE = "unreadable"


@dataclass(frozen=True)
class RegistryInspection:
    """One read of ``.brain/local/workspaces.json``: its condition and the rows that can be salvaged."""

    path: Path
    condition: RegistryCondition
    message: str
    rows: dict[str, dict] = field(default_factory=dict)
    # The exact bytes the condition was read from, the base of a compare-and-swap rewrite.
    content: bytes | None = None
    # For an unreadable or unparseable file, the structured reason a finding declares as evidence.
    reason: str | None = None

    @property
    def present(self) -> bool:
        return self.condition is not RegistryCondition.ABSENT

    @property
    def healthy(self) -> bool:
        return self.condition in {RegistryCondition.ABSENT, RegistryCondition.HEALTHY}

    @property
    def backup_required(self) -> bool:
        return self.condition in {RegistryCondition.INVALID_ROWS, RegistryCondition.UNPARSEABLE}


def inspect_registry(vault_root: Path) -> RegistryInspection:
    """Inspect .brain/local/workspaces.json without mutating it."""
    import workspace_registry

    path = vault_root / ".brain" / "local" / "workspaces.json"
    if not path.is_file():
        return RegistryInspection(path, RegistryCondition.ABSENT,
                                  "No linked workspace registry is present for this vault.")
    try:
        content = path.read_bytes()
    except OSError as exc:
        import errno

        return RegistryInspection(path, RegistryCondition.UNREADABLE, (
            f"The linked workspace registry could not be read ({exc}); nothing rewrites it. Restore read access "
            "to the file, then run vault.check again."), reason=errno.errorcode.get(exc.errno, type(exc).__name__))

    def unparseable(reason: str, problem: str) -> RegistryInspection:
        return RegistryInspection(path, RegistryCondition.UNPARSEABLE, content=content, reason=reason, message=(
            f"The linked workspace registry {problem}, so none of its rows can be read and it is never rebuilt "
            "automatically. Restore it from a copy, or rebuild it empty (keeping the file as "
            f"{path.name}.bak) with `{registry_rebuild_command(vault_root)}` and run brain workspace setup from "
            "each linked folder."))

    try:
        raw = json.loads(content.decode("utf-8"))
    except UnicodeDecodeError:
        return unparseable("not_utf8", "is not UTF-8")
    except json.JSONDecodeError as exc:
        return unparseable("invalid_json", f"is not valid JSON ({exc})")
    if not isinstance(raw, dict):
        return unparseable("not_an_object", "is not a JSON object")
    workspaces = raw.get("workspaces", {})
    if not isinstance(workspaces, dict):
        return unparseable("workspaces_not_an_object", "has a `workspaces` value that is not an object")

    rows: dict[str, dict] = {}
    invalid: list[str] = []
    for key, entry in workspaces.items():
        row = workspace_registry.salvage_row(key, entry)
        if row is None:
            invalid.append(key)
        else:
            rows[key] = row
    if invalid:
        return RegistryInspection(path, RegistryCondition.INVALID_ROWS, (
            "The linked workspace registry has rows that name no usable folder (an invalid key, or a path that "
            "is empty, relative or contains a NUL byte): " + ", ".join(repr(key) for key in sorted(invalid))
            + ". The repair rebuilds it without them and keeps the file as a backup."), rows, content)
    if raw != {"workspaces": rows}:
        return RegistryInspection(path, RegistryCondition.NORMALISABLE,
                                  "The linked workspace registry needs canonical JSON normalisation.", rows, content)
    return RegistryInspection(path, RegistryCondition.HEALTHY, "Registry is healthy.", rows, content)


def registry_rebuild_command(vault_root: Path) -> str:
    """The person's explicit rebuild of a registry whose rows cannot be read: it may lose rows."""
    from _repair_common import REPAIR_SCOPES, build_catalogue_argv

    family = REPAIR_SCOPES["registry"]
    return join_argv([*build_catalogue_argv(vault_root, family),
                      "--request-json", json.dumps({"allow_row_loss": True}, separators=(",", ":"))])


def _expected_project_server_config(vault_root: Path) -> dict:
    venv_python = str(resolve_vault_venv_python(vault_root))
    return build_mcp_config(venv_python, vault_root, workspace_dir=vault_root)


def _record_matches(record: dict, *, client: str, config_path: Path, server_config: dict) -> bool:
    return (
        record.get("client") == client
        and record.get("scope") == "project"
        and record.get("target_path")
        == str(
            config_path.parent.parent
            if client in {"codex", "grok"}
            else config_path.parent
        )
        and record.get("config_path") == str(config_path)
        and record.get("server_config") == server_config
    )


def _session_hook_commands(settings: dict) -> list[str]:
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return []
    session_start = hooks.get("SessionStart")
    if not isinstance(session_start, list):
        return []
    commands: list[str] = []
    for entry in session_start:
        if not isinstance(entry, dict):
            continue
        child_hooks = entry.get("hooks")
        if not isinstance(child_hooks, list):
            continue
        for child in child_hooks:
            if not isinstance(child, dict):
                continue
            command = child.get("command")
            if isinstance(command, str):
                commands.append(command)
    return commands


def _session_hook_state(settings: dict, expected_command: str, vault_root: Path, target_dir: Path) -> dict:
    commands = _session_hook_commands(settings)
    brain_commands = [
        command
        for command in commands
        if is_session_hook_command(command, vault_root, target_dir)
    ]
    stale_commands = [command for command in brain_commands if command != expected_command]
    return {
        "present": bool(brain_commands),
        "exact": expected_command in brain_commands,
        "stale_count": len(stale_commands),
        "duplicate_count": max(0, len(brain_commands) - 1),
        "commands": brain_commands,
    }


def _project_records_for_vault(vault_root: Path) -> list[dict]:
    return matching_records(
        vault_root,
        ["claude", "codex", "grok"],
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
    server = servers.get(BRAIN_SERVER_NAME)
    return server if isinstance(server, dict) else None


def inspect_mcp(vault_root: Path) -> dict:
    """Inspect a vault's project MCP state without mutating user scope."""
    server_config = _expected_project_server_config(vault_root)
    claude_config_path = vault_root / CLAUDE_PROJECT_CONFIG_FILE
    codex_config_path = vault_root / CODEX_CONFIG_REL
    claude_settings_path = vault_root / CLAUDE_LOCAL_SETTINGS_FILE
    claude_md_path = vault_root / CLAUDE_MD_FILE
    hook_python = session_hook_python(server_config)
    expected_hook = build_session_hook_command(
        vault_root,
        vault_root,
        python_path=hook_python,
    )
    expected_bootstrap = bootstrap_line_for_target(vault_root)

    claude_server = _read_claude_project_server(claude_config_path)
    codex_server = read_toml_server_config(codex_config_path)
    expected_command = server_config["command"]
    claude_command = claude_server.get("command") if isinstance(claude_server, dict) else None
    codex_command = codex_server.get("command") if isinstance(codex_server, dict) else None
    claude_config_ok = claude_server == server_config
    codex_config_ok = codex_server == server_config
    claude_command_ok = claude_server is None or (
        isinstance(claude_command, str) and same_executable_path(claude_command, expected_command)
    )
    codex_command_ok = codex_server is None or (
        isinstance(codex_command, str) and same_executable_path(codex_command, expected_command)
    )

    bootstrap_reason = None
    bootstrap_message = None
    try:
        claude_md_text = claude_md_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        bootstrap_ok = False
        bootstrap_reason = "unreadable"
        bootstrap_message = (
            "Claude bootstrap cannot be inspected because CLAUDE.md is unreadable as UTF-8. "
            "Run vault.check for per-file text diagnosis."
        )
    except OSError:
        bootstrap_ok = False
    else:
        bootstrap_ok = expected_bootstrap in claude_md_text

    settings, _ = _read_json_safe(claude_settings_path)
    hook_state = _session_hook_state(settings or {}, expected_hook, vault_root, vault_root)
    hook_ok = hook_state["exact"] and hook_state["stale_count"] == 0 and hook_state["duplicate_count"] == 0

    records = _project_records_for_vault(vault_root)
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

    from _bootstrap.grok_mcp import RULE_CONTENT, read_server

    grok_path = vault_root / GROK_CONFIG_REL
    grok_invalid = False
    try:
        grok_content = grok_path.read_text(encoding="utf-8")
        grok_server = read_server(grok_content)
    except (OSError, ValueError):
        grok_server = None
        grok_invalid = grok_path.exists()
    try:
        grok_bootstrap_ok = (vault_root / GROK_RULE_REL).read_text(
            encoding="utf-8"
        ) == RULE_CONTENT
    except (OSError, UnicodeError):
        grok_bootstrap_ok = False
    grok_command = grok_server.get("command") if grok_server else None
    grok_config_ok = (
        grok_server is not None
        and all(grok_server.get(key) == value for key, value in server_config.items())
        and not any(key in grok_server for key in ("url", "headers", "http_headers"))
    )
    grok_record_ok = any(
        _record_matches(
            record, client="grok", config_path=grok_path, server_config=server_config
        )
        for record in records
    )
    return {
        "grok": {
            "config_path": grok_path,
            "present": grok_invalid
            or grok_server is not None
            or _has_project_record(records, "grok"),
            "healthy": grok_config_ok and grok_bootstrap_ok and grok_record_ok,
            "config_ok": grok_config_ok,
            "command": grok_command,
            "command_ok": grok_server is None
            or (
                isinstance(grok_command, str)
                and same_executable_path(grok_command, expected_command)
            ),
            "bootstrap_ok": grok_bootstrap_ok,
            "record_ok": grok_record_ok,
        },
        "server_config": server_config,
        "claude": {
            "config_path": claude_config_path,
            "present": claude_present,
            "healthy": claude_config_ok
            and bootstrap_ok
            and hook_ok
            and claude_record_ok,
            "config_ok": claude_config_ok,
            "command": claude_command,
            "command_ok": claude_command_ok,
            "bootstrap_ok": bootstrap_ok,
            "bootstrap_reason": bootstrap_reason,
            "bootstrap_message": bootstrap_message,
            "hook_ok": hook_ok,
            "hook_state": hook_state,
            "record_ok": claude_record_ok,
        },
        "codex": {
            "config_path": codex_config_path,
            "present": codex_present,
            "healthy": codex_config_ok and codex_record_ok,
            "config_ok": codex_config_ok,
            "command": codex_command,
            "command_ok": codex_command_ok,
            "record_ok": codex_record_ok,
        },
    }


def local_mcp_state_present(vault_root: Path) -> bool:
    return any(
        (vault_root / rel).exists()
        for rel in (
            CLAUDE_PROJECT_CONFIG_FILE,
            CODEX_CONFIG_REL,
            GROK_CONFIG_REL,
            GROK_RULE_REL,
            INIT_STATE_REL,
        )
    )


def _workspace_command(vault_root: Path, verb: str, request: dict, *, workspace: str | None = None) -> str:
    from _repair_common import find_launcher_binary

    argv = [find_launcher_binary() or "brain", "--vault", str(vault_root), "workspace", verb]
    if workspace is not None:
        argv += ["--workspace", workspace]
    return join_argv([*argv, "--request-json", json.dumps(request, separators=(",", ":"))])


def collect_registry_check_findings(vault_root: str | Path) -> list[dict]:
    """Return launcher-safe linked-workspace registry findings for one vault.

    The file's condition is one finding; each row whose manifest positively
    disagrees, cannot be verified or cannot be reached is one finding of its
    own, keyed by ``.brain/local/workspaces.json#<key>`` so it is claimed,
    dismissed and reopened alone. The repairable codes and the two codes for a
    file whose rows cannot be read are warnings; the rest are ``info``. The
    two unreadable-file codes declare the structured reason as evidence, so a
    file that breaks differently reopens a dismissal (DD-086).
    """
    import workspace_registry
    from _bootstrap.workspace_binding import LINK_DISAGREEMENT, LinkVerdict, describe_link, link_fields

    vault_root = Path(vault_root)
    registry = inspect_registry(vault_root)
    if not registry.present:
        return []
    relative = str(registry.path.relative_to(vault_root))
    if registry.condition in {RegistryCondition.UNREADABLE, RegistryCondition.UNPARSEABLE}:
        # No automatic repair: rewriting a file whose rows cannot be read would lose every row.
        code = ("workspace_registry_unreadable" if registry.condition is RegistryCondition.UNREADABLE
                else "workspace_registry_unparseable")
        return [{"check": "workspace_registry", "code": code, "severity": "warning",
                 "file": relative, "evidence": {"reason": registry.reason}, "message": registry.message}]
    findings: list[dict] = []
    if not registry.healthy:
        findings.append(attach_repair_guidance({
            "check": "workspace_registry", "code": "workspace_registry_malformed", "severity": "warning",
            "file": relative, "message": registry.message,
        }, vault_root, "registry"))
    verification = workspace_registry.verify_rows(vault_root, registry.rows)
    if verification.reason is workspace_registry.Unverified.VAULT_REGISTRY_UNREADABLE:
        remedy = "Restore read access to this machine's vault registry, then run vault.check again."
        findings.append({
            "check": "workspace_registry", "code": "workspace_links_unverified", "severity": "info",
            "file": relative, "evidence": {"reason": verification.reason.value},
            "message": f"{verification.reason.describe()} {remedy}", "fix": remedy,
        })

    def unregister(key: str) -> str:
        return _workspace_command(vault_root, "unregister", {"key": key})

    for row in verification.rows:
        verdict = row.classification.verdict
        if verdict is LinkVerdict.MATCHES:
            continue
        file = f"{relative}#{row.key}"
        why = describe_link(row.classification)
        if verdict in LINK_DISAGREEMENT:
            manifest_brain, manifest_key = link_fields(row.classification.state.data)
            findings.append(attach_repair_guidance({
                "check": "workspace_registry", "code": "workspace_link_disagreement", "severity": "warning",
                "file": file, "message": (f"Linked workspace {row.key} at {row.path} disagrees with its manifest: "
                                          f"{why}. The repair removes the row."),
                "evidence": {"key": row.key, "path": row.path,
                             "manifest_brain": manifest_brain, "manifest_key": manifest_key},
            }, vault_root, "registry"))
            continue
        if verdict is LinkVerdict.UNREACHABLE:
            code = "workspace_folder_unreachable"
            evidence = {"key": row.key, "path": row.path}
            remedy = (f"Reconnect the folder, run `{_workspace_command(vault_root, 'setup', {})}` from its new "
                      f"location, run `{unregister(row.key)}` to forget it, or dismiss this while it is away.")
            message = f"Linked workspace {row.key} is not reachable at {row.path}. {remedy}"
        else:
            code = "workspace_link_unverifiable"
            evidence = {"key": row.key, "path": row.path, "reason": verdict.value}
            if verdict is LinkVerdict.VAULT_ROOT:
                remedy = f"Run `{unregister(row.key)}` to forget the row, or dismiss this."
            else:
                setup = _workspace_command(vault_root, "setup", {}, workspace=row.path)
                remedy = f"Run `{setup}` to link it again, `{unregister(row.key)}` to forget it, or dismiss this."
            message = f"Linked workspace {row.key} at {row.path} cannot be verified: {why}. {remedy}"
        findings.append({"check": "workspace_registry", "code": code, "severity": "info",
                         "file": file, "evidence": evidence, "message": message, "fix": remedy})
    return findings


def collect_runtime_check_findings(vault_root: str | Path) -> list[dict]:
    """Return launcher-safe managed-runtime findings for one vault."""
    vault_root = Path(vault_root)
    findings: list[dict] = []
    if not local_mcp_state_present(vault_root):
        return findings

    runtime = inspect_runtime(vault_root)
    if not runtime["healthy"]:
        for issue in runtime["issues"]:
            finding = {
                "check": f"runtime:{issue}",
                "severity": "warning",
                "file": None,
                "message": runtime.get("message") or _runtime_issue_message(issue),
            }
            findings.append(attach_repair_guidance(finding, vault_root, "runtime"))
    return findings


def collect_mcp_check_findings(vault_root: str | Path) -> list[dict]:
    """Return launcher-safe Brain MCP registration findings for one vault."""
    vault_root = Path(vault_root)
    findings: list[dict] = []
    if not local_mcp_state_present(vault_root):
        return findings

    mcp = inspect_mcp(vault_root)
    specifically_reported_clients: set[str] = set()

    if mcp["claude"]["command"] is not None and not mcp["claude"]["command_ok"]:
        specifically_reported_clients.add("claude")
        findings.append(attach_repair_guidance({
            "check": "mcp_registration:claude_python_mismatch",
            "severity": "warning",
            "file": CLAUDE_PROJECT_CONFIG_FILE,
            "message": "Claude Brain MCP config does not point at the canonical managed Python.",
        }, vault_root, "mcp"))

    if mcp["codex"]["command"] is not None and not mcp["codex"]["command_ok"]:
        specifically_reported_clients.add("codex")
        findings.append(attach_repair_guidance({
            "check": "mcp_registration:codex_python_mismatch",
            "severity": "warning",
            "file": CODEX_CONFIG_REL,
            "message": "Codex Brain MCP config does not point at the canonical managed Python.",
        }, vault_root, "mcp"))

    if mcp["claude"]["present"]:
        hook_state = mcp["claude"].get("hook_state", {})
        if not hook_state.get("exact"):
            specifically_reported_clients.add("claude")
            findings.append(attach_repair_guidance({
                "check": "mcp_registration:claude_session_hook_missing",
                "severity": "warning",
                "file": CLAUDE_LOCAL_SETTINGS_FILE,
                "message": "Claude SessionStart hook for session.start is missing or does not match the canonical command.",
            }, vault_root, "mcp"))
        elif hook_state.get("stale_count", 0) or hook_state.get("duplicate_count", 0):
            specifically_reported_clients.add("claude")
            findings.append(attach_repair_guidance({
                "check": "mcp_registration:claude_session_hook_drift",
                "severity": "warning",
                "file": CLAUDE_LOCAL_SETTINGS_FILE,
                "message": "Claude SessionStart contains stale or duplicate Brain hooks.",
            }, vault_root, "mcp"))

    if mcp["grok"]["command"] is not None and not mcp["grok"]["command_ok"]:
        specifically_reported_clients.add("grok")
        findings.append(
            attach_repair_guidance(
                {
                    "check": "mcp_registration:grok_python_mismatch",
                    "severity": "warning",
                    "file": GROK_CONFIG_REL,
                    "message": "Grok Brain MCP config does not point at the canonical managed Python.",
                },
                vault_root,
                "mcp",
            )
        )

    client_labels = {"claude": "Claude", "codex": "Codex", "grok": "Grok"}
    for client in ("claude", "codex", "grok"):
        if (
            mcp[client]["present"]
            and not mcp[client]["healthy"]
            and client not in specifically_reported_clients
        ):
            finding = {
                "check": "mcp_registration",
                "severity": "warning",
                "file": None,
                "message": (
                    mcp[client]["bootstrap_message"]
                    if client == "claude" and mcp[client].get("bootstrap_reason") == "unreadable"
                    else f"{client_labels[client]} Brain MCP project registration state is drifted or incomplete."
                ),
            }
            findings.append(attach_repair_guidance(finding, vault_root, "mcp"))
    return findings


def collect_mcp_legacy_vault_root_findings(vault_root: str | Path) -> list[dict]:
    """Return informational findings for Brain MCP registrations carrying a legacy BRAIN_VAULT_ROOT.

    BRAIN_VAULT_ROOT is still honoured as a rung-3 override but is no longer written by new
    registrations.  Its presence marks a legacy config — informational, not an error or warning.
    """
    vault_root = Path(vault_root)
    findings: list[dict] = []
    if not local_mcp_state_present(vault_root):
        return findings

    clients: list[tuple[str, Path]] = [
        ("claude", vault_root / CLAUDE_PROJECT_CONFIG_FILE),
        ("codex", vault_root / CODEX_CONFIG_REL),
        ("grok", vault_root / GROK_CONFIG_REL),
    ]
    for client, config_path in clients:
        if client == "claude":
            server = _read_claude_project_server(config_path)
        else:
            server = read_toml_server_config(config_path)
        if server is None:
            continue
        legacy_root = configured_vault_root(server)
        if legacy_root is None:
            continue
        try:
            rel_config = str(config_path.relative_to(vault_root))
        except ValueError:
            rel_config = str(config_path)
        finding = {
            "check": "mcp_legacy_vault_root",
            "severity": "info",
            "file": rel_config,
            "message": (
                f"Brain MCP registration carries a legacy BRAIN_VAULT_ROOT env var "
                f"(pointing to {legacy_root}). "
                "This override is still honoured but is no longer written by new registrations. "
                "It can be left as-is or removed by repairing this vault's MCP registration."
            ),
        }
        findings.append(attach_repair_guidance(finding, vault_root, "mcp"))
    return findings


def collect_temporaries_check_findings(vault_root: str | Path, *, now=None) -> list[dict]:
    """Return one finding per stranded atomic-write temporary under .brain/local."""
    from datetime import datetime, timezone

    from _bootstrap.stranded_temporaries import find_stranded_temporaries

    vault_root = Path(vault_root)
    findings: list[dict] = []
    for rel_path in find_stranded_temporaries(vault_root, now or datetime.now(timezone.utc)):
        finding = {
            "check": "temporaries",
            "severity": "info",
            "file": rel_path,
            "message": "Stranded atomic-write temporary file; a Brain write did not complete.",
        }
        findings.append(attach_repair_guidance(finding, vault_root, "temporaries"))
    return findings


def collect_bootstrap_check_findings(vault_root: str | Path) -> list[dict]:
    """Return launcher-safe repair-oriented compliance findings."""
    findings = collect_registry_check_findings(vault_root)
    findings.extend(collect_runtime_check_findings(vault_root))
    findings.extend(collect_mcp_check_findings(vault_root))
    findings.extend(collect_mcp_legacy_vault_root_findings(vault_root))
    findings.extend(collect_temporaries_check_findings(vault_root))
    return findings
