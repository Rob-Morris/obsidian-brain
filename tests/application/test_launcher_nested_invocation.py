"""Nested launcher invocations keep their own identity and target the right vault."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_DIR = REPO_ROOT / "cli"
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

from _launcher.context import LauncherContext, ProviderBindings
from _launcher.contracts import ReceiptState
from _launcher.nested_invocation import invoke_nested
from _launcher import mcp as mcp_owner
from _launcher.mcp import McpClient, McpConfigureRequest, McpMutationStatus, McpRepairRequest, McpScope
from _launcher.runtime import RuntimeRepairRequest, RuntimeRepairStatus
from _bootstrap import diagnostics
from _bootstrap import runtime as bootstrap_runtime


NOW = datetime.fromisoformat("2026-09-30T08:00:00+10:00")


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
    def now(self):
        return NOW


def _vault(root: Path) -> Path:
    core = root / ".brain-core"
    core.mkdir(parents=True)
    (core / "VERSION").write_text("0.70.10\n")
    (core / "brain_mcp").mkdir()
    (core / "brain_mcp" / "requirements.txt").write_text("mcp==2.0.0\n")
    (core / "brain_mcp" / "requirements-semantic.txt").write_text("mcp==2.0.0\n")
    (core / "scripts/_common").mkdir(parents=True)
    shutil.copyfile(REPO_ROOT / "src/brain-core/scripts/_common/_venv.py", core / "scripts/_common/_venv.py")
    return root.resolve()


def _context(tmp_path, receipts, *, dry_run=False):
    return LauncherContext(
        profile="operator",
        authority=_Authority(),
        providers=ProviderBindings((_CallerFilesystem(),)),
        correlation_id="corr-outer",
        invocation_id="cli-outer",
        receipt_writer=receipts,
        clock=_Clock(),
        caller_dir=(tmp_path / "elsewhere").resolve(),
        home_dir=tmp_path.resolve(),
        cli_version="4.0.6",
        cli_binary=(tmp_path / "bin" / "brain").resolve(),
        launcher_python=Path(sys.executable).resolve(),
        current_vault=None,
        dry_run=dry_run,
    )


def _patch_bootstrap(monkeypatch, observed):
    def bootstrap(vault_root, **kwargs):
        observed.append(Path(vault_root))
        runtime_dir = (vault_root / "runtime").resolve()
        return {
            "managed_python": str(runtime_dir / "bin" / "python"),
            "runtime_dir": str(runtime_dir),
            "status": "ok",
            "effect_outcome": "committed",
            "message": "ready",
            "managed_runtime_ready": True,
            "steps": [{"name": "managed_runtime", "status": "changed", "message": "ready"}],
        }

    monkeypatch.setattr(bootstrap_runtime, "bootstrap_managed_runtime", bootstrap)
    monkeypatch.setattr(bootstrap_runtime, "target_runtime_contract", lambda _root: object())


def test_nested_invocation_targets_the_named_vault_and_records_its_own_receipt(tmp_path, monkeypatch):
    (tmp_path / "elsewhere").mkdir()
    target = _vault(tmp_path / "Brain Y")
    receipts = _Receipts()
    observed = []
    _patch_bootstrap(monkeypatch, observed)

    result = invoke_nested(
        _context(tmp_path, receipts),
        RuntimeRepairRequest(),
        vault_root=target,
        invocation_id="cli-outer-runtime-1",
    )

    assert result.status == "ok"
    assert result.result.status is RuntimeRepairStatus.REPAIRED
    assert observed == [target], "the sibling must act on the target, not the caller directory"
    assert [receipt.reference.invocation_id for receipt in receipts.values] == ["cli-outer-runtime-1"]
    assert receipts.values[0].state is ReceiptState.COMMITTED
    assert receipts.values[0].command_id == "runtime.repair"


def test_nested_invocation_requires_a_distinct_identity_and_absolute_root(tmp_path):
    (tmp_path / "elsewhere").mkdir()
    target = _vault(tmp_path / "Brain Y")
    context = _context(tmp_path, _Receipts())

    with pytest.raises(ValueError, match="own invocation identity"):
        invoke_nested(context, RuntimeRepairRequest(), vault_root=target, invocation_id="cli-outer")
    with pytest.raises(ValueError, match="absolute"):
        invoke_nested(context, RuntimeRepairRequest(), vault_root=Path("relative"), invocation_id="cli-outer-x")


def test_nested_mcp_repair_targets_the_vault_and_leaves_the_caller_directory_alone(tmp_path, monkeypatch):
    """``mcp.repair`` targets ``workspace_dir or caller_dir``; nesting rebinds both to the target."""
    from _bootstrap import runtime as bootstrap_runtime

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    target = _vault(tmp_path / "Brain Y")
    (tmp_path / "home").mkdir()
    python = str((tmp_path / "runtime" / "bin" / "python").resolve())
    monkeypatch.setattr(mcp_owner, "_verify_stable_launcher", lambda _context: None)
    monkeypatch.setattr(diagnostics, "inspect_runtime", lambda _vault: {"healthy": True, "python": python, "message": "ready"})
    monkeypatch.setattr(bootstrap_runtime, "target_managed_python", lambda *_args, **_kwargs: Path(python))
    receipts = _Receipts()
    context = _context(tmp_path, receipts)

    configured = invoke_nested(context, McpConfigureRequest(client=McpClient.CLAUDE, scope=McpScope.PROJECT),
                               vault_root=target, invocation_id="cli-outer-mcp-0")
    assert configured.status == "ok", getattr(configured, "error", None)
    assert Path(configured.result.target_dir) == target and (target / ".mcp.json").is_file()

    repaired = invoke_nested(context, McpRepairRequest(client=McpClient.CLAUDE), vault_root=target,
                             invocation_id="cli-outer-mcp-1")

    assert repaired.status == "ok", getattr(repaired, "error", None)
    assert repaired.result.status is McpMutationStatus.NOOP and Path(repaired.result.target_dir) == target
    assert not (elsewhere / ".mcp.json").exists(), "the caller directory is never the repair target"
    assert [receipt.reference.invocation_id for receipt in receipts.values] == ["cli-outer-mcp-0", "cli-outer-mcp-1"]
