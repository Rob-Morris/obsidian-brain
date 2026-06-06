#!/usr/bin/env python3
"""Launcher-safe composed `brain doctor` helper."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

import check as vault_check
import doctor_machine
from _common import find_runnable_python
from _repair_common import build_repair_command


def _no_runnable_python_guidance(current_vault: str) -> str:
    repair = build_repair_command(current_vault, "runtime")
    if sys.platform == "win32":
        return f"no runnable python for vault {current_vault} — run `{repair}`"
    return f"no runnable python for vault {current_vault} — run `{repair}` or `bash install.sh {current_vault}`"


def collect_cli_diagnosis(*, binary_path: str, cli_version: str, launcher_python: str | None) -> dict:
    """Collect CLI-owned doctor facts that remain outside the vault substrate."""
    binary = os.path.realpath(binary_path)
    binary_dir = os.path.dirname(binary)
    path_ok = binary_dir in os.environ.get("PATH", "").split(os.pathsep)

    launcher_version = None
    launcher_probe_failed = False
    if launcher_python:
        completed = subprocess.run(
            [launcher_python, "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if completed.returncode == 0:
            launcher_version = (completed.stdout or completed.stderr).strip() or None
        else:
            launcher_probe_failed = True

    return {
        "version": cli_version,
        "binary": binary,
        "binary_dir": binary_dir,
        "path_ok": path_ok,
        "launcher_python": launcher_python,
        "launcher_version": launcher_version,
        "launcher_probe_failed": launcher_probe_failed,
    }





def _supports_composed_doctor_render(payload: object) -> bool:
    """Return whether a check.py JSON payload carries the stable Doctor subset."""
    if not isinstance(payload, dict):
        return False
    summary = payload.get("summary")
    findings = payload.get("findings")
    if not isinstance(summary, dict) or not isinstance(findings, list):
        return False
    if any(not isinstance(summary.get(key), int) for key in ("errors", "warnings", "info")):
        return False
    for finding in findings:
        if not isinstance(finding, dict):
            return False
        if any(key not in finding for key in ("severity", "file", "message")):
            return False
    return True


def _vault_failure(vault_root: str | None, message: str, *, exit_code: int = 1) -> dict:
    return {
        "in_scope": vault_root is not None,
        "vault_root": vault_root,
        "available": False,
        "exit_code": exit_code,
        "message": message,
        "result": None,
    }



def collect_vault_diagnosis(
    *,
    current_vault: str | None,
    launcher_python: str | None,
    actionable: bool,
    severity: str | None,
) -> dict:
    """Collect the current-vault Doctor section via that vault's own check.py."""
    if current_vault is None:
        return {
            "in_scope": False,
            "vault_root": None,
            "available": False,
            "exit_code": 0,
            "message": "none in scope (run inside a vault or pass --vault)",
            "result": None,
        }

    vault_path = Path(current_vault)
    check_script = vault_path / ".brain-core" / "scripts" / "check.py"
    if not check_script.is_file():
        return _vault_failure(current_vault, "check.py missing — vault may be on an older brain-core")

    launcher_path = Path(launcher_python) if launcher_python else None
    runnable_python = find_runnable_python(vault_path, launcher=launcher_path)
    if runnable_python is None:
        return _vault_failure(current_vault, _no_runnable_python_guidance(current_vault))

    argv = [str(runnable_python), str(check_script), "--vault", current_vault, "--json"]
    if actionable:
        argv.append("--actionable")
    if severity:
        argv.extend(["--severity", severity])

    completed = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        message = completed.stderr.strip() or "check.py did not produce valid JSON output"
        return _vault_failure(current_vault, message, exit_code=max(completed.returncode, 1))

    if not _supports_composed_doctor_render(payload):
        return _vault_failure(
            current_vault,
            "check.py JSON schema is unsupported for composed Doctor output — upgrade the current Brain or the source Brain",
            exit_code=max(completed.returncode, 1),
        )

    return {
        "in_scope": True,
        "vault_root": current_vault,
        "available": True,
        "exit_code": completed.returncode,
        "message": None,
        "result": payload,
    }



