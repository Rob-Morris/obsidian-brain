"""Host-local approvals compose native edits, receipts and existing registration."""

from dataclasses import replace
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "cli"))

from _launcher.approvals import (ApprovalAction, ApprovalClient, ApprovalScope, ApprovalSurface,
                                 ApprovalsConfigureRequest, ApprovalsInspectRequest)
from _launcher import approval_management as manager
from test_launcher_agent_skill_owner import _invocation
from _bootstrap.approval_policy import CommandFact, snapshot
from _bootstrap.file_transaction import FileTransactionError
import vault_registry


@pytest.fixture
def configured(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / ".brain-core").mkdir(parents=True)
    (vault / ".brain-core/VERSION").write_text("0.70.3")
    (vault / ".brain-core/approval-contract.json").write_text(json.dumps(snapshot((
        CommandFact("artefact.read", 1, "application", "observation", "artefact_read", ("artefact", "read")),
        CommandFact("access.request", 1, "application", "control", "access_request", ("access", "request")),
    ))))
    vault_registry.register(vault)
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex/config.toml").write_text('[mcp_servers.brain]\ncommand="brain"\nargs=["mcp","serve"]\n')
    invocation = _invocation(tmp_path, home_dir=home, current_vault=vault)
    monkeypatch.setattr(manager, "_client_version", lambda client: "99.0.0")
    return invocation, home, vault


def request(**kwargs):
    return ApprovalsConfigureRequest(ApprovalClient.ALL, ApprovalScope.USER,
                                     (ApprovalSurface.MCP, ApprovalSurface.CLI), **kwargs)


def test_install_update_idempotence_and_ownership(configured):
    invocation, home, _ = configured
    first = invocation.invoke(request())
    assert first.status == "ok", first
    assert first.committed_effects
    before = (home / ".claude/settings.json").read_bytes()
    second = invocation.invoke(request())
    assert second.status == "ok", second
    assert not second.committed_effects
    assert (home / ".claude/settings.json").read_bytes() == before
    removed = invocation.invoke(request(action=ApprovalAction.REMOVE))
    assert removed.status == "ok", removed
    assert not manager.read_records(manager.FilePlan(), home)


def test_inspect_is_read_only_and_repair_does_not_opt_in(configured):
    invocation, home, _ = configured
    inspected = invocation.invoke(ApprovalsInspectRequest())
    assert inspected.status == "ok", inspected
    assert inspected.result.changed_paths
    assert not manager.ledger_path(home).exists()
    assert not (home / ".claude").exists()
    repaired = invocation.invoke(request(action=ApprovalAction.REPAIR))
    assert repaired.status == "ok", repaired
    assert not repaired.committed_effects


def test_unselected_surface_and_unrelated_settings_untouched(configured):
    invocation, home, _ = configured
    root = home / ".claude"
    root.mkdir()
    (root / "settings.json").write_text(json.dumps({"model": "opus", "permissions": {"ask": ["Read(secret)"]}}))
    result = invocation.invoke(ApprovalsConfigureRequest(ApprovalClient.CLAUDE, ApprovalScope.USER, (ApprovalSurface.MCP,)))
    assert result.status == "ok", result
    settings = json.loads((root / "settings.json").read_text())
    assert settings["model"] == "opus"
    assert "Read(secret)" in settings["permissions"]["ask"]
    assert not any(rule.startswith("Bash(") for rule in settings["permissions"]["allow"])


