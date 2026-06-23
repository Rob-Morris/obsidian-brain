"""Integration tests for `cli/brain` — the thin dispatch layer (DD-049)."""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import launcher_discovery_path


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = REPO_ROOT / "cli" / "brain"
SCRIPTS_DIR = REPO_ROOT / "src" / "brain-core" / "scripts"
CLI_BODY = CLI_PATH.read_text()
BRAIN_CORE_VERSION = (REPO_ROOT / "src" / "brain-core" / "VERSION").read_text().strip()


def _cli_shell_var(name: str) -> str:
    match = re.search(rf'{name}="([^"]+)"', CLI_BODY)
    assert match, f"Could not locate {name} in cli/brain"
    return match.group(1)


BRAIN_CLI_VERSION = _cli_shell_var("BRAIN_CLI_VERSION")

# Public dispatch contract. Legacy compatibility shims must not remain here.
PUBLIC_DISPATCH_CONTRACT = [
    "check", "create", "edit", "rename",
    "setup", "configure", "repair", "upgrade",
    "session", "read", "migrate-naming", "fix-links",
]
DISPATCH_COMPAT = []
GENERIC_DISPATCH_CONTRACT = [sub for sub in PUBLIC_DISPATCH_CONTRACT if sub != "session"] + DISPATCH_COMPAT
SCRIPT_CONTRACT = PUBLIC_DISPATCH_CONTRACT + DISPATCH_COMPAT


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _build_fake_vault(root: Path) -> Path:
    """Build a minimal vault that satisfies the CLI's discovery and dispatch."""
    vault = root / "vault"
    scripts = vault / ".brain-core" / "scripts"
    common = scripts / "_common"
    common.mkdir(parents=True)
    (vault / ".brain-core" / "VERSION").write_text("0.99.0\n")

    # _venv.py stub: support `python` and `runnable-python` subcommands by
    # printing the running interpreter, matching the contract the CLI uses.
    (common / "_venv.py").write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "# Echo the running interpreter regardless of subcommand or args.\n"
        "print(sys.executable)\n"
    )

    # Echo script — every dispatched subcommand can point at this body.
    echo_body = (
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "print(json.dumps({'argv': sys.argv[1:]}))\n"
    )
    for name in SCRIPT_CONTRACT:
        script = scripts / f"{name.replace('-', '_')}.py"
        script.write_text(echo_body)
    return vault


def _write_machine_resolution_runtime(
    home: Path,
    payload: dict,
    *,
    expected_workspace_env: str | None = None,
) -> Path:
    runtime = home / ".brain" / "resolution-runtime"
    runtime.mkdir(parents=True)
    guard = ""
    if expected_workspace_env is not None:
        guard = (
            "import os\n"
            f"expected = {expected_workspace_env!r}\n"
            "if os.environ.get('BRAIN_WORKSPACE_DIR') != expected:\n"
            "    print(json.dumps({'status': 'degraded', 'message': 'workspace env mismatch', "
            "'session_resolution': {'code': 'invalid_binding'}, 'recovery': {'action': 'fix', 'command': 'fix'}}))\n"
            "    raise SystemExit(0)\n"
        )
    (runtime / "resolve_brain.py").write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        f"{guard}"
        f"print(json.dumps({payload!r}))\n"
    )
    (runtime / "VERSION").write_text("test\n")
    return runtime


def _build_machine_helper_vault(root: Path) -> Path:
    """Vault carrying the real machine-helper substrate used by `brain doctor`."""
    vault = root / "machine-vault"
    scripts = vault / ".brain-core" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copytree(SCRIPTS_DIR / "_common", scripts / "_common")
    shutil.copytree(SCRIPTS_DIR / "_bootstrap", scripts / "_bootstrap")
    shutil.copytree(SCRIPTS_DIR / "_lifecycle", scripts / "_lifecycle")
    shutil.copytree(SCRIPTS_DIR / "_machine", scripts / "_machine")
    shutil.copytree(SCRIPTS_DIR / "_portable", scripts / "_portable")
    shutil.copy2(SCRIPTS_DIR / "check.py", scripts / "check.py")
    shutil.copy2(SCRIPTS_DIR / "doctor.py", scripts / "doctor.py")
    shutil.copy2(SCRIPTS_DIR / "doctor_machine.py", scripts / "doctor_machine.py")
    shutil.copy2(SCRIPTS_DIR / "machine.py", scripts / "machine.py")
    shutil.copy2(SCRIPTS_DIR / "vault_registry.py", scripts / "vault_registry.py")
    shutil.copy2(SCRIPTS_DIR / "_repair_common.py", scripts / "_repair_common.py")
    shutil.copy2(SCRIPTS_DIR / "_lifecycle_common.py", scripts / "_lifecycle_common.py")
    (vault / ".brain-core" / "VERSION").write_text(BRAIN_CORE_VERSION + "\n")
    brain_mcp = vault / ".brain-core" / "brain_mcp"
    brain_mcp.mkdir(parents=True)
    (brain_mcp / "requirements.txt").write_text("mcp==1.0.0\n")
    return vault


def _write_doctor_check_stub(
    vault: Path,
    *,
    check_name: str = "doctor-stub",
    message: str = "Vault drift",
    repair_scope: str = "registry",
    severity: str = "error",
    exit_code: int = 2,
) -> None:
    payload = {
        "vault_root": str(vault),
        "brain_core_version": "0.99.0",
        "checked_at": "2026-05-26T00:00:00+00:00",
        "summary": {"errors": 1 if severity == "error" else 0, "warnings": 1 if severity == "warning" else 0, "info": 0},
        "findings": [
            {
                "check": check_name,
                "severity": severity,
                "file": "Notes/example.md",
                "message": message,
                "repair": {
                    "scope": repair_scope,
                    "description": f"Repair {repair_scope}",
                    "command": f"python3 {vault}/.brain-core/scripts/repair.py {repair_scope} --vault {vault}",
                },
            }
        ],
    }
    (vault / ".brain-core" / "scripts" / "check.py").write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import sys\n"
        f"payload = {payload!r}\n"
        "print(json.dumps(payload))\n"
        f"raise SystemExit({exit_code})\n"
    )


