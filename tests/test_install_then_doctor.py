"""A fresh install is healthy under Doctor and the machine pass without a derived registry (DD-083)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

from brain_test_support import copy_install_source, folder_tree, register_other_brain
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


def _link_by_setup(vault: Path, folder: Path):
    """Make a link the one way a person makes it: workspace.setup from the folder."""
    from _application.context import Capability
    from _application.types import Availability, DependencyTier
    from _application.workspace.setup import WorkspaceSetupRequest
    from command_application import application_for

    class _Provider:
        provider_id = "caller_filesystem"

    folder.mkdir(parents=True, exist_ok=True)
    result = application_for(vault, dependency_tier=DependencyTier.PORTABLE, workspace_dir=folder,
                             providers=(_Provider(),),
                             capabilities=(Capability("caller_filesystem", Availability.AVAILABLE),)
                             ).invoke(WorkspaceSetupRequest())
    assert result.status == "ok", result.error.message if result.status == "error" else result
    return result


def _maintenance_pass(vault: Path):
    """One keyless maintenance pass whose automatic families run through the real application boundary."""
    import uuid
    from _application.application import CommandApplication
    from _application.maintenance.run import MaintenanceRunRequest
    from _application.registry import current_request_resolver
    from command_application import application_for, context_for

    class _Sibling:
        def repair(self, family, *, invocation_id):
            request = current_request_resolver().resolve(family.command_id, dict(family.request))
            return application_for(vault, invocation_id=invocation_id, context_kind="standalone").invoke(request)

    context = context_for(vault, context_kind="standalone", invocation_id=f"inv-{uuid.uuid4().hex[:8]}")
    context = replace(context, maintenance=_Sibling())
    context = replace(context, access=context.authorisation.bind(context))
    return CommandApplication(context, context.authorisation.catalogue).invoke(MaintenanceRunRequest())


def _doctor_with_real_vault_check(vault: Path, tmp_path: Path, monkeypatch):
    """The launcher ``brain.doctor`` owner, folding in this Brain's real ``vault.check`` result."""
    from launcher_catalogue import LAUNCHER_CATALOGUE
    from _launcher import doctor as launcher_doctor
    from _launcher.doctor import BrainDoctorRequest
    from _launcher.invocation import LauncherInvocation
    from _launcher.owners import LAUNCHER_OWNERS
    import check
    import doctor as doctor_script

    monkeypatch.setattr(doctor_script, "collect_cli_diagnosis", lambda **_kwargs: {
        "version": "4.0.6", "binary": str(tmp_path / "bin" / "brain"), "binary_dir": str(tmp_path / "bin"),
        "path_ok": True, "launcher_python": sys.executable, "launcher_version": "Python 3.12", "launcher_probe_failed": False,
    })
    monkeypatch.setattr(launcher_doctor, "run_vault_check",
                        lambda vault_root, *, actionable, severity: check.run_checks(str(vault_root)))
    context = _launcher_context(tmp_path, invocation_id=f"inv-doctor-{uuid.uuid4().hex[:8]}")
    return LauncherInvocation(context, LAUNCHER_CATALOGUE, LAUNCHER_OWNERS).invoke(BrainDoctorRequest(vault))


def _key_of(vault: Path, folder: Path) -> str:
    import workspace_registry

    return next(key for key, row in workspace_registry.load_registry(vault).items() if row["path"] == str(folder))


def _refreshed(vault: Path) -> Path:
    from _application.registry import current_request_resolver
    from command_application import application_for

    assert application_for(vault).invoke(current_request_resolver().resolve("runtime.refresh-router", {})).status == "ok"
    return vault


def test_deleted_and_rekeyed_manifests_settle_and_doctor_stays_healthy(installed_vault, tmp_path, monkeypatch):
    """Design acceptance criterion 6 for reachable folders, with Doctor's overall result asserted."""
    import check
    import workspace_registry

    vault = _refreshed(installed_vault)
    other = register_other_brain(tmp_path, "other-brain")
    emptied, rekeyed, rebrained = ((tmp_path / "work" / name).resolve() for name in ("emptied", "rekeyed", "rebrained"))
    for folder in (emptied, rekeyed, rebrained):
        _link_by_setup(vault, folder)
    emptied_key, rekeyed_key, rebrained_key = (_key_of(vault, folder) for folder in (emptied, rekeyed, rebrained))
    (emptied / ".brain" / "local" / "workspace.yaml").unlink()
    manifest = rekeyed / ".brain" / "local" / "workspace.yaml"
    manifest.write_text(manifest.read_text().replace(f"workspace: {rekeyed_key}", "workspace: someone-else"))
    manifest = rebrained / ".brain" / "local" / "workspace.yaml"
    brain_id = vault_registry.brain_id_for_path(str(vault))
    manifest.write_text(manifest.read_text().replace(f"brain: {brain_id}", "brain: other-brain"))
    assert other.is_dir()
    before = {folder: folder_tree(folder) for folder in (emptied, rekeyed, rebrained)}

    result = _maintenance_pass(vault)

    assert result.status == "ok", getattr(result, "error", None)
    rows = workspace_registry.load_registry(vault)
    assert rekeyed_key not in rows, "a manifest naming another hub key drops its row"
    assert rebrained_key not in rows, "a manifest edited to another registered Brain drops its row"
    assert emptied_key in rows, "a deleted manifest proves nothing, so its row is kept"
    assert {folder: folder_tree(folder) for folder in (emptied, rekeyed, rebrained)} == before, "the pass wrote nothing in any folder"
    findings = [item for item in check.run_checks(str(vault))["findings"] if item["check"] == "workspace_registry"]
    assert [(item["file"], item["code"], item["severity"]) for item in findings] == [
        (f".brain/local/workspaces.json#{emptied_key}", "workspace_link_unverifiable", "info")]

    # The stand-in other Brain has no brain_mcp requirements, and a registered Brain without them crashes
    # Doctor's runtime classification (a separate, pre-existing defect), so it is unregistered first.
    vault_registry.unregister(str(other))
    doctor = _doctor_with_real_vault_check(vault, tmp_path, monkeypatch)
    assert doctor.status == "ok", getattr(doctor, "error", None)
    assert doctor.result.machine.healthy
    assert doctor.result.vault.exit_code == 0
    assert doctor.result.healthy and doctor.result.exit_code == 0, "an info finding leaves Doctor's overall result healthy"


