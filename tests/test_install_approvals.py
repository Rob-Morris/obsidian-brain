"""Native installers retain explicit approval intent across lifecycle branches."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from brain_test_support import copy_install_source
from _bootstrap.mcp_registration import user_ledger_path
import vault_registry


ROOT = Path(__file__).resolve().parents[1]
OPT_IN = ["--approval-client", "claude", "--approval-scope", "project", "--approvals", "both"]


@pytest.fixture(autouse=True)
def isolated_machine(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local/state"))
    monkeypatch.setenv("PATH", os.pathsep.join((str(Path(sys.executable).parent), str(home / ".local/bin"), os.environ["PATH"])))


@pytest.mark.parametrize("options", [
    ["--approvals", "mcp"],
    ["--approval-client", "claude", "--approvals", "mcp"],
    ["--approval-client", "grok", "--approval-scope", "user", "--approvals", "mcp"],
    ["--approval-client", "claude", "--approval-scope", "invalid", "--approvals", "mcp"],
    ["--approval-client", "claude", "--approval-scope", "user", "--approvals", "invalid"],
    ["--approvals", ""],
])
def test_invalid_opt_in_fails_before_machine_or_vault_changes(tmp_path, options):
    target = tmp_path / "vault"
    result = subprocess.run(
        ["bash", str(ROOT / "install.sh"), "--non-interactive", "--skip-mcp", *options, str(target)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    assert "approval" in result.stderr.lower()
    assert not target.exists()
    assert list((tmp_path / "home").iterdir()) == []


@pytest.mark.parametrize("installed_version", ["1.0.0", "1.0.1", "1.0.2"])
def test_existing_vault_applies_opt_in_after_version_handling(tmp_path, installed_version):
    source = tmp_path / "source"
    source.mkdir()
    copy_install_source(source)
    (source / "src/brain-core/VERSION").write_text("1.0.1\n")
    scripts = source / "src/brain-core/scripts"
    (scripts / "upgrade.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "vault = Path(sys.argv[sys.argv.index('--vault') + 1])\n"
        "(vault / '.brain-core/VERSION').write_text('1.0.1\\n')\n"
    )
    (scripts / "configure.py").write_text(
        "import json, sys\nfrom pathlib import Path\n"
        "vault = Path(sys.argv[sys.argv.index('--workspace') + 1])\n"
        "(vault / 'approval-call.json').write_text(json.dumps({"
        "'args': sys.argv[1:], 'version': (vault / '.brain-core/VERSION').read_text().strip()}))\n"
    )
    vault = tmp_path / "vault with spaces"
    (vault / ".brain-core").mkdir(parents=True)
    (vault / ".brain-core/VERSION").write_text(installed_version + "\n")
    result = subprocess.run(
        ["bash", str(source / "install.sh"), "--non-interactive", "--skip-mcp", *OPT_IN, str(vault)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads((vault / "approval-call.json").read_text())
    assert observed["args"] == [
        "approvals", "--client", "claude", "--scope", "project", "--workspace", str(vault),
        "--surfaces", "mcp", "cli",
    ]
    assert observed["version"] == ("1.0.1" if installed_version == "1.0.0" else installed_version)


def test_windows_opt_in_validation_precedes_python_discovery():
    source = (ROOT / "install.ps1").read_text()
    assert source.index("Approval opt-in requires") < source.index("$python = Find-CompatiblePython")


def test_fresh_install_and_same_version_rerun_apply_managed_cli_approvals(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    copy_install_source(source)
    fake_bin = tmp_path / "clients"
    fake_bin.mkdir()
    claude = fake_bin / "claude"
    claude.write_text("#!/bin/sh\nprintf '99.0.0\\n'\n")
    claude.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin) + os.pathsep + os.environ["PATH"])
    vault = tmp_path / "vault"
    command = [
        "bash", str(source / "install.sh"), "--non-interactive", "--skip-mcp",
        "--approval-client", "claude", "--approval-scope", "user", "--approvals", "cli", str(vault),
    ]
    first = subprocess.run(command, cwd=tmp_path / "home", capture_output=True, text=True, timeout=60)
    assert first.returncode == 0, first.stderr + first.stdout
    settings = tmp_path / "home/.claude/settings.json"
    assert settings.is_file()
    before = settings.read_bytes()
    assert any(rule.startswith("Bash(") for rule in json.loads(before)["permissions"]["allow"])
    second = subprocess.run(command, cwd=tmp_path / "home", capture_output=True, text=True, timeout=60)
    assert second.returncode == 0, second.stderr + second.stdout
    assert "Configuring requested client approvals" in second.stderr
    assert settings.read_bytes() == before


def test_managed_registry_backfill_preserves_existing_identity_and_default(tmp_path, monkeypatch):
    home = tmp_path / "home"
    binary = home / ".local/bin/brain"
    installed = subprocess.run(
        [sys.executable, str(ROOT / "cli/_distribution.py"), str(ROOT), str(binary)],
        capture_output=True, text=True, timeout=60,
    )
    assert installed.returncode == 0, installed.stderr
    monkeypatch.setenv("PATH", str(binary.parent) + os.pathsep + os.environ["PATH"])
    vault = tmp_path / "brain"
    (vault / ".brain-core").mkdir(parents=True)
    (vault / ".brain-core/VERSION").write_text("0.70.3\n")
    vault_registry.register(vault, brain_id="retained-name")
    vault_registry.set_default("retained-name")
    registry = Path(vault_registry._registry_path())
    before = registry.read_bytes()
    ledger = user_ledger_path(home).with_name("client-approvals.json")
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(json.dumps({"schema": "brain.client-approvals/1", "records": {}}))

    result = subprocess.run(
        [sys.executable, str(ROOT / "src/brain-core/scripts/vault_registry.py"),
         "--backfill", str(vault), "--id", "ignored-for-backfill"],
        cwd=home, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    receipt = json.loads(result.stdout)
    assert receipt["command"] == "brain.register"
    assert receipt["result"]["brain_id"] == "retained-name"
    assert registry.read_bytes() == before
    assert vault_registry.get_default() == "retained-name"


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell is unavailable")
def test_windows_partial_opt_in_fails_before_python_discovery(tmp_path):
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(ROOT / "install.ps1"),
         "-NonInteractive", "-SkipMcp", "-ApprovalClient", "claude",
         "-Launcher", str(tmp_path / "missing-python"), "-VaultPath", str(tmp_path / "vault")],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    assert "Approval opt-in requires" in result.stderr
    assert not (tmp_path / "vault").exists()
