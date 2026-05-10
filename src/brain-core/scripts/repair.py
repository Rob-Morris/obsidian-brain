#!/usr/bin/env python3
"""
repair.py — explicit Brain infrastructure repair entry point.

Bootstrap layer:
  - self-contained and dependency-light
  - may be launched from any compatible Python 3.12+
  - converges execution into the central managed runtime
    (`~/.brain/venvs/<py-tag>-<req-hash>/`)

Runtime layer:
  - runs inside the central managed runtime
  - performs named repair scopes: mcp, router, index, registry, semantic
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import NoReturn

from init import find_vault_root

from _common import resolve_vault_venv_python
from _lifecycle_common import (
    bootstrap_managed_runtime,
    exit_code_for_result,
    exec_managed_runtime,
    find_repair_launcher,
    load_bootstrap_steps,
    make_result_envelope,
    render_human_result,
    required_modules_for_scope,
    step as _step,
)
from _repair_common import BOOTSTRAP_SUMMARY_ENV, MANAGED_RUNTIME_ENV, REPAIR_SCOPES


BOOTSTRAP_TIMEOUT = 300


def _bootstrap_summary(
    vault_root: Path,
    *,
    scope: str,
    launcher_python: str,
    dry_run: bool,
) -> dict:
    return bootstrap_managed_runtime(
        vault_root,
        required_modules=required_modules_for_scope(scope),
        dependency_owner="this repair scope",
        launcher_python=launcher_python,
        dry_run=dry_run,
        timeout=BOOTSTRAP_TIMEOUT,
    )


def _find_launcher_python() -> str:
    launcher = find_repair_launcher()
    if launcher:
        return launcher
    fatal(
        "No compatible Python 3.12+ launcher was found.\n"
        "Install Python 3.12 or 3.13 and rerun repair.py."
    )


def _exec_managed_runtime(args: argparse.Namespace, vault_root: Path, summary: dict) -> None:
    argv = [args.scope, "--vault", str(vault_root)]
    if args.dry_run:
        argv.append("--dry-run")
    if args.json:
        argv.append("--json")
    exec_managed_runtime(
        managed_python=summary["managed_python"],
        script_path=str(Path(__file__).resolve()),
        forwarded_args=argv,
        summary=summary,
        managed_runtime_env=MANAGED_RUNTIME_ENV,
        bootstrap_summary_env=BOOTSTRAP_SUMMARY_ENV,
    )


def _render_human(result: dict) -> str:
    return render_human_result(result, subject_label="Repair scope", subject_key="scope")


def fatal(message: str) -> NoReturn:
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(2)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Repair a named Brain infrastructure scope.",
    )
    parser.add_argument(
        "scope",
        choices=tuple(REPAIR_SCOPES.keys()),
        help="Repair scope to run.",
    )
    parser.add_argument(
        "--vault",
        help="Path to the Brain vault (default: auto-detect from script location or BRAIN_VAULT_ROOT).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview the planned mutations without applying them.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    vault_root = find_vault_root(args.vault)
    bootstrap_steps = []

    if os.environ.get(MANAGED_RUNTIME_ENV) != "1":
        launcher_python = _find_launcher_python()
        try:
            summary = _bootstrap_summary(
                vault_root,
                scope=args.scope,
                launcher_python=launcher_python,
                dry_run=args.dry_run,
            )
        except (subprocess.CalledProcessError, RuntimeError, AssertionError) as exc:
            if isinstance(exc, subprocess.CalledProcessError):
                message = f"Bootstrap command failed with exit code {exc.returncode}."
            else:
                message = str(exc)
            managed_python = str(resolve_vault_venv_python(vault_root))
            result = make_result_envelope(
                scope=args.scope,
                vault_root=vault_root,
                dry_run=args.dry_run,
                managed_python=managed_python,
                steps=[_step("managed_runtime", "error", message)],
                status="error",
            )
            if args.json:
                print(json.dumps(result, indent=2, ensure_ascii=False))
            else:
                print(_render_human(result))
            return exit_code_for_result(result)

        bootstrap_steps = summary["steps"]
        if summary["managed_runtime_ready"]:
            if os.path.realpath(sys.executable) != os.path.realpath(summary["managed_python"]):
                _exec_managed_runtime(args, vault_root, summary)
        elif args.dry_run:
            result = make_result_envelope(
                scope=args.scope,
                vault_root=vault_root,
                dry_run=True,
                managed_python=summary["managed_python"],
                steps=bootstrap_steps + [
                    _step(
                        args.scope,
                        "planned",
                        "Would continue the named repair scope after the managed runtime is ready.",
                    )
                ],
                status="planned",
            )
            if args.json:
                print(json.dumps(result, indent=2, ensure_ascii=False))
            else:
                print(_render_human(result))
            return 0
        else:
            fatal("Managed runtime bootstrap did not produce a usable central venv.")
    else:
        bootstrap_steps = load_bootstrap_steps(BOOTSTRAP_SUMMARY_ENV)

    from _repair_runtime import run_scope

    result = run_scope(
        args.scope,
        vault_root,
        dry_run=args.dry_run,
        bootstrap_steps=bootstrap_steps,
    )
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(_render_human(result))
    return exit_code_for_result(result)


if __name__ == "__main__":
    sys.exit(main())