def test_moved_and_unplugged_folders_keep_their_rows_and_are_dismissed_one_at_a_time(installed_vault, tmp_path, monkeypatch):
    """Design acceptance criterion 6 for unreachable folders: reported, dismissible, never unhealthy, and pruning waits."""
    import check
    import shutil
    import workspace_registry
    from _application.maintenance.dismiss import MaintenanceDismissRequest
    from _application.maintenance.list import MaintenanceListRequest
    from command_application import application_for

    vault = _refreshed(installed_vault)
    moved, away = ((tmp_path / "work" / name).resolve() for name in ("moved", "away"))
    for folder in (moved, away):
        _link_by_setup(vault, folder)
    moved_key, away_key = _key_of(vault, moved), _key_of(vault, away)
    moved_to = (tmp_path / "elsewhere" / "moved").resolve()
    moved_to.parent.mkdir()
    moved.rename(moved_to)
    shutil.rmtree(away)

    result = _maintenance_pass(vault)

    assert result.status == "ok", getattr(result, "error", None)
    assert {moved_key, away_key} <= set(workspace_registry.load_registry(vault)), "an unreachable folder never drops a row"
    assert not moved.exists() and not away.exists(), "the pass recreated neither folder"
    findings = {item["file"]: item for item in check.run_checks(str(vault))["findings"] if item["check"] == "workspace_registry"}
    for key in (moved_key, away_key):
        finding = findings[f".brain/local/workspaces.json#{key}"]
        assert (finding["code"], finding["severity"], "repair" in finding) == ("workspace_folder_unreachable", "info", False)

    before_dismissal = _doctor_with_real_vault_check(vault, tmp_path, monkeypatch)
    assert before_dismissal.result.machine.healthy and before_dismissal.result.exit_code == 0, (
        "health never depends on dismissing anything")

    app = application_for(vault)
    listed = {item.file: item for item in app.invoke(MaintenanceListRequest()).result.items}
    unplugged = listed[f".brain/local/workspaces.json#{away_key}"]
    assert app.invoke(MaintenanceDismissRequest(unplugged.key, unplugged.fingerprint, "drive unplugged", "rob")).status == "ok"
    still_listed = {item.file for item in app.invoke(MaintenanceListRequest()).result.items}
    assert f".brain/local/workspaces.json#{away_key}" not in still_listed
    assert f".brain/local/workspaces.json#{moved_key}" in still_listed, "dismissing one row leaves the other live"

    doctor = _doctor_with_real_vault_check(vault, tmp_path, monkeypatch)
    assert doctor.status == "ok", getattr(doctor, "error", None)
    machine = doctor.result.machine
    assert machine.healthy, "an unreachable folder is reported, never unhealthy"
    assert doctor.result.healthy and doctor.result.exit_code == 0
    assert {entry.path for entry in machine.unreachable_locations} == {str(moved), str(away)}
    assert not machine.tidy and not machine.registration_coverage_complete, "orphan detection waits for the folders"
    summary = collect_machine_summary(current_vault=str(vault), launcher_python=sys.executable)
    prune = maintenance.prune_orphaned_runtimes(summary, dry_run=True)
    blocked = prune["steps"][0]
    assert blocked["status"] == "error"
    assert str(moved) in blocked["message"] and str(away) in blocked["message"]
    assert "Reconnect them, or unregister them" in blocked["message"]

    _link_by_setup(vault, moved_to)
    assert workspace_registry.load_registry(vault)[moved_key] == {"path": str(moved_to)}
    assert f".brain/local/workspaces.json#{moved_key}" not in {item["file"] for item in check.run_checks(str(vault))["findings"]}


def test_an_unplugged_brain_is_reported_not_unhealthy_and_pauses_pruning(installed_vault, tmp_path):
    """E1 and E2 for a Brain root: its stale row is listed, its location named, and pruning waits."""
    import shutil

    unplugged = register_other_brain(tmp_path, "unplugged")
    shutil.rmtree(unplugged)

    summary = collect_machine_summary(current_vault=str(installed_vault), launcher_python=sys.executable)

    assert summary["healthy"], "a stale Brain registry row is reported, never unhealthy"
    assert [entry["path"] for entry in summary["stale_registry_entries"]] == [str(unplugged)]
    assert summary["unreachable_locations"] == [{"path": str(unplugged), "label": "registered Brain unplugged"}]
    assert not summary["tidy"] and not summary["registration_coverage_complete"]
    blocked = maintenance.prune_orphaned_runtimes(summary, dry_run=True)["steps"][0]
    assert blocked["status"] == "error" and "registered Brain unplugged" in blocked["message"]


def test_invalid_registration_state_still_makes_the_machine_unhealthy(installed_vault):
    """The other half of the split: a ledger that cannot be read is invalid, not unreachable."""
    (installed_vault / ".brain" / "local" / "init-state.json").write_text("{broken\n")

    summary = collect_machine_summary(current_vault=str(installed_vault), launcher_python=sys.executable)

    assert not summary["healthy"]
    assert summary["unreachable_locations"] == []
    assert "could not be read" in summary["registration_coverage_blocked"]
