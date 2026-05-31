#!/usr/bin/env python3
"""Packageful repair logic that runs inside the managed vault runtime."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import compile_router
from _bootstrap import mcp_transport
import workspace_registry
import _search.index as search_index
import _search.paths as search_paths
import _bootstrap.diagnostics as bootstrap_diagnostics
from _bootstrap.diagnostics import (
    ISSUE_MANAGED_RUNTIME_DEPENDENCIES_MISSING,
    ISSUE_RUNTIME_MISSING,
    ISSUE_RUNTIME_UNUSABLE,
)
from _bootstrap.runtime import iso_now, step as _step
from _lifecycle.frontmatter_repairs import normalize_duplicate_frontmatter_documents
from _common import iter_artefact_paths
from _lifecycle_common import make_result_envelope


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


def _backup_path(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return path.with_name(f"{path.name}.{stamp}.bak")


def _record_claude_direct(vault_root: Path, server_config: dict) -> None:
    config_path = vault_root / mcp_transport.CLAUDE_PROJECT_CONFIG_FILE
    mcp_transport.write_project_mcp_json(server_config, vault_root)
    bootstrap_path = mcp_transport.ensure_claude_md(vault_root)
    hook_path = mcp_transport.ensure_session_start_hook(vault_root, vault_root)
    record = {
        "client": "claude",
        "scope": "project",
        "target_path": str(vault_root),
        "config_path": str(config_path),
        "server_name": mcp_transport.BRAIN_SERVER_NAME,
        "server_config": server_config,
        "bootstrap_path": str(bootstrap_path),
        "bootstrap_line": mcp_transport.bootstrap_line_for_target(vault_root),
        "hook_path": str(hook_path),
        "hook_command": mcp_transport.build_session_hook_command(vault_root, vault_root),
        "method": f"{config_path} (direct repair)",
    }
    mcp_transport.record_init_target(vault_root, record)


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
    record = mcp_transport.register_codex(server_config, "project", vault_root)
    mcp_transport.record_init_target(vault_root, record)
    return _step("codex_project", "changed", "Repaired Codex project MCP config and init-state record.")


def repair_mcp(vault_root: Path, dry_run: bool, bootstrap_steps: list[dict] | None = None) -> dict:
    steps = list(bootstrap_steps or [])
    # `repair.py main()` already repaired/bootstraped the managed runtime before
    # re-exec. Keep this guard for direct library/test callers that bypass the
    # bootstrap layer entirely.
    runtime_state = bootstrap_diagnostics.inspect_runtime(vault_root)
    if not runtime_state["healthy"]:
        steps.append(_step("runtime", "error", runtime_state["message"]))
        return _finalise_result("mcp", vault_root, dry_run, steps)

    state = bootstrap_diagnostics.inspect_mcp(vault_root)
    server_config = state["server_config"]

    try:
        steps.append(_repair_claude(vault_root, server_config, state["claude"], dry_run))
    except (OSError, ValueError) as exc:
        steps.append(_step("claude_project", "error", str(exc)))
    try:
        steps.append(_repair_codex(vault_root, server_config, state["codex"], dry_run))
    except (OSError, ValueError) as exc:
        steps.append(_step("codex_project", "error", str(exc)))

    notes = mcp_transport.claude_project_followup_notes(vault_root) if state["claude"]["present"] else []
    return _finalise_result("mcp", vault_root, dry_run, steps, notes=notes)


def verify_runtime_post_bootstrap(vault_root: Path, dry_run: bool, bootstrap_steps: list[dict] | None = None) -> dict:
    """Verify the runtime scope after bootstrap has repaired/re-synced it."""
    steps = list(bootstrap_steps or [])
    state = bootstrap_diagnostics.inspect_runtime(vault_root)
    if not state["healthy"]:
        steps.append(_step("runtime", "error", state["message"]))
        return _finalise_result("runtime", vault_root, dry_run, steps)

    steps.append(_step("runtime", "noop", state["message"]))
    return _finalise_result("runtime", vault_root, dry_run, steps)


def repair_router(vault_root: Path, dry_run: bool, bootstrap_steps: list[dict] | None = None) -> dict:
    from _lifecycle import semantic_repairs

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
    semantic_repairs.clear_semantic_embeddings_outputs(vault_root)
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
    state = bootstrap_diagnostics.inspect_registry(vault_root)
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
        from _lifecycle import semantic_repairs

        return semantic_repairs.repair_semantic(vault_root, dry_run, bootstrap_steps)
    raise ValueError(f"Unknown repair scope: {scope}")
