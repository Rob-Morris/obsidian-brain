#!/usr/bin/env python3
"""Packageful repair logic that runs inside the managed vault runtime.

This module repairs vault-local state only. Never mutate machine-level state
here; the machine surface owns user-home registry/default state and shared
runtime maintenance.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import compile_router
import check as check_mod
import edit
from _bootstrap import mcp_transport
import workspace_registry
import _bootstrap.diagnostics as bootstrap_diagnostics
from _bootstrap.diagnostics import (
    ISSUE_MANAGED_RUNTIME_DEPENDENCIES_MISSING,
    ISSUE_RUNTIME_MISSING,
    ISSUE_RUNTIME_UNUSABLE,
)
from _bootstrap.runtime import iso_now, step as _step
from _lifecycle.frontmatter_repairs import normalize_duplicate_frontmatter_documents
from _lifecycle_common import make_result_envelope
from _common import (
    MutationLockError,
    PartialApplyError,
    mutation_lock_error_message,
    scan_artefact_key_reference_index,
    vault_mutation_lock,
    remove_empty_artefact_folders,
    scan_empty_artefact_folders,
)


def _finalise_result(
    scope: str,
    vault_root: Path,
    dry_run: bool,
    steps: list[dict],
    notes: list[str] | None = None,
    status: str | None = None,
) -> dict:
    return make_result_envelope(
        scope=scope,
        vault_root=vault_root,
        dry_run=dry_run,
        managed_python=sys.executable,
        steps=steps,
        checked_at=iso_now(),
        notes=notes,
        status=status,
    )


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

    try:
        grok = state["grok"]
        if not grok["present"] or grok["healthy"]:
            steps.append(
                _step("grok_project", "noop", "Grok project MCP needs no repair.")
            )
        elif dry_run:
            steps.append(
                _step(
                    "grok_project",
                    "planned",
                    "Would repair native Grok MCP, startup rule and init-state record.",
                )
            )
        else:
            record = mcp_transport.register_grok(server_config, "project", vault_root)
            mcp_transport.record_init_target(vault_root, record)
            steps.append(
                _step(
                    "grok_project",
                    "changed",
                    "Repaired native Grok MCP, startup rule and init-state record.",
                )
            )
    except (OSError, ValueError, RuntimeError) as exc:
        steps.append(_step("grok_project", "error", str(exc)))

    notes = mcp_transport.claude_project_followup_notes(vault_root) if state["claude"]["present"] else []
    if state["grok"]["present"]:
        notes.extend(mcp_transport.mcp_followup_notes(["grok"], "project", vault_root))
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
    from _portable.router_maintenance import maintain_router

    steps = list(bootstrap_steps or [])
    result = maintain_router(vault_root, dry_run=dry_run, force=False)
    if result.status == "noop":
        steps.append(_step("router", "noop", "Compiled router is already fresh."))
        return _finalise_result("router", vault_root, dry_run, steps)
    if result.status == "planned":
        steps.append(
            _step(
                "router",
                "planned",
                f"Would rebuild the compiled router ({result.reason}) and clear semantic embeddings sidecars.",
            )
        )
        return _finalise_result("router", vault_root, dry_run, steps)

    steps.append(
        _step(
            "router",
            "changed",
            f"Rebuilt the compiled router ({result.reason}) and cleared semantic embeddings sidecars.",
        )
    )
    if result.session_error:
        steps.append(_step(
            "router_session",
            "error",
            f"Router rebuilt but session markdown refresh failed: {result.session_error}",
        ))
    return _finalise_result("router", vault_root, dry_run, steps)


def repair_lexical(vault_root: Path, dry_run: bool, bootstrap_steps: list[dict] | None = None) -> dict:
    from _portable.lexical_maintenance import maintain_lexical_index

    steps = list(bootstrap_steps or [])
    result = maintain_lexical_index(vault_root, dry_run=dry_run, force=False)
    if result.status == "noop":
        steps.append(_step("lexical", "noop", "Lexical retrieval index is already fresh."))
        return _finalise_result("lexical", vault_root, dry_run, steps)
    if result.status == "planned":
        steps.append(_step("lexical", "planned", f"Would rebuild the lexical retrieval index ({result.reason})."))
        return _finalise_result("lexical", vault_root, dry_run, steps)

    steps.append(_step("lexical", "changed", f"Rebuilt the lexical retrieval index ({result.reason})."))
    return _finalise_result("lexical", vault_root, dry_run, steps)


def repair_registry(vault_root: Path, dry_run: bool, bootstrap_steps: list[dict] | None = None) -> dict:
    from _portable.registry_maintenance import (
        RegistryRepairPartialError,
        repair_registry as maintain_registry,
    )

    steps = list(bootstrap_steps or [])
    try:
        result = maintain_registry(vault_root, dry_run=dry_run)
    except RegistryRepairPartialError as exc:
        steps.append(
            _step(
                "registry_backup",
                "changed",
                f"Preserved the malformed registry at {vault_root / exc.backup_path}.",
            )
        )
        steps.append(_step("registry", "error", str(exc)))
        return _finalise_result(
            "registry", vault_root, dry_run, steps, status="partial"
        )
    except (OSError, ValueError) as exc:
        steps.append(_step("registry", "error", str(exc)))
        return _finalise_result("registry", vault_root, dry_run, steps)

    if result.status == "noop":
        steps.append(_step("registry", "noop", result.reason))
        return _finalise_result("registry", vault_root, dry_run, steps)
    registry_path = vault_root / workspace_registry.REGISTRY_REL
    if result.status == "planned":
        steps.append(_step("registry", "planned", f"Would repair {registry_path} ({result.reason})."))
        return _finalise_result("registry", vault_root, dry_run, steps)

    if result.backup_path is not None:
        backup_path = vault_root / result.backup_path
        steps.append(
            _step(
                "registry",
                "changed",
                f"Repaired {registry_path} and preserved the malformed copy at {backup_path}.",
            )
        )
        return _finalise_result("registry", vault_root, dry_run, steps)

    steps.append(_step("registry", "changed", f"Normalised {registry_path}."))
    return _finalise_result("registry", vault_root, dry_run, steps)


def repair_frontmatter(vault_root: Path, dry_run: bool, bootstrap_steps: list[dict] | None = None) -> dict:
    """Normalise duplicate frontmatter blocks, taking the vault mutation lock."""
    try:
        with vault_mutation_lock(vault_root):
            return repair_frontmatter_locked(vault_root, dry_run, bootstrap_steps)
    except MutationLockError as exc:
        steps = list(bootstrap_steps or [])
        steps.append(_step("frontmatter", "error", mutation_lock_error_message(exc)))
        return _finalise_result("frontmatter", vault_root, dry_run, steps)


@dataclass(frozen=True, slots=True)
class ArtefactRepairPlan:
    """A selected repair scope's concrete findings and optional move plan."""

    scope: str
    router: dict
    findings: tuple[dict, ...]
    movement: object | None = None
    error: str | None = None
    uninspected: tuple[str, ...] = ()


