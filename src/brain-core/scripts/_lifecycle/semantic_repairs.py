#!/usr/bin/env python3
"""Managed semantic inspection and repair helpers."""

from __future__ import annotations

import sys
from pathlib import Path

from _bootstrap.runtime import iso_now, probe_python, step as _step
from _common import resolve_vault_venv_python
from _lifecycle_common import make_result_envelope
from _repair_common import attach_repair_guidance

import _semantic.config as semantic_config
import _semantic.model as semantic_model
import _semantic.provision as semantic_provision
import _semantic.runtime as semantic_runtime


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


def _semantic_message(
    configured: bool,
    supported: bool,
    unsupported_message: str | None,
    issues: list[str],
) -> str:
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


def semantic_issue_message(issue: str) -> str:
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


def clear_semantic_embeddings_outputs(vault_root: Path) -> None:
    """Drop persisted semantic sidecars after router rebuilds."""
    semantic_runtime.clear_embeddings_outputs(str(vault_root))


def inspect_semantic(vault_root: Path) -> dict:
    """Inspect semantic config/runtime health for this vault."""
    try:
        cfg = semantic_config.load_config_checked(vault_root)
    except semantic_config.SemanticConfigLoadError as exc:
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

    retrieval_enabled = semantic_config.semantic_retrieval_enabled(vault_root, config=cfg)
    processing_enabled = semantic_config.semantic_processing_enabled(vault_root, config=cfg)
    configured = semantic_config.is_semantic_intent_active(vault_root, config=cfg)
    marker = semantic_config.semantic_engine_installed(vault_root, config=cfg)
    supported, unsupported_message = semantic_provision.semantic_runtime_supported_platform()
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
        modules=semantic_provision.SEMANTIC_RUNTIME_MODULES,
    )
    dependencies_ok = bool(managed_probe.get("ok"))
    model_state = semantic_model.inspect_model_state(vault_root)
    if dependencies_ok:
        model_state = semantic_model.verify_local_model_load(model_state)
    sidecars_present, sidecars_outdated = semantic_runtime.embeddings_sidecars_match_manifest(
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


def repair_semantic(
    vault_root: Path,
    dry_run: bool,
    bootstrap_steps: list[dict] | None = None,
) -> dict:
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
        semantic_provision.plan_runtime_step(
            steps,
            runtime_missing=dependencies_missing,
        )
        semantic_provision.plan_model_step(
            steps,
            model_needs_provision=model_needs_provision,
        )
        semantic_provision.plan_asset_step(steps, assets_missing=sidecars_need_refresh)
        if marker_missing or dependencies_missing or model_needs_provision or sidecars_need_refresh:
            semantic_provision.plan_marker_step(
                steps,
                marker_missing=True,
            )
        return _finalise_result("semantic", vault_root, dry_run, steps)

    try:
        outcome = semantic_provision.provision_semantic_runtime(
            vault_root,
            python_executable=sys.executable,
            runtime_ok=state["dependencies_ok"],
            refresh_assets=sidecars_need_refresh or model_needs_provision,
        )
    except semantic_provision.SemanticProvisionError as exc:
        steps.append(_step("semantic_runtime", "error", str(exc)))
        return _finalise_result("semantic", vault_root, dry_run, steps)
    semantic_provision.append_runtime_steps(
        steps,
        outcome,
    )
    notes: list[str] = []
    semantic_provision.append_asset_step(steps, notes, outcome)
    semantic_provision.append_marker_step(steps, outcome)
    if outcome.assets_changed or outcome.assets_error:
        return _finalise_result("semantic", vault_root, dry_run, steps, notes=notes or None)
    return _finalise_result("semantic", vault_root, dry_run, steps)


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
                "message": semantic_issue_message(issue),
            }
            findings.append(attach_repair_guidance(finding, vault_root, "semantic"))

    return findings
