"""Tool registration contract for brain_mcp.

The living contract is documented in `docs/functional/mcp-tools.md` under
`Tool Metadata Contract`. Architectural rationale lives in
`docs/architecture/decisions/dd-044-mcp-tool-metadata-contract.md`.

Two checks per registered tool:
  1. The docstring must be a tool-level summary only — no `Args:`, `Parameters:`,
     `Returns:` sections, and no per-parameter dash bullets.
  2. Every parameter in the generated `inputSchema` must have a non-empty
     `description` field (load-bearing prose lives in the schema, not the
     docstring, so it survives client-side description truncation).
"""

import asyncio
import re

import pytest
from jsonschema import Draft202012Validator

from brain_mcp import server
from _common import (
    SELECTOR_OCCURRENCE_DESCRIPTION,
    SELECTOR_WITHIN_DESCRIPTION,
    SELECTOR_WITHIN_OCCURRENCE_DESCRIPTION,
    SELECTOR_WITHIN_TARGET_DESCRIPTION,
)
from _resource_contract import RESOURCE_KINDS


_TOOL_NAMES = (
    "brain_action", "brain_create", "brain_edit", "brain_list", "brain_move",
    "brain_process", "brain_read", "brain_search", "brain_session",
)

_DOCSTRING_BANNED_HEADINGS = re.compile(
    r"^\s*(Args|Arguments|Parameters|Params|Returns|Return)\s*:\s*$",
    re.MULTILINE,
)
# A line like "  scope     — ..." or "  scope: optional ..." indicates per-parameter
# prose has bled into the docstring. The em-dash + indent pattern matches the old
# Parameters block style this contract is replacing.
_DOCSTRING_PARAM_BULLET = re.compile(r"^\s+\w+\s+[—-]\s+", re.MULTILINE)

_FIELD_DESCRIPTION_BUDGETS = {
    ("brain_read", "resource"): 90,
    ("brain_read", "name"): 360,
    ("brain_create", "fix_links"): 70,
    ("brain_edit", "fix_links"): 70,
    ("brain_edit", "scope"): 520,
}

_TOOL_DESCRIPTION_TOTAL_BUDGETS = {
    "brain_read": 500,
    "brain_search": 520,
    "brain_list": 950,
    "brain_create": 1500,
    "brain_edit": 2200,
}


@pytest.fixture(scope="module")
def registered_tools():
    return asyncio.run(server.mcp.list_tools())


def test_tools_are_registered(registered_tools):
    names = sorted(t.name for t in registered_tools)
    expected = sorted(_TOOL_NAMES)
    assert names == expected, (
        f"Registered MCP tool set has drifted from the contract. "
        f"Got {names}, expected {expected}. "
        f"If a new tool was added, add it here and ensure it satisfies the contract."
    )


@pytest.mark.parametrize("tool_name", _TOOL_NAMES)
def test_docstring_has_no_parameter_sections(registered_tools, tool_name):
    tool = next(t for t in registered_tools if t.name == tool_name)
    desc = tool.description or ""

    banned = _DOCSTRING_BANNED_HEADINGS.findall(desc)
    assert not banned, (
        f"{tool_name} docstring contains banned section heading(s): {banned}. "
        f"Per the contract, parameter prose lives in `Annotated[T, Field(description=...)]`, "
        f"not in the tool-level docstring."
    )

    bullets = _DOCSTRING_PARAM_BULLET.findall(desc)
    assert not bullets, (
        f"{tool_name} docstring contains {len(bullets)} per-parameter bullet line(s) "
        f"(pattern: 'name — ...'). Move the prose into the parameter's "
        f"`Field(description=...)` annotation."
    )


@pytest.mark.parametrize("tool_name", _TOOL_NAMES)
def test_every_parameter_has_schema_description(registered_tools, tool_name):
    tool = next(t for t in registered_tools if t.name == tool_name)
    props = list(_iter_exposed_properties(tool.inputSchema))
    assert props, f"{tool_name} has no exposed parameters in inputSchema"

    missing = [
        param for param, meta in props
        if not (meta.get("description") or "").strip()
    ]
    assert not missing, (
        f"{tool_name} parameters missing schema-resident description: {missing}. "
        f"Wrap the parameter as `Annotated[T, Field(description=\"...\")]`."
    )


def test_tool_docstrings_are_short(registered_tools):
    """Docstrings should be tool-level summaries (3-4 sentences). Hard cap at 600
    chars to catch regressions toward parameter manuals; tighten further if the
    pattern slides back."""
    too_long = []
    for tool in registered_tools:
        desc = (tool.description or "").strip()
        if len(desc) > 600:
            too_long.append((tool.name, len(desc)))
    assert not too_long, (
        "Tool descriptions exceed the 600-char tool-level summary budget: "
        f"{too_long}. Move per-parameter prose into Field(description=...)."
    )


@pytest.mark.parametrize(
    ("tool_name", "field_name", "budget"),
    [
        (tool_name, field_name, budget)
        for (tool_name, field_name), budget in _FIELD_DESCRIPTION_BUDGETS.items()
    ],
)
def test_heaviest_field_descriptions_stay_within_budget(
    registered_tools,
    tool_name,
    field_name,
    budget,
):
    tool = next(t for t in registered_tools if t.name == tool_name)
    description = tool.inputSchema["properties"][field_name]["description"]

    assert len(description) <= budget, (
        f"{tool_name}.{field_name} description is {len(description)} chars; "
        f"budget is {budget}. Keep the schema semantically legible, but move "
        f"excess examples or long-form behaviour into docs/functional/mcp-tools.md."
    )


