"""The machine pass over Doctor's feed (DD-082, phase 4; no automatic family since DD-083)."""

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
from _launcher.contracts import CommandError, CommittedEffect, Error, ErrorCode, Ok, OutcomeReference, OutcomeUnknownDetails, ReceiptState, RequestErrorDetails
from _launcher.invocation import LauncherInvocation
from _launcher.machine_maintenance import (
    MachineMaintenanceClaimRequest,
    MachineMaintenanceDismissRequest,
    MachineMaintenanceListRequest,
    MachineMaintenanceReleaseRequest,
    MachineMaintenanceRunRequest,
    detect_machine,
)
from _launcher.owners import LAUNCHER_OWNERS
from _bootstrap.maintenance_findings import Disposition, MaintenanceFinding, Owner, finding_key, group_by_family
from _bootstrap.maintenance_summary import GroupOutcome
from _common import config_home
from _machine import maintenance, topology
from _repair_common import RepairFamily


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
                              code=code, scope=scope, owner=Owner.MACHINE, key=finding_key("machine", kind, subject),
                              subject=subject, evidence=evidence)


@pytest.fixture
def fake_detection(monkeypatch):
    findings = {"value": (), "scan": True}
    monkeypatch.setattr(machine_maintenance, "detect_machine", lambda _context: (tuple(findings["value"]), findings["scan"]))
    return findings


FAKE_AUTOMATIC = "fake_sync"


@pytest.fixture
def fake_automatic_family(monkeypatch):
    """No real machine family is automatic, so the pass mechanics run over an injected one."""
    family = RepairFamily(FAKE_AUTOMATIC, "registry.remove-stale", {}, Disposition.AUTOMATIC, Owner.MACHINE, False, "fake")
    monkeypatch.setattr(machine_maintenance, "MACHINE_FAMILIES", {**machine_maintenance.MACHINE_FAMILIES, FAKE_AUTOMATIC: family})
    monkeypatch.setattr(machine_maintenance, "AUTOMATIC_KINDS", (FAKE_AUTOMATIC,))
    return family


def _automatic():
    return _finding(FAKE_AUTOMATIC, {}, disposition=Disposition.AUTOMATIC, scope=FAKE_AUTOMATIC)


# ---------------------------------------------------------------------------
# Doctor on an unregistered Brain
# ---------------------------------------------------------------------------

def test_doctor_reports_an_unregistered_current_brain_without_writing(tmp_path, fake_home, monkeypatch, capsys):
    import doctor_machine

    vault = _vault(tmp_path, "Brain A")
    monkeypatch.setattr(maintenance, "find_live_brain_runtime_processes",
                        lambda runtime_pythons, *, scan=None: {"available": True, "processes": {}})
    monkeypatch.setattr(topology, "classify_brain_runtime", lambda *_a, **_k: {
        "status": "central_exact", "message": "ok", "healthy_runtime": True, "legacy_runtime_present": False,
        "selected_runtime": sys.executable, "expected_runtime": sys.executable, "legacy_runtime_dir": None,
        "legacy_runtime_python": None})
    monkeypatch.setattr(maintenance, "classify_brain_runtime", topology.classify_brain_runtime)
    monkeypatch.setattr(sys, "argv", ["doctor_machine.py", "--vault", str(vault), "--current-vault", str(vault),
                                      "--launcher", sys.executable])

    assert doctor_machine.main() == 0, "registering is a person's choice, never a health failure"
    out = capsys.readouterr().out
    assert "unregistered brains:" in out and f"register: brain register --request-json '{{\"vault_root\":\"{vault}\"}}'" in out
    assert not (config_home() / "brain" / "brains.json").exists()


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
        "stale_registry_entries": [{"alias": "old", "path": str(tmp_path / "Old")}],
        "unregistered_brains": [str(tmp_path / "Unregistered")],
        "runtimes": [{"python": "/venvs/x/bin/python", "orphan_candidate": True}],
        "brains": [{
            "path": str(vault), "alias": "a",
            "runtime": {"status": "legacy_vault_venv"},
            "repair_findings": [
                {"check": "mcp_registration:claude_python_mismatch", "message": "stale", "repair": {"scope": "mcp"}},
                {"check": "mcp_registration", "message": "drifted", "repair": {"scope": "mcp"}},
                {"check": "workspace_registry", "code": "workspace_registry_malformed", "message": "bad",
                 "repair": {"scope": "registry"}},
                {"check": "workspace_registry", "code": "workspace_folder_unreachable", "message": "gone"},
            ],
        }],
        "mcp_registrations": {"registrations": [
            {"client": "claude", "scope": "user", "path": "/home/.claude.json", "state": "current", "action": "x"},
            {"client": "codex", "scope": "user", "path": "/home/.codex/config.toml", "state": "modified", "action": "x"},
            # The owning Brain reports an unreachable folder; the machine pass does not repeat it.
            {"state": "unreachable", "path": "/gone", "message": "gone", "action": "x"},
        ]},
    }
    monkeypatch.setattr(maintenance, "collect_machine_summary", lambda **_kwargs: summary)
    monkeypatch.setattr(topology, "find_orphaned_brain_processes", lambda *, scan=None: {"available": True, "processes": [
        {"pid": 7, "ppid": 1, "command": "/venv/bin/brain-cli-python x", "role": "cli"}]})

    findings, scan_available = detect_machine(_context(tmp_path))

    kinds = sorted(item.check for item in findings)
    assert kinds == ["brain_repair", "brain_repair", "brain_unregistered", "legacy_installation",
                     "mcp_registration", "orphan_runtime", "orphaned_process", "stale_vault_registry"]
    assert scan_available is True
    assert all(item.disposition is Disposition.JUDGEMENT for item in findings), "nothing on the machine side is automatic"
    unregistered = next(item for item in findings if item.check == "brain_unregistered")
    assert unregistered.subject == {"path": str(tmp_path / "Unregistered")}
    groups = {group.check: group for group in group_by_family(findings)}
    assert machine_maintenance._guidance(groups["brain_unregistered"]) == (
        f"""brain register --request-json '{{"vault_root":"{tmp_path / "Unregistered"}"}}'"""
    )
    repairs = [item for item in findings if item.check == "brain_repair"]
    assert {item.code for item in repairs} == {"mcp"}, "Brain-owned and family-less findings belong to the Brain pass or a person"
    assert len({item.key for item in repairs}) == 1, "one key per (brain, scope)"
    registration = next(item for item in findings if item.check == "mcp_registration")
    assert registration.evidence == {"state": "modified"}


