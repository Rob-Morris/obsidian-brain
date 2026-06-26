"""Shared fixtures for the MCP server test suite."""

import logging
import threading
import types
from unittest.mock import patch

import pytest

from brain_mcp import server
import obsidian_cli
import retrieval_embeddings


@pytest.fixture
def vault(tmp_path):
    """Create a minimal vault fixture with types, taxonomy, and content."""
    # .brain-core/VERSION
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.7.0\n")
    (bc / "session-core.md").write_text(
        "# Session Core\n\n"
        "## Principles\n\n"
        "Keep instruction files lean.\n\n"
        "## Core Docs\n\n"
        "- [Extend the vault: add artefact types, memories, and principles](standards/extending/README.md)\n"
        "- [Browse the artefact library: type definitions and install guidance](artefact-library/README.md)\n\n"
        "## Standards\n\n"
        "- [Track provenance and lineage between artefacts](standards/provenance.md)\n"
        "- [Run the artefact shaping process](standards/shaping.md)\n\n"
        "Always:\n"
        "- Prefer `brain_list` for exhaustive enumeration.\n"
    )

    # _Config/router.md
    config = tmp_path / "_Config"
    config.mkdir()
    (config / "router.md").write_text(
        "Prefer MCP tools.\n\n"
        "Always:\n"
        "- Every artefact belongs in a typed folder.\n"
        "- Keep instruction files lean.\n\n"
        "Conditional:\n"
        "- After meaningful work → [[_Config/Taxonomy/Temporal/logs]]\n"
    )

    # Living type: Wiki
    wiki_dir = tmp_path / "Wiki"
    wiki_dir.mkdir()
    (wiki_dir / "brain-overview-abc123.md").write_text(
        "---\ntype: living/wiki\ntags: [brain-core, overview]\nstatus: active\n---\n\n"
        "# Brain Overview\n\n"
        "The Brain is a personal knowledge management system.\n"
    )
    (wiki_dir / "python-guide-def456.md").write_text(
        "---\ntype: living/wiki\ntags: [python, guide]\nstatus: draft\n---\n\n"
        "# Python Guide\n\n"
        "Python is a versatile programming language used for scripting.\n"
    )

    # Temporal type: Logs
    temporal = tmp_path / "_Temporal"
    temporal.mkdir()
    logs_dir = temporal / "Logs"
    logs_dir.mkdir()
    month_dir = logs_dir / "2026-03"
    month_dir.mkdir()
    (month_dir / "log-test-ghi789.md").write_text(
        "---\ntype: temporal/logs\ntags: [session]\n---\n\n"
        "# Test Log\n\n"
        "Tested the MCP server implementation.\n"
    )

    # Taxonomy
    tax_living = config / "Taxonomy" / "Living"
    tax_living.mkdir(parents=True)
    (tax_living / "wiki.md").write_text(
        "# Wiki\n\n"
        "## Naming\n\n`{Title}.md` in `Wiki/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/wiki\ntags:\n  - topic-tag\n---\n```\n\n"
        "## Template\n\n[[_Config/Templates/Living/Wiki]]\n"
    )

    tax_temporal = config / "Taxonomy" / "Temporal"
    tax_temporal.mkdir(parents=True)
    (tax_temporal / "logs.md").write_text(
        "# Logs\n\n"
        "## Naming\n\n`log-{Title}.md` in `_Temporal/Logs/yyyy-mm/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: temporal/logs\ntags:\n  - session\n---\n```\n\n"
        "## Trigger\n\nAfter meaningful work, write a log entry.\n"
    )

    # Skills
    skills_dir = config / "Skills" / "Vault Maintenance"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text("# Vault Maintenance\n\nKeep the vault tidy.\n")

    # Styles
    styles_dir = config / "Styles"
    styles_dir.mkdir(parents=True)
    (styles_dir / "concise.md").write_text("# Concise\n\nBe brief and direct.\n")

    # Living type: Ideas
    ideas_dir = tmp_path / "Ideas"
    ideas_dir.mkdir()

    # Taxonomy: Ideas
    (tax_living / "ideas.md").write_text(
        "# Ideas\n\n"
        "## Lifecycle\n\n"
        "| `shaping` | Active exploration |\n"
        "| `adopted` | Terminal |\n\n"
        "## Archiving\n\n"
        "Ideas with `status: adopted` can be archived.\n\n"
        "## Naming\n\n`{Title}.md` in `Ideas/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/ideas\ntags:\n  - idea-tag\nstatus: shaping\n---\n```\n\n"
        "## Template\n\n[[_Config/Templates/Living/Ideas]]\n"
    )

    # Templates
    templates_dir = config / "Templates" / "Living"
    templates_dir.mkdir(parents=True)
    (templates_dir / "Wiki.md").write_text(
        "---\ntype: living/wiki\ntags: []\n---\n\n# {{title}}\n\n"
    )
    (templates_dir / "Ideas.md").write_text(
        "---\ntype: living/ideas\ntags: []\nstatus: shaping\n---\n\n# {{title}}\n\nWhat if...\n"
    )

    # Core skills
    core_skills_dir = bc / "skills" / "test-skill"
    core_skills_dir.mkdir(parents=True)
    (core_skills_dir / "SKILL.md").write_text(
        "---\nname: test-skill\n---\n\n"
        "# Test Skill (Core)\n\nA test core skill.\n"
    )

    # Plugins
    plugins_dir = tmp_path / "_Plugins" / "Undertask"
    plugins_dir.mkdir(parents=True)
    (plugins_dir / "SKILL.md").write_text("# Undertask\n\nTask management plugin.\n")

    return tmp_path


