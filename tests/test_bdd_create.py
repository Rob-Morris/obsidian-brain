"""BDD coverage for artefact creation flows."""

import os
import re

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

import compile_router
import create


scenarios("features/artefact_creation.feature")


@pytest.fixture
def artefact_creation_vault(tmp_path):
    """Create a small vault fixture for BDD artefact-creation scenarios."""
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("1.0.0\n")

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
        "## Frontmatter\n\n```yaml\n---\ntype: living/wiki\ntags:\n  - topic-tag\n---\n```\n\n"
        "## Template\n\n[[_Config/Templates/Living/Wiki]]\n"
    )

    tax_temporal = config / "Taxonomy" / "Temporal"
    tax_temporal.mkdir(parents=True)
    (tax_temporal / "logs.md").write_text(
        "# Logs\n\n"
        "## Naming\n\n`log~{Title}.md` in `_Temporal/Logs/yyyy-mm/`.\n\n"
        "## Frontmatter\n\n```yaml\n---\ntype: temporal/log\ntags:\n  - session\n---\n```\n\n"
        "## Template\n\n[[_Config/Templates/Temporal/Logs]]\n"
    )

    templates_living = config / "Templates" / "Living"
    templates_living.mkdir(parents=True)
    (templates_living / "Wiki.md").write_text(
        "---\ntype: living/wiki\ntags: []\n---\n\n# {{title}}\n\n"
    )

    templates_temporal = config / "Templates" / "Temporal"
    templates_temporal.mkdir(parents=True)
    (templates_temporal / "Logs.md").write_text(
        "---\ntype: temporal/logs\ntags:\n  - session\n---\n\n# Log\n\n"
    )

    return tmp_path


@given("a configured artefact creation vault", target_fixture="artefact_context")
def configured_artefact_creation_vault(artefact_creation_vault):
    """Compile the router for the artefact creation scenarios."""
    return {
        "vault": artefact_creation_vault,
        "router": compile_router.compile(str(artefact_creation_vault)),
    }


@when(
    parsers.parse('I create a "{type_key}" artefact titled "{title}"'),
    target_fixture="artefact_result",
)
def create_artefact_step(artefact_context, type_key, title):
    """Create an artefact in the configured vault."""
    return create.create_artefact(
        str(artefact_context["vault"]),
        artefact_context["router"],
        type_key,
        title,
    )


@then(parsers.parse('the created artefact path is "{expected_path}"'))
def assert_created_path(artefact_result, expected_path):
    """Assert the create result path exactly matches the expected path."""
    assert artefact_result["path"] == expected_path


@then(parsers.parse('the created artefact path matches "{pattern}"'))
def assert_created_path_matches(artefact_result, pattern):
    """Assert the created artefact path matches a regular expression."""
    assert re.match(pattern, artefact_result["path"])


@then("the created artefact file exists")
def assert_created_file_exists(artefact_context, artefact_result):
    """Assert the created file exists on disk."""
    abs_path = os.path.join(str(artefact_context["vault"]), artefact_result["path"])
    assert os.path.isfile(abs_path)


@then(parsers.parse('the created artefact result type is "{expected_type}"'))
def assert_created_result_type(artefact_result, expected_type):
    """Assert the created artefact reports the expected type."""
    assert artefact_result["type"] == expected_type
