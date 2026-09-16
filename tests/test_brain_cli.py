"""Released CLI noun/verb grammar and composed execution boundary."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "cli" / "brain"
WINDOWS_CLI = REPO_ROOT / "cli" / "brain.cmd"
CORE_VERSION = (REPO_ROOT / "src" / "brain-core" / "VERSION").read_text().strip()
CLI_TEXT = CLI.read_text()
WINDOWS_CLI_TEXT = WINDOWS_CLI.read_text()


def _shell_value(name):
    match = re.search(rf'^{name}="([^"]+)"$', CLI_TEXT, re.MULTILINE)
    assert match
    return match.group(1)


CLI_VERSION = _shell_value("BRAIN_CLI_VERSION")


def _run(tmp_path, *args, cwd=None, env=None):
    state = tmp_path / "state"
    environment = {
        **os.environ,
        "BRAIN_CLI_BUNDLE": str(REPO_ROOT),
        "XDG_STATE_HOME": str(state),
        "PYTHONPYCACHEPREFIX": str(tmp_path / "pycache"),
        "BRAIN_VAULT_ROOT": "",
        "BRAIN_WORKSPACE_DIR": "",
        **(env or {}),
    }
    return subprocess.run(
        ["bash", str(CLI), *args],
        cwd=cwd or tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _brain(tmp_path, version="0.55.0"):
    root = tmp_path / "Brain"
    scripts = root / ".brain-core" / "scripts"
    scripts.mkdir(parents=True)
    (root / ".brain-core" / "VERSION").write_text(version + "\n")
    return root


def _command_stub(brain):
    script = brain / ".brain-core" / "scripts" / "command.py"
    script.write_text(
        """#!/usr/bin/env python3
import json, sys
noun, verb = sys.argv[1:3]
command = noun + '.' + verb
raw = sys.argv[sys.argv.index('--request-json') + 1]
request = json.loads(raw)
base = {'schema':'brain.command-result/1','command':command,'command_version':1,'status':'ok','warnings':[],'committed_effects':[]}
if command == 'command.describe':
    target = request['target_command_id']
    base['result'] = {
        'catalogue_schema':'brain.command-catalogue/1',
        'catalogue_fingerprint':'sha256:' + '1' * 64,
        'command_id':target,
        'command_version':1,
        'summary':'Check one vault.',
        'dependency_tier':'portable',
    }
elif command == 'command.list':
    base['result'] = {
        'catalogue_schema':'brain.command-catalogue/1',
        'catalogue_fingerprint':'sha256:' + '1' * 64,
        'entries':[{
            'command_id':'vault.check','command_version':1,
            'summary':'Check one vault.','dependency_tier':'portable'
        }],
        'next_cursor':None,
    }
elif command == 'vault.check':
    base['result'] = {'request':request,'vault':sys.argv[sys.argv.index('--vault') + 1]}
else:
    raise SystemExit(4)