def _render_cli_lines(cli: dict) -> list[str]:
    lines = [f"brain CLI: {cli['version']}", f"  binary:  {cli['binary']}"]
    if cli["path_ok"]:
        lines.append(f"  PATH:    ok ({cli['binary_dir']} on PATH)")
    else:
        lines.append(f"  PATH:    WARN ({cli['binary_dir']} not on PATH)")

    if cli["launcher_python"]:
        if cli.get("launcher_probe_failed"):
            lines.append(f"python:    ERROR ({cli['launcher_python']} failed --version probe)")
        else:
            lines.append(f"python:    {cli['launcher_python']} ({cli['launcher_version']})")
    else:
        lines.append("python:    MISSING (need python3.12+ on PATH)")
    return lines



def _render_vault_lines(vault: dict, *, actionable: bool) -> list[str]:
    if not vault["in_scope"]:
        return ["  none in scope (run inside a vault or pass --vault)"]

    lines = [f"  {vault['vault_root']}"]
    if not vault["available"]:
        lines.append(f"  {vault['message']}")
        return lines

    result = vault["result"]
    assert result is not None
    lines.extend(vault_check.render_human_findings(result, actionable=actionable))
    lines.append("")
    lines.append(vault_check.render_human_summary(result))
    return lines



def render_human_report(*, cli: dict, machine: dict, vault: dict, actionable: bool) -> list[str]:
    lines = _render_cli_lines(cli)
    lines.extend(["", "machine diagnosis:"])
    lines.extend(doctor_machine.render_human_lines(machine))
    lines.extend(["", "vault diagnosis:"])
    lines.extend(_render_vault_lines(vault, actionable=actionable))
    return lines



def overall_exit_code(*, cli: dict, machine: dict, vault: dict) -> int:
    rc = 0
    if not cli["path_ok"] or cli["launcher_python"] is None or cli.get("launcher_probe_failed"):
        rc = max(rc, 1)
    rc = max(rc, 0 if machine["healthy"] else 1)
    if vault["in_scope"]:
        if not vault["available"]:
            assert vault["exit_code"] >= 1
        rc = max(rc, vault["exit_code"])
    return rc



def build_report(*, args) -> tuple[dict, int]:
    cli = collect_cli_diagnosis(
        binary_path=args.binary,
        cli_version=args.cli_version,
        launcher_python=args.launcher,
    )
    machine = doctor_machine.collect_machine_summary(
        current_vault=args.current_vault,
        launcher_python=args.launcher,
    )
    vault = collect_vault_diagnosis(
        current_vault=args.current_vault,
        launcher_python=args.launcher,
        actionable=args.actionable,
        severity=args.severity,
    )
    exit_code = overall_exit_code(cli=cli, machine=machine, vault=vault)
    report = {
        "doctor": {
            "source_vault": args.vault,
            "current_vault": args.current_vault,
            "healthy": exit_code == 0,
            "exit_code": exit_code,
        },
        "cli": cli,
        "machine": machine,
        "vault": vault,
    }
    return report, exit_code



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launcher-safe composed Brain Doctor helper")
    parser.add_argument("--vault", required=True, help="Vault providing the doctor helper code")
    parser.add_argument("--current-vault", help="Current vault in scope for this invocation, if any")
    parser.add_argument("--launcher", help="Launcher Python path already chosen by the CLI")
    parser.add_argument("--binary", required=True, help="Path to the running brain CLI binary")
    parser.add_argument("--cli-version", required=True, help="CLI version string reported by the shell layer")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--actionable", action="store_true")
    parser.add_argument("--severity", choices=vault_check.VALID_SEVERITIES)
    return parser.parse_args()



def main() -> int:
    args = parse_args()
    report, exit_code = build_report(args=args)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for line in render_human_report(
            cli=report["cli"],
            machine=report["machine"],
            vault=report["vault"],
            actionable=args.actionable,
        ):
            print(line)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
