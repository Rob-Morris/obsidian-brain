"""Typed launcher ownership for legacy Brain runtime migration."""

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
from _launcher.invocation import LauncherInvocation
from _launcher.machine import (
    LegacyBrainIdTarget,
    LegacyBrainPathTarget,
    LegacyMigrationOperation,
    LegacyMigrationStep,
    LegacyMigrationStepStatus,
    LegacyMigrationStatus,
    LegacyMigrationTarget,
    MachineMigrateLegacyRequest,
)
from _launcher.owners import LAUNCHER_OWNERS
from _machine import maintenance


NOW = datetime.fromisoformat("2026-08-10T07:00:00+10:00")


class _Authority:
    def allows(self, **_kwargs):
        return True


class _CallerFilesystem:
    provider_id = "caller_filesystem"

    def __init__(self, available=True):
        self.available = available


class _Receipts:
    def __init__(self):
        self.values = []

    def write(self, receipt):
        self.values.append(receipt)


class _Clock:
    def now(self):
        return NOW


def _invocation(
    tmp_path,
    *,
    provider=True,
    provider_available=True,
    dry_run=False,
    receipts=None,
    current_vault=None,
):
    providers = ((_CallerFilesystem(provider_available),) if provider else ())
    context = LauncherContext(
        profile="operator",
        authority=_Authority(),
        providers=ProviderBindings(providers),
        correlation_id="corr-machine-migrate",
        invocation_id="inv-machine-migrate",
        receipt_writer=receipts or _Receipts(),
        clock=_Clock(),
        caller_dir=tmp_path.resolve(),
        home_dir=tmp_path.resolve(),
        cli_version="2.0.0",
        cli_binary=(tmp_path / "bin" / "brain").resolve(),
        launcher_python=Path(sys.executable).resolve(),
        current_vault=(current_vault.resolve() if current_vault is not None else None),
        dry_run=dry_run,
    )
    return LauncherInvocation(context, LAUNCHER_CATALOGUE, LAUNCHER_OWNERS)


def _summary():
    return {"brains": []}


def _step(name, status, message=None, **extra):
    return {
        "name": name,
        "status": status,
        "message": message or f"{name} {status}",
        **extra,
    }


def _result(vault_root, *, status, target_status=None, steps=None):
    targets = []
    if target_status is not None:
        targets.append(
            {
                "brain": {"alias": "legacy-brain", "path": str(vault_root)},
                "label": "legacy-brain",
                "status": target_status,
                "steps": list(steps or []),
            }
        )
    return {
        "action": "migrate-legacy",
        "dry_run": status == "planned",
        "status": status,
        "steps": [_step("selection", "noop")],
        "targets": targets,
    }


def _patch_action(monkeypatch, result, calls=None):
    monkeypatch.setattr(
        maintenance,
        "collect_machine_summary",
        lambda **kwargs: (calls.append(("collect", kwargs)) if calls is not None else None)
        or _summary(),
    )
    monkeypatch.setattr(
        maintenance,
        "migrate_legacy_brains",
        lambda _summary_value, **kwargs: (
            calls.append(("migrate", kwargs)) if calls is not None else None
        )
        or result,
    )


def test_machine_migrate_owner_matches_machine_global_contract():
    entry = next(
        item
        for item in LAUNCHER_CATALOGUE.entries
        if item.command_id == "machine.migrate-legacy"
    )
    owner = next(
        item
        for item in LAUNCHER_OWNERS.entries
        if item.command_id == "machine.migrate-legacy"
    )

    assert entry.owner_ref == owner.owner_ref == "_launcher.machine:migrate_legacy"
    assert entry.authority == "operator"
    assert entry.effect_class == "machine_mutation"
    assert entry.retry_class == "receipt_required"
    assert entry.required_providers == ("caller_filesystem",)
    assert {item.projection: item.supported for item in entry.projections} == {
        "cli": True,
        "launcher": True,
        "mcp": False,
        "python": False,
        "script": False,
    }


def test_machine_migrate_request_requires_a_typed_selector(tmp_path):
    with pytest.raises(ValueError, match="target must be typed"):
        MachineMigrateLegacyRequest(target="legacy-brain")
    with pytest.raises(ValueError, match="canonical slug"):
        LegacyBrainIdTarget("Legacy Brain")
    with pytest.raises(ValueError, match="absolute Path"):
        LegacyBrainPathTarget(Path("relative"))

    assert LegacyBrainIdTarget("legacy-brain").brain_id == "legacy-brain"
    assert LegacyBrainPathTarget(tmp_path.resolve()).vault_root == tmp_path.resolve()


def test_machine_migrate_result_rejects_a_non_string_brain_id(tmp_path):
    with pytest.raises(ValueError, match="canonical slug"):
        LegacyMigrationTarget(
            brain_id=7,
            vault_root=str(tmp_path.resolve()),
            status=LegacyMigrationStatus.NOOP,
            steps=(
                LegacyMigrationStep(
                    LegacyMigrationOperation.VERIFY,
                    LegacyMigrationStepStatus.NOOP,
                    "already migrated",
                ),
            ),
        )


def test_machine_migrate_requires_an_available_caller_filesystem(tmp_path):
    request = MachineMigrateLegacyRequest()

    missing = _invocation(tmp_path, provider=False).invoke(request)
    unavailable = _invocation(
        tmp_path,
        provider_available=False,
    ).invoke(request)

    assert missing.error.code is ErrorCode.CAPABILITY_UNAVAILABLE
    assert missing.error.details.missing == ("provider:caller_filesystem",)
    assert unavailable.error.code is ErrorCode.CAPABILITY_UNAVAILABLE
    assert unavailable.error.details.missing == ("capability:caller_filesystem",)