print(json.dumps(base, separators=(',', ':')))
"""
    )


def test_release_versions_move_together():
    assert CLI_VERSION
    assert _shell_value("BRAIN_INSTALL_REF") == f"v{CORE_VERSION}"


def test_windows_bootloader_defaults_to_the_installed_distribution():
    default = (
        'set "DISTRIBUTION_ROOT=%SELF_DIR%..\\lib\\brain-cli\\'
        '%BRAIN_CLI_VERSION%"'
    )

    assert default in WINDOWS_CLI_TEXT
    assert WINDOWS_CLI_TEXT.index(default) < WINDOWS_CLI_TEXT.index(
        "if defined BRAIN_CLI_BUNDLE"
    )


def test_windows_python_probe_does_not_escape_comparison_inside_quotes():
    assert "sys.version_info < (3, 12)" in WINDOWS_CLI_TEXT
    assert "sys.version_info ^< (3, 12)" not in WINDOWS_CLI_TEXT


def test_version_and_launcher_discovery_need_no_selected_brain(tmp_path):
    version = _run(tmp_path, "--version")
    structural = _run(tmp_path, "version", "--json")
    listing = _run(tmp_path, "command", "list", "--owner", "launcher", "--json")

    assert version.returncode == 0
    assert version.stdout.strip() == f"brain {CLI_VERSION}"
    payload = json.loads(structural.stdout)
    assert structural.returncode == 0
    assert payload["command"] == "brain.version"
    assert payload["result"]["cli_version"] == CLI_VERSION
    commands = json.loads(listing.stdout)
    assert listing.returncode == 0
    assert commands["schema"] == "brain.local-command-list/2"
    command_ids = [entry["command_id"] for entry in commands["entries"]]
    assert len(command_ids) == len(set(command_ids))
    assert "brain.version" in command_ids
    assert len(commands["entries"]) == 24
    by_id = {entry["command_id"]: entry for entry in commands["entries"]}
    assert by_id["brain.version"]["entry_point"] == ["brain", "version"]
    assert by_id["runtime.inspect"]["entry_point"] == [
        "brain",
        "runtime",
        "inspect",
    ]
    assert {
        "brain.backfill",
        "brain.prune",
        "machine.migrate-legacy",
        "machine.prune-runtimes",
        "runtime.resolve",
        "runtime.resolve-runnable",
    }.isdisjoint(command_ids)


def test_help_and_parser_expose_application_grammar_and_launcher_entry_points(tmp_path):
    help_result = _run(tmp_path, "--help")
    legacy = _run(tmp_path, "check")
    canonical_id_spelling = _run(tmp_path, "brain", "version")

    assert help_result.returncode == 0
    assert "<noun> <verb>" in help_result.stdout
    assert "<launcher-entry-point>" in help_result.stdout
    assert "brain check" not in help_result.stdout
    assert legacy.returncode == canonical_id_spelling.returncode == 2
    assert "one installed launcher entry point" in legacy.stderr
    assert "uses the launcher entry point: brain version" in canonical_id_spelling.stderr


def test_pre_cutover_brain_exposes_launcher_recovery_but_not_application_emulation(
    tmp_path,
):
    brain = _brain(tmp_path, "0.54.59")

    result = _run(
        tmp_path,
        "vault",
        "check",
        "--vault",
        str(brain),
        "--json",
    )

    payload = json.loads(result.stdout)
    assert result.returncode == 4
    assert payload["error"]["code"] == "upgrade_required"
    assert payload["error"]["details"]["brain_core_version"] == "0.54.59"
    assert payload["error"]["next_action"]["command"] == "brain.upgrade"


def test_application_dispatch_discovers_then_invokes_selected_brain_owner(tmp_path):
    brain = _brain(tmp_path)
    _command_stub(brain)

    result = _run(
        tmp_path,
        "vault",
        "check",
        "--vault",
        str(brain),
        "--request-json",
        '{"severity":"warning"}',
        "--json",
    )

    payload = json.loads(result.stdout)
    assert result.returncode == 0, result.stderr
    assert payload["command"] == "vault.check"
    assert payload["result"]["request"] == {"severity": "warning"}
    assert payload["result"]["vault"] == str(brain)


def test_runtime_inspect_uses_selected_brain_with_empty_request(tmp_path):
    brain = _brain(tmp_path)
    requirements = brain / ".brain-core" / "brain_mcp" / "requirements.txt"
    requirements.parent.mkdir()
    requirements.write_text("", encoding="utf-8")
    requirements.with_name("requirements-semantic.txt").write_text("", encoding="utf-8")

    result = _run(
        tmp_path,
        "runtime",
        "inspect",
        "--vault",
        str(brain),
        "--request-json",
        "{}",
        "--json",
    )

    payload = json.loads(result.stdout)
    assert result.returncode == 0, result.stderr
    assert payload["command"] == "runtime.inspect"
    assert payload["result"]["vault_root"] == str(brain.resolve())


def test_composed_discovery_preserves_application_and_launcher_owners(tmp_path):
    brain = _brain(tmp_path)
    _command_stub(brain)

    result = _run(
        tmp_path,
        "command",
        "list",
        "--owner",
        "all",
        "--vault",
        str(brain),
        "--json",
    )

    payload = json.loads(result.stdout)
    assert result.returncode == 0, result.stderr
    by_id = {entry["command_id"]: entry for entry in payload["entries"]}
    assert by_id["vault.check"]["owner"] == "application"
    assert by_id["brain.version"]["owner"] == "launcher"
    assert payload["catalogues"]["application"]["schema"] == "brain.command-catalogue/1"
    assert payload["catalogues"]["launcher"]["schema"] == "brain.launcher-catalogue/1"


def test_launcher_description_carries_exact_schema_and_example(tmp_path):
    result = _run(
        tmp_path,
        "command",
        "describe",
        "brain.upgrade",
        "--owner",
        "launcher",
        "--json",
    )

    payload = json.loads(result.stdout)
    assert result.returncode == 0
    assert payload["command_id"] == "brain.upgrade"
    assert payload["command_version"] == 2
    schema = json.loads(payload["payload"]["request_schema_json"])
    assert "acknowledge_global_cli_cutover" in schema["properties"]
    assert "excluded_stale_brain_ids" in schema["properties"]


def test_valid_error_and_stderr_survive_the_released_cli(tmp_path):
    brain = _brain(tmp_path)
    _command_stub(brain)
    script = brain / ".brain-core/scripts/command.py"
    script.write_text(
        script.read_text().replace(
            "print(json.dumps(base, separators=(',', ':')))",
            """print('PRIVATE dependency diagnostic', file=sys.stderr)
if command == 'vault.check':
    base.update(status='error', result=None, error={
        'code':'internal_error', 'message':'Bounded failure.',
        'effects':'none', 'retryable':False})
print(json.dumps(base, separators=(',', ':')))
sys.exit(4 if command == 'vault.check' else 0)""",
        )
    )
    result = _run(tmp_path, "vault", "check", "--vault", str(brain), "--json")
    payload = json.loads(result.stdout)
    assert result.returncode == 4
    assert payload["command"] == "vault.check"
    assert payload["error"]["message"] == "Bounded failure."
    assert result.stderr == ""
    assert "PRIVATE" not in result.stdout


def test_released_cli_checks_a_disposable_installed_brain(
    command_vault_clone, tmp_path
):
    result = _run(
        tmp_path,
        "vault",
        "check",
        "--vault",
        str(command_vault_clone.vault_root),
        "--json",
    )
    payload = json.loads(result.stdout)
    assert result.returncode == 0, payload
    assert payload["command"] == "vault.check"
    assert payload["status"] == "ok"
    assert result.stderr == ""
