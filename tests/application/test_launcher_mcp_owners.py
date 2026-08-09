"""Behavioural tests for transactional launcher-owned MCP configuration."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sys


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
from _bootstrap.mcp_state import read_codex_server_config


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
    python = str((vault.parent / "runtime" / "bin" / "python").resolve())
    monkeypatch.setattr(
        diagnostics,
        "inspect_runtime",
        lambda _vault: {"healthy": True, "python": python, "message": "ready"},
    )
    return python


def test_mcp_owners_match_launcher_contract():
    entries = {
        item.command_id: item for item in LAUNCHER_CATALOGUE.entries
        if item.command_id.startswith("mcp.")
    }
    owners = {
        item.command_id: item for item in LAUNCHER_OWNERS.entries
        if item.command_id.startswith("mcp.")
    }

    assert tuple(entries) == ("mcp.configure", "mcp.repair")
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
        except ValueError:
            pass
        else:
            raise AssertionError(f"request unexpectedly accepted {kwargs}")


def test_configure_project_commits_all_files_transactionally(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    python = _healthy_runtime(monkeypatch, vault)
    receipts = _Receipts()

    result = _invocation(vault, receipts=receipts).invoke(McpConfigureRequest())

    assert result.result.status is McpMutationStatus.CHANGED
    assert tuple(client.value for client in result.result.clients) == ("claude", "codex")
    assert (vault / ".mcp.json").is_file()
    assert read_codex_server_config(vault / ".codex" / "config.toml")["command"] == python
    assert "ALWAYS DO FIRST" in (vault / "CLAUDE.md").read_text()
    assert (vault / ".claude" / "settings.local.json").is_file()
    state = json.loads((vault / ".brain" / "local" / "init-state.json").read_text())
    assert [record["client"] for record in state["records"]] == ["claude", "codex"]
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

    result = _invocation(vault).invoke(McpConfigureRequest())

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

    result = _invocation(vault).invoke(McpConfigureRequest())

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


def test_failed_rollback_reports_known_partial_files(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    _healthy_runtime(monkeypatch, vault)
    original_apply = file_transaction._apply
    calls = 0

    def fail_write_and_rollback(path, content):
        nonlocal calls
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

    assert result.result.status is McpMutationStatus.CHANGED
    assert "KEEP THIS LINE" in claude_md.read_text()
    remaining = json.loads(settings_path.read_text())
    assert remaining["hooks"]["SessionStart"] == [
        {"hooks": [{"type": "command", "command": "echo keep-this-hook"}]}
    ]


def test_user_scope_uses_trusted_home_and_local_all_degrades_explicitly(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    _healthy_runtime(monkeypatch, vault)
    home = (tmp_path / "operator-home").resolve()

    user = _invocation(vault, home=home).invoke(
        McpConfigureRequest(client=McpClient.CLAUDE, scope=McpScope.USER)
    )
    local = _invocation(vault, home=home).invoke(
        McpConfigureRequest(scope=McpScope.LOCAL)
    )

    assert user.result.target_dir is None
    assert (home / ".claude.json").is_file()
    assert tuple(client.value for client in local.result.clients) == ("claude",)
    assert local.warnings[0].code is WarningCode.DEGRADED_CAPABILITY


def test_repair_only_converges_present_project_clients(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    python = _healthy_runtime(monkeypatch, vault)
    (vault / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"brain": {"command": "/stale", "args": [], "env": {}}}})
    )

    result = _invocation(vault).invoke(McpRepairRequest())

    repaired = json.loads((vault / ".mcp.json").read_text())["mcpServers"]["brain"]
    assert result.result.status is McpMutationStatus.CHANGED
    assert tuple(client.value for client in result.result.clients) == ("claude",)
    assert repaired["command"] == python
    assert not (vault / ".codex" / "config.toml").exists()


def test_repair_is_noop_when_no_project_client_is_present(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    _healthy_runtime(monkeypatch, vault)

    result = _invocation(vault).invoke(McpRepairRequest())

    assert result.result.status is McpMutationStatus.NOOP
    assert result.result.clients == ()
    assert result.committed_effects == ()
    assert not (vault / ".brain").exists()
