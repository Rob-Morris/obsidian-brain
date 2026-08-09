"""Typed launcher ownership for orphaned managed-runtime pruning."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_DIR = REPO_ROOT / "cli"
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

from launcher_catalogue import LAUNCHER_CATALOGUE
from _launcher.context import LauncherContext, ProviderBindings
from _launcher.contracts import ErrorCode, ReceiptState
from _launcher.invocation import LauncherInvocation
from _launcher.machine import MachinePruneRuntimesRequest, RuntimePruneStatus
from _launcher.owners import LAUNCHER_OWNERS
from _machine import maintenance
import vault_registry


NOW = datetime.fromisoformat("2026-08-10T06:30:00+10:00")


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
        correlation_id="corr-machine-prune",
        invocation_id="inv-machine-prune",
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


def _summary(runtime_dir: Path | None = None, *, scan_available=True):
    runtimes = []
    if runtime_dir is not None:
        python = runtime_dir / "bin" / "python"
        runtimes.append(
            {
                "name": runtime_dir.name,
                "dir": str(runtime_dir),
                "python": str(python),
                "orphan_candidate": True,
            }
        )
    return {
        "live_process_scan_available": scan_available,
        "runtimes": runtimes,
    }


def test_machine_prune_owner_matches_machine_global_contract():
    entry = next(
        item
        for item in LAUNCHER_CATALOGUE.entries
        if item.command_id == "machine.prune-runtimes"
    )
    owner = next(
        item
        for item in LAUNCHER_OWNERS.entries
        if item.command_id == "machine.prune-runtimes"
    )

    assert entry.owner_ref == owner.owner_ref == "_launcher.machine:prune_runtimes"
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


def test_machine_prune_requires_an_available_caller_filesystem(tmp_path):
    request = MachinePruneRuntimesRequest()

    missing = _invocation(tmp_path, provider=False).invoke(request)
    unavailable = _invocation(
        tmp_path,
        provider_available=False,
    ).invoke(request)

    assert missing.error.code is ErrorCode.CAPABILITY_UNAVAILABLE
    assert missing.error.details.missing == ("provider:caller_filesystem",)
    assert unavailable.error.code is ErrorCode.CAPABILITY_UNAVAILABLE
    assert unavailable.error.details.missing == ("capability:caller_filesystem",)


def test_machine_prune_dry_run_is_real_and_does_not_remove(tmp_path, monkeypatch):
    runtime_dir = (tmp_path / "venvs" / "py3.12-orphan").resolve()
    (runtime_dir / "bin").mkdir(parents=True)
    (runtime_dir / "bin" / "python").write_text("python")
    monkeypatch.setattr(
        maintenance,
        "collect_machine_summary",
        lambda **_kwargs: _summary(runtime_dir),
    )
    receipts = _Receipts()

    result = _invocation(
        tmp_path,
        dry_run=True,
        receipts=receipts,
    ).invoke(MachinePruneRuntimesRequest())

    assert result.result.status is RuntimePruneStatus.PLANNED
    assert result.result.targets[0].status is RuntimePruneStatus.PLANNED
    assert result.committed_effects == ()
    assert runtime_dir.is_dir()
    assert receipts.values[-1].state is ReceiptState.COMMITTED


def test_machine_prune_removes_and_receipts_each_orphan(tmp_path, monkeypatch):
    runtime_dir = (tmp_path / "venvs" / "py3.12-orphan").resolve()
    (runtime_dir / "bin").mkdir(parents=True)
    (runtime_dir / "bin" / "python").write_text("python")
    monkeypatch.setattr(
        maintenance,
        "collect_machine_summary",
        lambda **_kwargs: _summary(runtime_dir),
    )

    result = _invocation(tmp_path).invoke(MachinePruneRuntimesRequest())

    assert result.result.status is RuntimePruneStatus.REMOVED
    assert result.result.targets[0].status is RuntimePruneStatus.REMOVED
    assert tuple(effect.subject for effect in result.committed_effects) == (
        f"managed-runtime:{runtime_dir}",
    )
    assert not runtime_dir.exists()


def test_machine_prune_returns_noop_when_no_orphans_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(
        maintenance,
        "collect_machine_summary",
        lambda **_kwargs: _summary(),
    )

    result = _invocation(tmp_path).invoke(MachinePruneRuntimesRequest())

    assert result.result.status is RuntimePruneStatus.NOOP
    assert result.result.targets == ()
    assert result.committed_effects == ()


def test_machine_prune_uses_trusted_current_vault_during_discovery(
    tmp_path,
    monkeypatch,
):
    current_vault = (tmp_path / "current-brain").resolve()
    current_vault.mkdir()
    calls = []

    def collect(**kwargs):
        calls.append(kwargs)
        return _summary()

    monkeypatch.setattr(maintenance, "collect_machine_summary", collect)

    result = _invocation(
        tmp_path,
        current_vault=current_vault,
    ).invoke(MachinePruneRuntimesRequest())

    assert result.result.status is RuntimePruneStatus.NOOP
    assert calls[0]["current_vault"] == str(current_vault)
    assert calls[0]["synchronise_registry"] is False


def test_machine_prune_fails_known_when_live_process_scan_is_unavailable(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        maintenance,
        "collect_machine_summary",
        lambda **_kwargs: _summary(scan_available=False),
    )

    result = _invocation(tmp_path).invoke(MachinePruneRuntimesRequest())

    assert result.error.code is ErrorCode.CONFLICT
    assert result.effects == "none"
    assert "live-process detection is unavailable" in result.error.message


def test_machine_prune_maps_registry_read_failure_to_known_no_effect(
    tmp_path,
    monkeypatch,
):
    def fail_summary(**_kwargs):
        raise vault_registry.RegistryReadError("registry is unreadable")

    monkeypatch.setattr(maintenance, "collect_machine_summary", fail_summary)

    result = _invocation(tmp_path).invoke(MachinePruneRuntimesRequest())

    assert result.error.code is ErrorCode.CONFLICT
    assert result.effects == "none"
    assert "registry is unreadable" in result.error.message


def test_machine_prune_deletion_failure_is_non_retryable_unknown(
    tmp_path,
    monkeypatch,
):
    runtime_dir = (tmp_path / "venvs" / "py3.12-orphan").resolve()
    (runtime_dir / "bin").mkdir(parents=True)
    (runtime_dir / "bin" / "python").write_text("python")
    monkeypatch.setattr(
        maintenance,
        "collect_machine_summary",
        lambda **_kwargs: _summary(runtime_dir),
    )
    monkeypatch.setattr(
        maintenance.shutil,
        "rmtree",
        lambda _path: (_ for _ in ()).throw(OSError("simulated partial deletion")),
    )
    receipts = _Receipts()

    result = _invocation(tmp_path, receipts=receipts).invoke(
        MachinePruneRuntimesRequest()
    )

    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
    assert result.effects == "unknown"
    assert result.retryable is False
    assert receipts.values[-1].state is ReceiptState.UNKNOWN