def plan_artefact_repair(vault_root, scope, *, effective_at=None):
    """Inspect the intrinsic repair scope once without applying any repair."""
    from _lifecycle.frontmatter_repairs import plan_duplicate_frontmatter_documents
    from rename import plan_move_and_links

    if scope == "frontmatter":
        unreadable = []
        findings = plan_duplicate_frontmatter_documents(vault_root, effective_at=effective_at,
                                                         unreadable=unreadable)
        return ArtefactRepairPlan(scope, {}, tuple(findings), uninspected=tuple(unreadable))
    router = compile_router.compile(str(vault_root))
    if scope == "empty_folders":
        unreadable = []
        findings = scan_empty_artefact_folders(str(vault_root), router, unreadable=unreadable)
        error = (f"Could not read {len(unreadable)} folder(s) during the scan: "
                 + ", ".join(unreadable)) if unreadable else None
        return ArtefactRepairPlan(scope, router, tuple(findings), error=error)
    if scope != "ownership":
        raise ValueError(f"Unknown artefact repair scope: {scope}")
    findings = [item for item in check_mod.check_parent_contract(str(vault_root), router)
                if item.get("repairable") is True]
    plans, moves, seen = [], [], set()
    try:
        references = scan_artefact_key_reference_index(str(vault_root), router) if findings else {}
        for finding in findings:
            plan = edit.plan_parent_projection_repair(str(vault_root), router,
                                                       finding["file"], reference_index=references)
            plans.append(plan)
            for move in plan["moves"]:
                pair = (move["source"], move["dest"])
                if pair not in seen:
                    seen.add(pair)
                    moves.append(move)
        movement = plan_move_and_links(str(vault_root), moves, prune_router=router)
    except (ValueError, OSError) as exc:
        return ArtefactRepairPlan(scope, router, tuple(plans), error=str(exc))
    return ArtefactRepairPlan(scope, router, tuple(plans), movement)