# ---------------------------------------------------------------------------
# The machine pass
# ---------------------------------------------------------------------------

def _sibling(result):
    calls = []

    def fake(context, request, *, invocation_id):
        calls.append((type(request).__name__, invocation_id, context.dry_run))
        return result(invocation_id) if callable(result) else result

    return fake, calls


def test_pass_runs_an_automatic_family_and_lists_the_rest(tmp_path, state_home, fake_detection, fake_automatic_family, monkeypatch):
    unregistered = str(tmp_path / "Unregistered")
    fake_detection["value"] = (_automatic(), _finding("stale_vault_registry", {"path": "/x"}),
                               _finding("brain_unregistered", {"path": unregistered}),
                               _finding("orphan_runtime", {"python": "/venvs/x/bin/python"}))
    effect = CommittedEffect("registry.remove-stale", "file:/registry")
    fake, calls = _sibling(Ok("registry.remove-stale", 1, object(), (effect,)))
    monkeypatch.setattr(nested_invocation, "invoke_sibling", fake)
    receipts = _Receipts()

    result = _invoke(tmp_path, MachineMaintenanceRunRequest(), receipts=receipts)

    assert result.status == "ok", getattr(result, "error", None)
    assert calls == [("RegistryRemoveStaleRequest", f"inv-machine-{result.result.pass_id}-{FAKE_AUTOMATIC}", False)]
    assert [(item.kind, item.outcome) for item in result.result.groups] == [(FAKE_AUTOMATIC, GroupOutcome.REPAIRED)]
    assert result.result.counts.needs_person == 3 and result.result.counts.failed == 0
    attention = {item.kind: item for item in result.result.attention}
    assert sorted(attention) == ["brain_unregistered", "orphan_runtime", "stale_vault_registry"]
    assert attention["orphan_runtime"].command == "brain runtime remove-orphans"
    assert attention["brain_unregistered"].command == f"""brain register --request-json '{{"vault_root":"{unregistered}"}}'"""
    assert result.committed_effects == (effect, CommittedEffect("machine-maintenance.summary", result.result.pass_id))
    summary = json.loads((state_home / "last-pass.json").read_text())
    assert summary["groups"] == {FAKE_AUTOMATIC: "repaired"} and summary["counts"]["needs_person"] == 3
    assert receipts.values[-1].state is ReceiptState.COMMITTED

    dry = _invoke(tmp_path, MachineMaintenanceRunRequest(), dry_run=True)
    assert dry.status == "ok" and dry.result.planned == (FAKE_AUTOMATIC,) and len(calls) == 1


