"""Catalogue-derived granular FastMCP adapter contracts."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from mcp.server.fastmcp import FastMCP

from brain_mcp._command_adapter import register_application_tools
from _application.projection import minimal_request_payload, project_identity, request_schema
from _application.receipts import MemoryReceiptStore
from _application.registry import current_application_catalogue, current_request_resolver
from _application.types import (
    Availability,
    DependencyTier,
    EffectClass,
    Projection,
    RetryClass,
    SnapshotFreshness,
)
from _command_interface.context import compose_local_context


NOW = datetime.fromisoformat("2026-08-10T11:00:00+10:00")


class _Clock:
    def now(self):
        return NOW


def _vault(tmp_path):
    root = (tmp_path / "Brain").resolve()
    (root / ".brain-core").mkdir(parents=True, exist_ok=True)
    (root / ".brain-core" / "VERSION").write_text("0.54.57\n")
    return root


def _context_factory(tmp_path, allowed_tools):
    counter = 0

    def create(**_metadata):
        nonlocal counter
        counter += 1
        clock = _Clock()
        return compose_local_context(
            vault_root=_vault(tmp_path),
            brain_id="test-brain",
            profile="operator",
            allowed_tools=frozenset(allowed_tools),
            dependency_tier=DependencyTier.MANAGED,
            provider_ids=(
                "caller_filesystem",
                "document_renderer",
                "obsidian_cli",
                "semantic_retrieval",
                "semantic_runtime",
            ),
            capability_states=tuple(
                (name, Availability.AVAILABLE)
                for name in (
                    "caller_filesystem",
                    "document_renderer",
                    "obsidian_cli",
                    "semantic_retrieval",
                    "semantic_runtime",
                )
            ),
            snapshot_token=f"snapshot-{counter}",
            snapshot_freshness=SnapshotFreshness.FRESH,
            snapshot_observed_at=NOW,
            correlation_id=f"corr-{counter}",
            invocation_id=f"inv-{counter}",
            receipt_store=MemoryReceiptStore(clock),
            clock=clock,
        )

    return create


def _registered(tmp_path, allowed_tools):
    catalogue = current_application_catalogue()
    resolver = current_request_resolver()
    mcp = FastMCP("granular-test")
    names = register_application_tools(
        mcp,
        catalogue=catalogue,
        resolver=resolver,
        context_factory=_context_factory(tmp_path, allowed_tools),
    )
    return mcp, catalogue, resolver, names


def test_every_mcp_eligible_command_registers_one_flat_canonical_schema(tmp_path):
    mcp, catalogue, _resolver, names = _registered(tmp_path, ())
    eligible = tuple(
        entry
        for entry in catalogue.entries
        if Projection.MCP in entry.eligible_projections
    )

    assert names == tuple(project_identity(entry.command_id).mcp_tool for entry in eligible)
    tools = asyncio.run(mcp.list_tools())
    assert tuple(tool.name for tool in tools) == names
    by_name = {tool.name: tool for tool in tools}
    for entry in eligible:
        tool = by_name[project_identity(entry.command_id).mcp_tool]
        assert tool.inputSchema == request_schema(entry.request_type)
        assert tool.description == entry.summary
        assert "request" not in tool.inputSchema["properties"]
        assert tool.annotations.readOnlyHint is (
            entry.effect_class is EffectClass.NONE
        )
        assert tool.annotations.destructiveHint is (
            entry.effect_class
            in {
                EffectClass.SELECTED_BRAIN_MUTATION,
                EffectClass.CALLER_LOCAL_MUTATION,
                EffectClass.MACHINE_MUTATION,
            }
        )
        assert tool.annotations.idempotentHint is (
            entry.retry_class is RetryClass.SAFE
        )
        assert tool.annotations.openWorldHint is False


def test_real_fastmcp_call_returns_structural_content_and_error_state(tmp_path):
    allowed = ("brain_command_list",)
    mcp, _catalogue, _resolver, _names = _registered(tmp_path, allowed)

    ok = asyncio.run(
        mcp.call_tool(
            "brain_command_list",
            {"dependency_tier": "managed", "page_size": 1},
        )
    )
    denied = asyncio.run(mcp.call_tool("brain_artefact_list", {}))

    assert ok.structuredContent["command"] == "command.list"
    assert ok.structuredContent["status"] == "ok"
    assert ok.isError is False
    assert denied.structuredContent["error"]["code"] == "authority_denied"
    assert denied.isError is True


@pytest.mark.parametrize(
    "payload",
    (
        {"request": {}},
        {"page_size": "not-an-integer"},
        {"unknown": True},
    ),
)
def test_real_fastmcp_maps_envelopes_wrong_types_and_unknown_fields(
    tmp_path,
    payload,
):
    mcp, _catalogue, _resolver, _names = _registered(
        tmp_path,
        ("brain_command_list",),
    )

    result = asyncio.run(mcp.call_tool("brain_command_list", payload))

    assert result.structuredContent["command"] == "command.list"
    assert result.structuredContent["error"]["code"] == "invalid_request"
    assert result.isError is True


def test_every_minimal_request_survives_real_fastmcp_projection(tmp_path):
    mcp, catalogue, resolver, _names = _registered(tmp_path, ())

    for entry in catalogue.entries:
        if Projection.MCP not in entry.eligible_projections:
            continue
        payload = minimal_request_payload(entry.request_type)
        resolved = resolver.resolve(entry.command_id, payload)
        assert type(resolved) is entry.request_type
        result = asyncio.run(
            mcp.call_tool(project_identity(entry.command_id).mcp_tool, payload)
        )
        assert result.structuredContent["command"] == entry.command_id
        assert (
            result.structuredContent["error"]["code"] == "authority_denied"
        ), (entry.command_id, payload, result.structuredContent)
        assert result.isError is True
