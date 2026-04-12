"""BDD coverage for compliance checking flows."""

import json
import os

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

import check
import compile_router


scenarios("features/compliance.feature")


def _write_md(path, frontmatter_fields=None, body=""):
    """Write a markdown file with optional frontmatter."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if frontmatter_fields:
        lines.append("---")
        for key, value in frontmatter_fields.items():
            if isinstance(value, list):
                lines.append(f"{key}:")
                for item in value:
                    lines.append(f"  - {item}")
            else:
                lines.append(f"{key}: {value}")
        lines.append("---")
    lines.append(body)
    path.write_text("\n".join(lines) + "\n")


@pytest.fixture
def compliance_vault(tmp_path):
    """Create a minimal vault fixture for compliance BDD scenarios."""
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
    (temporal / "Logs" / "2026-04").mkdir(parents=True)

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

    _write_md(
        tmp_path / "Wiki" / "Reference.md",
        {"type": "living/wiki", "tags": ["topic"]},
        "# Reference",
    )
    _write_md(
        tmp_path / "_Temporal" / "Logs" / "2026-04" / "20260411-log.md",
        {"type": "temporal/log", "tags": ["log"]},
        "# Log",
    )

    return tmp_path


@given("a compliance vault with a compiled router", target_fixture="compliance_context")
def compiled_compliance_vault(compliance_vault):
    """Compile and persist the router for compliance scenarios."""
    router = compile_router.compile(str(compliance_vault))
    brain_local = compliance_vault / ".brain" / "local"
    brain_local.mkdir(parents=True, exist_ok=True)
    (brain_local / "compiled-router.json").write_text(json.dumps(router, indent=2) + "\n")
    return {"vault": compliance_vault, "router": router}


@given(parsers.parse('a stray temporal file "{rel_path}"'))
def stray_temporal_file(compliance_context, rel_path):
    """Create a temporal file directly under the type folder to trigger a warning."""
    _write_md(
        compliance_context["vault"] / rel_path,
        {"type": "temporal/log", "tags": ["log"]},
        "# Stray",
    )


@when("I run compliance checks", target_fixture="compliance_result")
def run_compliance_checks(compliance_context):
    """Run compliance checks using the compiled router on disk."""
    return check.run_checks(str(compliance_context["vault"]))


@then(parsers.parse('the compliance findings include check "{check_name}" for "{rel_path}"'))
def assert_compliance_finding(compliance_result, check_name, rel_path):
    """Assert the expected finding exists."""
    assert any(
        finding["check"] == check_name and finding.get("file") == rel_path
        for finding in compliance_result["findings"]
    )


@then(parsers.parse("the compliance summary has at least {minimum:d} warning"))
def assert_compliance_warnings(compliance_result, minimum):
    """Assert the compliance summary warning count meets the threshold."""
    assert compliance_result["summary"]["warnings"] >= minimum