def test_a_real_pass_has_no_automatic_family_and_writes_an_empty_summary(tmp_path, fake_home, state_home, monkeypatch):
    from dataclasses import replace

    vault = _vault(tmp_path, "Brain A")
    monkeypatch.setattr(maintenance, "find_live_brain_runtime_processes",
                        lambda runtime_pythons, *, scan=None: {"available": True, "processes": {}})
    monkeypatch.setattr(topology, "scan_processes", lambda: {"available": True, "processes": []})
    monkeypatch.setattr(nested_invocation, "invoke_sibling",
                        lambda *_args, **_kwargs: pytest.fail("no machine family is automatic, so nothing is invoked"))
    receipts = _Receipts()
    context = replace(_context(tmp_path, receipts=receipts), current_vault=vault)

    result = LauncherInvocation(context, LAUNCHER_CATALOGUE, LAUNCHER_OWNERS).invoke(MachineMaintenanceRunRequest())

    assert result.status == "ok", getattr(result, "error", None)
    assert result.result.groups == () and result.result.planned == ()
    assert "brain_unregistered" in {item.kind for item in result.result.attention}
    assert result.committed_effects == (CommittedEffect("machine-maintenance.summary", result.result.pass_id),)
    summary = json.loads((state_home / "last-pass.json").read_text())
    assert summary["groups"] == {} and summary["outcome"] == "ok"
    assert receipts.values[-1].state is ReceiptState.COMMITTED
    assert not (config_home() / "brain" / "brains.json").exists()


def test_a_retryable_sibling_conflict_defers_the_group(tmp_path, state_home, fake_detection, fake_automatic_family, monkeypatch):
    fake_detection["value"] = (_automatic(),)
    busy = Error("registry.remove-stale", 1, CommandError(ErrorCode.CONFLICT, "busy", RequestErrorDetails(None, "busy")),
                 retryable=True)
    fake, _calls = _sibling(busy)
    monkeypatch.setattr(nested_invocation, "invoke_sibling", fake)

    result = _invoke(tmp_path, MachineMaintenanceRunRequest())

    assert result.status == "ok"
    assert [(item.kind, item.outcome) for item in result.result.groups] == [(FAKE_AUTOMATIC, GroupOutcome.DEFERRED)]
    assert result.result.counts.deferred == 1


def test_unknown_launcher_sibling_is_recorded_unknown_and_reported_as_unknown_effects(tmp_path, state_home, fake_detection, fake_automatic_family, monkeypatch):
    fake_detection["value"] = (_automatic(),)

    def unknown(invocation_id):
        reference = OutcomeReference(invocation_id)
        return Error("registry.remove-stale", 1, CommandError(ErrorCode.COMMAND_OUTCOME_UNKNOWN, "lost",
                     OutcomeUnknownDetails(reference)), effects="unknown", outcome_reference=reference)

    fake, _calls = _sibling(unknown)
    monkeypatch.setattr(nested_invocation, "invoke_sibling", fake)

    result = _invoke(tmp_path, MachineMaintenanceRunRequest())

    assert result.status == "error" and result.effects == "unknown"
    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN and result.outcome_reference.invocation_id.endswith(FAKE_AUTOMATIC)
    assert "machine-maintenance.summary" in result.error.message, "the summary write is a known committed effect"
    summary = json.loads((state_home / "last-pass.json").read_text())
    assert summary["groups"] == {FAKE_AUTOMATIC: "unknown"} and summary["counts"]["failed"] == 1
    listed = _invoke(tmp_path, MachineMaintenanceListRequest())
    assert [(item.kind, item.last_outcome) for item in listed.result.items] == [(FAKE_AUTOMATIC, "unknown")]


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