@pytest.mark.parametrize(("tool_name", "budget"), _TOOL_DESCRIPTION_TOTAL_BUDGETS.items())
def test_high_traffic_tools_keep_total_description_weight_in_check(
    registered_tools,
    tool_name,
    budget,
):
    tool = next(t for t in registered_tools if t.name == tool_name)
    total = sum(
        len((meta.get("description") or ""))
        for _, meta in _iter_exposed_properties(tool.inputSchema)
    )

    assert total <= budget, (
        f"{tool_name} exposes {total} schema-description chars; budget is {budget}. "
        f"Prefer keeping load-bearing semantics in schema and moving extra narrative "
        f"detail into docs/functional/mcp-tools.md."
    )


def _resolve_local_ref(schema, ref):
    assert ref.startswith("#/"), f"Only local refs are supported in this test, got {ref!r}"
    node = schema
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def _iter_exposed_properties(schema):
    props = schema.get("properties")
    if props:
        yield from props.items()
        return

    for variant in [*schema.get("oneOf", []), *schema.get("anyOf", [])]:
        node = _resolve_local_ref(schema, variant["$ref"]) if "$ref" in variant else variant
        yield from node.get("properties", {}).items()


def test_brain_edit_selector_schema_is_structured(registered_tools):
    tool = next(t for t in registered_tools if t.name == "brain_edit")
    schema = tool.inputSchema
    selector = schema["properties"]["selector"]
    selector_ref = next(
        variant["$ref"] for variant in selector["anyOf"]
        if "$ref" in variant
    )
    selector_schema = _resolve_local_ref(schema, selector_ref)

    assert selector_schema.get("additionalProperties") is False
    selector_props = selector_schema["properties"]
    assert set(selector_props) == {"occurrence", "within"}
    assert selector_props["occurrence"]["description"] == SELECTOR_OCCURRENCE_DESCRIPTION
    assert selector_props["occurrence"]["anyOf"][0]["minimum"] == 1
    assert selector_props["within"]["description"] == SELECTOR_WITHIN_DESCRIPTION

    within_ref = selector_props["within"]["anyOf"][0]["items"]["$ref"]
    within_schema = _resolve_local_ref(schema, within_ref)
    assert within_schema.get("additionalProperties") is False
    within_props = within_schema["properties"]
    assert set(within_props) == {"target", "occurrence"}
    assert within_props["target"]["description"] == SELECTOR_WITHIN_TARGET_DESCRIPTION
    assert within_props["occurrence"]["description"] == SELECTOR_WITHIN_OCCURRENCE_DESCRIPTION
    assert within_props["occurrence"]["anyOf"][0]["minimum"] == 1


def test_brain_move_schema_is_flat_and_first_class(registered_tools):
    tool = next(t for t in registered_tools if t.name == "brain_move")
    schema = tool.inputSchema
    assert "oneOf" not in schema
    props = schema["properties"]
    assert set(props) == {"op", "source", "dest", "path", "target_type", "parent"}
    assert props["op"]["enum"] == ["rename", "convert", "archive", "unarchive"]
    assert props["op"]["description"].strip()
    assert props["source"]["description"].strip()
    assert props["dest"]["description"].strip()
    assert props["path"]["description"].strip()
    assert props["target_type"]["description"].strip()
    assert props["parent"]["description"].strip()


def test_brain_action_schema_exposes_nested_param_variants(registered_tools):
    tool = next(t for t in registered_tools if t.name == "brain_action")
    schema = tool.inputSchema
    assert "oneOf" not in schema
    props = schema["properties"]
    assert set(props) == {"action", "params"}
    assert props["action"]["enum"] == [
        "delete",
        "shape-printable",
        "shape-presentation",
        "start-shaping",
        "fix-links",
    ]
    assert props["action"]["description"].strip()
    assert props["params"]["description"].strip()

    variant_refs = [
        variant["$ref"] for variant in props["params"]["anyOf"]
        if "$ref" in variant
    ]
    assert len(variant_refs) == 5

    variant_shapes = {
        frozenset(_resolve_local_ref(schema, ref)["properties"]): ref
        for ref in variant_refs
    }
    assert set(variant_shapes) == {
        frozenset({"path"}),
        frozenset({"source", "slug", "render", "keep_heading_with_next", "pdf_engine"}),
        frozenset({"source", "slug", "render", "preview"}),
        frozenset({"target", "title", "skill_type"}),
        frozenset({"fix", "path", "links"}),
    }

    for ref in variant_refs:
        variant_schema = _resolve_local_ref(schema, ref)
        assert variant_schema.get("additionalProperties") is False
        for meta in variant_schema["properties"].values():
            assert meta["description"].strip()

    validator = Draft202012Validator(schema)
    assert not list(validator.iter_errors({"action": "delete", "params": {"path": "Wiki/x.md"}}))
    assert list(validator.iter_errors({"action": "archive", "params": {"path": "Ideas/x.md"}}))
    assert not list(validator.iter_errors({
        "action": "delete",
        "params": {"source": "Wiki/x.md", "slug": "x"},
    })), (
        "brain_action currently exposes nested param variants for discoverability, "
        "but action-to-params pairing remains a runtime contract rather than a "
        "schema-discriminated one."
    )


def test_brain_create_resource_schema_is_enumerated(registered_tools):
    tool = next(t for t in registered_tools if t.name == "brain_create")
    schema = tool.inputSchema
    validator = Draft202012Validator(schema)

    assert schema["properties"]["resource"]["enum"] == list(RESOURCE_KINDS)
    assert not list(validator.iter_errors({"resource": "skill", "name": "x", "body": "y"}))
    assert list(validator.iter_errors({"resource": "bogus", "name": "x", "body": "y"}))
