#!/usr/bin/env python3
"""Packageful repair logic that runs inside the managed vault runtime."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import build_index
import compile_router
import init
import workspace_registry
from _common import iter_artefact_paths
from _repair_common import attach_repair_guidance, iso_now, make_result_envelope, step as _step


def _derive_status(steps: list[dict], dry_run: bool) -> str:
    step_statuses = {step["status"] for step in steps}
    if "error" in step_statuses:
        success_like = {"changed", "noop", "planned"}
        return "partial" if any(s["status"] in success_like for s in steps) else "error"
    if dry_run and "planned" in step_statuses:
        return "planned"
    if "changed" in step_statuses:
        return "ok"
    return "noop"


def _finalise_result(
    scope: str,
    vault_root: Path,
    dry_run: bool,
    steps: list[dict],
    notes: list[str] | None = None,
) -> dict:
    return make_result_envelope(
        scope=scope,
        vault_root=vault_root,
        dry_run=dry_run,
        managed_python=os.path.realpath(sys.executable),
        steps=steps,
        status=_derive_status(steps, dry_run),
        notes=notes,
    )


def _router_is_stale(vault_root: Path) -> tuple[bool, str]:
    router_path = vault_root / compile_router.OUTPUT_PATH
    if not router_path.is_file():
        return True, "missing"

    try:
        data = json.loads(router_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True, "invalid-json"
    if not isinstance(data, dict):
        return True, "invalid-payload"

    meta = data.get("meta", {})
    compiled_at = meta.get("compiled_at")
    sources = meta.get("sources", {})
    if not compiled_at or not isinstance(sources, dict) or not sources:
        return True, "missing-metadata"

    try:
        compiled_ts = datetime.fromisoformat(compiled_at).timestamp()
    except (ValueError, TypeError):
        return True, "invalid-timestamp"

    artefacts = data.get("artefacts", [])
    artefact_index = data.get("artefact_index", {})
    artefact_index_sources = meta.get("artefact_index_sources")
    if artefact_index and artefact_index_sources is None:
        return True, "missing-artefact-index-sources"
    if artefact_index_sources is not None and not isinstance(artefact_index_sources, list):
        return True, "invalid-artefact-index-sources"
    artefact_index_source_paths = set(artefact_index_sources or [])

    expected_index_source_count = meta.get("artefact_index_source_count")
    if expected_index_source_count is not None:
        current_index_source_count = compile_router.count_living_artefact_index_entries(
            str(vault_root), artefacts
        )
        if current_index_source_count != expected_index_source_count:
            return True, "artefact-index-count-drift"

    for key, fs_count in compile_router.resource_counts(str(vault_root)).items():
        if fs_count != len(data.get(key, [])):
            return True, f"{key}-count-drift"

    for rel_path, expected_hash in sources.items():
        abs_path = vault_root / rel_path
        if rel_path in artefact_index_source_paths:
            try:
                current_hash = compile_router.hash_living_artefact_source(str(abs_path))
            except (OSError, UnicodeDecodeError):
                return True, "artefact-index-source-unreadable"
            if current_hash != expected_hash:
                return True, "artefact-index-source-drift"
            continue

        try:
            if os.path.getmtime(abs_path) > compiled_ts:
                return True, "source-newer-than-router"
        except OSError:
            return True, "missing-source"

    return False, "fresh"


def _index_is_stale(vault_root: Path) -> tuple[bool, str]:
    index_path = vault_root / build_index.OUTPUT_PATH
    if not index_path.is_file():
        return True, "missing"

    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True, "invalid-json"
    if not isinstance(data, dict):
        return True, "invalid-payload"

    if data.get("meta", {}).get("index_version") != build_index.INDEX_VERSION:
        return True, "version-drift"

    built_at = data.get("meta", {}).get("built_at")
    if not built_at:
        return True, "missing-built-at"
    try:
        threshold = datetime.fromisoformat(built_at).timestamp()
    except (ValueError, TypeError):
        return True, "invalid-built-at"

    expected_count = data.get("meta", {}).get("document_count", 0)
    all_types = compile_router.scan_living_types(str(vault_root)) + compile_router.scan_temporal_types(str(vault_root))
    count = 0
    for type_info in all_types:
        for rel_path in iter_artefact_paths(str(vault_root), type_info):
            count += 1
            if count > expected_count:
                return True, "document-count-drift"
            try:
                if os.path.getmtime(vault_root / rel_path) > threshold:
                    return True, "document-newer-than-index"
            except OSError:
                continue
    if count != expected_count:
        return True, "document-count-drift"

    return False, "fresh"


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
    needs_normalisation = False
    for slug, entry in workspaces.items():
        if isinstance(entry, str):
            canonical[slug] = {"path": entry}
            needs_normalisation = True
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


def _backup_path(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return path.with_name(f"{path.name}.{stamp}.bak")


def _expected_project_server_config(vault_root: Path) -> dict:
    venv_python = str((vault_root / init.VENV_PYTHON_REL).resolve())
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


def inspect_mcp(vault_root: Path) -> dict:
    """Inspect current-vault project MCP state without mutating user scope."""
    server_config = _expected_project_server_config(vault_root)
    claude_config_path = vault_root / init.CLAUDE_PROJECT_CONFIG_FILE
    codex_config_path = vault_root / init.CODEX_CONFIG_REL
    claude_settings_path = vault_root / init.CLAUDE_LOCAL_SETTINGS_FILE
    claude_md_path = vault_root / init.CLAUDE_MD_FILE
    expected_hook = init.build_session_hook_command(vault_root, vault_root)
    expected_bootstrap = init.bootstrap_line_for_target(vault_root)

    claude_payload, _ = _read_json_safe(claude_config_path)
    claude_servers = claude_payload.get("mcpServers", {}) if isinstance(claude_payload, dict) else {}
    claude_config_ok = (
        isinstance(claude_servers, dict)
        and claude_servers.get(init.BRAIN_SERVER_NAME) == server_config
    )
    codex_config_ok = init.read_codex_server_config(codex_config_path) == server_config

    try:
        claude_md_text = claude_md_path.read_text(encoding="utf-8")
    except OSError:
        claude_md_text = ""
    bootstrap_ok = expected_bootstrap in claude_md_text

    settings, _ = _read_json_safe(claude_settings_path)
    hook_ok = _has_hook_entry(settings or {}, expected_hook)

    records = init.matching_records(
        vault_root,
        ["claude", "codex"],
        "project",
        vault_root,
    )
    claude_record_ok = any(
        _record_matches(record, client="claude", config_path=claude_config_path, server_config=server_config)
        for record in records
    )
    codex_record_ok = any(
        _record_matches(record, client="codex", config_path=codex_config_path, server_config=server_config)
        for record in records
    )

    local_state_present = any(
        path.exists()
        for path in (
            claude_config_path,
            codex_config_path,
            claude_settings_path,
            claude_md_path,
            vault_root / init.INIT_STATE_REL,
        )
    )

    return {
        "present": local_state_present,
        "server_config": server_config,
        "claude": {
            "config_path": claude_config_path,
            "healthy": claude_config_ok and bootstrap_ok and hook_ok and claude_record_ok,
            "config_ok": claude_config_ok,
            "bootstrap_ok": bootstrap_ok,
            "hook_ok": hook_ok,
            "record_ok": claude_record_ok,
        },
        "codex": {
            "config_path": codex_config_path,
            "healthy": codex_config_ok and codex_record_ok,
            "config_ok": codex_config_ok,
            "record_ok": codex_record_ok,
        },
    }


def _record_claude_direct(vault_root: Path, server_config: dict) -> None:
    config_path = vault_root / init.CLAUDE_PROJECT_CONFIG_FILE
    init.write_project_mcp_json(server_config, vault_root)
    bootstrap_path = init.ensure_claude_md(vault_root)
    hook_path = init.ensure_session_start_hook(vault_root, vault_root)
    record = {
        "client": "claude",
        "scope": "project",
        "target_path": str(vault_root),
        "config_path": str(config_path),
        "server_name": init.BRAIN_SERVER_NAME,
        "server_config": server_config,
        "bootstrap_path": str(bootstrap_path),
        "bootstrap_line": init.bootstrap_line_for_target(vault_root),
        "hook_path": str(hook_path),
        "hook_command": init.build_session_hook_command(vault_root, vault_root),
        "method": f"{config_path} (direct repair)",
    }
    init.record_init_target(vault_root, record)


def _repair_claude(vault_root: Path, server_config: dict, claude_state: dict, dry_run: bool) -> dict:
    if claude_state["healthy"]:
        return _step("claude_project", "noop", "Claude project MCP state is already healthy.")
    if dry_run:
        return _step("claude_project", "planned", "Would repair .mcp.json, CLAUDE.md, session hook, and init-state record.")
    _record_claude_direct(vault_root, server_config)
    return _step("claude_project", "changed", "Repaired Claude project MCP config, bootstrap, hook, and init-state record.")


def _repair_codex(vault_root: Path, server_config: dict, codex_state: dict, dry_run: bool) -> dict:
    if codex_state["healthy"]:
        return _step("codex_project", "noop", "Codex project MCP state is already healthy.")
    if dry_run:
        return _step("codex_project", "planned", "Would repair .codex/config.toml and the init-state record.")
    record = init.register_codex(server_config, "project", vault_root)
    init.record_init_target(vault_root, record)
    return _step("codex_project", "changed", "Repaired Codex project MCP config and init-state record.")


def repair_mcp(vault_root: Path, dry_run: bool, bootstrap_steps: list[dict] | None = None) -> dict:
    steps = list(bootstrap_steps or [])
    state = inspect_mcp(vault_root)
    server_config = state["server_config"]

    try:
        steps.append(_repair_claude(vault_root, server_config, state["claude"], dry_run))
    except Exception as exc:
        steps.append(_step("claude_project", "error", str(exc)))
    try:
        steps.append(_repair_codex(vault_root, server_config, state["codex"], dry_run))
    except Exception as exc:
        steps.append(_step("codex_project", "error", str(exc)))

    notes = init.claude_project_followup_notes(vault_root)
    return _finalise_result("mcp", vault_root, dry_run, steps, notes=notes)


def repair_router(vault_root: Path, dry_run: bool, bootstrap_steps: list[dict] | None = None) -> dict:
    steps = list(bootstrap_steps or [])
    stale, reason = _router_is_stale(vault_root)
    if not stale:
        steps.append(_step("router", "noop", "Compiled router is already fresh."))
        return _finalise_result("router", vault_root, dry_run, steps)
    if dry_run:
        steps.append(_step("router", "planned", f"Would rebuild the compiled router ({reason})."))
        return _finalise_result("router", vault_root, dry_run, steps)

    compiled = compile_router.compile(str(vault_root))
    output_path = vault_root / compile_router.OUTPUT_PATH
    compile_router.safe_write(
        str(output_path),
        json.dumps(compiled, indent=2, ensure_ascii=False) + "\n",
        bounds=str(vault_root),
    )
    steps.append(_step("router", "changed", f"Rebuilt the compiled router ({reason})."))
    try:
        model = compile_router.session.build_session_model(compiled, str(vault_root))
        compile_router.session.persist_session_markdown(model, str(vault_root))
    except Exception as exc:
        steps.append(_step(
            "router_session",
            "error",
            f"Router rebuilt but session markdown refresh failed: {exc}",
        ))
    return _finalise_result("router", vault_root, dry_run, steps)


def repair_index(vault_root: Path, dry_run: bool, bootstrap_steps: list[dict] | None = None) -> dict:
    steps = list(bootstrap_steps or [])
    stale, reason = _index_is_stale(vault_root)
    if not stale:
        steps.append(_step("index", "noop", "Retrieval index is already fresh."))
        return _finalise_result("index", vault_root, dry_run, steps)
    if dry_run:
        steps.append(_step("index", "planned", f"Would rebuild the retrieval index ({reason})."))
        return _finalise_result("index", vault_root, dry_run, steps)

    index = build_index.build_index(str(vault_root))
    output_path = vault_root / build_index.OUTPUT_PATH
    build_index.safe_write(
        str(output_path),
        json.dumps(index, indent=2, ensure_ascii=False) + "\n",
        bounds=str(vault_root),
    )
    steps.append(_step("index", "changed", f"Rebuilt the retrieval index ({reason})."))
    return _finalise_result("index", vault_root, dry_run, steps)


def repair_registry(vault_root: Path, dry_run: bool, bootstrap_steps: list[dict] | None = None) -> dict:
    steps = list(bootstrap_steps or [])
    state = inspect_registry(vault_root)
    if state["healthy"]:
        steps.append(_step("registry", "noop", state["message"]))
        return _finalise_result("registry", vault_root, dry_run, steps)
    if dry_run:
        steps.append(_step("registry", "planned", f"Would repair {state['path']} ({state['message']})."))
        return _finalise_result("registry", vault_root, dry_run, steps)

    path = state["path"]
    if state.get("backup_required") and path.is_file():
        backup_path = _backup_path(path)
        path.rename(backup_path)
        workspace_registry.save_registry(str(vault_root), state["canonical"]["workspaces"])
        steps.append(
            _step(
                "registry",
                "changed",
                f"Repaired {path} and preserved the malformed copy at {backup_path}.",
            )
        )
        return _finalise_result("registry", vault_root, dry_run, steps)

    workspace_registry.save_registry(str(vault_root), state["canonical"]["workspaces"])
    steps.append(_step("registry", "changed", f"Normalised {path}."))
    return _finalise_result("registry", vault_root, dry_run, steps)


def run_scope(
    scope: str,
    vault_root: Path,
    *,
    dry_run: bool = False,
    bootstrap_steps: list[dict] | None = None,
) -> dict:
    if scope == "mcp":
        return repair_mcp(vault_root, dry_run, bootstrap_steps)
    if scope == "router":
        return repair_router(vault_root, dry_run, bootstrap_steps)
    if scope == "index":
        return repair_index(vault_root, dry_run, bootstrap_steps)
    if scope == "registry":
        return repair_registry(vault_root, dry_run, bootstrap_steps)
    raise ValueError(f"Unknown repair scope: {scope}")


def _local_mcp_state_present(vault_root: Path) -> bool:
    return any(
        (vault_root / rel).exists()
        for rel in (
            init.CLAUDE_PROJECT_CONFIG_FILE,
            init.CODEX_CONFIG_REL,
            init.CLAUDE_LOCAL_SETTINGS_FILE,
            init.CLAUDE_MD_FILE,
            init.INIT_STATE_REL,
        )
    )


def collect_check_findings(vault_root: str | Path) -> list[dict]:
    """Return additive repair-oriented compliance findings."""
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

    if _local_mcp_state_present(vault_root):
        mcp = inspect_mcp(vault_root)
        if not mcp["claude"]["healthy"] or not mcp["codex"]["healthy"]:
            finding = {
                "check": "mcp_registration",
                "severity": "warning",
                "file": None,
                "message": "Current-vault MCP registration state is drifted or incomplete.",
            }
            findings.append(attach_repair_guidance(finding, vault_root, "mcp"))

    return findings
