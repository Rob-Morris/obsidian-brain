#!/usr/bin/env python3
"""Packageful repair logic that runs inside the managed vault runtime."""

from __future__ import annotations

import json
import importlib.util
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import compile_router
import init
import workspace_registry
import _search.index as search_index
import _search.paths as search_paths
from _bootstrap.diagnostics import (
    ISSUE_MANAGED_RUNTIME_DEPENDENCIES_MISSING,
    ISSUE_RUNTIME_MISSING,
    ISSUE_RUNTIME_UNUSABLE,
    inspect_mcp as _bootstrap_inspect_mcp,
    inspect_registry as _bootstrap_inspect_registry,
    inspect_runtime as _bootstrap_inspect_runtime,
    local_mcp_state_present as _bootstrap_local_mcp_state_present,
)
import _semantic.config as _semantic_config
import _semantic.model as _semantic_model
import _semantic.provision as _semantic_provision
import _semantic.runtime as _semantic_runtime
from _lifecycle.frontmatter_repairs import normalize_duplicate_frontmatter_documents
from _common import iter_artefact_paths, resolve_vault_venv_python
from _lifecycle_common import (
    iso_now,
    make_result_envelope,
    probe_python,
    required_modules_for_scope,
    step as _step,
)
from _repair_common import attach_repair_guidance

ISSUE_CONFIG_LOAD_ERROR = "config-load-error"
ISSUE_UNSUPPORTED_PLATFORM = "unsupported-platform"
ISSUE_RUNTIME_NOT_PROVISIONED = "runtime-not-provisioned"
ISSUE_SEMANTIC_RUNTIME_DEPENDENCIES_MISSING = "runtime-dependencies-missing"
ISSUE_SEMANTIC_MODEL_MANIFEST_MISSING = "semantic-model-manifest-missing"
ISSUE_SEMANTIC_MODEL_PATH_MISSING = "semantic-model-path-missing"
ISSUE_SEMANTIC_MODEL_REVISION_MISMATCH = "semantic-model-revision-mismatch"
ISSUE_SEMANTIC_MODEL_LOAD_ERROR = "semantic-model-load-error"
ISSUE_SEMANTIC_SIDECARS_MISSING = "semantic-sidecars-missing"
ISSUE_SEMANTIC_SIDECARS_OUTDATED = "semantic-sidecars-outdated"
def _runtime_issue_message(issue: str) -> str:
    messages = {
        ISSUE_RUNTIME_MISSING: "Central managed runtime is missing for this vault.",
        ISSUE_RUNTIME_UNUSABLE: "Central managed runtime is present but unusable.",
        ISSUE_MANAGED_RUNTIME_DEPENDENCIES_MISSING: "Central managed runtime is present but missing required baseline packages.",
    }
    return messages.get(issue, "Central managed runtime is unhealthy.")


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
        checked_at=iso_now(),
        notes=notes,
    )


def inspect_runtime(vault_root: Path) -> dict:
    """Inspect the central managed runtime for this vault."""
    return _bootstrap_inspect_runtime(vault_root)


def _semantic_message(configured: bool, supported: bool, unsupported_message: str | None, issues: list[str]) -> str:
    if not configured:
        return "Semantic retrieval is not configured on for this vault."
    if not supported:
        return unsupported_message or "Semantic runtime is unsupported on this platform."

    issue_set = set(issues)
    if ISSUE_SEMANTIC_MODEL_LOAD_ERROR in issue_set:
        return "Semantic retrieval is configured on, but the provisioned semantic model failed to load locally."
    if ISSUE_SEMANTIC_MODEL_REVISION_MISMATCH in issue_set:
        return "Semantic retrieval is configured on, but the provisioned semantic model revision no longer matches the shipped pin."
    if ISSUE_SEMANTIC_MODEL_PATH_MISSING in issue_set:
        return "Semantic retrieval is configured on, but the provisioned semantic model snapshot is missing from disk."
    if ISSUE_SEMANTIC_MODEL_MANIFEST_MISSING in issue_set:
        return "Semantic retrieval is configured on, but the local semantic model manifest is missing or unreadable."
    if ISSUE_RUNTIME_NOT_PROVISIONED in issue_set and ISSUE_SEMANTIC_RUNTIME_DEPENDENCIES_MISSING in issue_set:
        return "Semantic retrieval is configured on, but the local semantic runtime has not been provisioned."
    if ISSUE_SEMANTIC_RUNTIME_DEPENDENCIES_MISSING in issue_set:
        return "Semantic retrieval is configured on, but the local semantic runtime dependencies are unavailable."
    if ISSUE_RUNTIME_NOT_PROVISIONED in issue_set:
        return "Semantic retrieval is configured on, but the local semantic runtime marker is not set."
    if ISSUE_SEMANTIC_SIDECARS_OUTDATED in issue_set:
        return "Semantic retrieval is configured on, but the embeddings sidecars were built against a different semantic model revision."
    if ISSUE_SEMANTIC_SIDECARS_MISSING in issue_set:
        return "Semantic retrieval is configured on, but the embeddings sidecars are missing."
    return "Semantic retrieval runtime is healthy."


