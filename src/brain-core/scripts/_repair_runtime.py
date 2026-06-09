#!/usr/bin/env python3
"""Packageful repair logic that runs inside the managed vault runtime."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import compile_router
from _bootstrap import mcp_transport
import workspace_registry
import _search.index as search_index
import _bootstrap.diagnostics as bootstrap_diagnostics
from _bootstrap.diagnostics import (
    ISSUE_MANAGED_RUNTIME_DEPENDENCIES_MISSING,
    ISSUE_RUNTIME_MISSING,
    ISSUE_RUNTIME_UNUSABLE,
)
from _bootstrap.runtime import iso_now, step as _step
from _lifecycle.derived_cache_state import inspect_lexical_cache, inspect_router_cache
from _lifecycle.frontmatter_repairs import normalize_duplicate_frontmatter_documents
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
        managed_python=sys.executable,
        steps=steps,
        checked_at=iso_now(),
        notes=notes,
    )


def _backup_path(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return path.with_name(f"{path.name}.{stamp}.bak")


def _record_claude_direct(vault_root: Path, server_config: dict) -> None:
    config_path = vault_root / mcp_transport.CLAUDE_PROJECT_CONFIG_FILE
    mcp_transport.write_project_mcp_json(server_config, vault_root)
    bootstrap_path = mcp_transport.ensure_claude_md(vault_root)
    hook_python = mcp_transport.session_hook_python(server_config)
    hook_path = mcp_transport.ensure_session_start_hook(vault_root, vault_root, python_path=hook_python)
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
        "hook_command": mcp_transport.build_session_hook_command(
            vault_root, vault_root, python_path=hook_python
        ),
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
    state = inspect_router_cache(vault_root)
    if not state.stale:
        steps.append(_step("router", "noop", "Compiled router is already fresh."))
        return _finalise_result("router", vault_root, dry_run, steps)
    if dry_run:
        steps.append(_step("router", "planned", f"Would rebuild the compiled router ({state.reason})."))
        return _finalise_result("router", vault_root, dry_run, steps)

    compiled = compile_router.compile(str(vault_root))
    compile_router.persist_compiled_router(str(vault_root), compiled)
    semantic_repairs.clear_semantic_embeddings_outputs(vault_root)
    steps.append(_step("router", "changed", f"Rebuilt the compiled router ({state.reason})."))
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
    state = inspect_lexical_cache(vault_root)
    if not state.stale:
        steps.append(_step("lexical", "noop", "Lexical retrieval index is already fresh."))
        return _finalise_result("lexical", vault_root, dry_run, steps)
    if dry_run:
        steps.append(_step("lexical", "planned", f"Would rebuild the lexical retrieval index ({state.reason})."))
        return _finalise_result("lexical", vault_root, dry_run, steps)

    build_result = search_index.build_index(str(vault_root))
    search_index.persist_retrieval_index(str(vault_root), build_result.index)
    steps.append(_step("lexical", "changed", f"Rebuilt the lexical retrieval index ({state.reason})."))
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
