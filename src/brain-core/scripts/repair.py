#!/usr/bin/env python3
"""
repair.py — explicit Brain repair entry point.

Bootstrap layer:
  - self-contained and dependency-light
  - may be launched from any compatible Python 3.12+
  - converges execution into the central managed runtime
    (`~/.brain/venvs/<py-tag>-<req-hash>/`)

Runtime layer:
  - runs inside the central managed runtime
  - performs named repair scopes: runtime, mcp, router, lexical, registry, frontmatter,
    semantic
  - every scope may rely on the shared managed-runtime owner to recover a
    usable managed interpreter path; scope-specific requirements only control
    extra managed-package needs beyond that bootstrap

Repair altitude:
  - repair.py owns vault-local repair only
  - never mutate machine-level state here — the machine surface owns the
    user-home Brain registry, default pointer, and shared-runtime maintenance
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import NoReturn

from _bootstrap.vaults import find_vault_root

from _common import resolve_vault_venv_python
from _bootstrap.runtime import (
    current_process_in_managed_runtime,
    exec_managed_runtime,
    handoff_current_script_to_managed_runtime,
    preview_managed_runtime,
    required_modules_for_scope,
    step as _step,
)
from _lifecycle_common import (
    emit_lifecycle_result,
    exit_code_for_result,
    make_result_envelope,
    render_human_result,
)
from _repair_common import REPAIR_SCOPES


BOOTSTRAP_TIMEOUT = 300
LEGACY_SCOPE_RENAMES = {
    "index": "lexical",
}
def _planned_scope_message(scope: str) -> str:
    if scope == "runtime":
        return "Would repair and verify the central managed runtime."
    if scope == "mcp":
        return "Would continue current-vault MCP registration repair after the managed runtime is ready."
    return "Would continue the named repair scope after the managed runtime is ready."


def _render_human(result: dict) -> str:
    return render_human_result(result, subject_label="Repair scope", subject_key="scope")


def _emit_result(result: dict, *, as_json: bool) -> int:
    emit_lifecycle_result(result, as_json=as_json, render_human=_render_human)
    return exit_code_for_result(result)


def _managed_runtime_error_result(
    scope: str,
    vault_root: Path,
    *,
    dry_run: bool,
    message: str,
) -> dict:
    return make_result_envelope(
        scope=scope,
        vault_root=vault_root,
        dry_run=dry_run,
        managed_python=str(resolve_vault_venv_python(vault_root)),
        steps=[_step("managed_runtime", "error", message)],
        status="error",
    )


def fatal(message: str) -> NoReturn:
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(2)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Repair a named Brain scope.",
    )
    parser.add_argument(
        "scope",
        help="Repair scope to run.",
    )
    parser.add_argument(
        "--vault",
        help="Path to the Brain vault (default: auto-detect from script location or BRAIN_VAULT_ROOT).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview the planned mutations without applying them.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args(argv)
    if args.scope in LEGACY_SCOPE_RENAMES:
        replacement = LEGACY_SCOPE_RENAMES[args.scope]
        parser.error(f"repair scope '{args.scope}' was renamed to '{replacement}'")
    if args.scope not in REPAIR_SCOPES:
        choices = ", ".join(REPAIR_SCOPES.keys())
        parser.error(f"invalid choice: {args.scope!r} (choose from {choices})")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    vault_root = find_vault_root(args.vault)
    forwarded_args = list(argv) if argv is not None else sys.argv[1:]
    if args.dry_run:
        try:
            summary = preview_managed_runtime(
                vault_root,
                dependency_owner="this repair scope",
                required_modules=required_modules_for_scope(args.scope),
                timeout=BOOTSTRAP_TIMEOUT,
            )
        except RuntimeError as exc:
            result = _managed_runtime_error_result(
                args.scope,
                vault_root,
                dry_run=True,
                message=str(exc),
            )
            return _emit_result(result, as_json=args.json)

        if not summary["managed_runtime_ready"]:
            status = "planned" if summary["status"] == "planned" else "error"
            steps = list(summary["steps"])
            if status == "planned":
                steps.append(
                    _step(
                        args.scope,
                        "planned",
                        _planned_scope_message(args.scope),
                    )
                )
            result = make_result_envelope(
                scope=args.scope,
                vault_root=vault_root,
                dry_run=True,
                managed_python=summary["managed_python"],
                steps=steps,
                status=status,
            )
            return _emit_result(result, as_json=args.json)

        if not current_process_in_managed_runtime(vault_root):
            exec_managed_runtime(
                managed_python=summary["managed_python"],
                script_path=str(Path(__file__).resolve()),
                forwarded_args=forwarded_args,
                summary=summary,
            )
        bootstrap_steps = summary["steps"]
    else:
        try:
            summary = handoff_current_script_to_managed_runtime(
                vault_root,
                dependency_owner="this repair scope",
                required_modules=required_modules_for_scope(args.scope),
                forwarded_args=forwarded_args,
                script_path=str(Path(__file__).resolve()),
                timeout=BOOTSTRAP_TIMEOUT,
            )
        except RuntimeError as exc:
            result = _managed_runtime_error_result(
                args.scope,
                vault_root,
                dry_run=False,
                message=str(exc),
            )
            return _emit_result(result, as_json=args.json)
        bootstrap_steps = summary["steps"]

    from _repair_runtime import run_scope

    result = run_scope(
        args.scope,
        vault_root,
        dry_run=args.dry_run,
        bootstrap_steps=bootstrap_steps,
    )
    return _emit_result(result, as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
