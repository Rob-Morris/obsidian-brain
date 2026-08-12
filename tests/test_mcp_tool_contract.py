"""Metadata and schema gates for the catalogue-derived MCP projection."""

from __future__ import annotations

import asyncio
import re

from jsonschema import Draft202012Validator

from brain_mcp import server


_BANNED_HEADINGS = re.compile(
    r"^\s*(Args|Arguments|Parameters|Params|Returns|Return)\s*:\s*$",
    re.MULTILINE,
)


def _tools():
    return asyncio.run(server.mcp.list_tools())


def _resolve_ref(schema, reference):
    node = schema
    for part in reference.removeprefix("#/").split("/"):
        node = node[part]
    return node


def _reachable_properties(schema):
    visited = set()

    def visit(node, path):
        if not isinstance(node, dict):
            return
        reference = node.get("$ref")
        if reference:
            if reference in visited:
                return
            visited.add(reference)
            yield from visit(_resolve_ref(schema, reference), path)
            return
        for name, metadata in node.get("properties", {}).items():
            qualified = ".".join((*path, name))
            yield qualified, metadata
            yield from visit(metadata, (*path, name))
        for keyword in ("oneOf", "anyOf", "allOf", "prefixItems"):
            for child in node.get(keyword, []):
                yield from visit(child, path)
        if isinstance(node.get("items"), dict):
            yield from visit(node["items"], (*path, "items"))

    yield from visit(schema, ())


def test_every_projected_tool_has_strict_valid_described_schema():
    tools = _tools()
    assert len(tools) == 78
    for tool in tools:
        Draft202012Validator.check_schema(tool.input_schema)
        assert tool.input_schema.get("type") == "object"
        assert tool.input_schema.get("additionalProperties") is False
        assert not _BANNED_HEADINGS.search(tool.description or "")
        assert len((tool.description or "").strip()) <= 600
        missing = [
            path
            for path, metadata in _reachable_properties(tool.input_schema)
            if not str(metadata.get("description", "")).strip()
        ]
        assert not missing, f"{tool.name} has undocumented fields: {missing}"


def test_mcp_safety_hints_are_complete_and_closed():
    for tool in _tools():
        annotations = tool.annotations
        assert annotations is not None
        assert isinstance(annotations.read_only_hint, bool)
        assert isinstance(annotations.destructive_hint, bool)
        assert isinstance(annotations.idempotent_hint, bool)
        assert annotations.open_world_hint is False