def _run_cli(*args, cwd=None, env_extra=None, set_launcher_override=True):
    env = os.environ.copy()
    if set_launcher_override:
        # Most tests pin the launcher to keep dispatch deterministic regardless
        # of the test machine's PATH. Tests that exercise the CLI's own PATH
        # discovery pass set_launcher_override=False.
        env["BRAIN_VENV_LAUNCHER"] = sys.executable
    else:
        env.pop("BRAIN_VENV_LAUNCHER", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(CLI_PATH), *args],
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# Contract verification (DD-049): no silent drift between CLI list and scripts.
# ---------------------------------------------------------------------------

def test_dispatch_contract_matches_existing_scripts():
    for sub in SCRIPT_CONTRACT:
        script = SCRIPTS_DIR / f"{sub.replace('-', '_')}.py"
        assert script.is_file(), (
            f"Dispatch contract names '{sub}' but {script} does not exist. "
            "Either rename the CLI subcommand (CLI major bump) or restore the script."
        )


def test_cli_dispatch_array_matches_test_contract():
    """The CLI script's hard-coded list must match this test's list, byte-for-byte."""
    match = re.search(r"DISPATCH_SUBCOMMANDS=\(([^)]*)\)", CLI_BODY)
    assert match, "Could not locate DISPATCH_SUBCOMMANDS in cli/brain"
    declared = match.group(1).split()
    assert declared == GENERIC_DISPATCH_CONTRACT


def test_install_ref_matches_brain_core_version():
    assert _cli_shell_var("BRAIN_INSTALL_REF") == f"v{BRAIN_CORE_VERSION}"


# ---------------------------------------------------------------------------
# CLI-only surface
# ---------------------------------------------------------------------------

def test_version_long_form():
    result = _run_cli("--version")
    assert result.returncode == 0
    assert result.stdout.strip() == f"brain {BRAIN_CLI_VERSION}"


def test_version_subcommand():
    result = _run_cli("version")
    assert result.returncode == 0
    assert result.stdout.strip() == f"brain {BRAIN_CLI_VERSION}"


def test_help_lists_dispatch_subcommands():
    result = _run_cli("--help")
    assert result.returncode == 0
    for sub in PUBLIC_DISPATCH_CONTRACT:
        assert sub in result.stdout
    for sub in DISPATCH_COMPAT:
        assert sub not in result.stdout


def test_no_args_prints_help():
    result = _run_cli()
    assert result.returncode == 0
    assert "Usage:" in result.stdout


def test_install_uses_pinned_release_installer(tmp_path):
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    curl_path = fake_bin / "curl"
    curl_path.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$@\" > \"$BRAIN_CURL_ARGS_FILE\"\n"
        "cat <<'EOF'\n"
        "#!/bin/sh\n"
        "printf '%s\\n' \"$@\" > \"$BRAIN_INSTALL_ARGS_FILE\"\n"
        "EOF\n"
    )
    curl_path.chmod(0o755)

    curl_args_file = tmp_path / "curl-args.txt"
    install_args_file = tmp_path / "install-args.txt"
    target = tmp_path / "new-vault"

    result = _run_cli(
        "install",
        str(target),
        "--skip-mcp",
        env_extra={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "BRAIN_CURL_ARGS_FILE": str(curl_args_file),
            "BRAIN_INSTALL_ARGS_FILE": str(install_args_file),
        },
        set_launcher_override=False,
    )

    assert result.returncode == 0, result.stderr
    curl_args = curl_args_file.read_text().splitlines()
    assert "-fsSL" in curl_args
    assert any(f"/v{BRAIN_CORE_VERSION}/install.sh" in arg for arg in curl_args)
    assert not any("/main/install.sh" in arg for arg in curl_args)
    assert install_args_file.read_text().splitlines() == [str(target), "--skip-mcp"]


def test_unknown_subcommand_fails():
    result = _run_cli("frobnicate")
    assert result.returncode == 2
    assert "unknown subcommand" in result.stderr


def test_init_dispatch_noun_is_removed_without_redirect_stub():
    result = _run_cli("init")
    assert result.returncode == 2
    assert "unknown subcommand: init" in result.stderr
    assert "configure" in result.stderr
    assert "deprecated" not in result.stderr.lower()


# ---------------------------------------------------------------------------
# Vault discovery
# ---------------------------------------------------------------------------

def test_dispatch_with_explicit_vault_absolute(tmp_path):
    vault = _build_fake_vault(tmp_path)
    result = _run_cli("check", "--vault", str(vault), "--foo", "bar")
    assert result.returncode == 0, result.stderr
    assert f'"--vault", "{vault}"' in result.stdout or f"'--vault', '{vault}'" in result.stdout
    assert "--foo" in result.stdout and "bar" in result.stdout


