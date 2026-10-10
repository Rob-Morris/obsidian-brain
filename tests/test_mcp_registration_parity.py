"""Canonical ownership, migration and composable MCP repair behaviour."""

import json
import sys
import tomllib
from pathlib import Path

import pytest

from _bootstrap import mcp_registration as owner, mcp_inventory, mcp_migration
from _bootstrap.file_transaction import FilePlan, apply_file_changes


def apply(plan):
    plan.validate()
    apply_file_changes(plan.changes())


def test_user_ownership_has_no_brain_dependency(tmp_path):
    home = tmp_path / "home"
    server = owner.stable_server_config(tmp_path / "bin/brain")
    clients = owner._clients(owner.McpClient.ALL, owner.McpScope.USER)
    apply(owner._configure_plan(None, home, None, owner.McpScope.USER, clients, server))
    _, records = owner.read_records(FilePlan(), None, home, owner.McpScope.USER)
    assert len(records) == 3
    assert all(record["target_path"] is None and record["server_config"] == server for record in records)
    assert not owner._configure_plan(None, home, None, owner.McpScope.USER, clients, server, repair=True).changes()
    apply(owner._remove_plan(None, home, None, owner.McpScope.USER, clients))
    assert not owner.user_ledger_path(home).exists()


def test_repair_restores_owned_missing_not_unregistered_client(tmp_path):
    home = tmp_path / "home"
    server = owner.stable_server_config(tmp_path / "bin/brain")
    apply(owner._configure_plan(None, home, None, owner.McpScope.USER, (owner.McpClient.CLAUDE,), server))
    (home / ".claude.json").unlink()
    apply(owner._configure_plan(None, home, None, owner.McpScope.USER,
                               owner._clients(owner.McpClient.ALL, owner.McpScope.USER), server, repair=True))
    assert (home / ".claude.json").exists()
    assert not (home / ".codex/config.toml").exists()


@pytest.mark.parametrize("recorded", [False, True])
def test_custom_slot_is_never_adopted_or_overwritten(tmp_path, recorded):
    home = tmp_path / "home"
    server = owner.stable_server_config(tmp_path / "bin/brain")
    if recorded:
        apply(owner._configure_plan(None, home, None, owner.McpScope.USER, (owner.McpClient.CLAUDE,), server))
    home.mkdir(exist_ok=True)
    path = home / ".claude.json"
    path.write_text(json.dumps({"mcpServers": {"brain": {"command": "custom"}}}))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="unowned or modified"):
        owner._configure_plan(None, home, None, owner.McpScope.USER, (owner.McpClient.CLAUDE,), server)
    assert path.read_bytes() == before


def legacy_fixture(tmp_path, monkeypatch):
    import vault_registry

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    vault = tmp_path / "Brain"
    (vault / ".brain-core").mkdir(parents=True)
    (vault / ".brain-core/VERSION").write_text("0.69.1")
    (vault / "AGENTS.md").write_text("Brain")
    vault_registry.register(vault, "brain")
    server = {"command": "/old/python", "args": ["-m", "brain_mcp.proxy"], "env": {}}
    destination = home / ".claude.json"
    destination.write_text(json.dumps({"custom": True, "mcpServers": {"brain": server}}))
    state = vault / ".brain/local/init-state.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"version": 1, "records": [{
        "client": "claude", "scope": "user", "target_path": None,
        "config_path": str(destination), "server_config": server,
    }]}))
    return home, vault, state, destination


def test_migration_transfers_ownership_and_is_idempotent(tmp_path, monkeypatch):
    home, vault, state, destination = legacy_fixture(tmp_path, monkeypatch)
    binary = tmp_path / "bin/brain"
    with pytest.raises(ValueError, match="migration required"):
        owner.read_records(FilePlan(), vault, home, owner.McpScope.PROJECT)
    changes = mcp_migration.migration_plan(home, binary)
    assert state.exists()  # inspection is read-only
    mcp_migration.apply_migration(changes, home)
    assert not state.exists()
    assert json.loads(destination.read_text())["custom"] is True
    assert json.loads(destination.read_text())["mcpServers"]["brain"] == owner.stable_server_config(binary)
    assert json.loads(mcp_migration.journal_path(home).read_text())["phase"] == "complete"
    assert not mcp_migration.migration_plan(home, binary).changes()