def _semantic_issue_message(issue: str) -> str:
    """Return a specific user-facing message for one semantic health issue."""
    messages = {
        ISSUE_CONFIG_LOAD_ERROR: "Semantic config could not be loaded for this vault.",
        ISSUE_UNSUPPORTED_PLATFORM: "Semantic runtime is unsupported on this platform.",
        ISSUE_RUNTIME_NOT_PROVISIONED: "Semantic retrieval is configured on, but the local semantic runtime marker is not set.",
        ISSUE_SEMANTIC_RUNTIME_DEPENDENCIES_MISSING: "Semantic retrieval is configured on, but the local semantic runtime dependencies are unavailable.",
        ISSUE_SEMANTIC_MODEL_MANIFEST_MISSING: "Semantic retrieval is configured on, but the local semantic model manifest is missing or unreadable.",
        ISSUE_SEMANTIC_MODEL_PATH_MISSING: "Semantic retrieval is configured on, but the provisioned semantic model snapshot is missing from disk.",
        ISSUE_SEMANTIC_MODEL_REVISION_MISMATCH: "Semantic retrieval is configured on, but the provisioned semantic model revision no longer matches the shipped pin.",
        ISSUE_SEMANTIC_MODEL_LOAD_ERROR: "Semantic retrieval is configured on, but the provisioned semantic model failed to load locally.",
        ISSUE_SEMANTIC_SIDECARS_MISSING: "Semantic retrieval is configured on, but the embeddings sidecars are missing.",
        ISSUE_SEMANTIC_SIDECARS_OUTDATED: "Semantic retrieval is configured on, but the embeddings sidecars were built against a different semantic model revision.",
    }
    return messages.get(issue, "Semantic retrieval runtime is unhealthy.")


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
    index_path = vault_root / search_paths.OUTPUT_PATH
    if not index_path.is_file():
        return True, "missing"

    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True, "invalid-json"
    if not isinstance(data, dict):
        return True, "invalid-payload"

    if data.get("meta", {}).get("index_version") != search_paths.INDEX_VERSION:
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
    return _bootstrap_inspect_registry(vault_root)


