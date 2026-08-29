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


@given("a discovery-shaped type that preserves lifecycle status")
def preserved_discovery_type(router_vault):
    """Add a shapeable living type whose status describes enduring state."""
    (router_vault / "People").mkdir()
    taxonomy = router_vault / "_Config" / "Taxonomy" / "Living" / "people.md"
    taxonomy.write_text(
        "# People\n\n"
        "## Naming\n\n`{Title}.md` in `People/`.\n\n"
        "## Frontmatter\n\n"
        "```yaml\n---\ntype: living/person\nstatus: active\n---\n```\n\n"
        "## Lifecycle\n\n"
        "| Status | Meaning |\n|---|---|\n"
        "| `active` | Current record. |\n"
        "| `shaping` | Explicit sustained shaping. |\n"
        "| `deprecated` | Terminal. |\n\n"
        "## Shaping\n\n"
        "**Flavour:** Discovery\n"
        "**Bar:** The current intended scope is faithfully captured.\n"
        "**Status behaviour:** `preserve`\n"
    )


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


@then(parsers.parse('the compiled artefact "{artefact_key}" preserves shaping lifecycle status'))
def assert_preserved_shaping_status(compiled_router, artefact_key):
    """Assert discovery shaping compiles without a completion transition."""
    artefact = next(
        item for item in compiled_router["artefacts"] if item["key"] == artefact_key
    )
    assert artefact["shaping"] == {
        "flavour": "discovery",
        "bar": "The current intended scope is faithfully captured.",
        "status_behaviour": "preserve",
    }