def test_machine_decisions_claim_dismiss_release_over_the_machine_file(tmp_path, state_home, fake_detection, fake_automatic_family):
    stale = _finding("orphaned_process", {"pid": 7, "command": "x"})
    fake_detection["value"] = (_automatic(), stale)
    clock = _Clock()

    listed = _invoke(tmp_path, MachineMaintenanceListRequest(all=True), clock=clock)
    items = {item.kind: item for item in listed.result.items}
    assert items["orphaned_process"].command is None and items[FAKE_AUTOMATIC].command == "brain registry remove-stale"

    claimed = _invoke(tmp_path, MachineMaintenanceClaimRequest(stale.key, "rob"), clock=clock)
    assert claimed.status == "ok" and claimed.result.item.state == "held"
    assert json.loads((state_home / "decisions.json").read_text())["claims"][stale.key]["claimant"] == "rob"

    automatic = _invoke(tmp_path, MachineMaintenanceDismissRequest(items[FAKE_AUTOMATIC].key,
                                                                    items[FAKE_AUTOMATIC].fingerprint, "x", "rob"), clock=clock)
    assert automatic.status == "error" and automatic.error.code is ErrorCode.INVALID_REQUEST

    other = _invoke(tmp_path, MachineMaintenanceDismissRequest(stale.key, items["orphaned_process"].fingerprint, "x", "sam"), clock=clock)
    assert other.status == "error" and other.error.code is ErrorCode.CONFLICT

    released = _invoke(tmp_path, MachineMaintenanceReleaseRequest(stale.key, "rob"), clock=clock)
    assert released.status == "ok" and released.result.item.state == "open"

    dismissed = _invoke(tmp_path, MachineMaintenanceDismissRequest(stale.key, items["orphaned_process"].fingerprint,
                                                                    "unplugged drive", "rob"), clock=clock)
    assert dismissed.status == "ok" and dismissed.result.item.state == "quiet"
    quiet = _invoke(tmp_path, MachineMaintenanceListRequest(), clock=clock)
    assert quiet.result.hidden_quiet == 1 and [item.kind for item in quiet.result.items] == []

    # A claimed automatic group is held by the pass; an expired claim is a review item.
    clock.now_value = NOW + timedelta(hours=2)
    assert _invoke(tmp_path, MachineMaintenanceClaimRequest(items[FAKE_AUTOMATIC].key, "rob"), clock=clock).status == "ok"
    held = _invoke(tmp_path, MachineMaintenanceRunRequest(), clock=clock)
    assert held.result.groups == () and [item.state for item in held.result.attention] == ["held"]
    clock.now_value = NOW + timedelta(hours=4)
    expired = _invoke(tmp_path, MachineMaintenanceRunRequest(), clock=clock)
    assert [item.kind for item in expired.result.groups] == [FAKE_AUTOMATIC], "only a live claim withholds"
    assert expired.result.counts.claim_expired == 1


def test_machine_maintenance_entries_match_the_launcher_contract():
    entries = {entry.command_id: entry for entry in LAUNCHER_CATALOGUE.entries}
    assert entries["machine-maintenance.list"].effect_class == "none" and entries["machine-maintenance.list"].authority == "reader"
    for command_id in ("machine-maintenance.run", "machine-maintenance.claim", "machine-maintenance.dismiss",
                       "machine-maintenance.release"):
        entry = entries[command_id]
        assert entry.effect_class == "machine_mutation" and entry.retry_class == "receipt_required"
        assert entry.authority == "operator" and entry.approval_transition is None
    assert entries["machine-maintenance.run"].entry_point == ("brain", "machine-maintenance", "run")
    assert "machine-registry.sync" not in entries


@pytest.mark.parametrize("case", ["uninspectable-folder", "malformed-registry"])
def test_a_brain_level_registration_item_is_listed_by_path_not_crashed_on(tmp_path, fake_home, state_home, monkeypatch, case):
    """An invalid registration cause (DD-083 item 12) names a vault, not a client slot."""
    import os
    from dataclasses import replace
    import vault_registry
    import workspace_registry

    if case == "uninspectable-folder" and (sys.platform == "win32" or os.geteuid() == 0):
        pytest.skip("POSIX permission bits that bind the test user")
    vault = _vault(tmp_path, "Brain A")
    vault_registry.register(vault, "brain-a")
    locked = tmp_path / "locked"
    if case == "uninspectable-folder":
        (locked / "ws").mkdir(parents=True)
        workspace_registry.register_workspace(vault, "ws", locked / "ws")
        locked.chmod(0)
    else:
        (vault / workspace_registry.REGISTRY_REL).parent.mkdir(parents=True, exist_ok=True)
        (vault / workspace_registry.REGISTRY_REL).write_text('{"workspaces": {"bad": 5}}\n')
    monkeypatch.setattr(maintenance, "find_live_brain_runtime_processes",
                        lambda runtime_pythons, *, scan=None: {"available": True, "processes": {}})
    monkeypatch.setattr(topology, "scan_processes", lambda: {"available": True, "processes": []})
    context = replace(_context(tmp_path), current_vault=vault)
    try:
        result = LauncherInvocation(context, LAUNCHER_CATALOGUE, LAUNCHER_OWNERS).invoke(MachineMaintenanceRunRequest())
    finally:
        if case == "uninspectable-folder":
            locked.chmod(0o755)

    assert result.status == "ok", getattr(result, "error", None)
    registrations = [item for item in result.result.attention if item.kind == "mcp_registration"]
    assert {"path": str(vault)} in [dict(item.subject) for item in registrations]
