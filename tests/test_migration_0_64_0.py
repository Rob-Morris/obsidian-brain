"""Bootstrap-only migration keeps custom instructions and permission IDs intact."""

from pathlib import Path
import pytest
import migrate_to_0_64_0 as migration
from _bootstrap.mcp_state import migrate_bootstrap_text


@pytest.mark.parametrize("bootstrap_name", ["AGENTS.md", ".claude/CLAUDE.local.md"])
def test_migration_is_exact_idempotent_and_declares_rollback_surface(
    tmp_path, bootstrap_name
):
    bootstrap = tmp_path / bootstrap_name
    bootstrap.parent.mkdir(parents=True, exist_ok=True)
    bootstrap.write_text(
        "Custom session.start reference.\nALWAYS DO FIRST: Call MCP `session.start`.\n"
    )
    config = tmp_path / ".brain/config.yaml"
    config.parent.mkdir()
    config.write_text("allow: [artefact.delete, session.start]\n")
    assert migration.prospective_effects(tmp_path) == [str(bootstrap.resolve())]
    assert migration.migrate(tmp_path)["bootstraps"] == [bootstrap_name]
    assert (
        bootstrap.read_text()
        == "Custom session.start reference.\nALWAYS DO FIRST: Call MCP `session_start`.\n"
    )
    assert "session.start" in config.read_text()
    assert migration.migrate(tmp_path)["status"] == "skipped"


def test_migration_refuses_external_bootstrap_before_writes(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    outside = tmp_path / "external.md"
    outside.write_text("ALWAYS DO FIRST: Call MCP `session.start`.\n")
    (root / "AGENTS.md").symlink_to(outside)
    with pytest.raises(ValueError, match="outside"):
        migration.migrate(root)
    assert "session.start" in outside.read_text()


def test_project_bootstrap_replacement_preserves_line_endings():
    old = "ALWAYS DO FIRST: Call MCP `session.start`; if MCP is unavailable, run `brain session start --json` from this workspace.\r\n"
    assert migrate_bootstrap_text(old) == old.replace(
        "`session.start`", "`session_start`"
    )