def test_migration_modified_entry_leaves_all_claims_untouched(tmp_path, monkeypatch):
    home, vault, state, destination = legacy_fixture(tmp_path, monkeypatch)
    destination.write_text('{"mcpServers":{"brain":{"command":"custom"}}}')
    before = state.read_bytes()
    with pytest.raises(ValueError, match="Modified/conflicting"):
        mcp_migration.migration_plan(home, tmp_path / "bin/brain")
    assert state.read_bytes() == before
    assert not owner.user_ledger_path(home).exists()


@pytest.mark.parametrize("inline", [False, True])
def test_codex_approval_policy_survives_migration_repair_and_configuration(tmp_path, monkeypatch, inline):
    home, vault, state, _ = legacy_fixture(tmp_path, monkeypatch)
    evidence = json.loads(state.read_text())
    record = evidence["records"][0]
    destination = home / ".codex/config.toml"
    destination.parent.mkdir()
    record.update(client="codex", config_path=str(destination))
    state.write_text(json.dumps(evidence))
    policy = ('default_tools_approval_mode = "prompt"\n'
              'enabled_tools = ["read", "write"]\n'
              'disabled_tools = ["delete"]\n')
    tools = ('tools = { write = { approval_mode = "approve" } }\n' if inline else
             '[mcp_servers.brain.tools.write]\napproval_mode = "approve"\n')
    destination.write_text('[mcp_servers.brain]\ncommand = "/old/python"\n'
                           'args = ["-m", "brain_mcp.proxy"]\n' + policy + tools)
    original_policy = {key: value for key, value in tomllib.loads(destination.read_text())["mcp_servers"]["brain"].items()
                       if key not in {"command", "args", "env"}}
    binary = tmp_path / "bin/brain"
    mcp_migration.apply_migration(mcp_migration.migration_plan(home, binary), home)
    for repair in (True, False):
        server = owner.stable_server_config(binary)
        apply(owner._configure_plan(None, home, None, owner.McpScope.USER,
                                    (owner.McpClient.CODEX,), server, repair=repair))
        observed = owner.observed_server(FilePlan(), owner.McpClient.CODEX, destination)
        assert {key: observed[key] for key in original_policy} == original_policy
        assert observed["command"] == str(binary)
        _, records = owner.read_records(FilePlan(), None, home, owner.McpScope.USER)
        assert records[0]["server_config"] == server
    assert not mcp_migration.migration_plan(home, binary).changes()
    inventory = mcp_inventory.inspect_registrations(home, (vault,), binary)
    assert next(item for item in inventory["registrations"] if item.get("client") == "codex" and item.get("scope") == "user")["state"] == "current"
    before = destination.read_bytes()
    with pytest.raises(ValueError, match="client-owned policy"):
        owner._remove_plan(None, home, None, owner.McpScope.USER, (owner.McpClient.CODEX,))
    assert destination.read_bytes() == before


@pytest.mark.parametrize("extra", [{"url": "https://example.invalid"}, {"cwd": "/elsewhere"},
                                    {"env_vars": ["SECRET"]}, {"unknown": True},
                                    {"tools": {"write": {}}},
                                    {"tools": {"write": {"approval_mode": "approve", "unknown": True}}}])
def test_approval_policy_does_not_hide_transport_conflicts(tmp_path, extra):
    server = owner.stable_server_config(tmp_path / "brain")
    observed = {**server, "tools": {"write": {"approval_mode": "approve"}}, **extra}
    assert not owner.server_matches(owner.McpClient.CODEX, observed, server)