def test_pending_journal_blocks_writes_and_explicit_recovery_preserves_edits(configured, monkeypatch):
    invocation, home, _ = configured
    real = manager.apply_file_changes
    count = 0
    def interrupted(changes, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            real(changes[:1], **kwargs)
            raise RuntimeError("interrupted")
        return real(changes, **kwargs)
    monkeypatch.setattr(manager, "apply_file_changes", interrupted)
    result = invocation.invoke(request())
    assert result.status == "partial", result
    assert manager.journal_path(home).exists()
    monkeypatch.setattr(manager, "apply_file_changes", real)
    assert invocation.invoke(request()).status == "error"
    recovered = invocation.invoke(request(action=ApprovalAction.RECOVER))
    assert recovered.status == "ok", recovered
    assert not manager.journal_path(home).exists()


def test_multi_scope_recovery_restores_only_selected_layout_before_ledger(configured, monkeypatch):
    invocation, home, vault = configured
    user_path = home / ".claude/settings.json"
    project_path = vault / ".claude/settings.json"
    plan = manager.FilePlan()
    plan.write_text(user_path, '{"permissions": {"allow": ["mcp__brain__artefact_read"]}}')
    plan.write_text(project_path, '{"permissions": {"ask": ["mcp__brain__access_request"]}}')
    manager._save_records(plan, home, {"fixture": {}})
    real = manager.apply_file_changes
    calls = 0
    def crash_after_writes(changes, **kwargs):
        nonlocal calls
        calls += 1
        real(changes, **kwargs)
        if calls == 2:
            raise RuntimeError("process stopped before receipt cleanup")
    monkeypatch.setattr(manager, "apply_file_changes", crash_after_writes)
    with pytest.raises(FileTransactionError):
        manager.commit(plan, home)
    monkeypatch.setattr(manager, "apply_file_changes", real)
    ledger_after = manager.ledger_path(home).read_bytes()
    request_user = ApprovalsConfigureRequest(ApprovalClient.CLAUDE, ApprovalScope.USER,
                                             (ApprovalSurface.MCP,), action=ApprovalAction.RECOVER)
    refused = invocation.invoke(request_user)
    assert refused.status == "ok" and not refused.result.complete
    assert user_path.exists() and project_path.exists()
    assert "both MCP and CLI" in refused.result.targets[0].activation
    request_user = replace(request_user, surfaces=(ApprovalSurface.MCP, ApprovalSurface.CLI))
    first = invocation.invoke(request_user)
    assert first.status == "ok" and not first.result.complete
    assert not user_path.exists() and project_path.exists()
    assert manager.ledger_path(home).read_bytes() == ledger_after
    assert manager.journal_path(home).exists()
    second = manager.manage(replace(invocation._context, workspace_dir=vault),
                            replace(request_user, scope=ApprovalScope.PROJECT))
    assert second.complete
    assert not project_path.exists() and not manager.ledger_path(home).exists()
    assert not manager.journal_path(home).exists()


def test_registration_tightens_shared_policy_before_new_target_is_visible(configured):
    from _launcher.approval_lifecycle import invoke, transition_path
    from _launcher.registry import BrainRegisterRequest, BrainRegisterPayload, RegistryMutationStatus
    from _launcher.contracts import Ok
    import tomllib

    invocation, home, vault = configured
    assert invocation.invoke(request()).status == "ok"
    other = vault.parent / "other"
    (other / ".brain-core").mkdir(parents=True)
    (other / ".brain-core/VERSION").write_text("0.70.4")
    (other / ".brain-core/approval-contract.json").write_text(json.dumps(snapshot((
        CommandFact("artefact.read", 2, "application", "exceptional", "artefact_read", ("artefact", "read")),
    ))))
    req = BrainRegisterRequest(other, "other")
    def register(context, request):
        data = tomllib.loads((home / ".codex/config.toml").read_text())
        assert data["mcp_servers"]["brain"]["tools"]["artefact_read"]["approval_mode"] == "prompt"
        assert transition_path(home).exists()
        vault_registry.register(other, brain_id="other")
        return Ok(req.COMMAND_ID, req.COMMAND_VERSION, BrainRegisterPayload(RegistryMutationStatus.CHANGED, "other"))
    result = invoke(invocation._context, req, "add", register)
    assert result.status == "ok", result
    assert not transition_path(home).exists()
    assert json.loads((home / ".claude/settings.json").read_text())["permissions"]["ask"].count("mcp__brain__artefact_read") == 1


def test_unavailable_client_does_not_abort_other_selected_surfaces(configured, monkeypatch):
    invocation, home, _ = configured
    def version(client):
        if client == "codex":
            raise ValueError("Codex is unavailable")
        return "99.0.0"
    monkeypatch.setattr(manager, "_client_version", version)
    result = invocation.invoke(request())
    assert result.status == "ok" and not result.result.complete
    assert {t.state for t in result.result.targets if t.client == "codex"} == {"blocked"}
    assert (home / ".claude/settings.json").exists()
    assert {r["client"] for r in manager.read_records(manager.FilePlan(), home).values()} == {"claude"}


def test_remove_missing_codex_file_retires_receipt_without_recreating_file(configured):
    invocation, home, _ = configured
    req = ApprovalsConfigureRequest(ApprovalClient.CODEX, ApprovalScope.USER, (ApprovalSurface.MCP,))
    assert invocation.invoke(req).result.complete
    path = home / ".codex/config.toml"
    path.unlink()
    result = invocation.invoke(replace(req, action=ApprovalAction.REMOVE))
    assert result.result.complete and not path.exists()
    assert manager.read_records(manager.FilePlan(), home) == {}


def test_remove_does_not_require_registered_brains_to_be_available(configured):
    invocation, home, vault = configured
    assert invocation.invoke(request()).result.complete
    (vault / ".brain-core/VERSION").unlink()
    result = invocation.invoke(request(action=ApprovalAction.REMOVE))
    assert result.result.complete
    assert manager.read_records(manager.FilePlan(), home) == {}


def test_codex_quoted_executable_is_reported_unsupported(configured):
    invocation, home, _ = configured
    context = replace(invocation._context, cli_binary=home / "Brain CLI/brain")
    result = manager.manage(context, request())
    assert any(t.client == "codex" and t.surface == "cli" and t.state == "blocked" for t in result.targets)
    assert not (home / ".codex/rules/brain.rules").exists()
    assert any(t.client == "claude" and t.surface == "cli" and t.state == "changed" for t in result.targets)


def test_stale_root_prune_removes_project_rules_using_recorded_owner(configured):
    from _launcher.approval_lifecycle import invoke
    from _launcher.registry import RegistryRemoveStaleRequest, execute_remove_stale

    invocation, home, vault = configured
    project = vault.parent / "project"
    project.mkdir()
    (vault / ".brain/local").mkdir(parents=True)
    (vault / ".brain/local/workspaces.json").write_text(json.dumps({"workspaces": {"project": {"path": str(project)}}}))
    context = replace(invocation._context, workspace_dir=project)
    req = ApprovalsConfigureRequest(ApprovalClient.CLAUDE, ApprovalScope.PROJECT, (ApprovalSurface.MCP,))
    assert manager.manage(context, req).complete
    vault.rename(vault.with_name("retired-vault"))
    result = invoke(replace(context, current_vault=None), RegistryRemoveStaleRequest(), "prune", execute_remove_stale)
    assert result.status == "ok", result
    assert not manager.read_records(manager.FilePlan(), home)
    assert "mcp__brain__artefact_read" not in (project / ".claude/settings.json").read_text()


def test_failed_removal_keeps_opt_in_and_reconciles_still_registered_owner(configured):
    from _launcher.approval_lifecycle import invoke
    from _launcher.registry import BrainUnregisterRequest
    from _launcher.contracts import ErrorCode, no_effect_error

    invocation, home, vault = configured
    context = replace(invocation._context, workspace_dir=vault)
    req = ApprovalsConfigureRequest(ApprovalClient.CLAUDE, ApprovalScope.PROJECT, (ApprovalSurface.MCP,))
    assert manager.manage(context, req).complete
    path = vault / ".claude/settings.json"
    original = path.read_text()
    def refused(ctx, operation):
        assert "mcp__brain__artefact_read" not in path.read_text()
        assert manager.read_records(manager.FilePlan(), home)
        return no_effect_error(type(operation), ErrorCode.CONFLICT, "owner removal refused")
    result = invoke(context, BrainUnregisterRequest(vault), "remove", refused)
    assert result.status == "partial", result
    assert path.read_text() == original
    assert manager.read_records(manager.FilePlan(), home)


def test_inventory_migration_tightens_against_frozen_selected_targets(configured, monkeypatch):
    from _launcher.approval_lifecycle import invoke
    from _launcher.machine import BrainMigrateLegacyInstallationsRequest
    from _launcher import machine
    from _launcher.contracts import Ok
    import tomllib

    invocation, home, vault = configured
    assert invocation.invoke(request()).result.complete
    other = vault.parent / "legacy"
    (other / ".brain-core").mkdir(parents=True)
    (other / ".brain-core/VERSION").write_text("0.70.4")
    (other / ".brain-core/approval-contract.json").write_text(json.dumps(snapshot((
        CommandFact("artefact.read", 2, "application", "exceptional", "artefact_read", ("artefact", "read")),
    ))))
    summary = {"brains": [{"path": str(other), "runtime": {"status": "legacy_vault_venv"}}]}
    monkeypatch.setattr(machine, "prepare_legacy_migration", lambda ctx: summary)
    def migrate(ctx, req, prepared):
        assert prepared is summary
        data = tomllib.loads((home / ".codex/config.toml").read_text())
        assert data["mcp_servers"]["brain"]["tools"]["artefact_read"]["approval_mode"] == "prompt"
        vault_registry.register(other, brain_id="legacy")
        return Ok(req.COMMAND_ID, req.COMMAND_VERSION, None)
    monkeypatch.setattr(machine, "execute_prepared_legacy_migration", migrate)
    result = invoke(invocation._context, BrainMigrateLegacyInstallationsRequest(), "inventory", None)
    assert result.status == "ok", result


def test_project_mcp_uses_pinned_transport_owner_after_workspace_moves(configured):
    from _bootstrap import mcp_registration as registration
    from _bootstrap.approval_clients import Selection

    invocation, home, vault = configured
    project = vault.parent / "project"
    project.mkdir()
    other = vault.parent / "other"
    (other / ".brain/local").mkdir(parents=True)
    (other / ".brain/local/workspaces.json").write_text(json.dumps({"workspaces": {"project": {"path": str(project)}}}))
    plan = manager.FilePlan()
    slot = registration._record_for(registration.McpClient.CLAUDE, registration.McpScope.PROJECT,
                                    project, project / ".mcp.json", {"command": "brain", "args": ["mcp", "serve"]})
    registration._save_records(plan, vault / ".brain/local/init-state.json", [slot])
    selection = Selection("claude", "project", "mcp", project / ".claude", invocation._context.cli_binary)
    assert manager._target_roots(plan, selection, project, (vault, other), home) == (vault,)


def test_first_opt_in_cannot_race_an_unmanaged_target_transition(configured):
    from _launcher.approval_lifecycle import invoke, transition_path
    from _launcher.registry import BrainRegisterRequest
    from _launcher.contracts import Ok

    invocation, home, vault = configured
    def transition(ctx, req):
        assert transition_path(home).exists()
        assert invocation.invoke(request()).status == "error"
        return Ok(req.COMMAND_ID, req.COMMAND_VERSION, None)
    result = invoke(invocation._context, BrainRegisterRequest(vault, "vault"), "add", transition)
    assert result.status == "ok"
    assert not manager.ledger_path(home).exists()
    assert not transition_path(home).exists()


def test_unknown_unopted_transition_is_recoverable_with_missing_root(configured):
    from _launcher.approval_lifecycle import invoke, transition_path
    from _launcher.registry import BrainRegisterRequest

    invocation, home, vault = configured
    def uncertain(ctx, req):
        (vault / ".brain-core/VERSION").unlink()
        raise RuntimeError("owner did not return an outcome")
    with pytest.raises(RuntimeError, match="did not return"):
        invoke(invocation._context, BrainRegisterRequest(vault, "vault"), "add", uncertain)
    assert transition_path(home).exists()
    recovered = invocation.invoke(request(action=ApprovalAction.RECOVER))
    assert recovered.status == "ok", recovered
    assert not transition_path(home).exists()


def test_legacy_migration_uses_same_transaction_and_restores_on_remove(configured):
    invocation, home, _ = configured
    req = ApprovalsConfigureRequest(ApprovalClient.CODEX, ApprovalScope.USER, (ApprovalSurface.CLI,))
    assert invocation.invoke(req).result.complete
    generated = home / ".codex/rules/brain.rules"
    rule = next(line for line in generated.read_text().splitlines() if '"artefact", "read"' in line)
    assert invocation.invoke(replace(req, action=ApprovalAction.REMOVE)).result.complete
    legacy = generated.with_name("default.rules")
    original = rule + "\n# unrelated\n" + rule + "\n"
    legacy.write_text(original)
    inspected = invocation.invoke(ApprovalsInspectRequest(ApprovalClient.CODEX, ApprovalScope.USER, (ApprovalSurface.CLI,)))
    key = next(i.identity for t in inspected.result.targets for i in t.items if i.state == "legacy_matching")
    adopted = invocation.invoke(replace(req, action=ApprovalAction.ADOPT, adopt_items=(key,)))
    assert adopted.result.complete
    assert legacy.read_text() == "# unrelated\n"
    assert invocation.invoke(replace(req, action=ApprovalAction.REMOVE)).result.complete
    assert legacy.read_text() == original


def test_deleted_rule_is_not_restored_without_explicit_selection(configured):
    invocation, home, _ = configured
    req = ApprovalsConfigureRequest(ApprovalClient.CLAUDE, ApprovalScope.USER, (ApprovalSurface.MCP,))
    assert invocation.invoke(req).result.complete
    path = home / ".claude/settings.json"
    data = json.loads(path.read_text())
    data["permissions"]["allow"].remove("mcp__brain__artefact_read")
    path.write_text(json.dumps(data))
    result = invocation.invoke(replace(req, action=ApprovalAction.REPAIR))
    assert not result.result.complete
    key = next(i.identity for t in result.result.targets for i in t.items if i.state == "deleted")
    assert "mcp__brain__artefact_read" not in json.loads(path.read_text())["permissions"]["allow"]
    restored = invocation.invoke(replace(req, action=ApprovalAction.REPAIR, restore_items=(key,)))
    assert restored.result.complete
    assert "mcp__brain__artefact_read" in json.loads(path.read_text())["permissions"]["allow"]


def test_doctor_pending_recovery_is_read_only(configured):
    invocation, home, _ = configured
    manager.journal_path(home).parent.mkdir(parents=True)
    manager.journal_path(home).write_text("{}")
    before = manager.journal_path(home).read_bytes()
    result = manager.inspect_registered(invocation._context)
    assert result[0].state == "blocked"
    assert manager.journal_path(home).read_bytes() == before
    assert not manager.ledger_path(home).exists()


def test_concurrent_native_edit_is_preserved_and_journal_retained(configured, monkeypatch):
    invocation, home, _ = configured
    real = manager.apply_file_changes
    def edit_after_journal(changes, **kwargs):
        result = real(changes, **kwargs)
        if any(change.path == manager.journal_path(home) for change in changes):
            with (home / ".codex/config.toml").open("a") as file:
                file.write("# concurrent user edit\n")
        return result
    monkeypatch.setattr(manager, "apply_file_changes", edit_after_journal)
    result = invocation.invoke(request())
    assert result.status == "partial", result
    assert "# concurrent user edit" in (home / ".codex/config.toml").read_text()
    assert manager.journal_path(home).exists()


def test_unchanged_transition_has_no_policy_effects(configured):
    from _launcher.approval_lifecycle import invoke
    from _launcher.registry import BrainRegisterRequest, BrainRegisterPayload, RegistryMutationStatus
    from _launcher.contracts import Ok
    invocation, _, vault = configured
    assert invocation.invoke(request()).result.complete
    req = BrainRegisterRequest(vault, "vault")
    result = invoke(invocation._context, req, "add", lambda context, request: Ok(
        req.COMMAND_ID, req.COMMAND_VERSION, BrainRegisterPayload(RegistryMutationStatus.NOOP, "vault")))
    assert result.status == "ok", result
    assert not result.committed_effects


def test_new_allowance_follows_committed_contract_and_failed_transition_does_not_widen(configured, tmp_path):
    from _launcher.approval_lifecycle import invoke
    from _launcher.lifecycle import BrainUpgradeRequest
    from _launcher.contracts import Ok, no_effect_error, ErrorCode
    from types import SimpleNamespace
    invocation, home, vault = configured
    assert invocation.invoke(request()).result.complete
    source = tmp_path / "source"
    core = source / "src/brain-core"
    core.mkdir(parents=True)
    (source / "cli").mkdir()
    (source / "cli/approval-contract.json").write_bytes((ROOT / "cli/approval-contract.json").read_bytes())
    old = (vault / ".brain-core/approval-contract.json").read_text()
    facts = manager.read_snapshot(json.loads(old))
    new = json.dumps(snapshot((*facts, CommandFact("artefact.new", 1, "application", "content", "artefact_new", ("artefact", "new")))))
    (core / "approval-contract.json").write_text(new)
    context = replace(invocation._context, distribution_root=source)
    req = BrainUpgradeRequest()
    def check():
        data = json.loads((home / ".claude/settings.json").read_text())
        assert "mcp__brain__artefact_new" not in data["permissions"]["allow"]
    def fail(context, request):
        check()
        return no_effect_error(type(req), ErrorCode.CONFLICT, "fixture transition refused")
    assert invoke(context, req, "version", fail).status == "partial"
    check()
    def succeed(context, request):
        check()
        (vault / ".brain-core/approval-contract.json").write_text(new)
        return Ok(req.COMMAND_ID, req.COMMAND_VERSION, SimpleNamespace())
    assert invoke(context, req, "version", succeed).status == "ok"
    assert "mcp__brain__artefact_new" in json.loads((home / ".claude/settings.json").read_text())["permissions"]["allow"]
