"""BDD coverage for router compilation flows."""

import pytest
from pytest_bdd import given, scenarios, then, when, parsers

import compile_router


scenarios("features/router_compilation.feature")


@pytest.fixture
def router_compilation_vault(tmp_path):
    """Create a small vault fixture for router compilation scenarios."""
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("1.0.0\n")
    (bc / "session-core.md").write_text("# Session Core\n")

    config = tmp_path / "_Config"
    config.mkdir()
    (config / "router.md").write_text(
        "Prefer MCP tools.\n\nAlways:\n- Every artefact belongs in a typed folder.\n"
    )

    (tmp_path / "Wiki").mkdir()
    temporal = tmp_path / "_Temporal"
    temporal.mkdir()
    (temporal / "Logs").mkdir()

    tax_living = config / "Taxonomy" / "Living"
    tax_living.mkdir(parents=True)
    (tax_living / "wiki.md").write_text(
        "# Wiki\n\n"
        "## Naming\n\n`{Title}.md` in `Wiki/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: living/wiki\ntags:\n  - topic-tag\n---\n```\n"
    )

    tax_temporal = config / "Taxonomy" / "Temporal"
    tax_temporal.mkdir(parents=True)
    (tax_temporal / "logs.md").write_text(
        "# Logs\n\n"
        "## Naming\n\n`yyyymmdd-log.md` in `_Temporal/Logs/yyyy-mm/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: temporal/log\ntags:\n  - log\n---\n```\n"
    )

    return tmp_path


@given("a compilable router vault", target_fixture="router_vault")
def compilable_router_vault(router_compilation_vault):
    """Provide the vault root for router compilation scenarios."""
    return router_compilation_vault


@when("I compile the router", target_fixture="compiled_router")
def compile_router_step(router_vault):
    """Compile the router for the configured vault."""
    return compile_router.compile(str(router_vault))


@then(parsers.parse('the compiled router contains a configured artefact "{artefact_key}"'))
def assert_configured_artefact(compiled_router, artefact_key):
    """Assert the compiled router includes the expected configured artefact."""
    assert any(
        artefact["key"] == artefact_key and artefact.get("configured")
        for artefact in compiled_router["artefacts"]
    )


@then(parsers.parse('the compiled router always rules include "{expected_rule}"'))
def assert_always_rule(compiled_router, expected_rule):
    """Assert the compiled router includes the expected always rule."""
    assert expected_rule in compiled_router["always_rules"]
