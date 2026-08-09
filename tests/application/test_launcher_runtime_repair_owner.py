"""Typed launcher ownership for managed-runtime repair."""

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
from _launcher.owners import LAUNCHER_OWNERS
from _launcher.runtime import RuntimeRepairRequest, RuntimeRepairStatus
from _bootstrap import runtime as bootstrap_runtime


NOW = datetime.fromisoformat("2026-08-10T08:00:00+10:00")


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


def _vault(tmp_path):
    vault = (tmp_path / "Brain").resolve()
    core = vault / ".brain-core"
    core.mkdir(parents=True)
    (core / "VERSION").write_text("0.54.41\n")
    (core / "requirements.txt").write_text("mcp>=1\n")
    return vault


def _invocation(
    tmp_path,
    *,
    vault=None,
    provider=True,
    provider_available=True,
    dry_run=False,
    receipts=None,
):
    providers = ((_CallerFilesystem(provider_available),) if provider else ())
    context = LauncherContext(
        profile="operator",
        authority=_Authority(),
        providers=ProviderBindings(providers),
        correlation_id="corr-runtime-repair",
        invocation_id="inv-runtime-repair",
        receipt_writer=receipts or _Receipts(),
        clock=_Clock(),
        caller_dir=tmp_path.resolve(),
        home_dir=tmp_path.resolve(),
        cli_version="2.0.0",
        cli_binary=(tmp_path / "bin" / "brain").resolve(),
        launcher_python=Path(sys.executable).resolve(),
        current_vault=vault,
        dry_run=dry_run,
    )
    return LauncherInvocation(context, LAUNCHER_CATALOGUE, LAUNCHER_OWNERS)


def _summary(vault, *, status, effect_outcome, step_status, message="ready"):
    runtime_dir = (vault / "runtime").resolve()
    managed_python = runtime_dir / "bin" / "python"
    return {
        "managed_python": str(managed_python),
        "runtime_dir": str(runtime_dir),
        "status": status,
        "effect_outcome": effect_outcome,
        "message": message,
        "managed_runtime_ready": status == "ready",
        "steps": [
            {
                "name": "managed_runtime",
                "status": step_status,
                "message": message,
            },
            {
                "name": "managed_dependencies",
                "status": step_status,
                "message": message,
            },
        ],
    }


def test_runtime_repair_owner_matches_launcher_contract():
    entry = next(
        item for item in LAUNCHER_CATALOGUE.entries
        if item.command_id == "runtime.repair"
    )
    owner = next(
        item for item in LAUNCHER_OWNERS.entries
        if item.command_id == "runtime.repair"
    )

    assert entry.owner_ref == owner.owner_ref == "_launcher.runtime:repair"
    assert entry.required_providers == ("caller_filesystem",)
    assert entry.effect_class == "machine_mutation"
    assert entry.retry_class == "receipt_required"


def test_runtime_repair_requires_provider_and_current_brain(tmp_path):
    request = RuntimeRepairRequest()

    missing_provider = _invocation(tmp_path, provider=False).invoke(request)
    no_brain = _invocation(tmp_path).invoke(request)

    assert missing_provider.error.code is ErrorCode.CAPABILITY_UNAVAILABLE
    assert missing_provider.effects == "none"
    assert no_brain.error.code is ErrorCode.NOT_FOUND
    assert no_brain.effects == "none"


def test_runtime_repair_dry_run_returns_real_typed_plan(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    calls = []

    def fake_bootstrap(*args, **kwargs):
        calls.append((args, kwargs))
        return _summary(
            vault,
            status="planned",
            effect_outcome="none",
            step_status="planned",
            message="would repair",
        )

    monkeypatch.setattr(bootstrap_runtime, "bootstrap_managed_runtime", fake_bootstrap)

    result = _invocation(tmp_path, vault=vault, dry_run=True).invoke(
        RuntimeRepairRequest()
    )

    assert result.result.status is RuntimeRepairStatus.PLANNED
    assert result.committed_effects == ()
    assert calls[0][1]["dry_run"] is True
    assert calls[0][1]["launcher_python"] == str(Path(sys.executable).resolve())


def test_runtime_repair_noop_has_no_effect(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    monkeypatch.setattr(
        bootstrap_runtime,
        "bootstrap_managed_runtime",
        lambda *_args, **_kwargs: _summary(
            vault,
            status="ready",
            effect_outcome="none",
            step_status="noop",
        ),
    )

    result = _invocation(tmp_path, vault=vault).invoke(RuntimeRepairRequest())

    assert result.result.status is RuntimeRepairStatus.NOOP
    assert result.committed_effects == ()


def test_runtime_repair_commits_one_runtime_scope_effect(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    monkeypatch.setattr(
        bootstrap_runtime,
        "bootstrap_managed_runtime",
        lambda *_args, **_kwargs: _summary(
            vault,
            status="ready",
            effect_outcome="committed",
            step_status="changed",
        ),
    )

    result = _invocation(tmp_path, vault=vault).invoke(RuntimeRepairRequest())

    assert result.result.status is RuntimeRepairStatus.REPAIRED
    assert tuple(effect.subject for effect in result.committed_effects) == (
        f"managed-runtime:{vault / 'runtime'}",
    )


def test_runtime_repair_preserves_known_partial_scope(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    receipts = _Receipts()
    monkeypatch.setattr(
        bootstrap_runtime,
        "bootstrap_managed_runtime",
        lambda *_args, **_kwargs: _summary(
            vault,
            status="error",
            effect_outcome="partial",
            step_status="changed",
            message="dependencies changed but verification failed",
        ),
    )

    result = _invocation(tmp_path, vault=vault, receipts=receipts).invoke(
        RuntimeRepairRequest()
    )

    assert result.status == "partial"
    assert tuple(effect.subject for effect in result.committed_effects) == (
        f"managed-runtime:{vault / 'runtime'}",
    )
    assert receipts.values[-1].state is ReceiptState.KNOWN_PARTIAL


def test_runtime_repair_unknown_mutation_is_non_retryable(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    receipts = _Receipts()
    monkeypatch.setattr(
        bootstrap_runtime,
        "bootstrap_managed_runtime",
        lambda *_args, **_kwargs: _summary(
            vault,
            status="error",
            effect_outcome="unknown",
            step_status="noop",
            message="pip timed out",
        ),
    )

    result = _invocation(tmp_path, vault=vault, receipts=receipts).invoke(
        RuntimeRepairRequest()
    )

    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
    assert result.effects == "unknown"
    assert result.retryable is False
    assert receipts.values[-1].state is ReceiptState.UNKNOWN


def test_runtime_repair_known_prewrite_failure_has_no_effect(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    monkeypatch.setattr(
        bootstrap_runtime,
        "bootstrap_managed_runtime",
        lambda *_args, **_kwargs: _summary(
            vault,
            status="error",
            effect_outcome="none",
            step_status="noop",
            message="launcher unavailable",
        ),
    )

    result = _invocation(tmp_path, vault=vault).invoke(RuntimeRepairRequest())

    assert result.error.code is ErrorCode.CONFLICT
    assert result.effects == "none"
