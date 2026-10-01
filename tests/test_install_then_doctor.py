"""A fresh install is healthy under Doctor and the machine pass without a derived registry (DD-083)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from brain_test_support import copy_install_source
from _common import config_home, resolve_vault_venv_python
from _machine import maintenance, topology
from _machine.maintenance import collect_machine_summary
import doctor_machine
import vault_registry


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI_DIR = REPO_ROOT / "cli"
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))


@pytest.fixture(autouse=True)
def _no_live_processes(monkeypatch):
    monkeypatch.setattr(maintenance, "find_live_brain_runtime_processes",
                        lambda runtime_pythons, *, scan=None: {"available": True, "processes": {}})
    monkeypatch.setattr(topology, "scan_processes", lambda: {"available": True, "processes": []})


@pytest.fixture
def installed_vault(tmp_path, fake_home):
    """Install into an empty home as a real ``install.py`` subprocess, then stand in a central runtime.

    A ``--mcp-scope skip`` install provisions no managed runtime, and Doctor
    needs one to report a healthy Brain, so the symlink supplies what a
    non-skip install would have created.
    """
    source = tmp_path / "source"
    source.mkdir()
    copy_install_source(source)
    vault = tmp_path / "Brain"
    completed = subprocess.run(
        [sys.executable, str(source / "src" / "brain-core" / "scripts" / "install.py"), str(vault),
         "--source-root", str(source), "--mcp-scope", "skip", "--json"],
        capture_output=True, text=True, env=os.environ.copy(), check=False,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert json.loads(completed.stdout)["status"] == "ok"
    runtime = resolve_vault_venv_python(vault, launcher=Path(sys.executable))
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.symlink_to(sys.executable)
    return vault.resolve()


class _Authority:
    def allows(self, **_kwargs):
        return True


class _CallerFilesystem:
    provider_id = "caller_filesystem"
    available = True


class _Receipts:
    def write(self, receipt):
        pass


class _Clock:
    def now(self):
        return datetime(2026, 10, 1, tzinfo=timezone.utc)


def _launcher_context(tmp_path: Path, *, invocation_id: str):
    from _launcher.context import LauncherContext, ProviderBindings

    return LauncherContext(
        profile="operator", authority=_Authority(), providers=ProviderBindings((_CallerFilesystem(),)),
        correlation_id=f"corr-{invocation_id}", invocation_id=invocation_id, receipt_writer=_Receipts(), clock=_Clock(),
        caller_dir=tmp_path.resolve(), home_dir=tmp_path.resolve(), cli_version="4.0.6",
        cli_binary=(tmp_path / "bin" / "brain").resolve(), launcher_python=Path(sys.executable).resolve(),
    )


def _doctor_machine_subprocess(vault: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(vault / ".brain-core" / "scripts" / "doctor_machine.py"),
         "--vault", str(vault), "--current-vault", str(vault), "--launcher", sys.executable, "--json"],
        capture_output=True, text=True, env=os.environ.copy(), check=False,
    )


def test_fresh_install_is_healthy_in_process(installed_vault):
    summary = collect_machine_summary(current_vault=str(installed_vault), launcher_python=sys.executable)

    assert summary["healthy"] and summary["tidy"]
    assert summary["counts"]["repair_findings"] == 0
    assert summary["unregistered_brains"] == []
    assert summary["registry"] == {"path": str(vault_registry.registry_path()), "brains_count": 1, "stale": False}
    assert [brain["sources"] for brain in summary["brains"]] == [["current", "vault_registry"]]
    assert not (config_home() / "brain" / "brains.json").exists()


def test_fresh_install_then_doctor_machine_subprocess_exits_zero(installed_vault):
    # The path the brain-lab post-install step takes: the installed Core's own script, as a subprocess.
    completed = _doctor_machine_subprocess(installed_vault)

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["healthy"] and payload["tidy"] and payload["counts"]["repair_findings"] == 0
    assert payload["registry"]["brains_count"] == 1 and payload["registry"]["stale"] is False


def _launcher_doctor(installed_vault, tmp_path, monkeypatch):
    """Run the launcher ``brain.doctor`` owner with clean CLI facts and a clean ``vault.check``."""
    from launcher_catalogue import LAUNCHER_CATALOGUE
    from _launcher import doctor as launcher_doctor
    from _launcher.doctor import BrainDoctorRequest
    from _launcher.invocation import LauncherInvocation
    from _launcher.owners import LAUNCHER_OWNERS
    import doctor as doctor_script

    monkeypatch.setattr(doctor_script, "collect_cli_diagnosis", lambda **_kwargs: {
        "version": "4.0.6", "binary": str(tmp_path / "bin" / "brain"), "binary_dir": str(tmp_path / "bin"),
        "path_ok": True, "launcher_python": sys.executable, "launcher_version": "Python 3.12", "launcher_probe_failed": False,
    })
    monkeypatch.setattr(launcher_doctor, "run_vault_check", lambda vault_root, *, actionable, severity: {
        "summary": {"errors": 0, "warnings": 0, "info": 0}, "findings": [],
    })
    context = _launcher_context(tmp_path, invocation_id="inv-doctor")
    return LauncherInvocation(context, LAUNCHER_CATALOGUE, LAUNCHER_OWNERS).invoke(BrainDoctorRequest(installed_vault))


def test_fresh_install_then_launcher_doctor_is_healthy(installed_vault, tmp_path, monkeypatch):
    from _launcher.doctor import DoctorRegistryState

    result = _launcher_doctor(installed_vault, tmp_path, monkeypatch)

    assert result.status == "ok", getattr(result, "error", None)
    assert result.command_version == 3
    assert result.result.healthy and result.result.exit_code == 0
    assert result.result.machine.healthy
    assert result.result.machine.registry.state is DoctorRegistryState.CURRENT
    assert result.result.machine.registry.brains_count == 1
    assert result.result.machine.unregistered_brains == ()


def test_leftover_derived_registry_is_inert(installed_vault, tmp_path, monkeypatch):
    from launcher_catalogue import LAUNCHER_CATALOGUE
    from _launcher.invocation import LauncherInvocation
    from _launcher.machine_maintenance import MachineMaintenanceRunRequest
    from _launcher.owners import LAUNCHER_OWNERS

    leftover = config_home() / "brain" / "brains.json"
    leftover.write_text('{"version": 1, "brains": [{"alias": "old", "path": "/gone"}]}\n', encoding="utf-8")
    Path(str(leftover) + ".lock").write_text("", encoding="utf-8")
    before = {path: path.read_bytes() for path in (leftover, Path(str(leftover) + ".lock"))}

    completed = _doctor_machine_subprocess(installed_vault)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["healthy"]

    from _launcher.doctor import DoctorRegistryState

    doctor = _launcher_doctor(installed_vault, tmp_path, monkeypatch)
    assert doctor.status == "ok", getattr(doctor, "error", None)
    assert doctor.result.healthy and doctor.result.exit_code == 0
    assert doctor.result.machine.registry.state is DoctorRegistryState.CURRENT

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    context = replace(_launcher_context(tmp_path, invocation_id="inv-pass"), current_vault=installed_vault)

    result = LauncherInvocation(context, LAUNCHER_CATALOGUE, LAUNCHER_OWNERS).invoke(MachineMaintenanceRunRequest())

    assert result.status == "ok", getattr(result, "error", None)
    assert result.result.groups == () and result.result.outcome == "ok"
    assert json.loads((tmp_path / "state" / "brain" / "maintenance" / "last-pass.json").read_text())["groups"] == {}
    assert {path: path.read_bytes() for path in before} == before, "a leftover derived file is never read or touched"


def test_unregistered_current_vault_is_reported_with_the_registering_command(installed_vault, capsys, monkeypatch):
    vault_registry.unregister(str(installed_vault))

    summary = collect_machine_summary(current_vault=str(installed_vault), launcher_python=sys.executable)
    assert summary["healthy"] and summary["unregistered_brains"] == [str(installed_vault)]

    monkeypatch.setattr(sys, "argv", ["doctor_machine.py", "--vault", str(installed_vault), "--current-vault",
                                      str(installed_vault), "--launcher", sys.executable])
    assert doctor_machine.main() == 0
    out = capsys.readouterr().out
    assert "unregistered brains:" in out
    assert f"""register: brain register --request-json '{{"vault_root":"{installed_vault}"}}'""" in out
