#!/usr/bin/env python3
"""Run a bounded exact-release-to-target Brain upgrade acceptance probe."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
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
RETIRED_CORE_SKILLS = ("code-review", "superpowers-brain", "swarm-test")
EXPECTED_CUSTOM_PROFILE_ALLOW = {
    "artefact.create",
    "artefact.read",
    "invocation.read",
    "resource.create",
    "resource.read",
    "runtime.read-environment",
    "vault.read-file",
    "vault.read-router",
    "workspace.read",
}
CUSTOM_BOOTSTRAP_PROSE = "Keep this user-authored bootstrap guidance unchanged."
CUSTOM_MEMORY_PROSE = "\nUpgrade acceptance user-memory marker.\n"


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
        "created: 2026-08-28T00:00:00+00:00\n"
        "modified: 2026-08-28T00:00:00+00:00\n"
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
        "created: 2026-08-28T00:00:00+00:00\n"
        "modified: 2026-08-28T00:00:00+00:00\n"
        "---\n\n"
        "# Legacy terminal design\n\n"
        "> [!info] Deprecated — retained upgrade fixture\n",
        encoding="utf-8",
    )
    return inherited, terminal


def _yaml_helpers(target_source: Path):
    scripts = str(target_source / "src" / "brain-core" / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from _common._yaml import dump_yaml_text, load_mapping_file

    return dump_yaml_text, load_mapping_file


def _prepare_upgrade_fixture(vault: Path, target_source: Path) -> dict[str, str]:
    """Add representative legacy authority, bootstrap, and user-owned state."""

    dump_yaml_text, load_mapping_file = _yaml_helpers(target_source)
    old_defaults = load_mapping_file(vault / ".brain-core" / "defaults" / "config.yaml")
    legacy_profiles = json.loads(json.dumps(old_defaults["vault"]["profiles"]))
    if set(legacy_profiles) != {"reader", "contributor", "operator"}:
        raise AcceptanceFailure("historical built-in profiles are not the expected three-profile set")
    legacy_profiles["author"] = {
        "allow": ["brain_read", "brain_create"],
        "description": "User-owned read and create authority.",
    }
    config = {
        "vault": {
            "brain_name": "Upgrade Acceptance Brain",
            "profiles": legacy_profiles,
        },
        "defaults": {"default_profile": "operator"},
    }
    config_path = vault / ".brain" / "config.yaml"
    config_path.write_text(dump_yaml_text(config), encoding="utf-8")

    for filename in ("AGENTS.md", "CLAUDE.md"):
        path = vault / filename
        content = path.read_text(encoding="utf-8")
        if "brain_session" not in content:
            raise AcceptanceFailure(f"historical {filename} does not carry the expected bootstrap")
        path.write_text(f"{content.rstrip()}\n\n{CUSTOM_BOOTSTRAP_PROSE}\n", encoding="utf-8")

    memory = vault / "_Config" / "Memories" / "README.md"
    original_memory = memory.read_text(encoding="utf-8")
    memory.write_text(original_memory + CUSTOM_MEMORY_PROSE, encoding="utf-8")
    user_skill = vault / "_Config" / "Skills" / "upgrade-acceptance-user-skill" / "SKILL.md"
    user_skill.parent.mkdir(parents=True)
    user_skill.write_text(
        "---\n"
        "name: upgrade-acceptance-user-skill\n"
        "description: User-owned upgrade acceptance skill.\n"
        "---\n\n"
        "# Upgrade acceptance user skill\n",
        encoding="utf-8",
    )
    return {
        str(memory.relative_to(vault)): memory.read_text(encoding="utf-8"),
        str(user_skill.relative_to(vault)): user_skill.read_text(encoding="utf-8"),
    }


def _portable_manifest(vault: Path) -> dict[str, tuple[str, str]]:
    """Hash upgrade-owned and user-owned state, excluding local runtime records."""

    manifest = {}
    for path in sorted(vault.rglob("*")):
        relative = path.relative_to(vault)
        if relative.parts[:2] == (".brain", "local"):
            continue
        key = relative.as_posix()
        if path.is_symlink():
            manifest[key] = ("symlink", os.readlink(path))
        elif path.is_file():
            manifest[key] = ("file", hashlib.sha256(path.read_bytes()).hexdigest())
        elif path.is_dir():
            manifest[key] = ("directory", "")
    return manifest


def _assert_migrated_user_state(
    vault: Path,
    target_source: Path,
    preserved: dict[str, str],
) -> None:
    _, load_mapping_file = _yaml_helpers(target_source)
    config = load_mapping_file(vault / ".brain" / "config.yaml")
    profiles = config["vault"]["profiles"]
    current_defaults = load_mapping_file(
        vault / ".brain-core" / "defaults" / "config.yaml"
    )["vault"]["profiles"]
    expected_names = set(current_defaults) | {"author"}
    if set(profiles) != expected_names:
        raise AcceptanceFailure("upgrade did not produce the five built-ins plus the custom profile")
    for name, expected in current_defaults.items():
        if profiles[name]["allow"] != expected["allow"]:
            raise AcceptanceFailure(f"upgrade did not migrate built-in profile {name!r} exactly")
    if set(profiles["author"]["allow"]) != EXPECTED_CUSTOM_PROFILE_ALLOW:
        raise AcceptanceFailure("upgrade changed or widened the custom profile unexpectedly")
    if profiles["author"].get("description") != "User-owned read and create authority.":
        raise AcceptanceFailure("upgrade did not preserve custom profile metadata")
    if config["vault"].get("brain_name") != "Upgrade Acceptance Brain":
        raise AcceptanceFailure("upgrade did not preserve the shared Brain name")
    if config.get("defaults", {}).get("default_profile") != "operator":
        raise AcceptanceFailure("upgrade did not preserve the configured default profile")

    for filename in ("AGENTS.md", "CLAUDE.md"):
        content = (vault / filename).read_text(encoding="utf-8")
        if "Call MCP `session.start`" not in content or "brain_session" in content:
            raise AcceptanceFailure(f"upgrade did not rewrite the {filename} bootstrap")
        if CUSTOM_BOOTSTRAP_PROSE not in content:
            raise AcceptanceFailure(f"upgrade did not preserve custom prose in {filename}")
    for relative, expected in preserved.items():
        if (vault / relative).read_text(encoding="utf-8") != expected:
            raise AcceptanceFailure(f"upgrade did not preserve user-owned content: {relative}")
    for skill in RETIRED_CORE_SKILLS:
        if (vault / ".brain-core" / "skills" / skill).exists():
            raise AcceptanceFailure(f"retired Core skill remains installed: {skill}")


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


def run_acceptance(
    vault: Path,
    target_source: Path,
    historical_version: str,
) -> dict[str, Any]:
    vault = vault.resolve()
    target_source = target_source.resolve()
    installed_historical = (
        vault / ".brain-core" / "VERSION"
    ).read_text(encoding="utf-8").strip()
    if installed_historical != historical_version:
        raise AcceptanceFailure(
            "historical acceptance requires the declared exact baseline "
            f"{historical_version}, found {installed_historical}"
        )
    target_core, target_cli = _target_versions(target_source)
    preserved = _prepare_upgrade_fixture(vault, target_source)
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
        raise AcceptanceFailure("terminal fixture was not valid under the historical check contract")

    cli_binary = shutil.which("brain")
    if cli_binary is None:
        raise AcceptanceFailure("historical baseline did not install the brain CLI")
    _, receipt = _run(
        [
            sys.executable,
            str(target_source / "cli" / "_distribution.py"),
            str(target_source),
            cli_binary,
        ],
        cwd=vault,
    )
    commands.append(receipt)

    upgrade_request = json.dumps(
        {"acknowledge_global_cli_cutover": True},
        separators=(",", ":"),
    )
    portable_before_preview = _portable_manifest(vault)
    preview, receipt = _run_json(
        [
            "brain",
            "--vault",
            str(vault),
            "upgrade",
            "--request-json",
            upgrade_request,
            "--dry-run",
            "--json",
        ],
        cwd=vault,
    )
    commands.append(receipt)
    preview_result = _require_ok_envelope(preview, "brain.upgrade dry run")
    if preview.get("committed_effects") != []:
        raise AcceptanceFailure("brain.upgrade dry run reported committed effects")
    if preview_result.get("status") != "planned":
        raise AcceptanceFailure("brain.upgrade dry run did not return a planned result")
    if preview_result.get("old_version") != historical_version:
        raise AcceptanceFailure("brain.upgrade dry run selected the wrong historical version")
    if preview_result.get("new_version") != target_core:
        raise AcceptanceFailure("brain.upgrade dry run selected the wrong target version")
    preview_migrations = preview_result.get("migrations")
    if not isinstance(preview_migrations, list) or not REQUIRED_MIGRATION_RECORDS <= set(
        preview_migrations
    ):
        raise AcceptanceFailure("brain.upgrade dry run omitted required migration previews")
    if not all(
        isinstance(preview_result.get(field), int) and preview_result[field] > 0
        for field in ("files_modified", "files_removed")
    ):
        raise AcceptanceFailure("brain.upgrade dry run omitted Core replacement effects")
    if _portable_manifest(vault) != portable_before_preview:
        raise AcceptanceFailure("brain.upgrade dry run changed portable vault state")

    applied, receipt = _run_json(
        [
            "brain",
            "--vault",
            str(vault),
            "upgrade",
            "--request-json",
            upgrade_request,
            "--json",
        ],
        cwd=vault,
        timeout=1800,
    )
    commands.append(receipt)
    applied_result = _require_ok_envelope(applied, "brain.upgrade")
    if applied_result.get("status") != "changed":
        raise AcceptanceFailure("brain.upgrade did not report a changed result")
    if not applied.get("committed_effects"):
        raise AcceptanceFailure("brain.upgrade did not report its committed effects")

    installed_core = (vault / ".brain-core" / "VERSION").read_text(encoding="utf-8").strip()
    if installed_core != target_core:
        raise AcceptanceFailure(f"installed Core {installed_core!r} does not match {target_core!r}")
    cli_version, receipt = _run(["brain", "--version"], cwd=vault)
    commands.append(receipt)
    if cli_version.stdout.strip() != f"brain {target_cli}":
        raise AcceptanceFailure("installed CLI version does not match the target source")

    _assert_migrated_user_state(vault, target_source, preserved)

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
    skill_names = {
        item.get("name")
        for item in session_result.get("skills", [])
        if isinstance(item, dict)
    }
    if "upgrade-acceptance-user-skill" not in skill_names:
        raise AcceptanceFailure("session.start did not preserve the user-owned skill")

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
        "historical_core_version": historical_version,
        "target_core_version": target_core,
        "target_cli_version": target_cli,
        "upgrade_preview_no_portable_effects": True,
        "profiles_migrated": True,
        "bootstraps_migrated": True,
        "user_state_preserved": True,
        "retired_core_skills_absent": list(RETIRED_CORE_SKILLS),
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
    parser.add_argument("--historical-version", required=True)
    args = parser.parse_args()
    try:
        result = run_acceptance(
            args.vault,
            args.target_source,
            args.historical_version,
        )
    except (AcceptanceFailure, OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
