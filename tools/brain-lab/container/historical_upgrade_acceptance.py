#!/usr/bin/env python3
"""Run the bounded v0.54-to-target Brain upgrade acceptance probe."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
from typing import Any


MAX_CAPTURE_BYTES = 8 * 1024 * 1024
REQUIRED_MIGRATION_RECORDS = {
    "0.55.0",
    "0.56.0",
    "0.57.0",
    "0.59.0",
    "0.62.2@pre_compile_patch",
    "0.62.4",
}


class AcceptanceFailure(RuntimeError):
    pass


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _terminate_process_group(process: subprocess.Popen[bytes], process_group_id: int) -> None:
    try:
        os.killpg(process_group_id, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process_group_id, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if process.poll() is None:
        process.wait(timeout=5)


def _run(
    argv: list[str],
    *,
    cwd: Path,
    accepted: frozenset[int] | set[int] = frozenset({0}),
    timeout: float = 1800,
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        raise AcceptanceFailure(f"command could not start: {argv[0]}: {exc}") from exc
    assert process.stdout is not None and process.stderr is not None
    process_group_id = process.pid
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    totals = {"stdout": 0, "stderr": 0}
    overflow = threading.Event()
    stream_errors: list[Exception] = []

    def drain(name: str, stream: Any) -> None:
        try:
            while chunk := stream.read(64 * 1024):
                totals[name] += len(chunk)
                remaining = max(0, MAX_CAPTURE_BYTES - len(captured[name]))
                if remaining:
                    captured[name].extend(chunk[:remaining])
                if totals[name] > MAX_CAPTURE_BYTES:
                    overflow.set()
        except (OSError, ValueError) as exc:
            stream_errors.append(exc)

    readers = [
        threading.Thread(target=drain, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=drain, args=("stderr", process.stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + timeout
    timed_out = False
    while process.poll() is None:
        if overflow.is_set():
            _terminate_process_group(process, process_group_id)
            break
        if time.monotonic() >= deadline:
            timed_out = True
            _terminate_process_group(process, process_group_id)
            break
        time.sleep(0.01)
    for reader in readers:
        reader.join(timeout=0.1)
    if any(reader.is_alive() for reader in readers):
        _terminate_process_group(process, process_group_id)
        for reader in readers:
            reader.join(timeout=5)
        if any(reader.is_alive() for reader in readers):
            raise AcceptanceFailure(f"command streams did not close: {argv[0]}")
    if stream_errors:
        raise AcceptanceFailure(f"command stream could not be read: {argv[0]}: {stream_errors[0]}")
    if timed_out:
        raise AcceptanceFailure(f"command timed out: {argv[0]}")
    for name in ("stdout", "stderr"):
        if totals[name] > MAX_CAPTURE_BYTES:
            raise AcceptanceFailure(f"command {name} exceeded the acceptance bound: {argv[0]}")
    try:
        stdout = captured["stdout"].decode("utf-8")
        stderr = captured["stderr"].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AcceptanceFailure(f"command output was not UTF-8: {argv[0]}") from exc
    completed = subprocess.CompletedProcess(
        args=argv,
        returncode=process.returncode,
        stdout=stdout,
        stderr=stderr,
    )
    receipt = {
        "argv": argv,
        "returncode": completed.returncode,
        "stdout_sha256": _digest(completed.stdout),
        "stderr_sha256": _digest(completed.stderr),
    }
    if completed.returncode not in accepted:
        detail = completed.stderr.strip()[-2000:] or completed.stdout.strip()[-2000:]
        raise AcceptanceFailure(
            f"command failed with exit {completed.returncode}: {argv!r}: {detail}"
        )
    return completed, receipt


def _run_json(
    argv: list[str],
    *,
    cwd: Path,
    accepted: frozenset[int] | set[int] = frozenset({0}),
    timeout: float = 1800,
) -> tuple[dict[str, Any], dict[str, Any]]:
    completed, receipt = _run(argv, cwd=cwd, accepted=accepted, timeout=timeout)
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AcceptanceFailure(f"command returned invalid JSON: {argv!r}") from exc
    if not isinstance(value, dict):
        raise AcceptanceFailure(f"command JSON root is not an object: {argv!r}")
    return value, receipt


def _write_legacy_records(vault: Path) -> tuple[Path, Path]:
    inherited = vault / "Designs" / "Inherited.md"
    terminal = vault / "Designs" / "+Deprecated" / "Legacy.md"
    inherited.parent.mkdir(parents=True, exist_ok=True)
    terminal.parent.mkdir(parents=True, exist_ok=True)
    inherited.write_text(
        "---\n"
        "type: living/design\n"
        "tags:\n"
        "  - design\n"
        "status: shaping\n"
        "---\n\n"
        "# Inherited missing key\n",
        encoding="utf-8",
    )
    terminal.write_text(
        "---\n"
        "type: living/design\n"
        "tags:\n"
        "  - design\n"
        "status: deprecated\n"
        "---\n\n"
        "# Legacy terminal design\n\n"
        "> [!info] Deprecated — retained upgrade fixture\n",
        encoding="utf-8",
    )
    return inherited, terminal


def _payload(value: dict[str, Any]) -> dict[str, Any]:
    result = value.get("result")
    return result if isinstance(result, dict) else value


def _findings(value: dict[str, Any]) -> list[dict[str, Any]]:
    findings = _payload(value).get("findings")
    if not isinstance(findings, list) or not all(isinstance(item, dict) for item in findings):
        raise AcceptanceFailure("vault check did not return a findings array")
    return findings


def finding_identity(finding: dict[str, Any]) -> tuple[str, str, str]:
    values = (finding.get("severity"), finding.get("check"), finding.get("file"))
    if not all(value is None or isinstance(value, str) for value in values):
        raise AcceptanceFailure("vault check finding identity is invalid")
    return str(values[0] or ""), str(values[1] or ""), str(values[2] or "")


def finding_delta(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
) -> dict[str, list[tuple[str, str, str]]]:
    before_ids = {finding_identity(item) for item in before}
    after_ids = {finding_identity(item) for item in after}
    return {
        "added": sorted(after_ids - before_ids),
        "removed": sorted(before_ids - after_ids),
        "retained": sorted(before_ids & after_ids),
    }


def _require_ok_envelope(value: dict[str, Any], command: str) -> dict[str, Any]:
    if value.get("status") != "ok":
        raise AcceptanceFailure(f"{command} did not return status=ok")
    result = value.get("result")
    if not isinstance(result, dict):
        raise AcceptanceFailure(f"{command} did not return an object result")
    return result


def _target_versions(source: Path) -> tuple[str, str]:
    core = (source / "src" / "brain-core" / "VERSION").read_text(encoding="utf-8").strip()
    launcher = (source / "cli" / "brain").read_text(encoding="utf-8")
    matched = re.search(r'^BRAIN_CLI_VERSION="([0-9]+\.[0-9]+\.[0-9]+)"$', launcher, re.MULTILINE)
    if not core or matched is None:
        raise AcceptanceFailure("target source does not declare Core and CLI versions")
    return core, matched.group(1)


def run_acceptance(vault: Path, target_source: Path) -> dict[str, Any]:
    vault = vault.resolve()
    target_source = target_source.resolve()
    if (vault / ".brain-core" / "VERSION").read_text(encoding="utf-8").strip() != "0.54.0":
        raise AcceptanceFailure("historical acceptance requires an exact v0.54.0 baseline")
    target_core, target_cli = _target_versions(target_source)
    inherited, terminal = _write_legacy_records(vault)
    commands: list[dict[str, Any]] = []

    before, receipt = _run_json(
        [
            sys.executable,
            str(vault / ".brain-core" / "scripts" / "check.py"),
            "--vault",
            str(vault),
            "--json",
        ],
        cwd=vault,
        accepted={0, 1, 2},
    )
    commands.append(receipt)
    before_findings = _findings(before)
    if not any(item.get("file") == "Designs/Inherited.md" for item in before_findings):
        raise AcceptanceFailure("controlled inherited finding was not present before upgrade")
    if any(item.get("file") == "Designs/+Deprecated/Legacy.md" for item in before_findings):
        raise AcceptanceFailure("terminal fixture was not valid under the v0.54 check contract")

    _, receipt = _run(
        [
            "bash",
            str(target_source / "install.sh"),
            "--non-interactive",
            "--acknowledge-global-cli-cutover",
            str(vault),
        ],
        cwd=vault,
    )
    commands.append(receipt)

    installed_core = (vault / ".brain-core" / "VERSION").read_text(encoding="utf-8").strip()
    if installed_core != target_core:
        raise AcceptanceFailure(f"installed Core {installed_core!r} does not match {target_core!r}")
    cli_version, receipt = _run(["brain", "--version"], cwd=vault)
    commands.append(receipt)
    if cli_version.stdout.strip() != f"brain {target_cli}":
        raise AcceptanceFailure("installed CLI version does not match the target source")

    upgrade_log = json.loads(
        (vault / ".brain" / "local" / "last-upgrade.json").read_text(encoding="utf-8")
    )
    if upgrade_log.get("runtime_readiness", {}).get("outcome") != "ok":
        raise AcceptanceFailure("upgrade did not record completed runtime readiness")
    marker = (vault / ".brain" / "local" / ".migrated-version").read_text(encoding="utf-8").strip()
    if marker != target_core:
        raise AcceptanceFailure("upgrade did not record complete migration coverage")
    ledger = json.loads(
        (vault / ".brain" / "local" / "migrations.json").read_text(encoding="utf-8")
    )
    recorded = ledger.get("migrations")
    if not isinstance(recorded, dict):
        raise AcceptanceFailure("migration ledger is invalid")
    missing_records = sorted(REQUIRED_MIGRATION_RECORDS - set(recorded))
    if missing_records:
        raise AcceptanceFailure(f"migration ledger is missing required records: {missing_records}")

    after, receipt = _run_json(
        [
            "brain",
            "vault",
            "check",
            "--vault",
            str(vault),
            "--request-json",
            "{}",
            "--json",
        ],
        cwd=vault,
    )
    commands.append(receipt)
    after_findings = _findings(after)
    delta = finding_delta(before_findings, after_findings)
    if delta["added"]:
        raise AcceptanceFailure(f"upgrade introduced unexplained findings: {delta['added']}")
    if not any(item.get("file") == "Designs/Inherited.md" for item in after_findings):
        raise AcceptanceFailure("controlled inherited finding was not preserved after upgrade")
    if any(item.get("file") == "Designs/+Deprecated/Legacy.md" for item in after_findings):
        raise AcceptanceFailure("terminal-status compatibility finding remains after upgrade")
    if "key:" not in terminal.read_text(encoding="utf-8"):
        raise AcceptanceFailure("terminal-status compatibility migration did not add a key")
    if "key:" in inherited.read_text(encoding="utf-8"):
        raise AcceptanceFailure("bounded compatibility migration changed the inherited root record")

    session, receipt = _run_json(
        ["brain", "session", "start", "--request-json", "{}", "--json"],
        cwd=vault,
    )
    commands.append(receipt)
    session_result = _require_ok_envelope(session, "session.start")
    if session_result.get("brain_core_version") != target_core:
        raise AcceptanceFailure("first session.start selected the wrong Brain Core")

    dry_cleanup, receipt = _run_json(
        ["brain", "runtime", "remove-orphans", "--dry-run", "--json"],
        cwd=vault,
    )
    commands.append(receipt)
    _require_ok_envelope(dry_cleanup, "runtime.remove-orphans dry run")
    cleanup, receipt = _run_json(
        ["brain", "runtime", "remove-orphans", "--json"],
        cwd=vault,
    )
    commands.append(receipt)
    _require_ok_envelope(cleanup, "runtime.remove-orphans")

    doctor, receipt = _run_json(
        [
            "brain",
            "doctor",
            "--vault",
            str(vault),
            "--request-json",
            json.dumps({"current_vault": str(vault)}, separators=(",", ":")),
            "--json",
        ],
        cwd=vault,
    )
    commands.append(receipt)
    doctor_result = _require_ok_envelope(doctor, "brain.doctor")
    if doctor_result.get("machine", {}).get("tidy") is not True:
        raise AcceptanceFailure("machine runtime state is not tidy after canonical cleanup")

    mcp, receipt = _run_json(
        [sys.executable, "/usr/local/lib/brain-lab/mcp_probe.py", "--vault", str(vault)],
        cwd=vault,
    )
    commands.append(receipt)
    if mcp.get("read_only_round_trip") != "tools/list" or not mcp.get("tool_count"):
        raise AcceptanceFailure("MCP read-only health probe did not pass")

    paths, receipt = _run_json(
        [sys.executable, "/usr/local/lib/brain-lab/active_path_probe.py"],
        cwd=vault,
    )
    commands.append(receipt)
    if paths.get("safe") is not True:
        raise AcceptanceFailure("active Brain paths contain host-state leakage")

    return {
        "schema": "brain-lab.historical-upgrade-acceptance/1",
        "historical_core_version": "0.54.0",
        "target_core_version": target_core,
        "target_cli_version": target_cli,
        "migration_records": sorted(REQUIRED_MIGRATION_RECORDS),
        "validation": {
            "before_count": len(before_findings),
            "after_count": len(after_findings),
            "added": [list(item) for item in delta["added"]],
            "removed": [list(item) for item in delta["removed"]],
            "retained": [list(item) for item in delta["retained"]],
        },
        "first_session_ready": True,
        "runtime_tidy": True,
        "mcp_tool_count": mcp["tool_count"],
        "active_paths_safe": True,
        "commands": commands,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault", required=True, type=Path)
    parser.add_argument("--target-source", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = run_acceptance(args.vault, args.target_source)
    except (AcceptanceFailure, OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