def test_machine_migrate_uses_typed_selector_and_trusted_current_vault(
    tmp_path,
    monkeypatch,
):
    current_vault = (tmp_path / "current").resolve()
    current_vault.mkdir()
    calls = []
    _patch_action(
        monkeypatch,
        _result(current_vault, status="noop"),
        calls,
    )

    result = _invocation(
        tmp_path,
        current_vault=current_vault,
    ).invoke(
        MachineMigrateLegacyRequest(LegacyBrainIdTarget("legacy-brain"))
    )

    assert result.result.status is LegacyMigrationStatus.NOOP
    assert calls[0][1]["current_vault"] == str(current_vault)
    assert calls[0][1]["synchronise_registry"] is False
    assert calls[1][1]["selector"] == "legacy-brain"


def test_machine_migrate_dry_run_returns_typed_plan_without_effects(
    tmp_path,
    monkeypatch,
):
    vault = (tmp_path / "legacy").resolve()
    steps = [
        _step("runtime", "planned", outcome="none"),
        _step("legacy_venv", "planned", path=str(vault / ".venv")),
    ]
    _patch_action(
        monkeypatch,
        _result(vault, status="planned", target_status="planned", steps=steps),
    )
    receipts = _Receipts()

    result = _invocation(
        tmp_path,
        dry_run=True,
        receipts=receipts,
    ).invoke(MachineMigrateLegacyRequest())

    assert result.result.status is LegacyMigrationStatus.PLANNED
    assert result.result.targets[0].status is LegacyMigrationStatus.PLANNED
    assert tuple(step.operation for step in result.result.targets[0].steps) == (
        LegacyMigrationOperation.RUNTIME,
        LegacyMigrationOperation.LEGACY_RUNTIME,
    )
    assert result.committed_effects == ()
    assert receipts.values[-1].state is ReceiptState.COMMITTED


def test_machine_migrate_success_reports_each_committed_effect(
    tmp_path,
    monkeypatch,
):
    vault = (tmp_path / "legacy").resolve()
    legacy_dir = vault / ".venv"
    steps = [
        _step("runtime", "changed", outcome="committed"),
        _step("mcp", "noop", outcome="none"),
        _step("registry", "changed", outcome="committed"),
        _step("legacy_venv", "changed", path=str(legacy_dir)),
        _step("verify", "noop"),
    ]
    _patch_action(
        monkeypatch,
        _result(vault, status="ok", target_status="ok", steps=steps),
    )

    result = _invocation(tmp_path).invoke(MachineMigrateLegacyRequest())

    assert result.result.status is LegacyMigrationStatus.CHANGED
    assert tuple(effect.subject for effect in result.committed_effects) == (
        f"brain-runtime:{vault}",
        f"brain-registry:{vault}",
        f"legacy-runtime:{legacy_dir}",
    )


def test_machine_migrate_known_partial_enumerates_committed_scopes(
    tmp_path,
    monkeypatch,
):
    vault = (tmp_path / "legacy").resolve()
    steps = [
        _step("runtime", "changed", outcome="committed"),
        _step("legacy_venv", "error", "runtime is still live"),
    ]
    _patch_action(
        monkeypatch,
        _result(vault, status="partial", target_status="partial", steps=steps),
    )
    receipts = _Receipts()

    result = _invocation(tmp_path, receipts=receipts).invoke(
        MachineMigrateLegacyRequest()
    )

    assert result.status == "partial"
    assert result.error.code is ErrorCode.CONFLICT
    assert tuple(effect.subject for effect in result.committed_effects) == (
        f"brain-runtime:{vault}",
    )
    assert receipts.values[-1].state is ReceiptState.KNOWN_PARTIAL


def test_machine_migrate_delegated_partial_is_a_known_scope_effect(
    tmp_path,
    monkeypatch,
):
    vault = (tmp_path / "legacy").resolve()
    steps = [
        _step("mcp", "partial", "MCP repair applied partially", outcome="partial"),
        _step("legacy_venv", "noop"),
    ]
    _patch_action(
        monkeypatch,
        _result(vault, status="partial", target_status="partial", steps=steps),
    )

    result = _invocation(tmp_path).invoke(MachineMigrateLegacyRequest())

    assert result.status == "partial"
    assert tuple(effect.subject for effect in result.committed_effects) == (
        f"brain-mcp:{vault}",
    )


def test_machine_migrate_unknown_child_outcome_is_non_retryable(
    tmp_path,
    monkeypatch,
):
    vault = (tmp_path / "legacy").resolve()
    steps = [
        _step("runtime", "error", "child timed out", outcome="unknown"),
        _step("legacy_venv", "noop"),
    ]
    _patch_action(
        monkeypatch,
        _result(vault, status="partial", target_status="partial", steps=steps),
    )
    receipts = _Receipts()

    result = _invocation(tmp_path, receipts=receipts).invoke(
        MachineMigrateLegacyRequest()
    )

    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
    assert result.effects == "unknown"
    assert result.retryable is False
    assert receipts.values[-1].state is ReceiptState.UNKNOWN


def test_machine_migrate_missing_selector_is_known_not_found(
    tmp_path,
    monkeypatch,
):
    vault = (tmp_path / "legacy").resolve()
    raw = _result(vault, status="error")
    raw["steps"] = [_step("selection", "error", "No discovered Brain matches")]
    _patch_action(monkeypatch, raw)

    result = _invocation(tmp_path).invoke(
        MachineMigrateLegacyRequest(LegacyBrainIdTarget("missing-brain"))
    )

    assert result.error.code is ErrorCode.NOT_FOUND
    assert result.effects == "none"
