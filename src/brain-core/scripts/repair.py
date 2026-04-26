#!/usr/bin/env python3
"""
repair.py — explicit Brain infrastructure repair entry point.

Bootstrap layer:
  - self-contained and dependency-light
  - may be launched from any compatible Python 3.12+
  - converges execution into the vault-local `.venv`

Runtime layer:
  - runs inside the managed vault `.venv`
  - performs named repair scopes: mcp, router, index, registry
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

from _repair_common import (
    BOOTSTRAP_SUMMARY_ENV,
    MANAGED_RUNTIME_ENV,
    REPAIR_SCOPES,
    REQUIREMENTS_REL,
    VENV_PYTHON_REL,
    probe_python,
    find_repair_launcher,
    iso_now,
    make_result_envelope,
    required_modules_for_scope,
    step as _step,
)


BOOTSTRAP_TIMEOUT = 300


def _managed_python(vault_root: Path) -> Path:
    return vault_root / VENV_PYTHON_REL


def _bootstrap_summary(
    vault_root: Path,
    *,
    scope: str,
    launcher_python: str,
    dry_run: bool,
) -> dict:
    managed_python = _managed_python(vault_root)
    requirements = vault_root / REQUIREMENTS_REL
    required_modules = required_modules_for_scope(scope)
    steps: list[dict] = []

    runtime_probe = probe_python(str(managed_python))
    managed_exists = managed_python.is_file()

    if managed_exists and runtime_probe.get("compatible"):
        steps.append(_step(
            "managed_runtime",
            "noop",
            "Vault-local managed runtime is already present.",
            python=str(managed_python),
        ))
    elif dry_run:
        steps.append(_step(
            "managed_runtime",
            "planned",
            "Would create or repair the vault-local managed runtime.",
            python=str(managed_python),
        ))
    else:
        subprocess.run(
            [launcher_python, "-m", "venv", str(vault_root / ".venv")],
            check=True,
            timeout=BOOTSTRAP_TIMEOUT,
        )
        runtime_probe = probe_python(str(managed_python))
        if not runtime_probe.get("compatible"):
            raise RuntimeError("Created vault-local .venv is not Python 3.12+")
        steps.append(_step(
            "managed_runtime",
            "changed",
            "Created or repaired the vault-local managed runtime.",
            python=str(managed_python),
        ))

    if not required_modules:
        steps.append(_step(
            "managed_dependencies",
            "noop",
            "This repair scope does not require additional managed runtime dependencies.",
            requirements=str(requirements),
        ))
        ready = managed_python.is_file() and runtime_probe.get("compatible", False)
    else:
        dependency_probe = probe_python(str(managed_python), modules=required_modules)
        missing = tuple(dependency_probe.get("missing", []))
        if not missing:
            steps.append(_step(
                "managed_dependencies",
                "noop",
                "Managed runtime dependencies required by this repair scope are already available.",
                requirements=str(requirements),
            ))
        elif dry_run:
            steps.append(_step(
                "managed_dependencies",
                "planned",
                "Would sync the managed runtime dependencies required by this repair scope.",
                requirements=str(requirements),
                missing=list(missing),
            ))
        else:
            subprocess.run(
                [str(managed_python), "-m", "pip", "install", "--quiet", "-r", str(requirements)],
                check=True,
                timeout=BOOTSTRAP_TIMEOUT,
            )
            dependency_probe = probe_python(str(managed_python), modules=required_modules)
            if not dependency_probe.get("ok"):
                raise RuntimeError(
                    "Vault-local dependency sync completed, but required modules are still unavailable."
                )
            steps.append(_step(
                "managed_dependencies",
                "changed",
                "Synced the managed runtime dependencies required by this repair scope.",
                requirements=str(requirements),
            ))
        ready = managed_python.is_file() and dependency_probe.get("ok", False)

    if dry_run and any(step["status"] == "planned" for step in steps):
        status = "planned"
    elif ready:
        status = "ready"
    else:
        status = "error"

    return {
        "checked_at": iso_now(),
        "launcher_python": launcher_python,
        "managed_python": str(managed_python),
        "status": status,
        "steps": steps,
        "managed_runtime_ready": ready,
    }


def _find_launcher_python() -> str:
    launcher = find_repair_launcher()
    if launcher:
        return launcher
    fatal(
        "No compatible Python 3.12+ launcher was found.\n"
        "Install Python 3.12 or 3.13 and rerun repair.py."
    )


def _exec_managed_runtime(args: argparse.Namespace, vault_root: Path, summary: dict) -> None:
    managed_python = summary["managed_python"]
    env = os.environ.copy()
    env[MANAGED_RUNTIME_ENV] = "1"
    env[BOOTSTRAP_SUMMARY_ENV] = json.dumps(summary)
    argv = [
        managed_python,
        str(Path(__file__).resolve()),
        args.scope,
        "--vault",
        str(vault_root),
    ]
    if args.dry_run:
        argv.append("--dry-run")
    if args.json:
        argv.append("--json")
    os.execve(managed_python, argv, env)


def _render_human(result: dict) -> str:
    lines = [
        f"Repair scope: {result['scope']}",
        f"Vault: {result['vault_root']}",
        f"Status: {result['status']}",
    ]
    for step in result.get("steps", []):
        label = {
            "changed": "CHANGED",
            "noop": "OK     ",
            "planned": "PLAN   ",
            "error": "ERROR  ",
        }.get(step["status"], step["status"].upper())
        lines.append(f"  {label}  {step['name']}: {step['message']}")
    for note in result.get("notes", []):
        lines.append(f"  NOTE    {note}")
    return "\n".join(lines)


def _exit_code_for_result(result: dict) -> int:
    status = result.get("status")
    if status == "error":
        return 2
    if status == "partial":
        return 1
    return 0


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
    bootstrap_env = os.environ.get(BOOTSTRAP_SUMMARY_ENV)
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
        except (subprocess.CalledProcessError, RuntimeError) as exc:
            if isinstance(exc, subprocess.CalledProcessError):
                message = f"Bootstrap command failed with exit code {exc.returncode}."
            else:
                message = str(exc)
            result = make_result_envelope(
                scope=args.scope,
                vault_root=vault_root,
                dry_run=args.dry_run,
                managed_python=str(_managed_python(vault_root)),
                steps=[_step("managed_runtime", "error", message)],
                status="error",
            )
            if args.json:
                print(json.dumps(result, indent=2, ensure_ascii=False))
            else:
                print(_render_human(result))
            return _exit_code_for_result(result)

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
            fatal("Managed runtime bootstrap did not produce a usable vault-local .venv.")
    elif bootstrap_env:
        try:
            bootstrap_steps = json.loads(bootstrap_env).get("steps", [])
        except json.JSONDecodeError:
            bootstrap_steps = []

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
    return _exit_code_for_result(result)


if __name__ == "__main__":
    sys.exit(main())