@pytest.fixture
def initialized(vault):
    """Return vault root after initializing the server against the vault fixture."""
    server.startup(vault_root=str(vault))
    assert server._wait_for_warmup(timeout=5.0), "warmup did not complete in fixture setup"
    # Reset staleness-check TTLs so tests can trigger checks immediately
    server._router_checked_at = 0.0
    server._index_checked_at = 0.0
    return vault


@pytest.fixture(autouse=True)
def _block_real_obsidian():
    """Prevent tests from connecting to a real Obsidian IPC socket.

    Tests that want CLI available mock the public API (check_available, search,
    move) directly — they never need the real socket.
    """
    with patch.object(obsidian_cli, "_socket_exists", return_value=False):
        yield


@pytest.fixture
def cli_available():
    """Temporarily enable CLI availability for tests that need the CLI path."""
    server._cli_available = True
    server._vault_name = "test"
    yield
    server._cli_available = False
    server._vault_name = None


@pytest.fixture
def gated_router_warmup(monkeypatch):
    """Patch router warmup to block until released; yield the gate.

    The fixture wires the patch but does not call ``server.startup`` —
    callers do, so they can time the call or interleave assertions.
    The gate exposes two ``threading.Event`` attributes: ``entered``
    (set when warmup begins router loading) and ``release`` (callers
    set this to let warmup proceed). Teardown auto-releases and joins
    the warmup thread, idempotent if the test already released.
    """
    release = threading.Event()
    entered = threading.Event()
    real = server._load_router_for_warmup

    def slow_load(vault_root, generation):
        entered.set()
        release.wait(timeout=2.0)
        return real(vault_root, generation)

    monkeypatch.setattr(server, "_load_router_for_warmup", slow_load)
    yield types.SimpleNamespace(release=release, entered=entered)
    release.set()
    server._wait_for_warmup(timeout=5.0)


@pytest.fixture
def gated_semantic_warmup(monkeypatch):
    """Patch semantic warmup disk load to block until released."""
    release = threading.Event()
    entered = threading.Event()
    real = retrieval_embeddings.load_embeddings_state

    def slow_load(vault_root):
        entered.set()
        assert release.wait(timeout=2.0), "semantic warmup gate did not release"
        return real(vault_root)

    monkeypatch.setattr(retrieval_embeddings, "load_embeddings_state", slow_load)
    yield types.SimpleNamespace(release=release, entered=entered)
    release.set()
    server._wait_for_semantic_warmup(timeout=5.0)


@pytest.fixture(autouse=True)
def _clean_logger():
    """Keep test logging isolated from third-party root logger setup."""
    root = logging.getLogger()
    root_handlers = list(root.handlers)
    root_level = root.level
    root.handlers.clear()

    logger = logging.getLogger("brain-core")
    old_propagate = logger.propagate
    logger.propagate = False
    yield
    logger.handlers.clear()
    logger.setLevel(logging.WARNING)  # reset to default
    logger.propagate = old_propagate
    root.handlers[:] = root_handlers
    root.setLevel(root_level)