def repair_frontmatter_locked(
    vault_root: Path,
    dry_run: bool,
    bootstrap_steps: list[dict] | None = None,
    *, prepared_plan=None,
) -> dict:
    """Plan and apply frontmatter repair while the caller holds the mutation lock."""
    steps = list(bootstrap_steps or [])
    plan = prepared_plan or plan_artefact_repair(vault_root, "frontmatter")
    result = normalize_duplicate_frontmatter_documents(
        vault_root, dry_run=dry_run, prepared_findings=plan.findings)
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


def repair_ownership(vault_root: Path, dry_run: bool, bootstrap_steps: list[dict] | None = None) -> dict:
    """Reconcile derived paths towards valid authoritative parent metadata."""
    try:
        with vault_mutation_lock(vault_root):
            return repair_ownership_locked(vault_root, dry_run, bootstrap_steps)
    except MutationLockError as exc:
        steps = list(bootstrap_steps or [])
        steps.append(_step("ownership", "error", mutation_lock_error_message(exc)))
        return _finalise_result("ownership", vault_root, dry_run, steps)


def repair_ownership_locked(
    vault_root: Path,
    dry_run: bool,
    bootstrap_steps: list[dict] | None = None,
    *, prepared_plan=None,
) -> dict:
    """Plan and apply ownership repair while the caller holds the mutation lock."""
    steps = list(bootstrap_steps or [])
    plan = prepared_plan or plan_artefact_repair(vault_root, "ownership")
    router, plans = plan.router, plan.findings
    if plan.error:
        steps.append(_step("ownership", "error", plan.error))
        return _finalise_result("ownership", vault_root, dry_run, steps)
    moves = plan.movement.moves

    if not moves:
        steps.append(
            _step("ownership", "noop", "Parent metadata and derived ownership paths agree.")
        )
        return _finalise_result("ownership", vault_root, dry_run, steps)

    notes = [f"{move['source']} -> {move['dest']}" for move in moves]
    if dry_run:
        steps.append(
            _step(
                "ownership",
                "planned",
                f"Would move {len(moves)} artefact(s) for {len(plans)} authoritative parent repair(s).",
            )
        )
        return _finalise_result("ownership", vault_root, dry_run, steps, notes=notes)

    try:
        from rename import apply_move_and_links
        result = apply_move_and_links(str(vault_root), plan.movement)
    except PartialApplyError as exc:
        steps.append(_step("ownership", "error", str(exc)))
        return _finalise_result(
            "ownership", vault_root, dry_run, steps, notes=notes, status="partial"
        )
    except (FileNotFoundError, FileExistsError, OSError, ValueError) as exc:
        steps.append(_step("ownership", "error", str(exc)))
        return _finalise_result("ownership", vault_root, dry_run, steps, notes=notes)

    steps.append(
        _step(
            "ownership",
            "changed",
            f"Moved {len(moves)} artefact(s); updated {result['links_updated']} wikilink(s).",
        )
    )
    return _finalise_result("ownership", vault_root, dry_run, steps, notes=notes)


def repair_empty_folders(vault_root: Path, dry_run: bool, bootstrap_steps: list[dict] | None = None) -> dict:
    """Remove vacated-empty artefact folders under type roots and ``_Archive``."""
    try:
        with vault_mutation_lock(vault_root):
            return repair_empty_folders_locked(vault_root, dry_run, bootstrap_steps)
    except MutationLockError as exc:
        steps = list(bootstrap_steps or [])
        steps.append(_step("empty_folders", "error", mutation_lock_error_message(exc)))
        return _finalise_result("empty_folders", vault_root, dry_run, steps)


