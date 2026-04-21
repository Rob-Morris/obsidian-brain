"""Tests for migrations/migrate_to_0_25_0.py — canonical bootstrap text."""

from conftest import filesystem_is_case_sensitive
from migrate_to_0_25_0 import NEW_BOOTSTRAP, migrate


def make_vault(tmp_path):
    """Create a minimal vault structure."""
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.24.12\n")
    (bc / "session-core.md").write_text("# Session Core\n")
    config = tmp_path / "_Config"
    config.mkdir()
    (config / "router.md").write_text("Conditional:\n- After work -> [[_Config/Taxonomy/Temporal/logs]]\n")
    return tmp_path


def test_updates_current_pre_unified_bootstrap(tmp_path):
    vault = make_vault(tmp_path)
    agents = vault / "AGENTS.md"
    agents.write_text("ALWAYS DO FIRST: Call brain_session. Read [[.brain-core/index]]\n")

    result = migrate(str(vault))

    assert result["status"] == "ok"
    assert agents.read_text() == NEW_BOOTSTRAP + "\n"


def test_updates_legacy_router_bootstrap_variant(tmp_path):
    vault = make_vault(tmp_path)
    agents = vault / "AGENTS.md"
    agents.write_text('If brain MCP tools are available, call brain_read(resource="router") at session start.\n')

    result = migrate(str(vault))

    assert result["status"] == "ok"
    assert NEW_BOOTSTRAP in agents.read_text()


def test_updates_legacy_agents_md_only_vault(tmp_path):
    vault = make_vault(tmp_path)
    legacy_agents = vault / "Agents.md"
    legacy_agents.write_text("ALWAYS DO FIRST: Call brain_session. Read [[.brain-core/index]]\n")

    result = migrate(str(vault))

    assert result["status"] == "ok"
    assert legacy_agents.read_text() == NEW_BOOTSTRAP + "\n"
    expected_name = "Agents.md" if filesystem_is_case_sensitive(tmp_path) else "AGENTS.md"
    assert f"updated bootstrap in {expected_name}" in result["actions"]


def test_does_not_rewrite_local_override_file(tmp_path):
    vault = make_vault(tmp_path)
    local_override = vault / "agents.local.md"
    local_override.write_text("ALWAYS DO FIRST: Call brain_session. Read [[.brain-core/index]]\n")

    result = migrate(str(vault))

    assert result["status"] == "skipped"
    assert local_override.read_text() == "ALWAYS DO FIRST: Call brain_session. Read [[.brain-core/index]]\n"


def test_canonicalises_manual_form_without_period(tmp_path):
    vault = make_vault(tmp_path)
    agents = vault / "AGENTS.md"
    agents.write_text("ALWAYS DO FIRST: Call MCP `brain_session`, else read `.brain-core/index.md` if it exists\n")

    result = migrate(str(vault))

    assert result["status"] == "ok"
    assert agents.read_text() == NEW_BOOTSTRAP + "\n"


def test_removes_stale_router_directive(tmp_path):
    vault = make_vault(tmp_path)
    router = vault / "_Config" / "router.md"
    router.write_text(
        "Always:\n"
        "- Prefer MCP tools.\n\n"
        "Always read [[.brain-core/index]].\n"
    )

    result = migrate(str(vault))

    assert result["status"] == "ok"
    assert "Always read [[.brain-core/index]]." not in router.read_text()


def test_skips_when_already_canonical(tmp_path):
    vault = make_vault(tmp_path)
    agents = vault / "AGENTS.md"
    agents.write_text(NEW_BOOTSTRAP + "\n")

    result = migrate(str(vault))

    assert result["status"] == "skipped"


def test_symlinked_claude_and_agents_only_updated_once(tmp_path):
    vault = make_vault(tmp_path)
    agents = vault / "AGENTS.md"
    agents.write_text("ALWAYS DO FIRST: Call brain_session. Read [[.brain-core/index]]\n")
    claude = vault / "CLAUDE.md"
    claude.symlink_to("AGENTS.md")

    result = migrate(str(vault))

    assert result["status"] == "ok"
    assert agents.read_text() == NEW_BOOTSTRAP + "\n"
    assert claude.resolve() == agents
