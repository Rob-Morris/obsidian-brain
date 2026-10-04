"""Brain-written Claude bootstrap lines: the closed history, migration and removal."""

import json
import sys
from pathlib import Path

import pytest

from _bootstrap import mcp_migration, mcp_registration as owner, mcp_transport
from _bootstrap.file_transaction import FilePlan, apply_file_changes
from _bootstrap.mcp_state import (
    BOOTSTRAP_LINE_HISTORY,
    BRAIN_BOOTSTRAP_LINES,
    CLAUDE_LOCAL_MD_FILE,
    CLAUDE_MD_BOOTSTRAP_PROJECT,
    CLAUDE_MD_BOOTSTRAP_VAULT,
    CLAUDE_MD_FILE,
    INIT_STATE_REL,
    build_mcp_config,
)

CURRENT = {"vault": CLAUDE_MD_BOOTSTRAP_VAULT, "project": CLAUDE_MD_BOOTSTRAP_PROJECT}
RETIRED = [release for release in BOOTSTRAP_LINE_HISTORY if release.last_version is not None]
BEFORE = "# My project\r\n\r\nKeep this prose.\n"
AFTER = "\nAnd this closing note, with no final newline"


def _version(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def text(path: Path) -> str:
    return path.read_bytes().decode()


def apply(plan):
    plan.validate()
    apply_file_changes(plan.changes())


def legacy_registration(tmp_path, monkeypatch, *, kind, scope=owner.McpScope.PROJECT):
    """Register Claude canonically, then rewrite the ledger as a pre-0.70 (version 1) record."""
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
    target = vault
    if kind == "project":
        target = (tmp_path / "project").resolve()
        (target / ".brain/local").mkdir(parents=True)
        (target / ".brain/local/workspace.yaml").write_text("brain: brain\nslug: project\nlinks:\n  workspace: project\n")
        workspace_registry.register_workspace(vault, "project", target)
    server = build_mcp_config(sys.executable, vault, workspace_dir=target)
    apply(owner._configure_plan(vault, home, target, scope, (owner.McpClient.CLAUDE,), server))
    state = vault / INIT_STATE_REL
    records = [{key: value for key, value in record.items() if key not in ("schema", "transport_enabled")}
               for record in json.loads(state.read_text())["records"]]
    bootstrap = target / (CLAUDE_LOCAL_MD_FILE if scope is owner.McpScope.LOCAL else CLAUDE_MD_FILE)
    return home, vault, target, state, records, bootstrap


def own_line(state: Path, records: list[dict], line: str) -> None:
    state.write_text(json.dumps({"version": 1, "records": [{**record, "bootstrap_line": line} for record in records]}))


def migrate(home: Path, tmp_path: Path) -> None:
    mcp_migration.apply_migration(mcp_migration.migration_plan(home, tmp_path / "bin/brain"), home)


def test_the_history_is_ordered_and_ends_with_the_current_line_for_each_target():
    for kind, current in CURRENT.items():
        rows = [release for release in BOOTSTRAP_LINE_HISTORY if release.target == kind]
        assert [release.line for release in rows if release.last_version is None] == [current]
        assert rows[-1].line == current
        for earlier, later in zip(rows, rows[1:]):
            assert _version(earlier.first_version) <= _version(earlier.last_version) < _version(later.first_version)
    assert BRAIN_BOOTSTRAP_LINES == {release.line for release in BOOTSTRAP_LINE_HISTORY}


@pytest.mark.parametrize("release", RETIRED, ids=lambda release: f"{release.target}-{release.first_version}")
def test_migration_replaces_each_retired_line_in_place_and_keeps_the_prose(tmp_path, monkeypatch, release):
    home, vault, target, state, records, bootstrap = legacy_registration(tmp_path, monkeypatch, kind=release.target)
    own_line(state, records, release.line)
    bootstrap.write_text(f"{BEFORE}{release.line}\r\n{AFTER}")

    migrate(home, tmp_path)

    assert text(bootstrap) == f"{BEFORE}{CURRENT[release.target]}\r\n{AFTER}"
    _, migrated = owner.read_records(FilePlan(), vault, home, owner.McpScope.PROJECT)
    assert [record["bootstrap_line"] for record in migrated] == [CURRENT[release.target]]
    assert [record["bootstrap_path"] for record in migrated] == [str(bootstrap)]
    assert not mcp_migration.migration_plan(home, tmp_path / "bin/brain").changes()


def test_migration_drops_the_retired_line_when_the_current_line_is_already_there(tmp_path, monkeypatch):
    """Upgrades from 0.55.0 and 0.64.0 rewrite vault-root files but not the record that owns the line."""
    home, vault, _target, state, records, bootstrap = legacy_registration(tmp_path, monkeypatch, kind="vault")
    retired = next(release.line for release in RETIRED if release.target == "vault")
    own_line(state, records, retired)
    bootstrap.write_text(f"{BEFORE}{CLAUDE_MD_BOOTSTRAP_VAULT}\n{retired}\n{AFTER}")

    migrate(home, tmp_path)

    assert text(bootstrap) == f"{BEFORE}{CLAUDE_MD_BOOTSTRAP_VAULT}\n{AFTER}"
    _, migrated = owner.read_records(FilePlan(), vault, home, owner.McpScope.PROJECT)
    assert [record["bootstrap_line"] for record in migrated] == [CLAUDE_MD_BOOTSTRAP_VAULT]


def test_migration_restores_the_current_line_like_repair_when_the_user_removed_it(tmp_path, monkeypatch):
    """Repair maintains the bootstrap, so migration does too; removing it means removing the registration."""
    home, vault, _target, state, records, bootstrap = legacy_registration(tmp_path, monkeypatch, kind="project")
    own_line(state, records, RETIRED[-1].line)
    bootstrap.write_text(BEFORE)

    migrate(home, tmp_path)

    assert text(bootstrap) == f"{BEFORE}\n{CLAUDE_MD_BOOTSTRAP_PROJECT}\n"
    _, migrated = owner.read_records(FilePlan(), vault, home, owner.McpScope.PROJECT)
    assert [record["bootstrap_line"] for record in migrated] == [CLAUDE_MD_BOOTSTRAP_PROJECT]


def test_migration_replaces_the_retired_line_in_the_local_bootstrap(tmp_path, monkeypatch):
    home, vault, _target, state, records, bootstrap = legacy_registration(
        tmp_path, monkeypatch, kind="vault", scope=owner.McpScope.LOCAL
    )
    retired = next(release.line for release in RETIRED if release.target == "vault" and "brain_session`" in release.line)
    own_line(state, records, retired)
    bootstrap.write_text(f"{BEFORE}{retired}\n")

    migrate(home, tmp_path)

    assert text(bootstrap) == f"{BEFORE}{CLAUDE_MD_BOOTSTRAP_VAULT}\n"
    _, migrated = owner.read_records(FilePlan(), vault, home, owner.McpScope.LOCAL)
    assert [record["bootstrap_line"] for record in migrated] == [CLAUDE_MD_BOOTSTRAP_VAULT]


@pytest.mark.parametrize("line", [
    "ALWAYS DO FIRST: Call MCP `brain_session`, else read `.brain-core/index.md` if it exists. Then check email.",
    "Always start by calling session_start.",
    ["ALWAYS DO FIRST: Call MCP `brain_session`."],
])
def test_migration_refuses_an_edited_or_unknown_line_before_any_write(tmp_path, monkeypatch, line):
    home, _vault, _target, state, records, bootstrap = legacy_registration(tmp_path, monkeypatch, kind="vault")
    own_line(state, records, line)
    content = f"{BEFORE}{line}\n" if isinstance(line, str) else BEFORE
    bootstrap.write_text(content)
    ledger = state.read_bytes()

    with pytest.raises(ValueError, match="Unrecognised bootstrap line ownership"):
        mcp_migration.migration_plan(home, tmp_path / "bin/brain")

    assert state.read_bytes() == ledger
    assert text(bootstrap) == content
    assert not mcp_migration.journal_path(home).exists()


@pytest.mark.parametrize("line", sorted(BRAIN_BOOTSTRAP_LINES))
def test_workspace_bootstrap_removal_removes_every_brain_line_and_keeps_the_prose(tmp_path, line):
    bootstrap = tmp_path / CLAUDE_MD_FILE
    bootstrap.write_bytes(f"Keep this.\r\n{line}\r\nAnd this.\r\n".encode())

    assert mcp_transport.cleanup_claude_bootstrap(tmp_path)

    assert text(bootstrap) == "Keep this.\r\nAnd this.\r\n"


def test_workspace_bootstrap_removal_leaves_an_edited_line(tmp_path):
    bootstrap = tmp_path / CLAUDE_MD_FILE
    content = f"{CLAUDE_MD_BOOTSTRAP_PROJECT} Also read NOTES.md.\n"
    bootstrap.write_text(content)

    assert not mcp_transport.cleanup_claude_bootstrap(tmp_path)

    assert text(bootstrap) == content


def test_an_upgraded_0_53_5_vault_migrates_then_repairs_to_one_current_line(tmp_path, monkeypatch):
    """The upgrade path: 0.55.0 and 0.64.0 already rewrote the vault file; migration and repair follow."""
    from _bootstrap import mcp_inventory
    from _bootstrap.mcp_state import CLAUDE_LOCAL_SETTINGS_FILE

    home, vault, _target, state, records, bootstrap = legacy_registration(tmp_path, monkeypatch, kind="vault")
    settings = vault / CLAUDE_LOCAL_SETTINGS_FILE
    current_hook = records[0]["hook_command"]
    old_hook = current_hook.replace("echo session.start called:", "echo 'brain_session called:'")
    assert old_hook != current_hook
    settings.write_text(settings.read_text().replace(json.dumps(current_hook)[1:-1], json.dumps(old_hook)[1:-1]))
    records = [{**record, "hook_command": old_hook} for record in records]
    own_line(state, records, "ALWAYS DO FIRST: Call MCP `brain_session`, else read `.brain-core/index.md` if it exists.")
    bootstrap.write_text(f"{BEFORE}{CLAUDE_MD_BOOTSTRAP_VAULT}\n{AFTER}")
    binary = tmp_path / "bin/brain"

    migrate(home, tmp_path)
    plan = FilePlan()
    mcp_inventory.plan_repair(plan, (vault,), home, binary)
    apply(plan)

    assert text(bootstrap) == f"{BEFORE}{CLAUDE_MD_BOOTSTRAP_VAULT}\n{AFTER}"
    hooks = [hook["command"] for entry in json.loads(settings.read_text())["hooks"]["SessionStart"] for hook in entry["hooks"]]
    assert hooks == [current_hook]
    _, migrated = owner.read_records(FilePlan(), vault, home, owner.McpScope.PROJECT)
    assert [(record["bootstrap_line"], record["hook_command"]) for record in migrated] == [(CLAUDE_MD_BOOTSTRAP_VAULT, current_hook)]
    again = FilePlan()
    mcp_inventory.plan_repair(again, (vault,), home, binary)
    assert not again.changes()


def test_migration_replaces_a_different_retired_line_from_the_one_recorded(tmp_path, monkeypatch):
    home, vault, target, state, records, bootstrap = legacy_registration(tmp_path, monkeypatch, kind="project")
    claimed, held = (release.line for release in RETIRED if release.target == "project" and release.first_version in ("0.48.10", "0.55.0"))
    own_line(state, records, claimed)
    bootstrap.write_text(f"{BEFORE}{held}\n{AFTER}")

    migrate(home, tmp_path)

    assert text(bootstrap) == f"{BEFORE}{CLAUDE_MD_BOOTSTRAP_PROJECT}\n{AFTER}"
    _, migrated = owner.read_records(FilePlan(), vault, home, owner.McpScope.PROJECT)
    assert [record["bootstrap_line"] for record in migrated] == [CLAUDE_MD_BOOTSTRAP_PROJECT]


@pytest.mark.parametrize(("content", "expected"), [
    ("a\nR1\nb\r\nC\nR2\n", "a\nCURRENT\nb\r\n"),
    ("a\n  R1  \r\nb", "a\n  CURRENT\r\nb"),
    ("a\nC edited\n", "a\nC edited\n\nCURRENT\n"),
    ("a\n- C\n", "a\n- C\n\nCURRENT\n"),
    ("a", "a\n\nCURRENT\n"),
    ("", "CURRENT\n"),
])
def test_converging_replaces_the_first_brain_line_drops_the_rest_and_keeps_every_other_byte(monkeypatch, content, expected):
    from _bootstrap import mcp_state

    monkeypatch.setattr(mcp_state, "BRAIN_BOOTSTRAP_LINES", frozenset({"R1", "R2", "C"}))
    assert mcp_state.converge_bootstrap_text(content, "CURRENT") == expected


@pytest.mark.parametrize(("content", "expected"), [
    ("keep\r\n\r\nR1\r\nmore\r\n  C\n", "keep\r\n\r\nmore\r\n"),
    ("keep\r\n\r\nR1\r\nC\n\n", "keep\r\n"),
    ("R1\nC", ""),
    ("keep\nC edited\n", "keep\nC edited\n"),
])
def test_removing_drops_every_brain_line_and_keeps_every_other_byte(monkeypatch, content, expected):
    from _bootstrap import mcp_state

    monkeypatch.setattr(mcp_state, "BRAIN_BOOTSTRAP_LINES", frozenset({"R1", "C"}))
    assert mcp_state.remove_bootstrap_text(content) == expected


@pytest.mark.parametrize("release", RETIRED, ids=lambda release: f"{release.target}-{release.first_version}")
def test_converging_is_idempotent_for_every_retired_line(release):
    from _bootstrap.mcp_state import converge_bootstrap_text

    once = converge_bootstrap_text(f"{BEFORE}{release.line}\r\n{AFTER}", CURRENT[release.target])
    assert once == f"{BEFORE}{CURRENT[release.target]}\r\n{AFTER}"
    assert converge_bootstrap_text(once, CURRENT[release.target]) == once


@pytest.mark.parametrize("name", [CLAUDE_MD_FILE, "AGENTS.md"])
def test_workspace_bootstrap_replaces_a_retired_line_in_place_and_is_idempotent(tmp_path, name):
    import configure

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    bootstrap = workspace / name
    retired = "ALWAYS DO FIRST: Call MCP `session.start`, else read `.brain-core/index.md` if it exists."
    bootstrap.write_bytes(f"{BEFORE}{retired}\r\n{AFTER}".encode())
    surface = "claude" if name == CLAUDE_MD_FILE else "agents"

    first = configure.configure_workspace_bootstrap_action(tmp_path / "Brain", workspace_dir=workspace, surface=surface)
    second = configure.configure_workspace_bootstrap_action(tmp_path / "Brain", workspace_dir=workspace, surface=surface)

    assert text(bootstrap) == f"{BEFORE}{CLAUDE_MD_BOOTSTRAP_PROJECT}\r\n{AFTER}"
    assert [step["status"] for step in first["steps"]] == ["changed"]
    assert [step["status"] for step in second["steps"]] == ["noop"]


def test_workspace_agents_bootstrap_is_the_workspace_line(tmp_path):
    import configure

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    configure.configure_workspace_bootstrap_action(tmp_path / "Brain", workspace_dir=workspace, surface="agents")

    assert text(workspace / "AGENTS.md") == f"{CLAUDE_MD_BOOTSTRAP_PROJECT}\n"


def test_mcp_configuration_converges_a_retired_workspace_line_in_place(tmp_path, monkeypatch):
    home, vault, target, _state, _records, bootstrap = legacy_registration(tmp_path, monkeypatch, kind="project")
    retired = next(release.line for release in RETIRED if release.target == "project")
    bootstrap.write_text(f"{BEFORE}{retired}\n{AFTER}")
    server = build_mcp_config(sys.executable, vault, workspace_dir=target)

    apply(owner._configure_plan(vault, home, target, owner.McpScope.PROJECT, (owner.McpClient.CLAUDE,), server))

    assert text(bootstrap) == f"{BEFORE}{CLAUDE_MD_BOOTSTRAP_PROJECT}\n{AFTER}"
    assert not owner._configure_plan(vault, home, target, owner.McpScope.PROJECT, (owner.McpClient.CLAUDE,), server).changes()


def test_mcp_removal_removes_every_brain_line_and_keeps_crlf_prose(tmp_path, monkeypatch):
    home, vault, target, _state, _records, bootstrap = legacy_registration(tmp_path, monkeypatch, kind="project")
    retired = next(release.line for release in RETIRED if release.target == "project" and release.first_version == "0.55.0")
    bootstrap.write_bytes(f"# Project\r\nKeep this.\r\n\r\n{retired}\r\n{CLAUDE_MD_BOOTSTRAP_PROJECT}\n".encode())

    apply(owner._remove_plan(vault, home, target, owner.McpScope.PROJECT, (owner.McpClient.CLAUDE,)))

    assert text(bootstrap) == "# Project\r\nKeep this.\r\n"
    assert owner.read_records(FilePlan(), vault, home, owner.McpScope.PROJECT)[1] == []


def test_a_canonical_record_holding_a_retired_line_reads_and_repair_converges_it(tmp_path, monkeypatch):
    from _bootstrap import mcp_inventory

    home, vault, _target, state, _records, bootstrap = legacy_registration(tmp_path, monkeypatch, kind="vault")
    retired = next(release.line for release in RETIRED if release.target == "vault" and release.first_version == "0.55.0")
    ledger = json.loads(state.read_text())
    ledger["records"] = [{**record, "bootstrap_line": retired} for record in ledger["records"]]
    state.write_text(json.dumps(ledger))
    bootstrap.write_bytes(f"{BEFORE}{retired}\r\n{AFTER}".encode())
    binary = tmp_path / "bin/brain"

    _, records = owner.read_records(FilePlan(), vault, home, owner.McpScope.PROJECT)
    assert [record["bootstrap_line"] for record in records] == [retired]
    migration = mcp_migration.migration_plan(home, binary)
    assert bootstrap not in {change.path for change in migration.changes()}, "a canonical ledger is left to repair"
    states = [item["state"] for item in mcp_inventory.inspect_registrations(home, (vault,), binary)["registrations"]]
    assert "stale" in states

    plan = FilePlan()
    mcp_inventory.plan_repair(plan, (vault,), home, binary)
    apply(plan)

    assert text(bootstrap) == f"{BEFORE}{CLAUDE_MD_BOOTSTRAP_VAULT}\r\n{AFTER}"
    _, repaired = owner.read_records(FilePlan(), vault, home, owner.McpScope.PROJECT)
    assert [record["bootstrap_line"] for record in repaired] == [CLAUDE_MD_BOOTSTRAP_VAULT]
    again = FilePlan()
    mcp_inventory.plan_repair(again, (vault,), home, binary)
    assert not again.changes()