def repair_empty_folders_locked(
    vault_root: Path,
    dry_run: bool,
    bootstrap_steps: list[dict] | None = None,
    *, prepared_plan=None,
) -> dict:
    """Plan and apply empty-folder removal while the caller holds the mutation lock.

    The dry run lists every directory and junk file each maximal finding would
    take with it. An unreadable folder aborts before any removal, because a
    plan built from a partial scan is not one the operator previewed. On
    apply, each finding is re-verified immediately before removal; one that
    gained content is skipped, never deleted, and the notes report what
    actually happened to each folder.
    """
    steps = list(bootstrap_steps or [])
    plan = prepared_plan or plan_artefact_repair(vault_root, "empty_folders")
    router, findings = plan.router, plan.findings
    if plan.error:
        steps.append(_step("empty_folders", "error", plan.error))
        return _finalise_result("empty_folders", vault_root, dry_run, steps)
    if not findings:
        steps.append(
            _step("empty_folders", "noop", "No vacated-empty artefact folders were found.")
        )
        return _finalise_result("empty_folders", vault_root, dry_run, steps)

    if dry_run:
        notes = []
        for finding in findings:
            notes.extend(f"{rel_dir}/" for rel_dir in finding["directories"])
            notes.extend(finding["junk_files"])
        directory_count = sum(len(finding["directories"]) for finding in findings)
        junk_count = sum(len(finding["junk_files"]) for finding in findings)
        steps.append(
            _step(
                "empty_folders",
                "planned",
                f"Would remove {len(findings)} vacated-empty folder(s): "
                f"{directory_count} directories and {junk_count} junk file(s).",
            )
        )
        return _finalise_result("empty_folders", vault_root, dry_run, steps, notes=notes)

    outcomes = remove_empty_artefact_folders(
        str(vault_root), router, [finding["path"] for finding in findings]
    )
    notes = [
        f"{item['status']}: {item['path']}"
        + (f" — {item['reason']}" if item["reason"] else "")
        + (
            f" (removed {len(item['removed'])} entr{'y' if len(item['removed']) == 1 else 'ies'} first)"
            if item["status"] == "failed" and item["removed"]
            else ""
        )
        for item in outcomes
    ]
    removed = [item for item in outcomes if item["status"] == "removed"]
    skipped = [item for item in outcomes if item["status"] == "skipped"]
    failed = [item for item in outcomes if item["status"] == "failed"]
    if failed:
        steps.append(
            _step(
                "empty_folders",
                "error",
                f"Removed {len(removed)} folder(s), skipped {len(skipped)}; "
                f"could not remove {len(failed)}: "
                + "; ".join(f"{item['path']}: {item['reason']}" for item in failed),
            )
        )
        return _finalise_result(
            "empty_folders", vault_root, dry_run, steps, notes=notes, status="partial"
        )
    if not removed:
        steps.append(
            _step(
                "empty_folders",
                "noop",
                f"Skipped {len(skipped)} folder(s) that changed since the scan.",
            )
        )
        return _finalise_result("empty_folders", vault_root, dry_run, steps, notes=notes)
    message = f"Removed {len(removed)} vacated-empty folder(s)."
    if skipped:
        message += f" Skipped {len(skipped)} that changed since the scan."
    steps.append(_step("empty_folders", "changed", message))
    return _finalise_result("empty_folders", vault_root, dry_run, steps, notes=notes)


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
    if scope == "ownership":
        return repair_ownership(vault_root, dry_run, bootstrap_steps)
    if scope == "empty_folders":
        return repair_empty_folders(vault_root, dry_run, bootstrap_steps)
    if scope == "semantic":
        from _lifecycle import semantic_repairs

        return semantic_repairs.repair_semantic(vault_root, dry_run, bootstrap_steps)
    raise ValueError(f"Unknown repair scope: {scope}")