@pytest.mark.parametrize("scope", [owner.McpScope.PROJECT, owner.McpScope.USER])
def test_changed_approval_policy_survives_transport_repair(tmp_path, monkeypatch, scope):
    home, vault, state, _ = legacy_fixture(tmp_path, monkeypatch)
    state.unlink()
    target = vault if scope is owner.McpScope.PROJECT else None
    server = owner.stable_server_config(tmp_path / "old/brain")
    clients = (owner.McpClient.CODEX,)
    apply(owner._configure_plan(vault, home, target, scope, clients, server))
    path = owner._config_path(owner.McpClient.CODEX, scope, target, home)
    policy = '\n[mcp_servers.brain.tools."write.special"]\napproval_mode = "prompt"\n'
    path.write_text(path.read_text() + policy)
    replacement = owner.stable_server_config(tmp_path / "new/brain")
    apply(owner._configure_plan(vault, home, target, scope, clients, replacement, repair=True))
    current = owner.observed_server(FilePlan(), owner.McpClient.CODEX, path)
    assert current["command"] == replacement["command"]
    assert current["tools"]["write.special"]["approval_mode"] == "prompt"
    assert not owner._configure_plan(vault, home, target, scope, clients, replacement, repair=True).changes()


@pytest.mark.parametrize("client", [owner.McpClient.CLAUDE, owner.McpClient.GROK])
def test_sibling_client_approval_policy_survives_lifecycle(tmp_path, monkeypatch, client):
    from _bootstrap import mcp_state

    home, vault, state, destination = legacy_fixture(tmp_path, monkeypatch)
    evidence = json.loads(state.read_text())
    record = evidence["records"][0]
    if client is owner.McpClient.GROK:
        destination = home / ".grok/config.toml"
        destination.parent.mkdir()
        content = ('[permission]\nrules = [{ action = "allow", tool = "MCPTool" }]\n'
                   '[ui]\npermission_mode = "ask"\n')
        destination.write_text(mcp_state.render_toml_config(content, record["server_config"]))
    else:
        data = json.loads(destination.read_text())
        data["projects"] = {str(vault): {"enabledMcpjsonServers": ["brain"], "disabledMcpjsonServers": ["other"]}}
        destination.write_text(json.dumps(data))
        settings = home / ".claude/settings.json"
        settings.parent.mkdir()
        settings.write_text(json.dumps({"permissions": {"allow": ["mcp__brain__read"], "ask": ["mcp__brain__write"], "deny": ["mcp__brain__delete"]}}))
        settings_before = settings.read_bytes()
    record.update(client=client.value, config_path=str(destination))
    state.write_text(json.dumps(evidence))
    parse = json.loads if client is owner.McpClient.CLAUDE else tomllib.loads
    before = parse(destination.read_text())
    binary = tmp_path / "bin/brain"
    mcp_migration.apply_migration(mcp_migration.migration_plan(home, binary), home)
    for repair in (True, False):
        apply(owner._configure_plan(None, home, None, owner.McpScope.USER, (client,), owner.stable_server_config(binary), repair=repair))
        after = parse(destination.read_text())
        container = "mcpServers" if client is owner.McpClient.CLAUDE else "mcp_servers"
        assert {key: value for key, value in after.items() if key != container} == {key: value for key, value in before.items() if key != container}
        if client is owner.McpClient.CLAUDE:
            assert settings.read_bytes() == settings_before


