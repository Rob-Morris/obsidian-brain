"""Behavioural tests for transactional launcher-owned MCP configuration."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_DIR = REPO_ROOT / "cli"
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

from launcher_catalogue import LAUNCHER_CATALOGUE
from _launcher import mcp as mcp_owner
from _launcher.context import LauncherContext, ProviderBindings
from _launcher.contracts import ErrorCode, ReceiptState, WarningCode
from _launcher.invocation import LauncherInvocation
from _launcher.mcp import (
    McpClient,
    McpConfigureAction,
    McpConfigureRequest,
    McpMutationStatus,
    McpRepairRequest,
    McpScope,
)
from _launcher.owners import LAUNCHER_OWNERS
from _bootstrap import diagnostics, file_transaction
from _bootstrap.mcp_state import read_toml_server_config

NOW = datetime.fromisoformat("2026-08-10T08:00:00+10:00")


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


def _vault(tmp_path):
    vault = (tmp_path / "Brain").resolve()
    core = vault / ".brain-core"
    core.mkdir(parents=True)
    (core / "VERSION").write_text("0.54.42\n")
    return vault


def _invocation(vault, *, caller=None, home=None, dry_run=False, receipts=None):
    context = LauncherContext(
        profile="operator",
        authority=_Authority(),
        providers=ProviderBindings((_CallerFilesystem(),)),
        correlation_id="corr-mcp",
        invocation_id="inv-mcp",
        receipt_writer=receipts or _Receipts(),
        clock=_Clock(),
        caller_dir=(caller or vault).resolve(),
        home_dir=(home or vault.parent / "home").resolve(),
        cli_version="2.0.0",
        cli_binary=(vault.parent / "bin" / "brain").resolve(),
        launcher_python=Path(sys.executable).resolve(),
        current_vault=vault,
        dry_run=dry_run,
    )
    return LauncherInvocation(context, LAUNCHER_CATALOGUE, LAUNCHER_OWNERS)


def _healthy_runtime(monkeypatch, vault):
    from _bootstrap import runtime
    monkeypatch.setattr(mcp_owner, "_verify_stable_launcher", lambda _context: None)
    python = str((vault.parent / "runtime" / "bin" / "python").resolve())
    monkeypatch.setattr(
        diagnostics,
        "inspect_runtime",
        lambda _vault: {"healthy": True, "python": python, "message": "ready"},
    )
    monkeypatch.setattr(runtime, "target_managed_python", lambda *_args, **_kwargs: Path(python))
    return python


@pytest.mark.parametrize("dry_run", [False, True])
def test_brain_repair_includes_runtime_without_creating_client_intent(tmp_path, monkeypatch, dry_run):
    from _bootstrap import runtime

    vault = _vault(tmp_path)
    python = _healthy_runtime(monkeypatch, vault)
    calls = []
    monkeypatch.setattr(runtime, "target_runtime_contract", lambda root: object())

    def repair(root, **kwargs):
        calls.append((root, kwargs["dry_run"] if "dry_run" in kwargs else False))
        preview = kwargs.get("dry_run", False)
        return {"status": "planned" if preview else "ready", "managed_python": python,
                "runtime_dir": str(Path(python).parent.parent), "effect_outcome": "none" if preview else "committed"}

    monkeypatch.setattr(runtime, "bootstrap_managed_runtime", repair)
    result = _invocation(vault, dry_run=dry_run).invoke(McpRepairRequest(breadth=mcp_owner.RepairBreadth.BRAIN))
    assert result.status == "ok", result
    assert result.result.breadth is mcp_owner.RepairBreadth.BRAIN
    assert len(result.result.runtimes) == 1
    assert result.result.clients == ()
    assert not (vault / ".mcp.json").exists()
    assert result.result.status is (McpMutationStatus.PLANNED if dry_run else McpMutationStatus.CHANGED)
    assert len(result.committed_effects) == (0 if dry_run else 1)
    assert len(calls) == (1 if dry_run else 2)


def test_composed_repair_preserves_unknown_runtime_effects(tmp_path, monkeypatch):
    from _bootstrap import runtime

    vault = _vault(tmp_path)
    python = _healthy_runtime(monkeypatch, vault)
    monkeypatch.setattr(runtime, "target_runtime_contract", lambda root: object())
    def repair(root, **kwargs):
        preview = kwargs.get("dry_run", False)
        return {"status": "planned" if preview else "error", "managed_python": python,
                "runtime_dir": str(Path(python).parent.parent), "effect_outcome": "none" if preview else "unknown",
                "message": "pip interrupted"}
    monkeypatch.setattr(runtime, "bootstrap_managed_runtime", repair)
    result = _invocation(vault).invoke(McpRepairRequest(breadth=mcp_owner.RepairBreadth.BRAIN))
    assert result.error.code is ErrorCode.COMMAND_OUTCOME_UNKNOWN
    assert result.effects == "unknown"
    assert str(Path(python).parent.parent) in result.error.details.recovery_paths
    assert not (vault / ".mcp.json").exists()


def test_machine_repair_composes_two_brains_and_deduplicates_shared_work(tmp_path, monkeypatch):
    import vault_registry
    from _bootstrap import runtime, mcp_registration as registration
    from _bootstrap.mcp_state import build_mcp_config

    first, second = _vault(tmp_path / "one"), _vault(tmp_path / "two")
    home = tmp_path / "home"
    python = _healthy_runtime(monkeypatch, first)
    for identity, vault, client in (("one", first, McpClient.CODEX), ("two", second, McpClient.GROK)):
        vault_registry.register(vault, identity)
        plan = registration._configure_plan(vault, home, vault, McpScope.PROJECT, (client,),
                                             build_mcp_config("/old/python", vault, workspace_dir=vault))
        file_transaction.apply_file_changes(plan.changes())
    shared = registration._configure_plan(None, home, None, McpScope.USER, (McpClient.CLAUDE,),
                                           registration.stable_server_config(tmp_path / "old/bin/brain"))
    file_transaction.apply_file_changes(shared.changes())
    calls = []
    monkeypatch.setattr(runtime, "target_runtime_contract", lambda root: object())
    def repair(root, **kwargs):
        preview = kwargs.get("dry_run", False)
        calls.append((root, preview))
        return {"status": "planned" if preview else "ready", "managed_python": python,
                "runtime_dir": str(Path(python).parent.parent), "effect_outcome": "none" if preview else "committed"}
    monkeypatch.setattr(runtime, "bootstrap_managed_runtime", repair)
    result = _invocation(first, home=home).invoke(McpRepairRequest(breadth=mcp_owner.RepairBreadth.MACHINE))
    assert result.status == "ok", result
    assert result.result.targets == tuple(sorted((str(first), str(second))))
    assert len([call for call in calls if not call[1]]) == 1
    assert len(result.result.runtimes) == 1
    assert sum(effect.subject == f"file:{home / '.claude.json'}" for effect in result.committed_effects) == 1
    assert not (first / ".mcp.json").exists()
    assert not (second / ".mcp.json").exists()


def test_brain_repair_admits_projections_before_runtime_effects(tmp_path, monkeypatch):
    from _bootstrap import runtime

    vault = _vault(tmp_path)
    python = _healthy_runtime(monkeypatch, vault)
    (vault / ".mcp.json").write_text('{"mcpServers":{"brain":{"command":"custom"}}}')
    calls = []
    monkeypatch.setattr(runtime, "target_runtime_contract", lambda root: object())

    def repair(root, **kwargs):
        calls.append(kwargs.get("dry_run", False))
        return {"status": "planned", "managed_python": python,
                "runtime_dir": str(Path(python).parent.parent), "effect_outcome": "none"}

    monkeypatch.setattr(runtime, "bootstrap_managed_runtime", repair)
    result = _invocation(vault).invoke(McpRepairRequest(breadth=mcp_owner.RepairBreadth.BRAIN))
    assert result.status == "error"
    assert "Unowned" in result.error.message
    assert calls == [True]
    assert result.effects == "none"


def test_modified_owned_hook_options_are_not_overwritten(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    _healthy_runtime(monkeypatch, vault)
    invocation = _invocation(vault)
    assert invocation.invoke(McpConfigureRequest(client=McpClient.CLAUDE)).status == "ok"
    path = vault / ".claude/settings.local.json"
    content = json.loads(path.read_text())
    content["hooks"]["SessionStart"][0]["hooks"][0]["timeout"] = 123
    path.write_text(json.dumps(content))
    before = path.read_bytes()
    result = invocation.invoke(McpRepairRequest(client=McpClient.CLAUDE))
    assert result.status == "error"
    assert "Modified owned" in result.error.message
    assert path.read_bytes() == before


def test_shared_route_retains_repairable_bootstrap_without_reinstalling_transport(tmp_path, monkeypatch):
    from _bootstrap import runtime

    vault = _vault(tmp_path)
    _healthy_runtime(monkeypatch, vault)
    invocation = _invocation(vault)
    assert invocation.invoke(McpConfigureRequest(client=McpClient.CLAUDE)).status == "ok"
    assert invocation.invoke(McpConfigureRequest(client=McpClient.CLAUDE, scope=McpScope.USER)).status == "ok"
    assert invocation.invoke(McpConfigureRequest(client=McpClient.CLAUDE, action=McpConfigureAction.REMOVE)).status == "ok"
    assert not (vault / ".mcp.json").exists()
    ledger = vault / ".brain/local/init-state.json"
    assert json.loads(ledger.read_text())["records"][0]["transport_enabled"] is False
    next_python = str(tmp_path / "new-runtime/bin/python")
    monkeypatch.setattr(runtime, "target_managed_python", lambda *args, **kwargs: Path(next_python))
    result = invocation.invoke(McpRepairRequest(client=McpClient.CLAUDE))
    assert result.status == "ok", result
    assert not (vault / ".mcp.json").exists()
    assert next_python in (vault / ".claude/settings.local.json").read_text()
    assert invocation.invoke(McpConfigureRequest(client=McpClient.CLAUDE, scope=McpScope.USER,
                                               action=McpConfigureAction.REMOVE)).status == "ok"
    assert invocation.invoke(McpRepairRequest(client=McpClient.CLAUDE)).status == "ok"
    assert not ledger.exists()
    assert not (vault / ".claude/settings.local.json").exists()
    assert not (vault / ".mcp.json").exists()


def test_mcp_owners_match_launcher_contract():
    entries = {
        item.command_id: item for item in LAUNCHER_CATALOGUE.entries
        if item.command_id.startswith("mcp.")
    }
    owners = {
        item.command_id: item for item in LAUNCHER_OWNERS.entries
        if item.command_id.startswith("mcp.")
    }

    assert tuple(entries) == ("mcp.configure", "mcp.migrate", "mcp.repair")
    assert entries["mcp.configure"].owner_ref == owners["mcp.configure"].owner_ref == "_launcher.mcp:configure"
    assert entries["mcp.repair"].owner_ref == owners["mcp.repair"].owner_ref == "_launcher.mcp:repair"
    assert all(item.required_providers == ("caller_filesystem",) for item in entries.values())
    assert all(item.retry_class == "receipt_required" for item in entries.values())


def test_mcp_request_grammar_is_closed():
    for kwargs in (
        {"client": "claude"},
        {"scope": "project"},
        {"action": "configure"},
        {"client": McpClient.CODEX, "scope": McpScope.LOCAL},
    ):
        try:
            McpConfigureRequest(**kwargs)
        except (ValueError, TypeError):
            pass
        else:
            raise AssertionError(f"request unexpectedly accepted {kwargs}")


def test_configure_project_commits_all_files_transactionally(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    python = _healthy_runtime(monkeypatch, vault)
    receipts = _Receipts()

    result = _invocation(vault, receipts=receipts).invoke(McpConfigureRequest(client=McpClient.ALL))

    assert result.result.status is McpMutationStatus.CHANGED
    assert tuple(client.value for client in result.result.clients) == (
        "claude",
        "codex",
        "grok",
    )
    assert (vault / ".mcp.json").is_file()
    assert (
        read_toml_server_config(vault / ".codex" / "config.toml")["command"] == python
    )
    assert "ALWAYS DO FIRST" in (vault / "CLAUDE.md").read_text()
    assert (vault / ".claude" / "settings.local.json").is_file()
    state = json.loads((vault / ".brain" / "local" / "init-state.json").read_text())
    assert [record["client"] for record in state["records"]] == [
        "claude",
        "codex",
        "grok",
    ]
    assert not (vault / ".brain" / "local" / "workspace.yaml").exists()
    assert not (vault / ".gitignore").exists()
    assert receipts.values[-1].state is ReceiptState.COMMITTED
    assert {effect.subject for effect in result.committed_effects} == {
        f"file:{step.path}" for step in result.result.files
    }


def test_configure_dry_run_reports_exact_plan_without_writes(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    _healthy_runtime(monkeypatch, vault)

    result = _invocation(vault, dry_run=True).invoke(
        McpConfigureRequest(client=McpClient.CLAUDE)
    )

    assert result.result.status is McpMutationStatus.PLANNED
    assert result.committed_effects == ()
    assert len(result.result.files) == 4
    assert not (vault / ".mcp.json").exists()
    assert not (vault / ".brain").exists()


def test_configure_requires_runtime_without_provisioning(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    monkeypatch.setattr(
        diagnostics,
        "inspect_runtime",
        lambda _vault: {"healthy": False, "python": "missing", "message": "runtime missing"},
    )

    result = _invocation(vault).invoke(McpConfigureRequest(client=McpClient.ALL))

    assert result.error.code is ErrorCode.CAPABILITY_UNAVAILABLE
    assert "runtime repair" in result.error.next_action.instruction
    assert result.effects == "none"
    assert not (vault / ".brain").exists()


def test_malformed_second_client_preflights_before_any_write(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    _healthy_runtime(monkeypatch, vault)
    codex = vault / ".codex" / "config.toml"
    codex.parent.mkdir()
    codex.write_text("invalid = [\n")

    result = _invocation(vault).invoke(McpConfigureRequest(client=McpClient.ALL))

    assert result.error.code is ErrorCode.CONFLICT
    assert result.effects == "none"
    assert codex.read_text() == "invalid = [\n"
    assert not (vault / ".mcp.json").exists()
    assert not (vault / ".brain").exists()


def test_symlinked_client_state_is_refused_before_writes(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    _healthy_runtime(monkeypatch, vault)
    external = tmp_path / "external.json"
    external.write_text('{"preserved": true}\n')
    (vault / ".mcp.json").symlink_to(external)

    result = _invocation(vault).invoke(
        McpConfigureRequest(client=McpClient.CLAUDE)
    )

    assert result.error.code is ErrorCode.CONFLICT
    assert result.effects == "none"
    assert external.read_text() == '{"preserved": true}\n'
    assert not (vault / ".brain").exists()


def test_apply_failure_rolls_back_files_and_created_directories(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    _healthy_runtime(monkeypatch, vault)
    original_apply = file_transaction._apply
    calls = 0

    def fail_second(path, content):
        nonlocal calls
        if not path.is_relative_to(vault):
            return original_apply(path, content)
        calls += 1
        if calls == 2:
            raise OSError("disk full")
        return original_apply(path, content)

    monkeypatch.setattr(file_transaction, "_apply", fail_second)

    result = _invocation(vault).invoke(
        McpConfigureRequest(client=McpClient.CLAUDE)
    )

    assert result.error.code is ErrorCode.CONFLICT
    assert result.effects == "none"
    assert not (vault / ".mcp.json").exists()
    assert not (vault / ".brain").exists()


def test_keyboard_interrupt_rolls_back_files_and_created_directories(
    tmp_path, monkeypatch
):
    vault = _vault(tmp_path)
    _healthy_runtime(monkeypatch, vault)
    original_apply = file_transaction._apply
    calls = 0

    def interrupt_second(path, content):
        nonlocal calls
        if not path.is_relative_to(vault):
            return original_apply(path, content)
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt()
        return original_apply(path, content)

    monkeypatch.setattr(file_transaction, "_apply", interrupt_second)

    with pytest.raises(KeyboardInterrupt):
        _invocation(vault).invoke(McpConfigureRequest(client=McpClient.CLAUDE))

    assert not (vault / ".mcp.json").exists()
    assert not (vault / ".brain").exists()


def test_after_effect_interrupt_rolls_back_files_and_created_directories(
    tmp_path, monkeypatch
):
    vault = _vault(tmp_path)
    _healthy_runtime(monkeypatch, vault)
    original_apply = file_transaction._apply
    calls = 0

    def interrupt_after_second_effect(path, content):
        nonlocal calls
        if not path.is_relative_to(vault):
            return original_apply(path, content)
        calls += 1
        original_apply(path, content)
        if calls == 2:
            raise KeyboardInterrupt()

    monkeypatch.setattr(file_transaction, "_apply", interrupt_after_second_effect)

    with pytest.raises(KeyboardInterrupt):
        _invocation(vault).invoke(McpConfigureRequest(client=McpClient.CLAUDE))

    assert not (vault / ".mcp.json").exists()
    assert not (vault / ".brain").exists()


def test_after_rollback_effect_interrupt_is_reconciled(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    _healthy_runtime(monkeypatch, vault)
    original_apply = file_transaction._apply
    calls = 0

    def fail_write_then_interrupt_after_restore(path, content):
        nonlocal calls
        if not path.is_relative_to(vault):
            return original_apply(path, content)
        calls += 1
        if calls == 2:
            raise OSError("disk full")
        original_apply(path, content)
        if calls == 3:
            raise KeyboardInterrupt()

    monkeypatch.setattr(
        file_transaction,
        "_apply",
        fail_write_then_interrupt_after_restore,
    )

    result = _invocation(vault).invoke(
        McpConfigureRequest(client=McpClient.CLAUDE)
    )

    assert result.status == "error"
    assert result.effects == "none"
    assert not (vault / ".mcp.json").exists()
    assert not (vault / ".brain").exists()


def test_failed_rollback_reports_known_partial_files(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    _healthy_runtime(monkeypatch, vault)
    original_apply = file_transaction._apply
    calls = 0

    def fail_write_and_rollback(path, content):
        nonlocal calls
        if not path.is_relative_to(vault):
            return original_apply(path, content)
        calls += 1
        if calls in {2, 3}:
            raise OSError("storage failure")
        return original_apply(path, content)

    monkeypatch.setattr(file_transaction, "_apply", fail_write_and_rollback)

    result = _invocation(vault).invoke(
        McpConfigureRequest(client=McpClient.CLAUDE)
    )

    assert result.status == "partial"
    assert result.committed_effects
    assert all(
        effect.subject.startswith(("file:", "directory:"))
        for effect in result.committed_effects
    )


def test_remove_uses_recorded_ownership_and_is_idempotent(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    _healthy_runtime(monkeypatch, vault)
    invocation = _invocation(vault)
    configured = invocation.invoke(McpConfigureRequest(client=McpClient.CLAUDE))
    assert configured.status == "ok"

    removed = invocation.invoke(
        McpConfigureRequest(
            client=McpClient.CLAUDE,
            action=McpConfigureAction.REMOVE,
        )
    )
    repeated = invocation.invoke(
        McpConfigureRequest(
            client=McpClient.CLAUDE,
            action=McpConfigureAction.REMOVE,
        )
    )

    assert removed.result.status is McpMutationStatus.CHANGED
    assert repeated.result.status is McpMutationStatus.NOOP
    assert not (vault / ".mcp.json").exists()
    assert not (vault / "CLAUDE.md").exists()
    assert not (vault / ".claude" / "settings.local.json").exists()
    assert not (vault / ".brain" / "local" / "init-state.json").exists()


def test_remove_remains_available_when_workspace_binding_is_broken(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    _healthy_runtime(monkeypatch, vault)
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir()
    monkeypatch.setattr(mcp_owner, "_validate_target", lambda *_args: None)
    monkeypatch.setattr(mcp_owner.registration, "_validate_target", lambda *_args: None)
    monkeypatch.setattr(mcp_owner.registration, "plan_reverse_registration", lambda *_args: None)
    invocation = _invocation(vault, caller=workspace)
    assert invocation.invoke(
        McpConfigureRequest(client=McpClient.CLAUDE)
    ).status == "ok"
    monkeypatch.setattr(
        mcp_owner,
        "_validate_target",
        lambda *_args: (_ for _ in ()).throw(ValueError("broken binding")),
    )

    result = invocation.invoke(
        McpConfigureRequest(
            client=McpClient.CLAUDE,
            action=McpConfigureAction.REMOVE,
        )
    )

    assert result.result.status is McpMutationStatus.CHANGED
    assert not (workspace / ".mcp.json").exists()


def test_remove_does_not_trust_recorded_markdown_or_hook_selectors(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    _healthy_runtime(monkeypatch, vault)
    invocation = _invocation(vault)
    assert invocation.invoke(
        McpConfigureRequest(client=McpClient.CLAUDE)
    ).status == "ok"
    claude_md = vault / "CLAUDE.md"
    claude_md.write_text(claude_md.read_text() + "KEEP THIS LINE\n")
    settings_path = vault / ".claude" / "settings.local.json"
    settings = json.loads(settings_path.read_text())
    settings["hooks"]["SessionStart"].append(
        {"hooks": [{"type": "command", "command": "echo keep-this-hook"}]}
    )
    settings_path.write_text(json.dumps(settings))
    state_path = vault / ".brain" / "local" / "init-state.json"
    state = json.loads(state_path.read_text())
    state["records"][0]["bootstrap_line"] = "KEEP THIS LINE"
    state["records"][0]["hook_command"] = "echo keep-this-hook"
    state_path.write_text(json.dumps(state))

    result = invocation.invoke(
        McpConfigureRequest(
            client=McpClient.CLAUDE,
            action=McpConfigureAction.REMOVE,
        )
    )

    assert result.status == "error"
    assert result.effects == "none"
    assert "KEEP THIS LINE" in claude_md.read_text()
    remaining = json.loads(settings_path.read_text())
    assert {"hooks": [{"type": "command", "command": "echo keep-this-hook"}]} in remaining["hooks"]["SessionStart"]


def test_user_scope_uses_trusted_home_and_local_all_degrades_explicitly(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    _healthy_runtime(monkeypatch, vault)
    home = (tmp_path / "operator-home").resolve()

    user = _invocation(vault, home=home).invoke(
        McpConfigureRequest(client=McpClient.CLAUDE, scope=McpScope.USER)
    )
    local = _invocation(vault, home=home).invoke(
        McpConfigureRequest(client=McpClient.ALL, scope=McpScope.LOCAL)
    )

    assert user.result.target_dir is None
    assert (home / ".claude.json").is_file()
    assert tuple(client.value for client in local.result.clients) == ("claude",)
    assert local.warnings[0].code is WarningCode.DEGRADED_CAPABILITY


def test_repair_refuses_unrecorded_project_client(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    _healthy_runtime(monkeypatch, vault)
    path = vault / ".mcp.json"
    path.write_text(json.dumps({"mcpServers": {"brain": {"command": "/stale", "args": [], "env": {}}}}))
    before = path.read_bytes()
    result = _invocation(vault).invoke(McpRepairRequest())
    assert result.status == "error"
    assert "migration" in result.error.message
    assert path.read_bytes() == before
    assert not (vault / ".codex/config.toml").exists()


def test_repair_is_noop_when_no_project_client_is_present(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    _healthy_runtime(monkeypatch, vault)

    result = _invocation(vault).invoke(McpRepairRequest())

    assert result.result.status is McpMutationStatus.NOOP
    assert result.result.clients == ()
    assert result.committed_effects == ()
    assert not (vault / ".brain").exists()


@pytest.mark.parametrize("scope", [McpScope.PROJECT, McpScope.USER])
def test_grok_native_transaction_dry_run_configure_repair_remove(
    tmp_path, monkeypatch, scope
):
    from _bootstrap.grok_mcp import RULE_CONTENT

    vault = _vault(tmp_path)
    _healthy_runtime(monkeypatch, vault)
    home = tmp_path / "home"
    root = home if scope is McpScope.USER else vault
    request = McpConfigureRequest(client=McpClient.GROK, scope=scope)
    result = _invocation(vault, home=home, dry_run=True).invoke(request)
    assert result.result.status is McpMutationStatus.PLANNED
    assert len(result.result.files) == 3
    assert not (root / ".grok").exists()
    result = _invocation(vault, home=home).invoke(request)
    assert result.result.status is McpMutationStatus.CHANGED
    assert (root / ".grok/rules/brain.md").read_text() == RULE_CONTENT
    assert not (root / ".claude").exists()
    assert not (root / ".codex").exists()
    if scope is McpScope.PROJECT:
        (root / ".grok/rules/brain.md").unlink()
        result = _invocation(vault).invoke(McpRepairRequest())
        assert result.result.status is McpMutationStatus.CHANGED
        assert tuple(result.result.clients) == (McpClient.GROK,)
    (root / ".grok/config.toml").unlink()
    result = _invocation(vault, home=home).invoke(
        McpConfigureRequest(
            client=McpClient.GROK, scope=scope, action=McpConfigureAction.REMOVE
        )
    )
    assert result.result.status is McpMutationStatus.CHANGED
    assert not (root / ".grok/rules/brain.md").exists()
    assert not (vault / ".brain/local/init-state.json").exists()
