"""Workspace upgrade is a separately admitted post-runtime Core follow-up."""

import pytest
import upgrade


@pytest.fixture
def reconciliation(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(upgrade, "_post_upgrade_sync", lambda *args, **kwargs: None)
    monkeypatch.setattr(upgrade, "_validate_compile", lambda *args: None)
    monkeypatch.setattr(upgrade, "_ensure_central_runtime", lambda *args, **kwargs:
                        events.append("runtime") or {"outcome": "reused"})
    monkeypatch.setattr(upgrade, "_repair_mcp_registration_after_upgrade", lambda *args:
                        events.append("mcp") or {"outcome": "ok"})
    monkeypatch.setattr(upgrade, "_ensure_machine_resolution_runtime", lambda *args: {"outcome": "ok"})
    monkeypatch.setattr(upgrade, "_repair_retrieval_assets_after_upgrade", lambda *args: {"outcome": "ok"})
    monkeypatch.setattr(upgrade, "_complete_runtime_readiness", lambda *args: {"outcome": "ok"})
    monkeypatch.setattr(upgrade, "_inspect_runtime_orphans", lambda *args: {"outcome": "ok"})
    monkeypatch.setattr(upgrade, "_deferred_mcp_registration_after_upgrade", lambda *args: {"outcome": "deferred"})
    monkeypatch.setattr(upgrade, "_write_upgrade_log", lambda *args: None)
    (tmp_path / ".brain-core").mkdir()
    (tmp_path / ".brain-core/VERSION").write_text("0.72.0\n")
    contract = tmp_path / ".brain-core/scripts/_bootstrap/workspace_schema.py"
    contract.parent.mkdir(parents=True)
    contract.write_text("SCHEMA_VERSION = 1\n")
    return events, tmp_path


def test_runtime_then_separate_workspace_admission_then_dependent_mcp(reconciliation):
    events, vault = reconciliation
    def child(**kwargs):
        assert kwargs["dry_run"] is False
        events.append("workspace-child")
        return {"outcome": "ok", "invocation_id": "independent-workspace-receipt"}
    result = upgrade._reconcile_after_commit(str(vault),
        {"status": "ok", "old_version": "0.71.4", "new_version": "0.72.0"},
        lambda *args: None, sync=False, sync_deps=True, workspace_callback=child)
    assert events == ["runtime", "workspace-child", "mcp"]
    assert result["workspace_upgrade"]["invocation_id"] == "independent-workspace-receipt"
    assert result["status"] == "ok"


@pytest.mark.parametrize("outcome", ["error", "partial", "unknown"])
def test_workspace_failure_keeps_core_witness_and_defers_mcp(reconciliation, outcome):
    events, vault = reconciliation
    result = upgrade._reconcile_after_commit(str(vault),
        {"status": "ok", "old_version": "0.71.4", "new_version": "0.72.0"},
        lambda *args: None, sync=False, sync_deps=True,
        workspace_callback=lambda **kwargs: {"outcome": outcome, "message": "external follow-up"})
    assert events == ["runtime"]
    assert result["status"] == "partial"
    assert result["mcp_registration_repair"]["outcome"] == "deferred"
    assert (vault / ".brain-core/VERSION").read_text() == "0.72.0\n"
    assert "workspace metadata" in result["message"]


def test_failed_runtime_does_not_admit_workspace_or_mcp(reconciliation, monkeypatch):
    events, vault = reconciliation
    monkeypatch.setattr(upgrade, "_ensure_central_runtime", lambda *args, **kwargs: {"outcome": "error"})
    registry = vault / ".brain/local/workspaces.json"
    registry.parent.mkdir(parents=True)
    registry.write_text('{"workspaces":{"shared":{"path":"/unavailable/folder"}}}')
    def must_not_run(**kwargs):
        pytest.fail("workspace writes cannot precede compatible runtime provisioning")
    result = upgrade._reconcile_after_commit(str(vault),
        {"status": "ok", "old_version": "0.71.4", "new_version": "0.72.0"},
        lambda *args: None, sync=False, sync_deps=True, workspace_callback=must_not_run)
    assert events == []
    assert result["status"] == "partial"
    assert result["workspace_upgrade"]["outcome"] == "deferred"


def test_preview_uses_the_same_child_owner_with_source_contract(tmp_path):
    observed = []
    source = tmp_path / "src/brain-core"
    contract = source / "scripts/_bootstrap/workspace_schema.py"
    contract.parent.mkdir(parents=True)
    contract.write_text("SCHEMA_VERSION = 1\n")
    summary = upgrade._workspace_upgrade_phase(tmp_path, str(source),
        lambda **kwargs: observed.append(kwargs) or {"outcome": "planned"}, dry_run=True)
    assert summary["outcome"] == "planned"
    assert observed == [{"dry_run": True, "source_core": source}]


def test_direct_script_delegates_to_existing_machine_cli_owner(monkeypatch, tmp_path):
    from _bootstrap import machine_cli
    calls = []
    def invoke(command, request, **kwargs):
        calls.append((command, request, kwargs))
        return {"schema": "brain.command-result/1", "status": "ok", "result": {"complete": True}}
    monkeypatch.setattr(machine_cli, "invoke", invoke)
    contract = tmp_path / ".brain-core/scripts/_bootstrap/workspace_schema.py"
    contract.parent.mkdir(parents=True)
    contract.write_text("SCHEMA_VERSION = 1\n")
    registry = tmp_path / ".brain/local/workspaces.json"
    registry.parent.mkdir(parents=True)
    registry.write_text('{"workspaces":{"shared":{"path":"/registered/local/folder"}}}')
    result = upgrade._workspace_upgrade_phase(tmp_path, None)
    assert result["outcome"] == "ok"
    assert calls == [("workspace.upgrade", {"scope": "brain"},
                      {"source_root": None, "vault": tmp_path, "dry_run": False})]


def test_postcommit_callback_exception_becomes_follow_up(tmp_path):
    def fail(**kwargs):
        raise OSError("child receipt store unavailable")
    contract = tmp_path / ".brain-core/scripts/_bootstrap/workspace_schema.py"
    contract.parent.mkdir(parents=True)
    contract.write_text("SCHEMA_VERSION = 1\n")
    result = upgrade._workspace_upgrade_phase(tmp_path, None, fail)
    assert result["outcome"] == "error"
    assert "receipt store" in result["message"]


def test_historical_target_preserves_its_existing_mcp_upgrade_behaviour(reconciliation):
    events, vault = reconciliation
    (vault / ".brain-core/scripts/_bootstrap/workspace_schema.py").unlink()
    result = upgrade._reconcile_after_commit(str(vault),
        {"status": "ok", "old_version": "0.10.0", "new_version": "0.11.0"},
        lambda *args: None, sync=False, sync_deps=True,
        workspace_callback=lambda **kwargs: pytest.fail("Historical target has no workspace phase"))
    assert result["workspace_upgrade"]["outcome"] == "not_applicable"
    assert result["status"] == "ok"
    assert events == ["runtime", "mcp"]


def test_compatible_core_noop_resumes_failed_workspace_child(reconciliation, monkeypatch):
    events, vault = reconciliation
    source = vault / ".brain-core"
    monkeypatch.setattr(upgrade, "_under_vault_lock", lambda *args:
        {"status": "skipped", "old_version": "0.72.0", "new_version": "0.72.0"})
    attempts = []
    def child(**kwargs):
        attempts.append(kwargs)
        return {"outcome": "partial" if len(attempts) == 1 else "ok", "invocation_id": "independent-child"}
    first = upgrade.upgrade(str(vault), str(source), sync=False, sync_deps=True, workspace_callback=child)
    assert first["status"] == "partial"
    assert first["core_skipped"] is True
    second = upgrade.upgrade(str(vault), str(source), sync=False, sync_deps=True, workspace_callback=child)
    assert second["status"] == "skipped"
    assert len(attempts) == 2
    assert events == ["runtime", "runtime", "mcp"]
    assert (vault / ".brain-core/VERSION").read_text() == "0.72.0\n"


@pytest.mark.parametrize("inventory", [None, '{"workspaces":{}}', '{"schema_version":1,"registrations":{}}'])
@pytest.mark.parametrize("dry_run", [False, True])
def test_empty_direct_workspace_phase_needs_no_installed_cli(reconciliation, monkeypatch, inventory, dry_run):
    from _bootstrap import machine_cli
    _, vault = reconciliation
    if inventory is not None:
        registry = vault / ".brain/local/workspaces.json"
        registry.parent.mkdir(parents=True)
        registry.write_text(inventory)
    monkeypatch.setattr(machine_cli, "invoke", lambda *args, **kwargs:
                        pytest.fail("An empty observed inventory must not delegate a mutation"))
    result = upgrade._workspace_upgrade_phase(vault, None, dry_run=dry_run)
    assert result == {"outcome": "no_work", "message": "No registered external workspace folders require migration."}


def test_empty_inventory_does_not_skip_an_explicit_child(reconciliation):
    _, vault = reconciliation
    calls = []
    def child(**kwargs):
        calls.append(kwargs)
        return {"outcome": "partial", "invocation_id": "denied-child", "message": "Fresh local authority is required"}
    result = upgrade._workspace_upgrade_phase(vault, None, child)
    assert len(calls) == 1
    assert result["outcome"] == "partial"
    assert result["invocation_id"] == "denied-child"


@pytest.mark.parametrize("inventory", ['{"workspaces":[]}', '{"schema_version":99,"registrations":{}}', 'invalid json'])
def test_invalid_inventory_is_not_an_empty_workspace_phase(reconciliation, monkeypatch, inventory):
    from _bootstrap import machine_cli
    _, vault = reconciliation
    registry = vault / ".brain/local/workspaces.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(inventory)
    monkeypatch.setattr(machine_cli, "invoke", lambda *args, **kwargs:
                        pytest.fail("Malformed inventory must refuse before delegation"))
    result = upgrade._workspace_upgrade_phase(vault, None)
    assert result["outcome"] == "error"
    assert registry.read_text() == inventory


def test_noop_core_with_empty_direct_workset_preserves_skip_and_preview(reconciliation, monkeypatch):
    events, vault = reconciliation
    source = vault / ".brain-core"
    skipped = {"status": "skipped", "old_version": "0.72.0", "new_version": "0.72.0",
               "message": "Already at 0.72.0. Use --force to re-apply."}
    monkeypatch.setattr(upgrade, "_under_vault_lock", lambda *args: dict(skipped))
    monkeypatch.setattr(upgrade, "_preview", lambda *args, **kwargs: dict(skipped))
    preview = upgrade.upgrade(str(vault), str(source), dry_run=True)
    applied = upgrade.upgrade(str(vault), str(source))
    assert applied == preview
    assert applied["status"] == "skipped"
    assert applied["message"] == skipped["message"]
    assert applied["workspace_upgrade"]["outcome"] == "no_work"
    assert events == []