def test_interrupted_migration_resumes_from_durable_evidence(tmp_path, monkeypatch):
    home, vault, state, destination = legacy_fixture(tmp_path, monkeypatch)
    plan = mcp_migration.migration_plan(home, tmp_path / "bin/brain")
    real_apply = mcp_migration.apply_file_changes
    calls = 0

    def interrupt(changes, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("power interruption")
        real_apply(changes, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(mcp_migration, "apply_file_changes", interrupt)
        with pytest.raises(OSError, match="power interruption"):
            mcp_migration.apply_migration(plan, home)
    assert json.loads(mcp_migration.journal_path(home).read_text())["phase"] == "pending"
    mcp_migration.resume_migration(home)
    assert not state.exists()
    assert json.loads(mcp_migration.journal_path(home).read_text())["phase"] == "complete"


def test_incomplete_registry_is_not_empty_workset(tmp_path, monkeypatch):
    import vault_registry

    path = tmp_path / "vaults"
    path.write_text("malformed\n")
    monkeypatch.setattr(vault_registry, "registry_path", lambda: str(path))
    with pytest.raises(ValueError, match="Incomplete Brain registry"):
        mcp_inventory.local_brains(FilePlan())


def test_plan_revalidates_read_only_admission_evidence(tmp_path):
    evidence = tmp_path / "identity"
    evidence.write_text("one")
    plan = FilePlan()
    assert plan.read_text(evidence) == "one"
    plan.write_text(tmp_path / "projection", "desired")
    evidence.write_text("two")
    with pytest.raises(RuntimeError, match="changed after inspection"):
        apply(plan)
    assert not (tmp_path / "projection").exists()


@pytest.mark.parametrize("evidence", [{}, {"version": 3, "phase": "complete", "changes": []},
                                      {"version": 1, "phase": "invalid", "changes": []}])
def test_malformed_recovery_evidence_is_not_noop(tmp_path, evidence):
    path = mcp_migration.journal_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(evidence))
    with pytest.raises(ValueError, match="Invalid MCP migration"):
        mcp_migration.resume_migration(tmp_path)


def test_cli_capability_retained_for_missing_owned_projection(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    binary = tmp_path / "bin/brain"
    apply(owner._configure_plan(None, home, None, owner.McpScope.USER, (owner.McpClient.CLAUDE,), owner.stable_server_config(binary)))
    (home / ".claude.json").unlink()
    with pytest.raises(ValueError, match="would strand"):
        owner.require_launcher_capability(binary, supports_stdio=False)
    owner.require_launcher_capability(binary, supports_stdio=True)


def test_modified_grok_rule_blocks_removal_without_removing_transport(tmp_path):
    home = tmp_path / "home"
    server = owner.stable_server_config(tmp_path / "bin/brain")
    clients = (owner.McpClient.GROK,)
    apply(owner._configure_plan(None, home, None, owner.McpScope.USER, clients, server))
    rule = home / ".grok/rules/brain.md"
    rule.write_text("my customised instruction\n")
    config = home / ".grok/config.toml"
    before = config.read_bytes()
    with pytest.raises(ValueError, match="Modified Grok bootstrap"):
        owner._remove_plan(None, home, None, owner.McpScope.USER, clients)
    assert config.read_bytes() == before
    assert rule.read_text() == "my customised instruction\n"


@pytest.mark.parametrize("state", [{}, {"version": 1, "records": [{"client": "claude", "scope": "project", "target_path": None}]}])
def test_invalid_migration_ownership_fails_before_writes(tmp_path, monkeypatch, state):
    home, vault, ledger, destination = legacy_fixture(tmp_path, monkeypatch)
    ledger.write_text(json.dumps(state))
    before = destination.read_bytes()
    with pytest.raises(ValueError):
        mcp_migration.migration_plan(home, tmp_path / "bin/brain")
    assert destination.read_bytes() == before


def test_migration_postcondition_failure_reports_all_committed_paths(tmp_path, monkeypatch):
    home, vault, state, destination = legacy_fixture(tmp_path, monkeypatch)
    plan = mcp_migration.migration_plan(home, tmp_path / "bin/brain")
    def fail_finish(*args):
        raise OSError("journal finalisation failed")
    monkeypatch.setattr(mcp_migration, "_finish_journal", fail_finish)
    with pytest.raises(OSError, match="finalisation") as error:
        mcp_migration.apply_migration(plan, home)
    assert set(error.value.surviving_paths) == {change.path for change in plan.changes()} | {mcp_migration.journal_path(home)}
    assert json.loads(mcp_migration.journal_path(home).read_text())["phase"] == "pending"


@pytest.mark.parametrize("stale", [False, True])
def test_registry_removal_cannot_drop_project_ownership(tmp_path, monkeypatch, stale):
    import vault_registry

    home, vault, state, destination = legacy_fixture(tmp_path, monkeypatch)
    state.unlink()
    apply(owner._configure_plan(vault, home, vault, owner.McpScope.PROJECT,
                               (owner.McpClient.CODEX,), {"command": "/managed/python", "args": [], "env": {}}))
    if stale:
        (vault / ".brain-core/VERSION").unlink()
    action = vault_registry.prune_action if stale else lambda: vault_registry.unregister_action(vault)
    with pytest.raises(ValueError, match="registered MCP integrations"):
        action()
    assert vault_registry.resolve("brain") == str(vault)


def test_an_unreachable_target_is_reported_while_the_reachable_one_is_repaired(tmp_path, monkeypatch):
    """An absent linked folder is named, never unhealthy, and never blocks repairing the rest (DD-083)."""
    import shutil
    import vault_registry
    import workspace_registry
    from _bootstrap.mcp_state import build_mcp_config

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    vault = (tmp_path / "Brain").resolve()
    (vault / ".brain-core").mkdir(parents=True)
    (vault / ".brain-core/VERSION").write_text("0.70.10")
    # A stand-in managed runtime keeps the test offline.
    monkeypatch.setattr(owner, "_runtime_python", lambda _vault: sys.executable)
    vault_registry.register(vault, "brain")
    folders = {}
    for key in ("here", "away"):
        folder = (tmp_path / key).resolve()
        (folder / ".brain/local").mkdir(parents=True)
        (folder / ".brain/local/workspace.yaml").write_text(f"brain: brain\nslug: {key}\nlinks:\n  workspace: {key}\n")
        workspace_registry.register_workspace(vault, key, folder)
        server = build_mcp_config(owner._runtime_python(vault), vault, workspace_dir=folder)
        apply(owner._configure_plan(vault, home, folder, owner.McpScope.PROJECT, (owner.McpClient.CLAUDE,), server))
        folders[key] = folder
    shutil.rmtree(folders["away"])
    binary = tmp_path / "bin/brain"

    inventory = mcp_inventory.inspect_registrations(home, (vault,), binary)
    unreachable = [item for item in inventory["registrations"] if item["state"] == "unreachable"]
    assert inventory["healthy"] and inventory["complete"], inventory
    assert [item["path"] for item in unreachable] == [str(folders["away"])]
    assert "migrate" not in unreachable[0]["action"]
    references, coverage = mcp_inventory.persisted_runtime_references(home, (vault,))
    assert [item.path for item in coverage.unreachable] == [folders["away"]]
    assert coverage.invalid == () and not coverage.complete
    assert "Reconnect them, or unregister them" in coverage.blocked_reason()

    projection = owner._config_path(owner.McpClient.CLAUDE, owner.McpScope.PROJECT, folders["here"], home)
    projection.unlink()
    with pytest.raises(ValueError, match="is unavailable"):
        mcp_inventory.plan_repair(FilePlan(), (vault,), home, binary)
    collected = []
    plan = FilePlan()
    _, targets = mcp_inventory.plan_repair(plan, (vault,), home, binary, unreachable=collected)
    assert targets == (str(folders["here"]),)
    assert [item.path for item in collected] == [folders["away"]]
    apply(plan)
    assert projection.exists(), "the reachable target is repaired"


def test_a_linked_path_replaced_by_a_file_is_absent_not_invalid(tmp_path, monkeypatch):
    """Every PROJECT and LOCAL slot lives under the folder, so an absent folder's slots are never read."""
    import vault_registry
    import workspace_registry

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setattr(owner, "_runtime_python", lambda _vault: sys.executable)
    vault = (tmp_path / "Brain").resolve()
    (vault / ".brain-core").mkdir(parents=True)
    (vault / ".brain-core/VERSION").write_text("0.70.10")
    vault_registry.register(vault, "brain")
    replaced = (tmp_path / "replaced").resolve()
    replaced.mkdir()
    workspace_registry.register_workspace(vault, "replaced", replaced)
    replaced.rmdir()
    replaced.write_text("now a file\n")

    _references, coverage = mcp_inventory.persisted_runtime_references(home, (vault,))
    inventory = mcp_inventory.inspect_registrations(home, (vault,), tmp_path / "bin/brain")

    assert [item.path for item in coverage.unreachable] == [replaced]
    assert coverage.invalid == ()
    assert inventory["healthy"], inventory


@pytest.mark.parametrize(("row", "absent"), [("~/linked-under-home", False), ("relative", None), ("/tmp/a\u0000b", None)])
def test_the_inventory_reads_rows_by_the_registry_row_rule(tmp_path, monkeypatch, row, absent):
    """`~` is a usable (absolute) row; a relative or NUL row is invalid, never absent and never cwd-relative."""
    import json as json_module
    import vault_registry

    home = tmp_path / "home"
    (home / "linked-under-home").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    vault = (tmp_path / "Brain").resolve()
    (vault / ".brain-core").mkdir(parents=True)
    (vault / ".brain-core/VERSION").write_text("0.70.10")
    (vault / ".brain/local").mkdir(parents=True)
    (vault / ".brain/local/workspaces.json").write_text(json_module.dumps({"workspaces": {"key": {"path": row}}}))
    vault_registry.register(vault, "brain")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "relative").mkdir()

    collected = []
    if absent is None:
        with pytest.raises(ValueError, match="names no usable folder"):
            mcp_inventory.workspace_paths(FilePlan(), vault, unreachable=collected)
        assert collected == []
    else:
        assert (home / "linked-under-home").resolve() in mcp_inventory.workspace_paths(FilePlan(), vault, unreachable=collected)


def _drift(vault: Path) -> Path:
    """Move a registered vault and leave a symlink at its old path, so its stored row is no longer canonical."""
    moved = vault.parent / f"{vault.name}-moved"
    vault.rename(moved)
    vault.symlink_to(moved)
    return moved.resolve()


def _integrate(vault: Path, home: Path) -> None:
    apply(owner._configure_plan(vault, home, vault, owner.McpScope.PROJECT,
                               (owner.McpClient.CODEX,), {"command": "/managed/python", "args": [], "env": {}}))


# Every stale shape: (shape, whether remove-stale may remove it). A row is never dropped while MCP
# integrations it owns survive; a duplicate row whose target another ID registers owns none.
STALE_SHAPES = [
    ("canonical-absent", True),
    ("canonical-not-a-brain-with-integrations", False),
    ("drifted-to-a-brain", True),
    ("drifted-to-a-brain-with-integrations", False),
    ("drifted-alias-pair", True),
    ("drifted-dangling-symlink", True),
    ("drifted-to-a-non-brain-with-integrations", False),
]


@pytest.mark.parametrize(("shape", "removable"), STALE_SHAPES)
def test_stale_guidance_names_remove_stale_only_where_it_succeeds(tmp_path, monkeypatch, shape, removable):
    """Parity: the guidance is executed, never only compared; a blocked row names no command."""
    import shlex
    import shutil
    import vault_registry

    home, vault, state, _destination = legacy_fixture(tmp_path, monkeypatch)
    state.unlink()
    if "with-integrations" in shape or shape == "drifted-alias-pair":
        _integrate(vault, home)
    moved = None
    if shape == "canonical-absent":
        shutil.rmtree(vault)
    elif shape == "canonical-not-a-brain-with-integrations":
        (vault / ".brain-core/VERSION").unlink()
    else:
        moved = _drift(vault)
        if shape == "drifted-alias-pair":
            vault_registry._save_registry_entries({**vault_registry.load_registry_entries(), "other": (
                vault_registry.RegistryEntry("other", vault_registry.TYPE_LOCAL, str(moved)))})
        elif shape == "drifted-dangling-symlink":
            shutil.rmtree(moved)
        elif shape == "drifted-to-a-non-brain-with-integrations":
            (moved / ".brain-core/VERSION").unlink()
    before = vault_registry.load_registry_entries()
    [row] = [item for item in vault_registry.list_entries() if item["alias"] == "brain"]
    commands = {"brain registry remove-stale": vault_registry.prune_action}

    assert row["stale"] is True
    assert (row["stale_guidance"] is not None) is removable, row
    if not removable:
        assert vault_registry.MANUAL_RECOVERY in row["stale_explanation"]
        with pytest.raises(vault_registry.RegistryConflictError, match="manual recovery"):
            vault_registry.prune_action()
        assert vault_registry.load_registry_entries() == before, "a refused removal removes nothing"
        return
    assert "brain" in commands[row["stale_guidance"]]().removed_brain_ids
    assert "brain" not in vault_registry.load_registry_entries()
    if shape == "drifted-alias-pair":
        assert vault_registry.resolve("other") == str(moved), "the alias target keeps its own row"
    if "brain register" in row["stale_explanation"]:
        assert shape == "drifted-to-a-brain"
        argv = shlex.split(row["stale_explanation"][row["stale_explanation"].index("brain register --request-json"):])
        request = json.loads(argv[3])
        vault_registry.register_action(request["vault_root"], request.get("brain_id"))
        assert vault_registry.resolve("brain") == str(moved), "the moved Brain is registered again under its ID"


def test_one_blocked_row_blocks_remove_stale_for_every_row(tmp_path, monkeypatch):
    """Remove-stale removes every stale row or none, so no row's guidance names it while one is blocked."""
    import shlex
    import vault_registry

    home, vault, state, _destination = legacy_fixture(tmp_path, monkeypatch)
    state.unlink()
    _integrate(vault, home)
    _drift(vault)
    vault_registry._save_registry_entries({
        **vault_registry.load_registry_entries(),
        "missing": vault_registry.RegistryEntry("missing", vault_registry.TYPE_LOCAL, str(tmp_path / "Missing Brain")),
    })

    rows = {item["alias"]: item for item in vault_registry.list_entries()}

    assert rows["brain"]["stale_guidance"] is None
    assert "while the stale row 'brain' remains" in rows["missing"]["stale_explanation"]
    assert vault_registry.MANUAL_RECOVERY not in rows["missing"]["stale_explanation"], "that sentence is about 'brain'"
    with pytest.raises(vault_registry.RegistryConflictError, match="manual recovery"):
        vault_registry.prune_action()
    assert set(vault_registry.load_registry_entries()) == {"brain", "missing"}
    # A canonical row removable on its own is named for brain unregister, which removes just that row.
    assert rows["missing"]["stale_guidance"] == vault_registry.unregister_guidance(tmp_path / "Missing Brain")
    request = json.loads(shlex.split(rows["missing"]["stale_guidance"])[3])
    assert vault_registry.unregister_action(request["vault_root"]).removed_brain_ids == ("missing",)
    assert set(vault_registry.load_registry_entries()) == {"brain"}


def test_one_drifted_row_never_blocks_removing_the_other_stale_rows(tmp_path, monkeypatch):
    import vault_registry

    _home, vault, state, _destination = legacy_fixture(tmp_path, monkeypatch)
    state.unlink()
    _drift(vault)
    missing = tmp_path / "Missing Brain"
    vault_registry._save_registry_entries({
        **vault_registry.load_registry_entries(),
        "missing": vault_registry.RegistryEntry("missing", vault_registry.TYPE_LOCAL, str(missing)),
    })

    assert vault_registry.prune_action().removed_brain_ids == ("brain", "missing")


def test_a_strict_inventory_refuses_a_drifted_row_with_its_recovery(tmp_path, monkeypatch):
    """The strict refusal names both paths and the recovery, not "reconnect" for an absent location."""
    import vault_registry

    _home, vault, state, _destination = legacy_fixture(tmp_path, monkeypatch)
    state.unlink()
    moved = _drift(vault)
    collected = []

    for options in ({}, {"allow_missing": True}):
        with pytest.raises(ValueError, match="no longer its canonical path") as refused:
            mcp_inventory.local_brains(FilePlan(), **options)
        assert str(vault) in str(refused.value) and str(moved) in str(refused.value)
        assert "brain registry remove-stale" in str(refused.value)
    assert mcp_inventory.local_brains(FilePlan(), unreachable=collected) == ()
    assert [item.path for item in collected] == [vault]
    message = mcp_inventory.unreachable_message(collected)
    assert "brain registry remove-stale" in message and "Reconnect" not in message, (
        "a drifted row carries its own recovery, never the generic reconnect-or-unregister remedy")
