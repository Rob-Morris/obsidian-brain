"""Managed semantic-retrieval opt-in lifecycle owner."""

from __future__ import annotations

import sys
from pathlib import Path

from _bootstrap.runtime import step as _step
from _lifecycle_common import make_result_envelope
import _semantic.config as semantic_config
import _semantic.provision as semantic_provision


def _result(
    vault_root: Path,
    steps: list[dict],
    *,
    notes: list[str] | None = None,
    dry_run: bool | None = None,
) -> dict:
    return make_result_envelope(
        action="semantic_enable",
        vault_root=vault_root,
        managed_python=sys.executable,
        steps=steps,
        notes=notes,
        dry_run=dry_run,
    )


def _apply_flag(vault_root: Path, steps: list[dict]) -> None:
    changed = semantic_config.set_semantic_retrieval_enabled(vault_root, enabled=True)
    steps.append(
        _step(
            "semantic_config",
            "changed" if changed else "noop",
            (
                "Enabled defaults.flags.semantic_retrieval in .brain/local/config.yaml."
                if changed
                else "defaults.flags.semantic_retrieval is already enabled."
            ),
        )
    )


def _plan_enable(vault_root: Path, steps: list[dict], *, provision: bool) -> dict:
    enabled = semantic_config.semantic_retrieval_enabled(vault_root)
    steps.append(
        _step(
            "semantic_config",
            "noop" if enabled else "planned",
            (
                "defaults.flags.semantic_retrieval is already enabled."
                if enabled
                else "Would enable defaults.flags.semantic_retrieval in .brain/local/config.yaml."
            ),
        )
    )
    notes: list[str] = []
    if provision:
        from _lifecycle.semantic_repairs import (
            ISSUE_SEMANTIC_MODEL_LOAD_ERROR,
            ISSUE_SEMANTIC_MODEL_MANIFEST_MISSING,
            ISSUE_SEMANTIC_MODEL_PATH_MISSING,
            ISSUE_SEMANTIC_MODEL_REVISION_MISMATCH,
            inspect_semantic,
        )

        state = inspect_semantic(vault_root)
        issues = set(state["issues"])
        model_needs_provision = (
            not state["configured"]
            or bool(
                issues
                & {
                    ISSUE_SEMANTIC_MODEL_LOAD_ERROR,
                    ISSUE_SEMANTIC_MODEL_MANIFEST_MISSING,
                    ISSUE_SEMANTIC_MODEL_PATH_MISSING,
                    ISSUE_SEMANTIC_MODEL_REVISION_MISMATCH,
                }
            )
        )
        semantic_provision.plan_runtime_step(
            steps,
            runtime_missing=not state["dependencies_ok"],
        )
        semantic_provision.plan_model_step(
            steps,
            model_needs_provision=model_needs_provision,
        )
        semantic_provision.plan_asset_step(
            steps,
            assets_missing=(
                not state["sidecars_present"]
                or bool(state.get("sidecars_outdated"))
            ),
        )
        semantic_provision.plan_marker_step(
            steps,
            marker_missing=not state["marker"],
        )
    else:
        notes.append(
            "Runtime provisioning would remain skipped; run "
            "retrieval.repair-semantic later if semantic search remains unavailable."
        )
    return _result(vault_root, steps, notes=notes, dry_run=True)


def enable_semantic(
    vault_root: str | Path,
    *,
    provision: bool,
    bootstrap_steps: list[dict] | None = None,
    dry_run: bool = False,
) -> dict:
    """Enable semantic retrieval and optionally converge its managed runtime."""
    root = Path(vault_root)
    steps = list(bootstrap_steps or ())
    if dry_run:
        return _plan_enable(root, steps, provision=provision)

    notes: list[str] = []
    _apply_flag(root, steps)
    if not provision:
        notes.append(
            "Runtime provisioning was skipped (--no-provision). "
            "Run `vault.check` with `actionable: true` or "
            "`python3 .brain-core/scripts/repair.py semantic` later if this vault "
            "remains unavailable for semantic search."
        )
        return _result(root, steps, notes=notes)

    try:
        outcome = semantic_provision.provision_semantic_runtime(
            root,
            python_executable=sys.executable,
            refresh_assets=True,
        )
    except semantic_provision.SemanticProvisionError as exc:
        steps.append(_step("semantic_runtime", "error", str(exc)))
        notes.append(
            "Semantic retrieval is configured on, but the managed runtime could "
            "not be provisioned on this machine."
        )
        return _result(root, steps, notes=notes)

    semantic_provision.append_runtime_steps(steps, outcome)
    semantic_provision.append_asset_step(steps, notes, outcome)
    semantic_provision.append_marker_step(steps, outcome)
    if outcome.assets_error:
        notes.append(
            "Run `python3 .brain-core/scripts/repair.py semantic` after resolving "
            "the underlying vault or runtime issue."
        )
    return _result(root, steps, notes=notes)