def test_dispatch_with_explicit_vault_relative_resolves_to_absolute(tmp_path):
    vault = _build_fake_vault(tmp_path)
    # Run from tmp_path, pass --vault as a relative path.
    result = _run_cli("check", "--vault", "vault", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    # The script must see an absolute path, not 'vault'.
    assert str(vault) in result.stdout
    assert '"--vault", "vault"' not in result.stdout


def test_dispatch_with_vault_equals_form(tmp_path):
    vault = _build_fake_vault(tmp_path)
    result = _run_cli("check", f"--vault={vault}")
    assert result.returncode == 0, result.stderr
    assert str(vault) in result.stdout


def test_dispatch_via_cwd_walk(tmp_path):
    vault = _build_fake_vault(tmp_path)
    nested = vault / "Daily Notes" / "2026-05"
    nested.mkdir(parents=True)
    result = _run_cli("check", cwd=nested)
    assert result.returncode == 0, result.stderr
    assert str(vault) in result.stdout


def test_dispatch_via_brain_vault_root_env(tmp_path):
    vault = _build_fake_vault(tmp_path)
    other_cwd = tmp_path / "elsewhere"
    other_cwd.mkdir()
    result = _run_cli("check", cwd=other_cwd, env_extra={"BRAIN_VAULT_ROOT": str(vault)})
    assert result.returncode == 0, result.stderr
    assert str(vault) in result.stdout


def test_no_vault_found_fails_clearly(tmp_path):
    elsewhere = tmp_path / "no-vault"
    elsewhere.mkdir()
    # Clear BRAIN_VAULT_ROOT in case the test environment has it.
    env = {"BRAIN_VAULT_ROOT": ""}
    result = _run_cli("check", cwd=elsewhere, env_extra=env)
    assert result.returncode != 0
    assert "no vault found" in result.stderr


def test_missing_script_fails_clearly(tmp_path):
    vault = _build_fake_vault(tmp_path)
    # Remove the check.py to simulate a brain-core that does not ship it.
    (vault / ".brain-core" / "scripts" / "check.py").unlink()
    result = _run_cli("check", "--vault", str(vault))
    assert result.returncode != 0
    assert "does not ship check.py" in result.stderr


def test_vault_path_does_not_exist(tmp_path):
    missing = tmp_path / "does-not-exist"
    result = _run_cli("check", "--vault", str(missing))
    assert result.returncode != 0
    assert "does not exist" in result.stderr


# ---------------------------------------------------------------------------
# Session dispatch through the machine-owned resolver
# ---------------------------------------------------------------------------

def test_session_with_explicit_vault_dispatches_directly(tmp_path):
    vault = _build_fake_vault(tmp_path)
    result = _run_cli("session", "--vault", str(vault), "--json")

    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["argv"] == ["--vault", str(vault), "--json"]


def test_session_without_vault_uses_machine_resolver_then_bound_brain(tmp_path):
    bound = _build_fake_vault(tmp_path / "bound-root")
    foreign = _build_fake_vault(tmp_path / "foreign-root")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    registry = xdg / "brain" / "vaults"
    registry.parent.mkdir(parents=True)
    registry.write_text(f"foreign\tlocal\t{foreign}\n")
    runtime = _write_machine_resolution_runtime(
        home,
        {
            "status": "ok",
            "target": {
                "kind": "local",
                "vault_root": str(bound),
                "workspace_dir": str(workspace),
                "source": "workspace_binding",
            },
        },
    )

    result = _run_cli(
        "session",
        "--json",
        cwd=foreign,
        env_extra={
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(xdg),
            "BRAIN_RESOLUTION_RUNTIME_DIR": str(runtime),
            "BRAIN_WORKSPACE_DIR": str(workspace),
            "BRAIN_VAULT_ROOT": "",
        },
    )

    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["argv"] == [
        "--vault",
        str(bound),
        "--workspace-dir",
        str(workspace),
        "--json",
    ]


def test_session_no_workspace_no_vault_uses_default_target_without_registry_carrier_scan(tmp_path):
    default_target = _build_fake_vault(tmp_path / "default-target")
    foreign = _build_fake_vault(tmp_path / "foreign-root")
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    registry = xdg / "brain" / "vaults"
    registry.parent.mkdir(parents=True)
    registry.write_text(f"foreign\tlocal\t{foreign}\n")
    runtime = _write_machine_resolution_runtime(
        home,
        {
            "status": "ok",
            "target": {
                "kind": "local",
                "vault_root": str(default_target),
                "workspace_dir": None,
                "source": "registry_default",
            },
        },
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    result = _run_cli(
        "session",
        "--json",
        cwd=elsewhere,
        env_extra={
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(xdg),
            "BRAIN_RESOLUTION_RUNTIME_DIR": str(runtime),
            "BRAIN_WORKSPACE_DIR": "",
            "BRAIN_VAULT_ROOT": "",
        },
    )

    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["argv"] == ["--vault", str(default_target), "--json"]
    assert str(foreign) not in result.stdout


def test_session_bare_default_target_dispatches_with_empty_forwarded_args(tmp_path):
    default_target = _build_fake_vault(tmp_path / "default-target")
    home = tmp_path / "home"
    runtime = _write_machine_resolution_runtime(
        home,
        {
            "status": "ok",
            "target": {
                "kind": "local",
                "vault_root": str(default_target),
                "workspace_dir": None,
                "source": "registry_default",
            },
        },
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    result = _run_cli(
        "session",
        cwd=elsewhere,
        env_extra={
            "HOME": str(home),
            "BRAIN_RESOLUTION_RUNTIME_DIR": str(runtime),
            "BRAIN_WORKSPACE_DIR": "",
            "BRAIN_VAULT_ROOT": "",
        },
    )

    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["argv"] == ["--vault", str(default_target)]


def test_session_resolved_target_without_session_script_fails_clearly(tmp_path):
    target = _build_fake_vault(tmp_path / "target-without-session")
    (target / ".brain-core" / "scripts" / "session.py").unlink()
    home = tmp_path / "home"
    runtime = _write_machine_resolution_runtime(
        home,
        {
            "status": "ok",
            "target": {
                "kind": "local",
                "vault_root": str(target),
                "workspace_dir": None,
                "source": "registry_default",
            },
        },
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    result = _run_cli(
        "session",
        "--json",
        cwd=elsewhere,
        env_extra={
            "HOME": str(home),
            "BRAIN_RESOLUTION_RUNTIME_DIR": str(runtime),
            "BRAIN_WORKSPACE_DIR": "",
            "BRAIN_VAULT_ROOT": "",
        },
    )

    assert result.returncode == 1
    assert "this brain-core version does not ship session.py" in result.stderr
    assert str(target) in result.stderr


def test_session_resolution_failure_emits_degraded_payload_when_json_requested(tmp_path):
    home = tmp_path / "home"
    runtime = _write_machine_resolution_runtime(
        home,
        {
            "status": "degraded",
            "recovery_class": "session_resolution",
            "vault_root": None,
            "message": "dangling default",
            "session_resolution": {
                "code": "stale_binding",
                "context": {
                    "workspace_env": None,
                    "vault_root_env": None,
                    "start_dir": str(tmp_path),
                    "workspace_anchor_explicit": False,
                    "vault_root_explicit": False,
                },
            },
            "recovery": {
                "action": "Re-bind/check the workspace, and check the machine default Brain.",
                "command": "brain doctor --actionable",
            },
        },
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    result = _run_cli(
        "session",
        "--json",
        cwd=elsewhere,
        env_extra={
            "HOME": str(home),
            "BRAIN_RESOLUTION_RUNTIME_DIR": str(runtime),
            "BRAIN_WORKSPACE_DIR": "",
            "BRAIN_VAULT_ROOT": "",
        },
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "degraded"
    assert payload["recovery_class"] == "session_resolution"
    assert payload["vault_root"] is None
    assert payload["message"] == "dangling default"
    assert payload["session_resolution"]["code"] == "stale_binding"
    assert payload["recovery"]["action"] == "Re-bind/check the workspace, and check the machine default Brain."
    assert payload["recovery"]["command"] == "brain doctor --actionable"


def test_session_workspace_dir_anchors_machine_resolution(tmp_path):
    bound = _build_fake_vault(tmp_path / "bound-root")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    runtime = _write_machine_resolution_runtime(
        home,
        {
            "status": "ok",
            "target": {
                "kind": "local",
                "vault_root": str(bound),
                "workspace_dir": str(workspace),
                "source": "workspace_binding",
            },
        },
        expected_workspace_env=str(workspace),
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    result = _run_cli(
        "session",
        "--workspace-dir",
        str(workspace),
        "--json",
        cwd=elsewhere,
        env_extra={
            "HOME": str(home),
            "BRAIN_RESOLUTION_RUNTIME_DIR": str(runtime),
            "BRAIN_WORKSPACE_DIR": "",
            "BRAIN_VAULT_ROOT": "",
        },
    )

    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["argv"] == [
        "--vault",
        str(bound),
        "--workspace-dir",
        str(workspace),
        "--json",
    ]


def test_session_deprecated_project_dir_alias_anchors_machine_resolution(tmp_path):
    bound = _build_fake_vault(tmp_path / "bound-root")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    runtime = _write_machine_resolution_runtime(
        home,
        {
            "status": "ok",
            "target": {
                "kind": "local",
                "vault_root": str(bound),
                "workspace_dir": str(workspace),
                "source": "workspace_binding",
            },
        },
        expected_workspace_env=str(workspace),
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    result = _run_cli(
        "session",
        "--project-dir",
        str(workspace),
        "--json",
        cwd=elsewhere,
        env_extra={
            "HOME": str(home),
            "BRAIN_RESOLUTION_RUNTIME_DIR": str(runtime),
            "BRAIN_WORKSPACE_DIR": "",
            "BRAIN_VAULT_ROOT": "",
        },
    )

    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["argv"] == [
        "--vault",
        str(bound),
        "--project-dir",
        str(workspace),
        "--json",
    ]


def test_session_workspace_dir_missing_value_fails_before_resolution(tmp_path):
    home = tmp_path / "home"
    runtime = _write_machine_resolution_runtime(
        home,
        {
            "status": "degraded",
            "message": "should not run",
            "session_resolution": {"code": "invalid_binding"},
            "recovery": {"action": "unused", "command": "unused"},
        },
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    result = _run_cli(
        "session",
        "--workspace-dir",
        cwd=elsewhere,
        env_extra={
            "HOME": str(home),
            "BRAIN_RESOLUTION_RUNTIME_DIR": str(runtime),
            "BRAIN_WORKSPACE_DIR": "",
            "BRAIN_VAULT_ROOT": "",
        },
    )

    assert result.returncode == 1
    assert "--workspace-dir requires a value" in result.stderr
    assert "should not run" not in result.stdout


def test_session_remote_target_returns_explicit_unsupported_payload(tmp_path):
    home = tmp_path / "home"
    runtime = _write_machine_resolution_runtime(
        home,
        {
            "status": "ok",
            "target": {
                "kind": "remote",
                "endpoint": "https://brain.example.com",
                "source": "workspace_binding",
            },
        },
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    result = _run_cli(
        "session",
        "--json",
        cwd=elsewhere,
        env_extra={
            "HOME": str(home),
            "BRAIN_RESOLUTION_RUNTIME_DIR": str(runtime),
            "BRAIN_WORKSPACE_DIR": "",
            "BRAIN_VAULT_ROOT": "",
        },
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "degraded"
    assert payload["session_resolution"]["code"] == "remote_unsupported"
    assert "remote Brain targets are not yet supported" in payload["message"]


def test_session_local_target_without_vault_root_is_internal_error(tmp_path):
    home = tmp_path / "home"
    runtime = _write_machine_resolution_runtime(
        home,
        {
            "status": "ok",
            "target": {
                "kind": "local",
                "workspace_dir": None,
                "source": "registry_default",
            },
        },
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    result = _run_cli(
        "session",
        "--json",
        cwd=elsewhere,
        env_extra={
            "HOME": str(home),
            "BRAIN_RESOLUTION_RUNTIME_DIR": str(runtime),
            "BRAIN_WORKSPACE_DIR": "",
            "BRAIN_VAULT_ROOT": "",
        },
    )

    assert result.returncode == 1
    assert "returned local target without vault_root" in result.stderr
    assert result.stdout == ""


def test_session_json_no_launcher_still_emits_degraded_payload(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = _run_cli(
        "session",
        "--workspace-dir",
        str(workspace),
        "--json",
        cwd=elsewhere,
        env_extra={
            "PATH": "/bin:/usr/bin",
            "BRAIN_WORKSPACE_DIR": "",
            "BRAIN_VAULT_ROOT": "",
        },
        set_launcher_override=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "degraded"
    assert payload["vault_root"] is None
    assert payload["session_resolution"]["code"] == "filesystem_access"
    assert payload["session_resolution"]["context"]["workspace_env"] == str(workspace)


def test_session_brain_vault_root_stale_fails_without_machine_fallback(tmp_path):
    home = tmp_path / "home"
    target = _build_fake_vault(tmp_path / "target")
    runtime = _write_machine_resolution_runtime(
        home,
        {
            "status": "ok",
            "target": {
                "kind": "local",
                "vault_root": str(target),
                "workspace_dir": None,
                "source": "registry_default",
            },
        },
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    missing = tmp_path / "missing-vault"

    result = _run_cli(
        "session",
        "--json",
        cwd=elsewhere,
        env_extra={
            "HOME": str(home),
            "BRAIN_RESOLUTION_RUNTIME_DIR": str(runtime),
            "BRAIN_WORKSPACE_DIR": "",
            "BRAIN_VAULT_ROOT": str(missing),
        },
    )

    assert result.returncode != 0
    assert "BRAIN_VAULT_ROOT path does not exist" in result.stderr
    assert str(target) not in result.stdout


# ---------------------------------------------------------------------------
# Argument forwarding
# ---------------------------------------------------------------------------

def test_arguments_pass_through_unchanged(tmp_path):
    vault = _build_fake_vault(tmp_path)
    result = _run_cli(
        "repair",
        "--vault", str(vault),
        "mcp",
        "--json",
        "--max-age", "30",
        "positional-arg",
    )
    assert result.returncode == 0, result.stderr
    # Echo script prints JSON: {"argv": [...]}
    import json
    body = json.loads(result.stdout)
    argv = body["argv"]
    # --vault appears first (CLI re-injection) followed by other args in user order.
    assert argv[0] == "--vault"
    assert argv[1] == str(vault)
    assert argv[2:] == ["mcp", "--json", "--max-age", "30", "positional-arg"]


def test_hyphenated_subcommand_dispatches_to_underscored_script(tmp_path):
    vault = _build_fake_vault(tmp_path)
    result = _run_cli("migrate-naming", "--vault", str(vault), "--check")
    assert result.returncode == 0, result.stderr
    # The fake migrate_naming.py is the one that ran.
    import json
    body = json.loads(result.stdout)
    assert "--check" in body["argv"]


# ---------------------------------------------------------------------------
# Doctor
# ---------------------------------------------------------------------------

def test_doctor_outside_vault_runs_machine_checks(tmp_path):
    elsewhere = tmp_path / "no-vault"
    elsewhere.mkdir()
    result = _run_cli(
        "doctor",
        cwd=elsewhere,
        env_extra={
            "BRAIN_VAULT_ROOT": "",
            "HOME": str(tmp_path),
            "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        },
    )
    # doctor's exit code reflects whether checks passed; we only require it ran.
    assert "brain CLI:" in result.stdout
    assert "machine diagnosis:" in result.stdout
    assert "vault diagnosis:" in result.stdout
    assert "none in scope" in result.stdout


def test_doctor_outside_vault_uses_machine_helper_from_registry(tmp_path):
    vault = _build_machine_helper_vault(tmp_path)
    xdg = tmp_path / "xdg"
    registry = xdg / "brain" / "vaults"
    registry.parent.mkdir(parents=True)
    registry.write_text(f"vault\tlocal\t{vault}\n")

    elsewhere = tmp_path / "no-vault"
    elsewhere.mkdir()
    result = _run_cli(
        "doctor",
        cwd=elsewhere,
        env_extra={"BRAIN_VAULT_ROOT": "", "XDG_CONFIG_HOME": str(xdg), "HOME": str(tmp_path)},
    )

    assert "1 discovered" in result.stdout
    assert "registry:" in result.stdout
    assert "brain routes:" in result.stdout
    assert "none in scope" in result.stdout


def test_doctor_outside_vault_accepts_legacy_two_column_registry_entries(tmp_path):
    vault = _build_machine_helper_vault(tmp_path)
    xdg = tmp_path / "xdg"
    registry = xdg / "brain" / "vaults"
    registry.parent.mkdir(parents=True)
    registry.write_text(f"vault\t{vault}\n")

    elsewhere = tmp_path / "no-vault"
    elsewhere.mkdir()
    result = _run_cli(
        "doctor",
        cwd=elsewhere,
        env_extra={"BRAIN_VAULT_ROOT": "", "XDG_CONFIG_HOME": str(xdg), "HOME": str(tmp_path)},
    )

    assert "1 discovered" in result.stdout
    assert "registry:" in result.stdout
    assert "brain routes:" in result.stdout
    assert "none in scope" in result.stdout


def test_doctor_outside_vault_ignores_non_local_registry_entries(tmp_path):
    vault = _build_machine_helper_vault(tmp_path)
    xdg = tmp_path / "xdg"
    registry = xdg / "brain" / "vaults"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        "team\tremote\thttps://brain.example.com\n"
        f"vault\tlocal\t{vault}\n"
    )

    elsewhere = tmp_path / "no-vault"
    elsewhere.mkdir()
    result = _run_cli(
        "doctor",
        cwd=elsewhere,
        env_extra={"BRAIN_VAULT_ROOT": "", "XDG_CONFIG_HOME": str(xdg), "HOME": str(tmp_path)},
    )

    assert "1 discovered" in result.stdout
    assert "registry:" in result.stdout
    assert "brain routes:" in result.stdout
    assert "none in scope" in result.stdout


def test_doctor_outside_vault_uses_machine_helper_from_brains_registry_fallback(tmp_path):
    vault = _build_machine_helper_vault(tmp_path)
    xdg = tmp_path / "xdg"
    brains = xdg / "brain" / "brains.json"
    brains.parent.mkdir(parents=True)
    brains.write_text(
        '{\n'
        '  "version": 1,\n'
        '  "brains": [\n'
        f'    {{"alias": "vault", "path": "{vault}"}}\n'
        '  ]\n'
        '}\n'
    )

    elsewhere = tmp_path / "no-vault"
    elsewhere.mkdir()
    result = _run_cli(
        "doctor",
        cwd=elsewhere,
        env_extra={"BRAIN_VAULT_ROOT": "", "XDG_CONFIG_HOME": str(xdg), "HOME": str(tmp_path)},
    )

    assert "1 discovered" in result.stdout
    assert "registry:" in result.stdout
    assert "brain routes:" in result.stdout
    assert "none in scope" in result.stdout


def test_machine_outside_vault_uses_machine_helper_from_registry(tmp_path):
    vault = _build_machine_helper_vault(tmp_path)
    xdg = tmp_path / "xdg"
    registry = xdg / "brain" / "vaults"
    registry.parent.mkdir(parents=True)
    registry.write_text(f"vault\tlocal\t{vault}\n")

    elsewhere = tmp_path / "no-vault"
    elsewhere.mkdir()
    result = _run_cli(
        "machine",
        "prune-runtimes",
        "--dry-run",
        "--json",
        cwd=elsewhere,
        env_extra={"BRAIN_VAULT_ROOT": "", "XDG_CONFIG_HOME": str(xdg), "HOME": str(tmp_path)},
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["action"] == "prune-runtimes"
    assert payload["source_vault"] == str(vault)
    assert payload["counts"]["targets"] == 0


def test_doctor_inside_older_vault_falls_back_to_registered_machine_helper(tmp_path):
    current_vault = _build_fake_vault(tmp_path)
    brain_mcp = current_vault / ".brain-core" / "brain_mcp"
    brain_mcp.mkdir(parents=True)
    (brain_mcp / "requirements.txt").write_text("mcp==1.0.0\n")
    _write_doctor_check_stub(current_vault, message="Older-vault drift")
    helper_vault = _build_machine_helper_vault(tmp_path)
    xdg = tmp_path / "xdg"
    registry = xdg / "brain" / "vaults"
    registry.parent.mkdir(parents=True)
    registry.write_text(f"helper\tlocal\t{helper_vault}\n")

    result = _run_cli(
        "doctor",
        cwd=current_vault,
        env_extra={"XDG_CONFIG_HOME": str(xdg), "HOME": str(tmp_path)},
    )

    assert "machine diagnosis:" in result.stdout
    assert "vault diagnosis:" in result.stdout
    assert "brains:" in result.stdout
    assert "Older-vault drift" in result.stdout
    assert str(current_vault) in result.stdout
    assert str(helper_vault) in result.stdout


def test_doctor_with_explicit_vault_outputs_composed_json(tmp_path):
    current_vault = _build_fake_vault(tmp_path)
    brain_mcp = current_vault / ".brain-core" / "brain_mcp"
    brain_mcp.mkdir(parents=True)
    (brain_mcp / "requirements.txt").write_text("mcp==1.0.0\n")
    _write_doctor_check_stub(current_vault, message="Explicit-vault drift")
    helper_vault = _build_machine_helper_vault(tmp_path)
    xdg = tmp_path / "xdg"
    registry = xdg / "brain" / "vaults"
    registry.parent.mkdir(parents=True)
    registry.write_text(f"helper\tlocal\t{helper_vault}\n")

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    result = _run_cli(
        "doctor",
        "--vault",
        str(current_vault),
        "--json",
        cwd=elsewhere,
        env_extra={"BRAIN_VAULT_ROOT": "", "XDG_CONFIG_HOME": str(xdg), "HOME": str(tmp_path)},
    )

    assert result.returncode == 2, result.stderr
    payload = json.loads(result.stdout)
    assert payload["doctor"]["current_vault"] == str(current_vault)
    assert payload["doctor"]["source_vault"] == str(helper_vault)
    assert payload["vault"]["available"] is True
    assert payload["vault"]["result"]["findings"][0]["message"] == "Explicit-vault drift"


def test_doctor_with_explicit_vault_carries_current_vault_derived_cache_finding(tmp_path):
    current_vault = _build_fake_vault(tmp_path)
    brain_mcp = current_vault / ".brain-core" / "brain_mcp"
    brain_mcp.mkdir(parents=True)
    (brain_mcp / "requirements.txt").write_text("mcp==1.0.0\n")
    _write_doctor_check_stub(
        current_vault,
        check_name="lexical_index",
        message="Lexical retrieval index cache is stale or unreadable (version-drift).",
        repair_scope="lexical",
        severity="warning",
        exit_code=1,
    )
    helper_vault = _build_machine_helper_vault(tmp_path)
    xdg = tmp_path / "xdg"
    registry = xdg / "brain" / "vaults"
    registry.parent.mkdir(parents=True)
    registry.write_text(f"helper\tlocal\t{helper_vault}\n")

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    result = _run_cli(
        "doctor",
        "--vault",
        str(current_vault),
        "--json",
        cwd=elsewhere,
        env_extra={"BRAIN_VAULT_ROOT": "", "XDG_CONFIG_HOME": str(xdg), "HOME": str(tmp_path)},
    )

    assert result.returncode == 1, result.stderr
    payload = json.loads(result.stdout)
    finding = payload["vault"]["result"]["findings"][0]
    assert finding["check"] == "lexical_index"
    assert finding["repair"]["scope"] == "lexical"
    assert "version-drift" in finding["message"]


def test_doctor_inside_legacy_vault_uses_shell_fallback_check(tmp_path):
    vault = _build_fake_vault(tmp_path)
    # Replace check.py with one that prints a sentinel so we can confirm dispatch.
    (vault / ".brain-core" / "scripts" / "check.py").write_text(
        "#!/usr/bin/env python3\n"
        "print('DOCTOR_DISPATCHED_CHECK')\n"
    )
    result = _run_cli(
        "doctor",
        cwd=vault,
        env_extra={"HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path / "xdg")},
    )
    assert "machine diagnosis:" in result.stdout
    assert "vault diagnosis:" in result.stdout
    assert "DOCTOR_DISPATCHED_CHECK" in result.stdout


def test_doctor_reports_source_vault_runtime_failure_in_shell_fallback(tmp_path):
    current_vault = _build_fake_vault(tmp_path)
    brain_mcp = current_vault / ".brain-core" / "brain_mcp"
    brain_mcp.mkdir(parents=True)
    (brain_mcp / "requirements.txt").write_text("mcp==1.0.0\n")
    _write_doctor_check_stub(current_vault, message="Fallback drift", exit_code=2)
    helper_vault = _build_machine_helper_vault(tmp_path)
    (helper_vault / ".brain-core" / "scripts" / "_common" / "_venv.py").unlink()
    xdg = tmp_path / "xdg"
    registry = xdg / "brain" / "vaults"
    registry.parent.mkdir(parents=True)
    registry.write_text(f"helper\tlocal\t{helper_vault}\n")

    elsewhere = tmp_path / "elsewhere-runtime-failure"
    elsewhere.mkdir()
    result = _run_cli(
        "doctor",
        "--vault",
        str(current_vault),
        cwd=elsewhere,
        env_extra={"BRAIN_VAULT_ROOT": "", "XDG_CONFIG_HOME": str(xdg), "HOME": str(tmp_path)},
    )

    assert result.returncode == 2, result.stderr
    assert "doctor handoff unavailable" in result.stdout
    assert str(helper_vault) in result.stdout
    assert "brain repair runtime --vault" in result.stdout
    assert "Fallback drift" in result.stdout


def _doctor_from_dir(install_dir: Path, on_path: bool, tmp_path: Path):
    """Run a copy of cli/brain installed at install_dir; control whether it's on PATH."""
    install_dir.mkdir(parents=True, exist_ok=True)
    brain_copy = install_dir / "brain"
    brain_copy.write_text(CLI_PATH.read_text())
    brain_copy.chmod(0o755)

    env = os.environ.copy()
    env["BRAIN_VENV_LAUNCHER"] = sys.executable
    env["BRAIN_VAULT_ROOT"] = ""
    env["HOME"] = str(tmp_path)
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    if on_path:
        env["PATH"] = f"{install_dir}:{env['PATH']}"
    else:
        # Strip install_dir if it happens to be on PATH (defensive).
        env["PATH"] = ":".join(p for p in env["PATH"].split(":") if p != str(install_dir))
    no_vault = tmp_path / "no-vault"
    no_vault.mkdir(exist_ok=True)
    return subprocess.run(
        ["bash", str(brain_copy), "doctor"],
        cwd=str(no_vault),
        env=env,
        capture_output=True,
        text=True,
    )


def test_doctor_path_check_ok_when_binary_dir_on_path(tmp_path):
    """Regression: --system installs ship to /usr/local/bin.

    Doctor must report OK whenever the dir holding the running brain binary is
    on PATH — not only when that dir is `~/.local/bin`.
    """
    result = _doctor_from_dir(tmp_path / "usr-local-bin", on_path=True, tmp_path=tmp_path)
    assert "PATH:    ok" in result.stdout
    assert str(tmp_path / "usr-local-bin") in result.stdout
    assert "WARN" not in result.stdout.split("PATH:")[1].splitlines()[0]


def test_doctor_path_check_warns_when_binary_dir_not_on_path(tmp_path):
    """Doctor reports WARN with the binary's actual directory when off PATH."""
    result = _doctor_from_dir(tmp_path / "elsewhere", on_path=False, tmp_path=tmp_path)
    assert "PATH:    WARN" in result.stdout
    assert str(tmp_path / "elsewhere") in result.stdout


# ---------------------------------------------------------------------------
# Runtime fallback (regression: brain CLI must work in --skip-mcp installs)
# ---------------------------------------------------------------------------

def _build_real_venv_vault(root: Path) -> Path:
    """Vault carrying the REAL _venv.py — exercises the runnable-python chain."""
    vault = root / "vault"
    scripts = vault / ".brain-core" / "scripts"
    common = scripts / "_common"
    common.mkdir(parents=True)
    (vault / ".brain-core" / "VERSION").write_text("0.99.0\n")
    bc_req = vault / ".brain-core" / "brain_mcp"
    bc_req.mkdir(parents=True)
    (bc_req / "requirements.txt").write_text("mcp==1.0.0\n")

    real_venv_py = REPO_ROOT / "src" / "brain-core" / "scripts" / "_common" / "_venv.py"
    (common / "_venv.py").write_text(real_venv_py.read_text())

    # Echo script for dispatch verification.
    (scripts / "check.py").write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "print(json.dumps({'argv': sys.argv[1:]}))\n"
    )
    return vault


def test_dispatch_falls_back_to_launcher_via_explicit_override(tmp_path):
    """Regression: --skip-mcp installs ship the CLI but no central venv. Dispatch must still work."""
    vault = _build_real_venv_vault(tmp_path)
    # No central venv. Point HOME at an empty dir so the resolver can't find one.
    env_extra = {"HOME": str(tmp_path / "empty-home"), "BRAIN_VENV_LAUNCHER": sys.executable}
    (tmp_path / "empty-home").mkdir()
    result = _run_cli("check", "--vault", str(vault), env_extra=env_extra)
    assert result.returncode == 0, result.stderr
    # check.py ran under the launcher fallback (sys.executable).
    assert "argv" in result.stdout


def test_dispatch_falls_back_to_launcher_via_path_discovery(tmp_path):
    """Regression: the PATH-token flow must work too.

    `cli/brain` `find_launcher_python` returns PATH-discovered binaries like
    `python3.12`. The earlier implementation left those as bare tokens, which then failed
    `Path(launcher).exists()` in `_venv.py` and collapsed the fallback chain.
    This test exercises the PATH flow explicitly — no `BRAIN_VENV_LAUNCHER`
    override. A stub `python3.12` on PATH delegates to `sys.executable`,
    keeping the test environment-independent (asdf shims, custom PATHs etc.).
    """
    vault = _build_real_venv_vault(tmp_path)

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_py = fake_bin / "python3.12"
    fake_py.write_text("#!/bin/sh\nexec " + sys.executable + " \"$@\"\n")
    fake_py.chmod(0o755)

    env_extra = {
        "HOME": str(tmp_path / "empty-home"),
        # Keep /usr/bin + /bin so bash / coreutils still work.
        "PATH": f"{fake_bin}:/usr/bin:/bin",
    }
    (tmp_path / "empty-home").mkdir()
    result = _run_cli(
        "check", "--vault", str(vault),
        env_extra=env_extra,
        set_launcher_override=False,
    )
    assert result.returncode == 0, result.stderr
    # `cli/brain` must resolve the bare `python3.12` token to the absolute
    # fake-bin path, not the bare name, before passing it to `_venv.py`.
    assert "argv" in result.stdout


def test_dispatch_falls_back_to_legacy_vault_venv(tmp_path):
    """Pre-D2 vaults have <vault>/.venv. Dispatch must reach it before the launcher."""
    vault = _build_real_venv_vault(tmp_path)
    legacy_bin = vault / ".venv" / "bin"
    legacy_bin.mkdir(parents=True)
    legacy_py = legacy_bin / "python"
    # Make the legacy python a symlink to the real interpreter so dispatch actually runs.
    legacy_py.symlink_to(sys.executable)

    env_extra = {"HOME": str(tmp_path / "empty-home")}
    (tmp_path / "empty-home").mkdir()
    result = _run_cli("check", "--vault", str(vault), env_extra=env_extra)
    assert result.returncode == 0, result.stderr
    assert "argv" in result.stdout


def test_path_launcher_below_3_12_is_rejected(tmp_path):
    """Regression: cli/brain must honour DD-048's 3.12+ floor.

    `python3` on PATH may be any version from 3.0 to 3.13. The earlier CLI resolved
    PATH tokens to absolute paths but did not probe the version; a 3.9
    interpreter would be silently accepted, corrupt the central-venv tag, and
    fail at import time. The fixed CLI probes the version before accepting it.

    Fake `python3` here reports <3.12 via the probe script's exit status.
    Both `python3.13` and `python3.12` are absent from the isolated PATH.
    The CLI must surface the launcher-missing error, not dispatch.
    """
    vault = _build_real_venv_vault(tmp_path)

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_py = fake_bin / "python3"
    fake_py.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  -c)\n"
        "    if echo \"$2\" | grep -q 'version_info'; then\n"
        "      # Probe checks >= (3, 12); exit 1 = not OK.\n"
        "      exit 1\n"
        "    fi\n"
        "    ;;\n"
        "esac\n"
        "exit 0\n"
    )
    fake_py.chmod(0o755)

    env_extra = {
        "HOME": str(tmp_path / "empty-home"),
        "PATH": f"{fake_bin}{os.pathsep}{launcher_discovery_path()}",
    }
    (tmp_path / "empty-home").mkdir()
    result = _run_cli(
        "check", "--vault", str(vault),
        env_extra=env_extra,
        set_launcher_override=False,
    )
    assert result.returncode != 0
    assert "no python3.12+" in result.stderr.lower()


def test_doctor_reports_missing_when_no_compatible_launcher(tmp_path):
    """brain doctor must say MISSING rather than presenting an incompatible Python.

    Same fake `python3` stub as the dispatch-rejection test above. Outside
    a vault, doctor runs machine checks only — the python: line must report
    MISSING.
    """
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_py = fake_bin / "python3"
    fake_py.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  -c)\n"
        "    if echo \"$2\" | grep -q 'version_info'; then\n"
        "      exit 1\n"
        "    fi\n"
        "    ;;\n"
        "esac\n"
        "exit 0\n"
    )
    fake_py.chmod(0o755)

    no_vault = tmp_path / "elsewhere"
    no_vault.mkdir()
    env_extra = {
        "HOME": str(tmp_path / "empty-home"),
        "PATH": f"{fake_bin}{os.pathsep}{launcher_discovery_path()}",
        "BRAIN_VAULT_ROOT": "",
    }
    (tmp_path / "empty-home").mkdir()
    result = _run_cli(
        "doctor",
        cwd=no_vault,
        env_extra=env_extra,
        set_launcher_override=False,
    )
    assert "MISSING" in result.stdout
    assert "python3.12+" in result.stdout


def test_dispatch_fails_clearly_when_launcher_unresolvable(tmp_path):
    """No central venv, no legacy venv, no compatible launcher → clear error.

    Sets BRAIN_VENV_LAUNCHER to a nonexistent path; CLI honours the override
    so find_launcher_python returns the bogus path, then runnable-python
    rejects it and the CLI surfaces an error. PATH keeps /bin so the test
    can still spawn bash.
    """
    vault = _build_real_venv_vault(tmp_path)
    env_extra = {
        "HOME": str(tmp_path / "empty-home"),
        "PATH": "/bin",
        "BRAIN_VENV_LAUNCHER": str(tmp_path / "does-not-exist" / "python"),
    }
    (tmp_path / "empty-home").mkdir()
    result = _run_cli("check", "--vault", str(vault), env_extra=env_extra)
    assert result.returncode != 0
    err = result.stderr.lower()
    assert ("no runnable python" in err
            or "no python3.12+" in err
            or "no such file" in err)