def _backup_path(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return path.with_name(f"{path.name}.{stamp}.bak")


def inspect_mcp(vault_root: Path) -> dict:
    """Inspect current-vault project MCP state without mutating user scope."""
    return _bootstrap_inspect_mcp(vault_root)


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
    if not claude_state["present"]:
        return _step("claude_project", "noop", "Claude project MCP is not installed for this vault.")
    if claude_state["healthy"]:
        return _step("claude_project", "noop", "Claude project MCP state is already healthy.")
    if dry_run:
        return _step("claude_project", "planned", "Would repair .mcp.json, CLAUDE.md, session hook, and init-state record.")
    _record_claude_direct(vault_root, server_config)
    return _step("claude_project", "changed", "Repaired Claude project MCP config, bootstrap, hook, and init-state record.")


def _repair_codex(vault_root: Path, server_config: dict, codex_state: dict, dry_run: bool) -> dict:
    if not codex_state["present"]:
        return _step("codex_project", "noop", "Codex project MCP is not installed for this vault.")
    if codex_state["healthy"]:
        return _step("codex_project", "noop", "Codex project MCP state is already healthy.")
    if dry_run:
        return _step("codex_project", "planned", "Would repair .codex/config.toml and the init-state record.")
    record = init.register_codex(server_config, "project", vault_root)
    init.record_init_target(vault_root, record)
    return _step("codex_project", "changed", "Repaired Codex project MCP config and init-state record.")


def repair_mcp(vault_root: Path, dry_run: bool, bootstrap_steps: list[dict] | None = None) -> dict:
    steps = list(bootstrap_steps or [])
    # `repair.py main()` already repaired/bootstraped the managed runtime before
    # re-exec. Keep this guard for direct library/test callers that bypass the
    # bootstrap layer entirely.
    runtime_state = inspect_runtime(vault_root)
    if not runtime_state["healthy"]:
        steps.append(_step("runtime", "error", runtime_state["message"]))
        return _finalise_result("mcp", vault_root, dry_run, steps)

    state = inspect_mcp(vault_root)
    server_config = state["server_config"]

    try:
        steps.append(_repair_claude(vault_root, server_config, state["claude"], dry_run))
    except (OSError, ValueError) as exc:
        steps.append(_step("claude_project", "error", str(exc)))
    try:
        steps.append(_repair_codex(vault_root, server_config, state["codex"], dry_run))
    except (OSError, ValueError) as exc:
        steps.append(_step("codex_project", "error", str(exc)))

    notes = init.claude_project_followup_notes(vault_root) if state["claude"]["present"] else []
    return _finalise_result("mcp", vault_root, dry_run, steps, notes=notes)


def verify_runtime_post_bootstrap(vault_root: Path, dry_run: bool, bootstrap_steps: list[dict] | None = None) -> dict:
    """Verify the runtime scope after bootstrap has repaired/re-synced it."""
    steps = list(bootstrap_steps or [])
    state = inspect_runtime(vault_root)
    if not state["healthy"]:
        steps.append(_step("runtime", "error", state["message"]))
        return _finalise_result("runtime", vault_root, dry_run, steps)

    steps.append(_step("runtime", "noop", state["message"]))
    return _finalise_result("runtime", vault_root, dry_run, steps)


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
    compile_router.persist_compiled_router(str(vault_root), compiled)
    _semantic_runtime.clear_embeddings_outputs(str(vault_root))
    steps.append(_step("router", "changed", f"Rebuilt the compiled router ({reason})."))
    try:
        compile_router.refresh_session_markdown(str(vault_root), compiled)
    except (OSError, ValueError) as exc:
        steps.append(_step(
            "router_session",
            "error",
            f"Router rebuilt but session markdown refresh failed: {exc}",
        ))
    return _finalise_result("router", vault_root, dry_run, steps)


def repair_lexical(vault_root: Path, dry_run: bool, bootstrap_steps: list[dict] | None = None) -> dict:
    steps = list(bootstrap_steps or [])
    stale, reason = _index_is_stale(vault_root)
    if not stale:
        steps.append(_step("lexical", "noop", "Lexical retrieval index is already fresh."))
        return _finalise_result("lexical", vault_root, dry_run, steps)
    if dry_run:
        steps.append(_step("lexical", "planned", f"Would rebuild the lexical retrieval index ({reason})."))
        return _finalise_result("lexical", vault_root, dry_run, steps)

    build_result = search_index.build_index(str(vault_root))
    search_index.persist_retrieval_index(str(vault_root), build_result.index)
    steps.append(_step("lexical", "changed", f"Rebuilt the lexical retrieval index ({reason})."))
    return _finalise_result("lexical", vault_root, dry_run, steps)


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


def repair_frontmatter(vault_root: Path, dry_run: bool, bootstrap_steps: list[dict] | None = None) -> dict:
    steps = list(bootstrap_steps or [])
    result = normalize_duplicate_frontmatter_documents(vault_root, dry_run=dry_run)
    if result["updated"] == 0:
        steps.append(
            _step(
                "frontmatter",
                "noop",
                "No duplicate frontmatter blocks were found in vault artefacts.",
            )
        )
        return _finalise_result("frontmatter", vault_root, dry_run, steps)

    status = "planned" if dry_run else "changed"
    verb = "Would normalise" if dry_run else "Normalised"
    steps.append(
        _step(
            "frontmatter",
            status,
            f"{verb} duplicate frontmatter in {result['updated']} artefact(s).",
        )
    )
    notes = [f"{verb}: {rel_path}" for rel_path in result["files"]]
    return _finalise_result("frontmatter", vault_root, dry_run, steps, notes=notes)


def inspect_semantic(vault_root: Path) -> dict:
    """Inspect semantic config/runtime health for this vault."""
    try:
        cfg = _semantic_config.load_config_checked(vault_root)
    except _semantic_config.SemanticConfigLoadError as exc:
        return {
            "configured": False,
            "healthy": False,
            "message": str(exc),
            "issues": [ISSUE_CONFIG_LOAD_ERROR],
            "retrieval_enabled": False,
            "processing_enabled": False,
            "marker": False,
            "dependencies_ok": False,
            "sidecars_present": False,
            "supported": True,
        }

    retrieval_enabled = _semantic_config.semantic_retrieval_enabled(vault_root, config=cfg)
    processing_enabled = _semantic_config.semantic_processing_enabled(vault_root, config=cfg)
    configured = _semantic_config.is_semantic_intent_active(vault_root, config=cfg)
    marker = _semantic_config.semantic_engine_installed(vault_root, config=cfg)
    supported, unsupported_message = _semantic_provision.semantic_runtime_supported_platform()
    if not configured:
        return {
            "configured": False,
            "healthy": False,
            "message": _semantic_message(False, supported, unsupported_message, []),
            "issues": [],
            "retrieval_enabled": retrieval_enabled,
            "processing_enabled": processing_enabled,
            "marker": marker,
            "dependencies_ok": False,
            "sidecars_present": False,
            "supported": supported,
        }

    managed_probe = probe_python(
        str(resolve_vault_venv_python(vault_root)),
        modules=_semantic_provision.SEMANTIC_RUNTIME_MODULES,
    )
    dependencies_ok = bool(managed_probe.get("ok"))
    model_state = _semantic_model.inspect_model_state(vault_root)
    if dependencies_ok:
        model_state = _semantic_model.verify_local_model_load(model_state)
    sidecars_present, sidecars_outdated = _semantic_runtime.embeddings_sidecars_match_manifest(
        vault_root,
        model_state.manifest,
    )

    issues: list[str] = []
    if not supported:
        issues.append(ISSUE_UNSUPPORTED_PLATFORM)
    if not marker:
        issues.append(ISSUE_RUNTIME_NOT_PROVISIONED)
    if not dependencies_ok:
        issues.append(ISSUE_SEMANTIC_RUNTIME_DEPENDENCIES_MISSING)
    if model_state.load_error:
        issues.append(ISSUE_SEMANTIC_MODEL_LOAD_ERROR)
    elif model_state.manifest_missing:
        issues.append(ISSUE_SEMANTIC_MODEL_MANIFEST_MISSING)
    elif model_state.model_revision_mismatch:
        issues.append(ISSUE_SEMANTIC_MODEL_REVISION_MISMATCH)
    elif model_state.model_path_missing:
        issues.append(ISSUE_SEMANTIC_MODEL_PATH_MISSING)
    if not sidecars_present:
        issues.append(ISSUE_SEMANTIC_SIDECARS_MISSING)
    elif sidecars_outdated:
        issues.append(ISSUE_SEMANTIC_SIDECARS_OUTDATED)

    return {
        "configured": configured,
        "healthy": configured and not issues,
        "message": _semantic_message(configured, supported, unsupported_message, issues),
        "issues": issues,
        "retrieval_enabled": retrieval_enabled,
        "processing_enabled": processing_enabled,
        "marker": marker,
        "dependencies_ok": dependencies_ok,
        "sidecars_present": sidecars_present,
        "sidecars_outdated": sidecars_outdated,
        "model_state": model_state,
        "supported": supported,
    }


def repair_semantic(vault_root: Path, dry_run: bool, bootstrap_steps: list[dict] | None = None) -> dict:
    steps = list(bootstrap_steps or [])
    state = inspect_semantic(vault_root)

    if ISSUE_CONFIG_LOAD_ERROR in state["issues"]:
        steps.append(_step("semantic_config", "error", state["message"]))
        return _finalise_result("semantic", vault_root, dry_run, steps)

    if not state["configured"]:
        steps.append(
            _step(
                "semantic_config",
                "noop",
                "Semantic retrieval is not configured on for this vault. Use configure.py semantic --enable first.",
            )
        )
        return _finalise_result("semantic", vault_root, dry_run, steps)

    if not state["supported"]:
        steps.append(_step("semantic_runtime", "error", state["message"]))
        return _finalise_result("semantic", vault_root, dry_run, steps)

    dependencies_missing = not state["dependencies_ok"]
    marker_missing = not state["marker"]
    model_state = state["model_state"]
    model_needs_provision = (
        model_state.manifest_missing
        or model_state.model_path_missing
        or model_state.model_revision_mismatch
        or bool(model_state.load_error)
    )
    sidecars_need_refresh = not state["sidecars_present"] or state["sidecars_outdated"]

    if not dependencies_missing and not marker_missing and not model_needs_provision and not sidecars_need_refresh:
        steps.append(_step("semantic_runtime", "noop", "Semantic runtime and embeddings sidecars are already healthy."))
        return _finalise_result("semantic", vault_root, dry_run, steps)

    if dry_run:
        _semantic_provision.plan_runtime_step(
            steps,
            runtime_missing=dependencies_missing,
        )
        _semantic_provision.plan_model_step(
            steps,
            model_needs_provision=model_needs_provision,
        )
        _semantic_provision.plan_asset_step(steps, assets_missing=sidecars_need_refresh)
        if marker_missing or dependencies_missing or model_needs_provision or sidecars_need_refresh:
            _semantic_provision.plan_marker_step(
                steps,
                marker_missing=True,
            )
        return _finalise_result("semantic", vault_root, dry_run, steps)

    try:
        outcome = _semantic_provision.provision_semantic_runtime(
            vault_root,
            python_executable=sys.executable,
            runtime_ok=state["dependencies_ok"],
            refresh_assets=sidecars_need_refresh or model_needs_provision,
        )
    except _semantic_provision.SemanticProvisionError as exc:
        steps.append(_step("semantic_runtime", "error", str(exc)))
        return _finalise_result("semantic", vault_root, dry_run, steps)
    _semantic_provision.append_runtime_steps(
        steps,
        outcome,
    )
    notes: list[str] = []
    _semantic_provision.append_asset_step(steps, notes, outcome)
    _semantic_provision.append_marker_step(steps, outcome)
    if outcome.assets_changed or outcome.assets_error:
        return _finalise_result("semantic", vault_root, dry_run, steps, notes=notes or None)
    return _finalise_result("semantic", vault_root, dry_run, steps)


def run_scope(
    scope: str,
    vault_root: Path,
    *,
    dry_run: bool = False,
    bootstrap_steps: list[dict] | None = None,
) -> dict:
    if scope == "runtime":
        return verify_runtime_post_bootstrap(vault_root, dry_run, bootstrap_steps)
    if scope == "mcp":
        return repair_mcp(vault_root, dry_run, bootstrap_steps)
    if scope == "router":
        return repair_router(vault_root, dry_run, bootstrap_steps)
    if scope == "lexical":
        return repair_lexical(vault_root, dry_run, bootstrap_steps)
    if scope == "registry":
        return repair_registry(vault_root, dry_run, bootstrap_steps)
    if scope == "frontmatter":
        return repair_frontmatter(vault_root, dry_run, bootstrap_steps)
    if scope == "semantic":
        return repair_semantic(vault_root, dry_run, bootstrap_steps)
    raise ValueError(f"Unknown repair scope: {scope}")


def collect_managed_check_findings(vault_root: str | Path) -> list[dict]:
    """Return managed-only additive compliance findings."""
    vault_root = Path(vault_root)
    findings: list[dict] = []
    semantic = inspect_semantic(vault_root)
    if semantic["configured"] and not semantic["healthy"]:
        for issue in semantic["issues"]:
            finding = {
                "check": f"semantic:{issue}",
                "severity": "warning",
                "file": None,
                "message": _semantic_issue_message(issue),
            }
            findings.append(attach_repair_guidance(finding, vault_root, "semantic"))

    return findings


collect_check_findings = collect_managed_check_findings
