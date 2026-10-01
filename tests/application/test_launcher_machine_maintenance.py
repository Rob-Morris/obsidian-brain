"""The machine pass over Doctor's feed and add-only registry sync (DD-082, phase 4)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_DIR = REPO_ROOT / "cli"
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

from launcher_catalogue import LAUNCHER_CATALOGUE
from _launcher import machine_maintenance, nested_invocation
from _launcher.context import LauncherContext, ProviderBindings
from _launcher.contracts import CommandError, CommittedEffect, Error, ErrorCode, Ok, OutcomeReference, OutcomeUnknownDetails, ReceiptState
from _launcher.invocation import LauncherInvocation
from _launcher.machine_maintenance import (
    MachineMaintenanceClaimRequest,
    MachineMaintenanceDismissRequest,
    MachineMaintenanceListRequest,
    MachineMaintenanceReleaseRequest,
    MachineMaintenanceRunRequest,
    detect_machine,
)
from _launcher.machine_registry import MachineRegistrySyncRequest, MachineRegistrySyncStatus
from _launcher.owners import LAUNCHER_OWNERS
from _bootstrap.maintenance_findings import Disposition, MaintenanceFinding, Owner, finding_key
from _bootstrap.maintenance_summary import GroupOutcome
from _machine import discovery, maintenance, topology
from _machine.discovery import add_and_refresh_machine_registry, discover_brains, machine_registry_path


NOW = datetime(2026, 9, 30, 9, 0, tzinfo=timezone.utc)


class _Authority:
    def allows(self, **_kwargs):
        return True


class _CallerFilesystem:
    provider_id = "caller_filesystem"
    available = True


class _Receipts:
    def __init__(self):
        self.values = []

    def write(self, receipt):
        self.values.append(receipt)


class _Clock:
    def __init__(self, now=NOW):
        self.now_value = now

    def now(self):
        return self.now_value


def _context(tmp_path, *, receipts=None, clock=None, dry_run=False, invocation_id="inv-machine"):
    return LauncherContext(
        profile="operator",
        authority=_Authority(),
        providers=ProviderBindings((_CallerFilesystem(),)),
        correlation_id="corr-machine",
        invocation_id=invocation_id,
        receipt_writer=receipts or _Receipts(),
        clock=clock or _Clock(),
        caller_dir=tmp_path.resolve(),
        home_dir=tmp_path.resolve(),
        cli_version="4.0.6",
        cli_binary=(tmp_path / "bin" / "brain").resolve(),
        launcher_python=Path(sys.executable).resolve(),
        dry_run=dry_run,
    )


def _invoke(tmp_path, request, **options):
    return LauncherInvocation(_context(tmp_path, **options), LAUNCHER_CATALOGUE, LAUNCHER_OWNERS).invoke(request)


def _vault(root: Path, name: str) -> Path:
    import shutil

    vault = root / name
    (vault / ".brain-core" / "brain_mcp").mkdir(parents=True)
    (vault / ".brain-core" / "VERSION").write_text("0.70.10\n")
    (vault / ".brain-core" / "brain_mcp" / "requirements.txt").write_text("mcp==1.0.0\n")
    (vault / ".brain-core" / "brain_mcp" / "requirements-semantic.txt").write_text("mcp==1.0.0\n")
    (vault / ".brain-core" / "scripts" / "_common").mkdir(parents=True)
    shutil.copyfile(REPO_ROOT / "src/brain-core/scripts/_common/_venv.py",
                    vault / ".brain-core" / "scripts" / "_common" / "_venv.py")
    return vault.resolve()


@pytest.fixture
def state_home(tmp_path, monkeypatch):
    home = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(home))
    return home / "brain" / "maintenance"


def _finding(kind, subject, *, disposition=Disposition.JUDGEMENT, scope=None, evidence=None, code=None, file=None):
    return MaintenanceFinding(kind, "warning", file or json.dumps(subject, sort_keys=True), f"{kind} message", disposition,
                              code=code, scope=scope, owner=Owner.MACHINE, key=finding_key("machine", kind, subject), evidence=evidence)


@pytest.fixture
def fake_detection(monkeypatch):
    findings = {"value": (), "scan": True}
    monkeypatch.setattr(machine_maintenance, "detect_machine", lambda _context: (tuple(findings["value"]), findings["scan"]))
    return findings


# ---------------------------------------------------------------------------
# Add-only registry synchronisation
# ---------------------------------------------------------------------------

class TestAddAndRefresh:
    def test_adds_discovered_brains_and_never_drops_stale_rows(self, tmp_path, fake_home):
        vault = _vault(tmp_path, "Brain A")
        path = machine_registry_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 1, "brains": [{"alias": "gone", "path": str(tmp_path / "Unplugged")}]}))

        result = add_and_refresh_machine_registry(discover_brains(current_vault=vault)["brains"])

        assert result["changed"] and result["added"] == [str(vault)] and result["refreshed"] == []
        rows = json.loads(path.read_text())["brains"]
        assert [row["path"] for row in rows] == [str(tmp_path / "Unplugged"), str(vault)], "stale rows stay"
        again = add_and_refresh_machine_registry(discover_brains(current_vault=vault)["brains"])
        assert not again["changed"] and again["brains_count"] == 2

    def test_refreshes_an_alias_and_plans_without_writing_on_dry_run(self, tmp_path, fake_home):
        import vault_registry

        vault = _vault(tmp_path, "Brain A")
        path = machine_registry_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 1, "brains": [{"alias": None, "path": str(vault)}]}))
        vault_registry.register(str(vault), "named-brain")
        brains = discover_brains(current_vault=vault)["brains"]

        planned = add_and_refresh_machine_registry(brains, dry_run=True)
        assert planned["changed"] and planned["refreshed"] == [str(vault)]
        assert json.loads(path.read_text())["brains"][0]["alias"] is None

        applied = add_and_refresh_machine_registry(brains)
        assert applied["refreshed"] == [str(vault)]
        assert json.loads(path.read_text())["brains"][0]["alias"] == "named-brain"

    @pytest.mark.parametrize("text, reason", [("{not json", "invalid-json"), ('{"version": 1, "brains": [7]}', "malformed")])
    def test_blocked_or_malformed_registries_are_left_untouched(self, tmp_path, fake_home, text, reason):
        vault = _vault(tmp_path, "Brain A")
        path = machine_registry_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

        result = add_and_refresh_machine_registry(discover_brains(current_vault=vault)["brains"])

        assert result["blocked"] and result["blocked_reason"] == reason
        assert path.read_text() == text

    def test_lock_wait_is_bounded(self, tmp_path, fake_home, monkeypatch):
        from _bootstrap.file_lock import MutationLockError, exclusive_file_lock

        vault = _vault(tmp_path, "Brain A")
        monkeypatch.setattr(discovery, "MACHINE_REGISTRY_LOCK_TIMEOUT", 0.1)
        lock = Path(str(machine_registry_path()) + ".lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(lock):
            with pytest.raises(MutationLockError):
                add_and_refresh_machine_registry(discover_brains(current_vault=vault)["brains"])


def test_machine_registry_sync_owner_reports_effects_and_busy_locks(tmp_path, fake_home, monkeypatch):
    from _bootstrap.file_lock import exclusive_file_lock

    vault = _vault(tmp_path, "Brain A")
    receipts = _Receipts()
    monkeypatch.setattr(maintenance, "find_live_brain_runtime_processes",
                        lambda runtime_pythons, *, scan=None: {"available": True, "processes": {}})
    monkeypatch.setattr(discovery, "MACHINE_REGISTRY_LOCK_TIMEOUT", 0.1)

    def invoke(dry_run=False):
        from dataclasses import replace

        ctx = replace(_context(tmp_path, receipts=receipts, dry_run=dry_run), current_vault=vault)
        return LauncherInvocation(ctx, LAUNCHER_CATALOGUE, LAUNCHER_OWNERS).invoke(MachineRegistrySyncRequest())

    planned = invoke(dry_run=True)
    assert planned.status == "ok" and planned.result.status is MachineRegistrySyncStatus.PLANNED
    assert not machine_registry_path().exists()

    changed = invoke()
    assert changed.status == "ok" and changed.result.status is MachineRegistrySyncStatus.CHANGED
    assert changed.result.added == (str(vault),)
    assert changed.committed_effects == (CommittedEffect("machine-registry.sync", f"file:{machine_registry_path()}"),)
    assert receipts.values[-1].state is ReceiptState.COMMITTED

    assert invoke().result.status is MachineRegistrySyncStatus.NOOP

    with exclusive_file_lock(Path(str(machine_registry_path()) + ".lock")):
        busy = invoke()
    assert busy.status == "error" and busy.error.code is ErrorCode.CONFLICT and busy.retryable


def test_doctor_scripts_never_write_the_registry(tmp_path, fake_home, monkeypatch, capsys):
    import doctor_machine

    vault = _vault(tmp_path, "Brain A")
    monkeypatch.setattr(maintenance, "find_live_brain_runtime_processes",
                        lambda runtime_pythons, *, scan=None: {"available": True, "processes": {}})
    monkeypatch.setattr(sys, "argv", ["doctor_machine.py", "--vault", str(vault), "--current-vault", str(vault),
                                      "--launcher", sys.executable])

    assert doctor_machine.main() == 1
    assert not machine_registry_path().exists()
    out = capsys.readouterr().out
    assert "drifted" in out and "brain machine-registry sync" in out


# ---------------------------------------------------------------------------
# Orphaned processes
# ---------------------------------------------------------------------------

def test_orphaned_brain_processes_are_role_interpreters_without_a_live_parent(monkeypatch):
    scan = {"available": True, "processes": [
        {"pid": 10, "ppid": 1, "command": "/venv/bin/brain-mcp-python -m brain_mcp.proxy /venv/bin/python brain_mcp.server"},
        {"pid": 11, "ppid": 10, "command": "/venv/bin/brain-mcp-python -m brain_mcp.server"},
        {"pid": 12, "ppid": 999, "command": "/venv/bin/brain-cli-python command.py artefact list"},
        {"pid": 13, "ppid": 1, "command": "/venv/bin/brain-cli-python workflow.py"},
        {"pid": 14, "ppid": 1, "command": "/usr/bin/python3 -m something"},
    ]}

    orphans = topology.find_orphaned_brain_processes(scan=scan)

    assert orphans["available"] is True
    assert [(item["pid"], item["role"]) for item in orphans["processes"]] == [(10, "mcp"), (12, "cli"), (13, "cli")]
    assert topology.find_orphaned_brain_processes(scan={"available": False, "processes": []}) == {"available": False, "processes": []}


def test_process_scan_reads_parent_pids_positionally(monkeypatch):
    import subprocess

    seen = []

    def fake_run(argv, **kwargs):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "  1     0 /sbin/launchd\n 42     1 /venv/bin/brain-cli-python a b c\nbad line\n", "")

    monkeypatch.setattr(topology.subprocess, "run", fake_run)

    scan = topology.scan_processes()

    assert seen == [["ps", "-A", "-ww", "-o", "pid=,ppid=,command="]], "procps and BSD ps both accept this argv"

    assert scan == {"available": True, "processes": [
        {"pid": 1, "ppid": 0, "command": "/sbin/launchd"},
        {"pid": 42, "ppid": 1, "command": "/venv/bin/brain-cli-python a b c"},
    ]}


# ---------------------------------------------------------------------------
# Detection over Doctor's feed
# ---------------------------------------------------------------------------

def test_detection_classifies_the_machine_feed_and_excludes_brain_owned_scopes(tmp_path, monkeypatch):
    vault = _vault(tmp_path, "Brain A")
    summary = {
        "machine_registry": {"drifted": True, "missing_brains": [str(vault)], "blocked": False, "malformed": False},
        "stale_registry_entries": [{"alias": "old", "path": str(tmp_path / "Old")}],
        "stale_machine_registry_entries": [{"alias": None, "path": str(tmp_path / "Unplugged")}],
        "runtimes": [{"python": "/venvs/x/bin/python", "orphan_candidate": True}],
        "brains": [{
            "path": str(vault), "alias": "a",
            "runtime": {"status": "legacy_vault_venv"},
            "repair_findings": [
                {"check": "mcp_registration:claude_python_mismatch", "message": "stale", "repair": {"scope": "mcp"}},
                {"check": "mcp_registration", "message": "drifted", "repair": {"scope": "mcp"}},
                {"check": "workspace_registry", "message": "bad", "repair": {"scope": "registry"}},
            ],
        }],
        "mcp_registrations": {"registrations": [
            {"client": "claude", "scope": "user", "path": "/home/.claude.json", "state": "current", "action": "x"},
            {"client": "codex", "scope": "user", "path": "/home/.codex/config.toml", "state": "modified", "action": "x"},
        ]},
    }
    monkeypatch.setattr(maintenance, "collect_machine_summary", lambda **_kwargs: summary)
    monkeypatch.setattr(topology, "find_orphaned_brain_processes", lambda *, scan=None: {"available": True, "processes": [
        {"pid": 7, "ppid": 1, "command": "/venv/bin/brain-cli-python x", "role": "cli"}]})

    findings, scan_available = detect_machine(_context(tmp_path))

    kinds = sorted(item.check for item in findings)
    assert kinds == ["brain_repair", "brain_repair", "legacy_installation", "machine_registry_drift",
                     "mcp_registration", "orphan_runtime", "orphaned_process", "stale_machine_registry", "stale_vault_registry"]
    assert scan_available is True
    drift = next(item for item in findings if item.check == "machine_registry_drift")
    assert drift.disposition == Disposition.AUTOMATIC and drift.evidence == {"missing": [str(vault)]}
    repairs = [item for item in findings if item.check == "brain_repair"]
    assert {item.code for item in repairs} == {"mcp"}, "Brain-owned registry findings belong to the Brain pass"
    assert len({item.key for item in repairs}) == 1, "one key per (brain, scope)"
    registration = next(item for item in findings if item.check == "mcp_registration")
    assert registration.evidence == {"state": "modified"}


# ---------------------------------------------------------------------------
# The machine pass
# ---------------------------------------------------------------------------

def _drift(vault):
    return _finding("machine_registry_drift", {}, disposition=Disposition.AUTOMATIC, scope="machine_registry_drift",
                    evidence={"missing": [str(vault)]})


def _sibling(result):
    calls = []

    def fake(context, request, *, invocation_id):
        calls.append((type(request).__name__, invocation_id, context.dry_run))
        return result(invocation_id) if callable(result) else result

    return fake, calls


def test_pass_runs_the_automatic_family_and_lists_the_rest(tmp_path, state_home, fake_detection, monkeypatch):
    vault = _vault(tmp_path, "Brain A")
    fake_detection["value"] = (_drift(vault), _finding("stale_vault_registry", {"path": "/x"}),
                               _finding("orphan_runtime", {"python": "/venvs/x/bin/python"}))
    effect = CommittedEffect("machine-registry.sync", "file:/registry")
    fake, calls = _sibling(Ok("machine-registry.sync", 1, object(), (effect,)))
    monkeypatch.setattr(nested_invocation, "invoke_sibling", fake)
    receipts = _Receipts()

    result = _invoke(tmp_path, MachineMaintenanceRunRequest(), receipts=receipts)

    assert result.status == "ok", getattr(result, "error", None)
    assert calls == [("MachineRegistrySyncRequest", f"inv-machine-{result.result.pass_id}-machine_registry_drift", False)]
    assert [(item.kind, item.outcome) for item in result.result.groups] == [("machine_registry_drift", GroupOutcome.REPAIRED)]
    assert result.result.counts.needs_person == 2 and result.result.counts.failed == 0
    assert sorted(item.kind for item in result.result.attention) == ["orphan_runtime", "stale_vault_registry"]
    assert next(item for item in result.result.attention if item.kind == "orphan_runtime").command == "brain runtime remove-orphans"
    assert result.committed_effects == (effect, CommittedEffect("machine-maintenance.summary", result.result.pass_id))
    summary = json.loads((state_home / "last-pass.json").read_text())
    assert summary["groups"] == {"machine_registry_drift": "repaired"} and summary["counts"]["needs_person"] == 2
    assert receipts.values[-1].state is ReceiptState.COMMITTED

    dry = _invoke(tmp_path, MachineMaintenanceRunRequest(), dry_run=True)
    assert dry.status == "ok" and dry.result.planned == ("machine_registry_drift",) and len(calls) == 1


def test_pass_repairs_registry_drift_through_the_real_sync_sibling(tmp_path, fake_home, state_home, monkeypatch):
    from dataclasses import replace

    vault = _vault(tmp_path, "Brain A")
    monkeypatch.setattr(maintenance, "find_live_brain_runtime_processes",
                        lambda runtime_pythons, *, scan=None: {"available": True, "processes": {}})
    monkeypatch.setattr(topology, "scan_processes", lambda: {"available": True, "processes": []})
    receipts = _Receipts()
    path = machine_registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "brains": [{"alias": "gone", "path": str(tmp_path / "Unplugged")}]}))

    def run():
        context = replace(_context(tmp_path, receipts=receipts), current_vault=vault)
        return LauncherInvocation(context, LAUNCHER_CATALOGUE, LAUNCHER_OWNERS).invoke(MachineMaintenanceRunRequest())

    first = run()

    assert first.status == "ok", getattr(first, "error", None)
    assert [(item.kind, item.outcome) for item in first.result.groups] == [("machine_registry_drift", GroupOutcome.REPAIRED)]
    assert first.committed_effects[0] == CommittedEffect("machine-registry.sync", f"file:{machine_registry_path()}")
    assert str(vault) in {row["path"] for row in json.loads(machine_registry_path().read_text())["brains"]}
    assert [receipt.state for receipt in receipts.values[-2:]] == [ReceiptState.COMMITTED, ReceiptState.COMMITTED]

    second = run()
    assert second.status == "ok" and second.result.groups == (), "the drift is gone, so nothing is invoked"
    assert [item.kind for item in second.result.attention] == ["stale_machine_registry"], "a stale row is judgement, never a loop"
    assert second.result.counts.needs_person == 1


def test_a_busy_registry_lock_defers_the_automatic_family(tmp_path, fake_home, state_home, monkeypatch):
    from dataclasses import replace

    from _bootstrap.file_lock import exclusive_file_lock

    vault = _vault(tmp_path, "Brain A")
    monkeypatch.setattr(maintenance, "find_live_brain_runtime_processes",
                        lambda runtime_pythons, *, scan=None: {"available": True, "processes": {}})
    monkeypatch.setattr(topology, "scan_processes", lambda: {"available": True, "processes": []})
    monkeypatch.setattr(discovery, "MACHINE_REGISTRY_LOCK_TIMEOUT", 0.1)
    lock = Path(str(machine_registry_path()) + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    context = replace(_context(tmp_path), current_vault=vault)

    with exclusive_file_lock(lock):
        result = LauncherInvocation(context, LAUNCHER_CATALOGUE, LAUNCHER_OWNERS).invoke(MachineMaintenanceRunRequest())

    assert result.status == "ok"
    assert [(item.kind, item.outcome) for item in result.result.groups] == [("machine_registry_drift", GroupOutcome.DEFERRED)]
    assert result.result.counts.deferred == 1
    assert not machine_registry_path().exists()


def test_unknown_launcher_sibling_is_recorded_unknown_and_reported_as_unknown_effects(tmp_path, state_home, fake_detection, monkeypatch):
    vault = _vault(tmp_path, "Brain A")
    fake_detection["value"] = (_drift(vault),)

    def unknown(invocation_id):
        reference = OutcomeReference(invocation_id)
        return Error("machine-registry.sync", 1, CommandError(ErrorCode.COMMAND_OUTCOME_UNKNOWN, "lost",
                     OutcomeUnknownDetails(reference)), effects="unknown", outcome_reference=reference)

    fake, _calls = _sibling(unknown)
    monkeypatch.setattr(nested_invocation, "invoke_sibling", fake)

    result = _invoke(tmp_path, MachineMaintenanceRunRequest())

    assert result.status == "error" and result.effects == "unknown"
    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN and result.outcome_reference.invocation_id.endswith("machine_registry_drift")
    assert "machine-maintenance.summary" in result.error.message, "the summary write is a known committed effect"
    summary = json.loads((state_home / "last-pass.json").read_text())
    assert summary["groups"] == {"machine_registry_drift": "unknown"} and summary["counts"]["failed"] == 1
    listed = _invoke(tmp_path, MachineMaintenanceListRequest())
    assert [(item.kind, item.last_outcome) for item in listed.result.items] == [("machine_registry_drift", "unknown")]


def test_overlapping_machine_passes_exit_busy_and_detection_failure_blocks(tmp_path, state_home, monkeypatch):
    from _bootstrap.file_lock import exclusive_file_lock

    state_home.mkdir(parents=True)
    with exclusive_file_lock(state_home / "pass.lock"):
        busy = _invoke(tmp_path, MachineMaintenanceRunRequest())
    assert busy.status == "error" and busy.error.code is ErrorCode.CONFLICT and busy.retryable

    def broken(_context):
        raise OSError("ps exploded")

    monkeypatch.setattr(machine_maintenance, "detect_machine", broken)
    failed = _invoke(tmp_path, MachineMaintenanceRunRequest())
    assert failed.status == "error" and failed.error.code is ErrorCode.CONFLICT
    assert json.loads((state_home / "last-pass.json").read_text())["blocked"] == "detection_failed"


def test_machine_decisions_claim_dismiss_release_over_the_machine_file(tmp_path, state_home, fake_detection):
    vault = _vault(tmp_path, "Brain A")
    stale = _finding("stale_machine_registry", {"path": "/Unplugged"})
    fake_detection["value"] = (_drift(vault), stale)
    clock = _Clock()

    listed = _invoke(tmp_path, MachineMaintenanceListRequest(all=True), clock=clock)
    items = {item.kind: item for item in listed.result.items}
    assert items["stale_machine_registry"].command is None and items["machine_registry_drift"].command == "brain machine-registry sync"

    claimed = _invoke(tmp_path, MachineMaintenanceClaimRequest(stale.key, "rob"), clock=clock)
    assert claimed.status == "ok" and claimed.result.item.state == "held"
    assert json.loads((state_home / "decisions.json").read_text())["claims"][stale.key]["claimant"] == "rob"

    automatic = _invoke(tmp_path, MachineMaintenanceDismissRequest(items["machine_registry_drift"].key,
                                                                    items["machine_registry_drift"].fingerprint, "x", "rob"), clock=clock)
    assert automatic.status == "error" and automatic.error.code is ErrorCode.INVALID_REQUEST

    other = _invoke(tmp_path, MachineMaintenanceDismissRequest(stale.key, items["stale_machine_registry"].fingerprint, "x", "sam"), clock=clock)
    assert other.status == "error" and other.error.code is ErrorCode.CONFLICT

    released = _invoke(tmp_path, MachineMaintenanceReleaseRequest(stale.key, "rob"), clock=clock)
    assert released.status == "ok" and released.result.item.state == "open"

    dismissed = _invoke(tmp_path, MachineMaintenanceDismissRequest(stale.key, items["stale_machine_registry"].fingerprint,
                                                                    "unplugged drive", "rob"), clock=clock)
    assert dismissed.status == "ok" and dismissed.result.item.state == "quiet"
    quiet = _invoke(tmp_path, MachineMaintenanceListRequest(), clock=clock)
    assert quiet.result.hidden_quiet == 1 and [item.kind for item in quiet.result.items] == []

    # A claimed automatic group is held by the pass; an expired claim is a review item.
    clock.now_value = NOW + timedelta(hours=2)
    assert _invoke(tmp_path, MachineMaintenanceClaimRequest(items["machine_registry_drift"].key, "rob"), clock=clock).status == "ok"
    held = _invoke(tmp_path, MachineMaintenanceRunRequest(), clock=clock)
    assert held.result.groups == () and [item.state for item in held.result.attention] == ["held"]
    clock.now_value = NOW + timedelta(hours=4)
    expired = _invoke(tmp_path, MachineMaintenanceRunRequest(), clock=clock)
    assert [item.kind for item in expired.result.groups] == ["machine_registry_drift"], "only a live claim withholds"
    assert expired.result.counts.claim_expired == 1


def test_machine_maintenance_entries_match_the_launcher_contract():
    entries = {entry.command_id: entry for entry in LAUNCHER_CATALOGUE.entries}
    assert entries["machine-maintenance.list"].effect_class == "none" and entries["machine-maintenance.list"].authority == "reader"
    for command_id in ("machine-maintenance.run", "machine-maintenance.claim", "machine-maintenance.dismiss",
                       "machine-maintenance.release", "machine-registry.sync"):
        entry = entries[command_id]
        assert entry.effect_class == "machine_mutation" and entry.retry_class == "receipt_required"
        assert entry.authority == "operator" and entry.approval_transition is None
    assert entries["machine-maintenance.run"].entry_point == ("brain", "machine-maintenance", "run")
    assert entries["machine-registry.sync"].entry_point == ("brain", "machine-registry", "sync")
