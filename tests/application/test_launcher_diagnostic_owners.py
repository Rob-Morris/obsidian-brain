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


def _invocation(tmp_path, *, authority=None, receipts=None):
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
        "machine_registry": {
            "path": str((tmp_path / ".config" / "brain" / "brains.json").resolve()),
            "brains_count": 0,
            "blocked": False,
            "blocked_reason": None,
            "changed": False,
            "drifted": True,
            "malformed": False,
            "malformed_rewritten": False,
            "stale_machine_registry_entries": [],
        },
        "counts": {
            "brains": 1,
            "repair_findings": 1,
            "stale_registry_entries": 0,
            "stale_machine_registry_entries": 0,
            "runtimes": 1,
            "orphan_candidates": 1,
        },
        "stale_registry_entries": [],
        "stale_machine_registry_entries": [],
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
                        "message": "MCP transport is stale.",
                        "repair": {
                            "scope": "mcp",
                            "command": "private shell detail",
                        },
                    }
                ],
            }
        ],
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

    result = _invocation(tmp_path, receipts=receipts).invoke(
        BrainDoctorRequest(
            (tmp_path / "Brain").resolve(),
            actionable=True,
            severity=DoctorSeverity.ERROR,
        )
    )

    assert result.status == "ok"
    assert result.result.healthy is False
    assert result.result.exit_code == 1
    assert result.result.machine.registry.state is DoctorRegistryState.DRIFTED
    assert result.result.machine.counts.orphan_candidates == 1
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
            "synchronise_registry": False,
        }
    ]
    assert receipts.values[-1].state is ReceiptState.NONE


def test_doctor_without_vault_returns_explicit_unscoped_state(tmp_path, monkeypatch):
    machine = _machine_report(tmp_path)
    machine["healthy"] = True
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
        lambda: BrainDoctorRequest(Path("relative")),
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
