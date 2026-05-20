#!/usr/bin/env python3
"""configure.py — manage optional local Brain capabilities."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from init import find_vault_root

from _bootstrap.runtime import (
    handoff_current_script_to_managed_runtime,
    required_modules_for_scope,
    step as _step,
)
from _lifecycle_common import (
    emit_lifecycle_result,
    exit_code_for_result,
    make_result_envelope,
    render_human_result,
)
BOOTSTRAP_TIMEOUT = 300


def _result_envelope(action: str, vault_root: Path, steps: list[dict], *, notes: list[str] | None = None) -> dict:
    return make_result_envelope(
        action=action,
        vault_root=vault_root,
        managed_python=os.path.realpath(sys.executable),
        steps=steps,
        notes=notes,
    )


def _render_human(result: dict) -> str:
    return render_human_result(result, subject_label="Configure action", subject_key="action")


def _emit_result(result: dict, *, as_json: bool) -> int:
    emit_lifecycle_result(result, as_json=as_json, render_human=_render_human)
    return exit_code_for_result(result)


def _managed_runtime_error_result(action: str, vault_root: Path, message: str) -> dict:
    return _result_envelope(
        action,
        vault_root,
        [_step("managed_runtime", "error", message)],
    )


def _apply_semantic_flag(vault_root: Path, steps: list[dict]) -> None:
    import _semantic.config as semantic_config

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


def _provision_runtime_or_record_error(vault_root: Path, steps: list[dict], notes: list[str]) -> dict | None:
    import _semantic.provision as semantic_provision

    try:
        outcome = semantic_provision.provision_semantic_runtime(
            vault_root,
            python_executable=sys.executable,
            refresh_assets=True,
        )
    except semantic_provision.SemanticProvisionError as exc:
        steps.append(_step("semantic_runtime", "error", str(exc)))
        notes.append(
            "Semantic retrieval is configured on, but the managed runtime could not be provisioned on this machine."
        )
        return _result_envelope("semantic_enable", vault_root, steps, notes=notes)
    semantic_provision.append_runtime_steps(steps, outcome)
    semantic_provision.append_asset_step(steps, notes, outcome)
    semantic_provision.append_marker_step(steps, outcome)
    if outcome.assets_error:
        notes.append(
            "Run `python3 .brain-core/scripts/repair.py semantic` after resolving the underlying vault or runtime issue."
        )
        return _result_envelope("semantic_enable", vault_root, steps, notes=notes)
    return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Configure optional local Brain capabilities.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    semantic = subparsers.add_parser(
        "semantic",
        help="Configure semantic retrieval support for this vault.",
    )
    semantic.add_argument(
        "--vault",
        help="Path to the Brain vault (default: auto-detect from script location or BRAIN_VAULT_ROOT).",
    )
    semantic.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    semantic.add_argument(
        "--enable",
        action="store_true",
        required=True,
        help="Enable semantic retrieval in local config and provision runtime support.",
    )
    semantic.add_argument(
        "--no-provision",
        action="store_true",
        help="Write config only; skip semantic runtime provisioning and asset refresh.",
    )

    return parser.parse_args(argv)


def _configure_semantic_enable(vault_root: Path, *, provision: bool, bootstrap_steps: list[dict]) -> dict:
    import _semantic.provision as semantic_provision

    steps = list(bootstrap_steps)
    notes: list[str] = []

    _apply_semantic_flag(vault_root, steps)

    if not provision:
        notes.append(
            "Runtime provisioning was skipped (--no-provision). "
            "Run `python3 .brain-core/scripts/check.py --actionable` or "
            "`python3 .brain-core/scripts/repair.py semantic` later if this vault remains unavailable for semantic search."
        )
        return _result_envelope("semantic_enable", vault_root, steps, notes=notes)

    error_result = _provision_runtime_or_record_error(vault_root, steps, notes)
    if error_result is not None:
        return error_result
    return _result_envelope("semantic_enable", vault_root, steps, notes=notes)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    vault_root = find_vault_root(args.vault)
    forwarded_args = list(argv) if argv is not None else sys.argv[1:]

    try:
        summary = handoff_current_script_to_managed_runtime(
            vault_root,
            dependency_owner="configure.py",
            required_modules=required_modules_for_scope("semantic"),
            forwarded_args=forwarded_args,
            script_path=str(Path(__file__).resolve()),
            timeout=BOOTSTRAP_TIMEOUT,
        )
    except RuntimeError as exc:
        result = _managed_runtime_error_result("semantic_enable", vault_root, str(exc))
        return _emit_result(result, as_json=args.json)

    result = _configure_semantic_enable(
        Path(vault_root),
        provision=not args.no_provision,
        bootstrap_steps=summary["steps"],
    )
    return _emit_result(result, as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
