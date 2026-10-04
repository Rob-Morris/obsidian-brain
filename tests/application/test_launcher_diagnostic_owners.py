"""Typed launcher ownership for Doctor and operator key generation."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_DIR = REPO_ROOT / "cli"
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

from launcher_catalogue import LAUNCHER_CATALOGUE
from _launcher.context import LauncherContext, ProviderBindings
from _launcher.contracts import ErrorCode, ReceiptState
from _launcher.doctor import (
    BrainDoctorRequest,
    DoctorRegistryState,
    DoctorSeverity,
    DoctorVaultState,
)
from _launcher.invocation import LauncherInvocation
from _launcher.operator import OperatorGenerateKeyRequest
from _launcher.owners import LAUNCHER_OWNERS
import doctor as doctor_script
import generate_key


NOW = datetime.fromisoformat("2026-08-10T05:00:00+10:00")


class _Authority:
    def __init__(self, allowed=True):
        self.allowed = allowed

    def allows(self, **_kwargs):
        return self.allowed


class _Receipts:
    def __init__(self):
        self.values = []

    def write(self, receipt):
        self.values.append(receipt)


class _Clock:
    def now(self):
        return NOW


def _invocation(tmp_path, *, authority=None, receipts=None, current_vault=None):
    context = LauncherContext(
        profile="operator",
        authority=authority or _Authority(),
        providers=ProviderBindings(),
        correlation_id="corr-diagnostics",
        invocation_id="inv-diagnostics",
        receipt_writer=receipts or _Receipts(),
        clock=_Clock(),
        caller_dir=tmp_path.resolve(),
        home_dir=tmp_path.resolve(),
        cli_version="2.0.0",
        cli_binary=(tmp_path / "bin" / "brain").resolve(),
        launcher_python=Path(sys.executable).resolve(),
        current_vault=current_vault,
    )
    return LauncherInvocation(context, LAUNCHER_CATALOGUE, LAUNCHER_OWNERS)


def _cli_report(tmp_path):
    return {
        "version": "2.0.0",
        "binary": str((tmp_path / "bin" / "brain").resolve()),
        "binary_dir": str((tmp_path / "bin").resolve()),
        "path_ok": True,
        "launcher_python": str(Path(sys.executable).resolve()),
        "launcher_version": "Python 3.12.13",
        "launcher_probe_failed": False,
    }


def _machine_report(tmp_path):
    vault = (tmp_path / "Brain").resolve()
    expected = (tmp_path / ".brain" / "venvs" / "expected" / "bin" / "python").resolve()
    orphan = (tmp_path / ".brain" / "venvs" / "orphan" / "bin" / "python").resolve()
    return {
        "healthy": False,
        "tidy": False,
        "live_process_scan_available": True,
        "venvs_root": str((tmp_path / ".brain" / "venvs").resolve()),
        "registry": {
            "path": str((tmp_path / ".config" / "brain" / "vaults").resolve()),
            "brains_count": 1,
            "stale": True,
        },
        "counts": {
            "brains": 1,
            "repair_findings": 1,
            "stale_registry_entries": 1,
            "unregistered_brains": 0,
            "runtimes": 1,
            "orphan_candidates": 1,
        },
        "stale_registry_entries": [{"alias": "gone", "path": str((tmp_path / "Gone").resolve()),
                                    "reason": "not_a_brain", "guidance": "brain registry remove-stale",
                                    "explanation": "Brain ID 'gone' points at a path that is not an installed Brain"}],
        "unregistered_brains": [],
        "brains": [
            {
                "alias": "brain",
                "path": str(vault),
                "sources": ["vault_registry"],
                "runtime": {
                    "status": "missing_runtime",
                    "message": "Brain has no central runtime.",
                    "selected_runtime": None,
                    "expected_runtime": str(expected),
                    "legacy_runtime_present": False,
                },
                "repair_findings": [
                    {
                        "check": "mcp_registration",
                        "message": "MCP transport is stale.",
                        "repair": {
                            "scope": "mcp",
                            "command": "private shell detail",
                        },
                    }
                ],
            }
        ],
        "memory": {
            "process_count": 2,
            "measured_count": 1,
            "total_bytes": 1650 * 1024**2,
            "process_warn_bytes": 512 * 1024**2,
            "total_warn_bytes": 2 * 1024**3,
            "total_over_threshold": False,
            "heavy_processes": [
                {"pid": 4242, "footprint_bytes": 1650 * 1024**2, "command": "python -m brain_mcp.server"}
            ],
        },
        "runtimes": [
            {
                "python": str(orphan),
                "orphan_candidate": True,
            }
        ],
    }


def _vault_report(tmp_path):
    return {
        "in_scope": True,
        "vault_root": str((tmp_path / "Brain").resolve()),
        "available": True,
        "exit_code": 1,
        "message": None,
        "result": {
            "summary": {"errors": 1, "warnings": 0, "info": 0},
            "findings": [
                {
                    "check": "workspace-registry",
                    "severity": "error",
                    "file": None,
                    "message": "Registry is malformed.",
                    "repair": {
                        "scope": "registry",
                        "command": "private shell detail",
                    },
                }
            ],
        },
    }


def test_remaining_read_owners_match_catalogue_authority_and_effects():
    entries = {entry.command_id: entry for entry in LAUNCHER_CATALOGUE.entries}
    owners = {owner.command_id: owner for owner in LAUNCHER_OWNERS.entries}

    assert owners["brain.doctor"].owner_ref == "_launcher.doctor:doctor"
    assert entries["brain.doctor"].authority == "reader"
    assert owners["operator.generate-key"].owner_ref == (
        "_launcher.operator:generate_key"
    )
    assert entries["operator.generate-key"].authority == "operator"
    for command_id in ("brain.doctor", "operator.generate-key"):
        assert entries[command_id].effect_class == "none"
        assert entries[command_id].retry_class == "safe"
        assert entries[command_id].required_providers == ()


def test_doctor_returns_bounded_typed_diagnosis_without_registry_sync(
    tmp_path,
    monkeypatch,
):
    calls = []
    monkeypatch.setattr(
        doctor_script,
        "collect_cli_diagnosis",
        lambda **_kwargs: _cli_report(tmp_path),
    )

    def _machine(**kwargs):
        calls.append(kwargs)
        return _machine_report(tmp_path)

    monkeypatch.setattr(doctor_script.doctor_machine, "collect_machine_summary", _machine)
    monkeypatch.setattr(
        doctor_script,
        "collect_vault_diagnosis",
        lambda **_kwargs: _vault_report(tmp_path),
    )
    receipts = _Receipts()

    result = _invocation(tmp_path, receipts=receipts, current_vault=(tmp_path / "Brain").resolve()).invoke(
        BrainDoctorRequest(actionable=True, severity=DoctorSeverity.ERROR)
    )

    assert result.status == "ok"
    assert result.result.healthy is False
    assert result.result.exit_code == 1
    assert result.result.machine.registry.state is DoctorRegistryState.STALE
    assert result.result.machine.registry.brains_count == 1
    [gone] = result.result.machine.stale_vault_registry_entries
    assert (gone.alias, gone.reason, gone.guidance) == ("gone", "not_a_brain", "brain registry remove-stale")
    assert gone.explanation == "Brain ID 'gone' points at a path that is not an installed Brain"
    assert result.result.machine.counts.orphan_candidates == 1
    assert result.result.machine.memory.measured_count == 1
    assert result.result.machine.memory.heavy_processes[0].pid == 4242
    assert result.result.machine.brains[0].repair_findings[0].command_id == "mcp.repair"
    assert not hasattr(result.result.machine.brains[0].repair_findings[0], "command")
    assert result.result.vault.state is DoctorVaultState.CHECKED
    assert result.result.vault.findings[0].file is None
    assert result.result.vault.findings[0].repair_command_id == (
        "workspace.repair-registry"
    )
    assert calls == [
        {
            "current_vault": str((tmp_path / "Brain").resolve()),
            "launcher_python": str(Path(sys.executable).resolve()),
            "measure_memory": True,
            "cli_binary": str((tmp_path / "bin" / "brain").resolve()),
        }
    ]
    assert receipts.values[-1].state is ReceiptState.NONE


def test_doctor_projects_findings_without_a_repair_family_and_unregistered_brains(tmp_path, monkeypatch):
    machine = _machine_report(tmp_path)
    unregistered = str((tmp_path / "Unregistered").resolve())
    machine["healthy"] = True
    machine["registry"] = {"path": machine["registry"]["path"], "brains_count": 0, "stale": False}
    machine["stale_registry_entries"] = []
    machine["unregistered_brains"] = [unregistered]
    machine["brains"][0]["repair_findings"] = [
        {"check": "workspace_registry", "code": "workspace_folder_unreachable", "file": ".brain/local/workspaces.json#a",
         "message": "Linked folder is unreachable."},
        {"check": "workspace_registry", "code": "workspace_registry_malformed", "file": ".brain/local/workspaces.json",
         "message": "Registry is malformed.", "repair": {"scope": "registry", "command": "x"}},
    ]
    machine["counts"].update(repair_findings=2, stale_registry_entries=0, unregistered_brains=1)
    monkeypatch.setattr(doctor_script, "collect_cli_diagnosis", lambda **_kwargs: _cli_report(tmp_path))
    monkeypatch.setattr(doctor_script.doctor_machine, "collect_machine_summary", lambda **_kwargs: machine)
    monkeypatch.setattr(doctor_script, "collect_vault_diagnosis", lambda **_kwargs: {
        "in_scope": False, "vault_root": None, "available": False, "exit_code": 0, "message": "none in scope", "result": None,
    })

    result = _invocation(tmp_path).invoke(BrainDoctorRequest())

    assert result.status == "ok"
    assert result.result.machine.registry.state is DoctorRegistryState.CURRENT
    assert result.result.machine.unregistered_brains == (unregistered,)
    assert result.result.machine.counts.unregistered_brains == 1
    findings = result.result.machine.brains[0].repair_findings
    assert (findings[0].scope, findings[0].command_id) == (None, None), "a family-less finding names no command"
    assert (findings[0].check, findings[0].code, findings[0].file) == (
        "workspace_registry", "workspace_folder_unreachable", ".brain/local/workspaces.json#a"
    ), "its own identity survives the projection"
    assert (findings[1].scope, findings[1].command_id, findings[1].file) == (
        "registry", "workspace.repair-registry", ".brain/local/workspaces.json"
    )
    assert (findings[1].check, findings[1].code) == ("workspace_registry", "workspace_registry_malformed")


@pytest.mark.parametrize("scope, command_id", [(None, "mcp.repair"), ("mcp", None)])
def test_doctor_repair_finding_pairs_scope_with_command(scope, command_id):
    from _launcher.doctor import DoctorRepairFinding

    with pytest.raises(ValueError, match="set or absent together"):
        DoctorRepairFinding("mcp_registration", scope, "m", command_id)


@pytest.mark.parametrize("user_registered", [False, True])
def test_doctor_without_vault_returns_explicit_unscoped_state(tmp_path, monkeypatch, user_registered):
    machine = _machine_report(tmp_path)
    machine["healthy"] = True
    if user_registered:
        machine["mcp_registrations"] = {"registrations": [{
            "path": str(tmp_path / ".claude.json"), "state": "current", "client": "claude",
            "scope": "user", "action": "reinstall CLI", "message": None,
        }]}
    monkeypatch.setattr(
        doctor_script,
        "collect_cli_diagnosis",
        lambda **_kwargs: _cli_report(tmp_path),
    )
    monkeypatch.setattr(
        doctor_script.doctor_machine,
        "collect_machine_summary",
        lambda **_kwargs: machine,
    )
    monkeypatch.setattr(
        doctor_script,
        "collect_vault_diagnosis",
        lambda **_kwargs: {
            "in_scope": False,
            "vault_root": None,
            "available": False,
            "exit_code": 0,
            "message": "none in scope",
            "result": None,
        },
    )

    result = _invocation(tmp_path).invoke(BrainDoctorRequest())

    assert result.result.vault.state is DoctorVaultState.NOT_SCOPED
    assert result.result.vault.vault_root is None
    assert not result.result.cli.mcp_bootstrap_available
    assert result.result.healthy is not user_registered


def test_operator_key_generation_returns_typed_candidates(tmp_path, monkeypatch):
    values = iter(
        (
            generate_key.OperatorKeyMaterial("amber-anchor-anvil", "a" * 64),
            generate_key.OperatorKeyMaterial("apple-arrow-aspen", "b" * 64),
        )
    )
    monkeypatch.setattr(generate_key, "generate_key_material", lambda: next(values))

    result = _invocation(tmp_path).invoke(OperatorGenerateKeyRequest(count=2))

    assert result.status == "ok"
    assert tuple(candidate.key for candidate in result.result.candidates) == (
        "amber-anchor-anvil",
        "apple-arrow-aspen",
    )
    assert tuple(candidate.sha256 for candidate in result.result.candidates) == (
        "a" * 64,
        "b" * 64,
    )


def test_diagnostic_authority_denial_precedes_secret_generation(tmp_path, monkeypatch):
    monkeypatch.setattr(
        generate_key,
        "generate_key_material",
        lambda: pytest.fail("denied key generation must not execute"),
    )

    result = _invocation(tmp_path, authority=_Authority(False)).invoke(
        OperatorGenerateKeyRequest()
    )

    assert result.error.code is ErrorCode.AUTHORITY_DENIED
    assert result.effects == "none"


def test_unexpected_doctor_failure_is_privacy_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(
        doctor_script,
        "collect_cli_diagnosis",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("private path")),
    )

    result = _invocation(tmp_path).invoke(BrainDoctorRequest())

    assert result.error.code is ErrorCode.INTERNAL_ERROR
    assert "private path" not in repr(result)


@pytest.mark.parametrize(
    "request_factory",
    (
        lambda: BrainDoctorRequest(actionable="yes"),
        lambda: BrainDoctorRequest(severity="error"),
        lambda: OperatorGenerateKeyRequest(0),
        lambda: OperatorGenerateKeyRequest(21),
        lambda: OperatorGenerateKeyRequest(True),
    ),
)
def test_diagnostic_requests_reject_invalid_intent(request_factory):
    with pytest.raises(ValueError):
        request_factory()


def test_vault_check_runner_projects_the_target_payload_and_skips_pre_cutover_targets(tmp_path, monkeypatch):
    import _repair_common
    from _launcher.doctor import _check_envelope, _vault_finding, run_vault_check

    old = (tmp_path / "Old").resolve()
    (old / ".brain-core").mkdir(parents=True)
    (old / ".brain-core" / "VERSION").write_text("0.54.0\n")
    assert run_vault_check(old, actionable=False, severity=None) is None

    monkeypatch.setattr(_repair_common, "find_launcher_binary", lambda: "/opt/bin/brain")
    vault = (tmp_path / "Brain").resolve()
    envelope = _check_envelope(vault, {
        "errors": 1, "warnings": 0, "info": 0,
        "findings": [
            {"check": "router", "severity": "error", "file": None, "message": "missing", "fix": None,
             "repair": {"scope": "router", "description": "d", "command_id": "runtime.refresh-router"}, "code": None},
            {"check": "future", "severity": "error", "file": "x.md", "message": "new", "fix": "Edit it",
             "repair": {"scope": "holograms", "description": "d", "command_id": "artefact.repair-holograms"}, "code": None},
        ],
    }, actionable=True)

    assert envelope["summary"] == {"errors": 1, "warnings": 0, "info": 0}
    assert envelope["findings"][0]["repair"]["command"] == f"/opt/bin/brain --vault {vault} runtime refresh-router"
    assert envelope["findings"][1]["repair"]["command"] == f"brain --vault {vault} artefact repair-holograms"
    assert envelope["findings"][1]["fix"] == "Edit it"
    # Doctor reads the target's command identity rather than its own bundled table.
    assert _vault_finding(envelope["findings"][1]).repair_command_id == "artefact.repair-holograms"
    assert _vault_finding({"check": "x", "severity": "info", "file": None, "message": "m",
                           "repair": {"scope": "router", "command": "python repair.py router"}}
                          ).repair_command_id == "runtime.refresh-router"


def _doctor_entry_point(tmp_path, monkeypatch, *, default=None):
    """Run ``brain doctor`` through the real launcher entry point on an isolated machine.

    Doctor's machine and CLI collectors are stubbed; the real vault-section
    collector runs over a clean ``vault.check`` result, so the reported vault
    section shows which Brain the launcher selection scoped. ``default`` makes
    the selected Brain (``"same"``) or a second Brain (``"other"``) the machine
    default.
    """
    import vault_registry
    from _launcher import doctor as launcher_doctor
    from _local_cli.main import run

    def brain(name):
        root = tmp_path / name
        (root / ".brain-core").mkdir(parents=True)
        (root / ".brain-core" / "VERSION").write_text("0.70.10\n", encoding="utf-8")
        return root.resolve()

    home = tmp_path / "home"
    home.mkdir()
    vault = brain("Brain")
    vault_registry.register(str(vault), "my-brain")
    if default == "same":
        vault_registry.set_default("my-brain")
    elif default == "other":
        vault_registry.register(str(brain("Other")), "other-brain")
        vault_registry.set_default("other-brain")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("BRAIN_CLI_BINARY", str(REPO_ROOT / "cli" / "brain"))
    monkeypatch.setenv("BRAIN_CLI_DISTRIBUTION_ROOT", str(REPO_ROOT))
    monkeypatch.delenv("BRAIN_VAULT_ROOT", raising=False)
    monkeypatch.delenv("BRAIN_WORKSPACE_DIR", raising=False)
    machine_calls = []
    checked = []
    machine = _machine_report(tmp_path)

    def _machine(**kwargs):
        machine_calls.append(kwargs["current_vault"])
        return machine

    def _vault_check(vault_root, *, actionable, severity):
        checked.append(vault_root)
        return {"summary": {"errors": 0, "warnings": 0, "info": 0}, "findings": []}

    monkeypatch.setattr(doctor_script, "collect_cli_diagnosis", lambda **_kwargs: _cli_report(tmp_path))
    monkeypatch.setattr(doctor_script.doctor_machine, "collect_machine_summary", _machine)
    monkeypatch.setattr(launcher_doctor, "run_vault_check", _vault_check)
    return vault, elsewhere, run, machine_calls, checked


def _bind(folder: Path, brain_id: str) -> Path:
    manifest = folder / ".brain" / "local" / "workspace.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(f"brain: {brain_id}\nslug: example\n", encoding="utf-8")
    return folder


def _bound(tmp_path, brain_id="my-brain"):
    folder = tmp_path / "bound"
    folder.mkdir()
    return _bind(folder, brain_id)


@pytest.mark.parametrize("default", ["same", "other"])
@pytest.mark.parametrize(
    "selection",
    ["vault", "brain", "vault_root_env", "bound_folder", "workspace", "workspace_env", "inside_vault"],
)
def test_doctor_entry_point_scopes_the_launcher_selected_brain(tmp_path, monkeypatch, capsys, selection, default):
    import json

    vault, elsewhere, run, machine_calls, checked = _doctor_entry_point(tmp_path, monkeypatch, default=default)
    argv = []
    if selection == "vault":
        argv = ["--vault", str(vault)]
    elif selection == "brain":
        argv = ["--brain", "my-brain"]
    elif selection == "vault_root_env":
        monkeypatch.setenv("BRAIN_VAULT_ROOT", str(vault))
    elif selection == "bound_folder":
        monkeypatch.chdir(_bind(elsewhere, "my-brain"))
    elif selection == "workspace":
        argv = ["--workspace", str(_bound(tmp_path))]
    elif selection == "workspace_env":
        monkeypatch.setenv("BRAIN_WORKSPACE_DIR", str(_bound(tmp_path)))
    elif selection == "inside_vault":
        monkeypatch.chdir(vault)

    run([*argv, "doctor", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok", payload
    assert payload["result"]["vault"]["state"] == "checked"
    assert payload["result"]["vault"]["vault_root"] == str(vault)
    assert checked == [vault]
    assert machine_calls == [str(vault)]


def test_doctor_in_a_session_job_checks_the_job_brain(tmp_path, monkeypatch, capsys):
    """A ``brain session run`` job exports its Brain as ``BRAIN_VAULT_ROOT``, which selects it explicitly."""
    import json
    from types import SimpleNamespace

    import _local_cli.main as cli_main

    vault, _elsewhere, run, machine_calls, checked = _doctor_entry_point(tmp_path, monkeypatch, default="same")
    job = SimpleNamespace(kind="cli-job", close=lambda: None)
    monkeypatch.setattr(cli_main, "OwnerAttachment", SimpleNamespace(capture=lambda: job))
    monkeypatch.setenv("BRAIN_VAULT_ROOT", str(vault))

    run(["doctor", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["result"]["vault"]["state"] == "checked"
    assert checked == [vault] and machine_calls == [str(vault)]


@pytest.mark.parametrize("fallback", ["default", "dangling_default", "nothing"])
def test_doctor_with_only_a_machine_fallback_stays_machine_wide(tmp_path, monkeypatch, capsys, fallback):
    import json

    vault, _elsewhere, run, machine_calls, checked = _doctor_entry_point(
        tmp_path, monkeypatch, default=None if fallback == "nothing" else "same")
    if fallback == "dangling_default":
        (vault / ".brain-core" / "VERSION").unlink()

    run(["doctor", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok", payload
    assert payload["result"]["vault"]["state"] == "not_scoped"
    assert payload["result"]["vault"]["vault_root"] is None
    assert payload["result"]["vault"]["message"] == "none in scope (select one with --vault or --brain, or run from a vault or a linked workspace)"
    assert checked == []
    assert machine_calls == [None]


@pytest.mark.parametrize(
    "selection, message",
    [
        ("stale_bound_folder", "cannot be resolved: Brain id 'gone-brain' is not in the registry"),
        ("malformed_bound_folder", "failed to load"),
        ("stale_workspace_env", "BRAIN_WORKSPACE_DIR points to a workspace that cannot be resolved"),
        ("unbound_workspace_env", "that workspace has no Brain binding"),
        ("vault_not_a_brain", "not an installed local Brain"),
        ("unknown_brain", "registered local Brain not found: unknown"),
    ],
)
def test_doctor_reports_a_selection_that_fails_before_the_machine_fallbacks(
    tmp_path, monkeypatch, capsys, selection, message,
):
    """A broken selection fails Doctor with its own message, even with a usable machine default."""
    _vault, elsewhere, run, machine_calls, checked = _doctor_entry_point(tmp_path, monkeypatch, default="same")
    argv = []
    if selection == "stale_bound_folder":
        monkeypatch.chdir(_bind(elsewhere, "gone-brain"))
    elif selection == "malformed_bound_folder":
        manifest = elsewhere / ".brain" / "local" / "workspace.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("brain: [unclosed\n", encoding="utf-8")
    elif selection == "stale_workspace_env":
        monkeypatch.setenv("BRAIN_WORKSPACE_DIR", str(_bound(tmp_path, "gone-brain")))
    elif selection == "unbound_workspace_env":
        monkeypatch.setenv("BRAIN_WORKSPACE_DIR", str(elsewhere))
    elif selection == "vault_not_a_brain":
        argv = ["--vault", str(elsewhere)]
    elif selection == "unknown_brain":
        argv = ["--brain", "unknown"]

    code = run([*argv, "doctor", "--json"])

    captured = capsys.readouterr()
    assert code == 4, captured
    assert captured.out == ""
    assert message in captured.err
    assert checked == [] and machine_calls == []


def test_doctor_request_no_longer_names_a_vault(tmp_path, monkeypatch, capsys):
    vault, _elsewhere, run, machine_calls, checked = _doctor_entry_point(tmp_path, monkeypatch)

    code = run(["doctor", "--request-json", f'{{"current_vault":"{vault}"}}', "--json"])

    captured = capsys.readouterr()
    assert code != 0
    assert "unknown fields: current_vault" in captured.err
    assert checked == [] and machine_calls == []
