"""Catalogue-derived granular MCPServer adapter contracts."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json

import pytest
from mcp.server import MCPServer

from brain_mcp._command_adapter import (
    application_interface_header,
    register_application_tools,
)
from _application.projection import minimal_request_payload, project_identity, request_schema
from command_application import context_for
from _application.registry import current_application_catalogue, current_request_resolver
from _application.types import (
    Availability,
    DependencyTier,
    EffectClass,
    InitialAuthorisationClass,
    Projection,
    RetryClass,
    SnapshotFreshness,
)
from _command_interface.context import compose_local_context
import _command_interface.direct as direct_context
from _common import _operational_log


NOW = datetime.fromisoformat("2026-08-10T11:00:00+10:00")


class _Clock:
    def now(self):
        return NOW


class _AdvancingClock:
    def __init__(self):
        self._calls = 0

    def now(self):
        self._calls += 1
        return NOW + timedelta(microseconds=self._calls)


def _vault(tmp_path):
    root = (tmp_path / "Brain").resolve()
    (root / ".brain-core").mkdir(parents=True, exist_ok=True)
    (root / ".brain-core" / "VERSION").write_text("0.55.0\n")
    return root


def _context_factory(tmp_path, allowed_tools):
    counter = 0

    def create(**_metadata):
        nonlocal counter
        counter += 1
        clock = _Clock()
        authorisation = context_for(_vault(tmp_path), allowed_commands=allowed_tools).authorisation
        return compose_local_context(
            vault_root=_vault(tmp_path),
            brain_id="test-brain",
            profile="operator",
            authorisation=authorisation,
            dependency_tier=DependencyTier.MANAGED,
            provider_ids=(
                "caller_filesystem",
                "document_renderer",
                "git_remote",
                "obsidian_cli",
                "semantic_retrieval",
                "semantic_runtime",
            ),
            capability_states=tuple(
                (name, Availability.AVAILABLE)
                for name in (
                    "caller_filesystem",
                    "document_renderer",
                    "git_remote",
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
            receipt_store=authorisation.receipts,
            clock=clock,
        )

    return create


def _registered(tmp_path, allowed_tools):
    catalogue = current_application_catalogue()
    resolver = current_request_resolver()
    mcp = MCPServer("granular-test")
    names = register_application_tools(
        mcp,
        catalogue=catalogue,
        resolver=resolver,
        context_factory=_context_factory(tmp_path, allowed_tools),
        invocation_guard=lambda: None,
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
        schema = request_schema(entry.request_type)
        actual = dict(tool.input_schema)
        properties = dict(actual["properties"])
        selector = properties.pop("brain_operation", None)
        if entry.initial_class is InitialAuthorisationClass.CONTROL:
            assert selector is None
        else:
            assert selector["type"] == ["string", "null"]
        assert "brain_operation" not in actual.get("required", ())
        actual["properties"] = properties
        assert actual == schema
        assert tool.description == entry.summary
        assert "request" not in tool.input_schema["properties"]
        assert tool.annotations.read_only_hint is (
            entry.effect_class is EffectClass.NONE
        )
        assert tool.annotations.destructive_hint is (
            entry.effect_class
            in {
                EffectClass.SELECTED_BRAIN_MUTATION,
                EffectClass.CALLER_LOCAL_MUTATION,
                EffectClass.MACHINE_MUTATION,
            }
        )
        assert tool.annotations.idempotent_hint is (
            entry.retry_class is RetryClass.SAFE
        )
        assert tool.annotations.open_world_hint is entry.open_world


def test_registration_and_proxy_header_share_one_ceiling_projection(tmp_path):
    catalogue = current_application_catalogue()
    allowed = frozenset(("access.status", "command.list"))
    mcp = MCPServer("ceiling-test")

    names = register_application_tools(
        mcp,
        catalogue=catalogue,
        resolver=current_request_resolver(),
        context_factory=_context_factory(tmp_path, allowed),
        invocation_guard=lambda: None,
        allowed_tools=allowed,
    )
    header = application_interface_header(catalogue, allowed_tools=allowed)

    assert names == ("access_status", "command_list")
    assert tuple(tool.name for tool in asyncio.run(mcp.list_tools())) == names
    assert tuple(name for name, _mapping in header.tools) == names


def test_real_mcpserver_call_returns_structural_content_and_error_state(tmp_path):
    allowed = ("command.list",)
    mcp, _catalogue, _resolver, _names = _registered(tmp_path, allowed)

    ok = asyncio.run(
        mcp.call_tool(
            "command_list",
            {"dependency_tier": "managed", "page_size": 1},
        )
    )
    denied = asyncio.run(mcp.call_tool("artefact_list", {}))

    assert ok.structured_content["command"] == "command.list"
    assert ok.structured_content["status"] == "ok"
    assert ok.is_error is False
    assert denied.structured_content["error"]["code"] == "authority_denied"
    assert denied.is_error is True


def test_mcp_call_labels_envelope_json_for_assistants_and_concise_text_for_users(
    tmp_path,
):
    allowed = ("command.list",)
    mcp, _catalogue, _resolver, _names = _registered(tmp_path, allowed)

    ok = asyncio.run(
        mcp.call_tool(
            "command_list",
            {"dependency_tier": "managed", "page_size": 1},
        )
    )
    denied = asyncio.run(mcp.call_tool("artefact_list", {}))

    assistant, user = ok.content
    assert assistant.annotations.audience == ["assistant"]
    assert json.loads(assistant.text) == ok.structured_content
    assert user.annotations.audience == ["user"]
    assert user.text == "command.list: ok"

    denied_assistant, denied_user = denied.content
    assert json.loads(denied_assistant.text) == denied.structured_content
    assert denied_assistant.annotations.audience == ["assistant"]
    assert denied_user.annotations.audience == ["user"]
    assert denied_user.text.startswith("artefact.list: authority_denied")


@pytest.mark.parametrize("refresh", (False, True))
def test_real_mcp_calls_reuse_a_command_list_snapshot_with_an_advancing_clock(
    tmp_path,
    monkeypatch,
    refresh,
):
    vault = _vault(tmp_path)
    shared = vault / ".brain/config.yaml"
    shared.parent.mkdir(parents=True)
    shared.write_text(
        "vault:\n"
        "  profiles:\n"
        "    operator:\n"
        "      allow: [command.describe, command.list, invocation.read]\n"
        "defaults:\n"
        "  default_profile: operator\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    monkeypatch.setattr(
        direct_context,
        "_probe_obsidian_cli",
        lambda: Availability.UNAVAILABLE,
    )
    monkeypatch.setattr(
        direct_context,
        "_probe_semantic_retrieval",
        lambda *_args: Availability.UNAVAILABLE,
    )
    composer = direct_context.DirectContextComposer(
        vault_root=vault,
        catalogue=current_application_catalogue(),
        clock=_AdvancingClock(),
    )
    allowed = composer.identity().allowed_tools
    mcp = MCPServer("pagination-test")
    register_application_tools(
        mcp,
        catalogue=current_application_catalogue(),
        resolver=current_request_resolver(),
        context_factory=lambda **metadata: composer.compose(
            command_id=metadata["command_id"]
        ),
        invocation_guard=lambda: None,
        allowed_tools=allowed,
    )

    first = asyncio.run(
        mcp.call_tool(
            "command_list",
            {"refresh": refresh, "page_size": 1},
        )
    )
    cursor = first.structured_content["result"]["next_cursor"]
    second = asyncio.run(
        mcp.call_tool("command_list", {"cursor": cursor, "page_size": 1})
    )

    assert first.is_error is False
    assert second.is_error is False
    assert first.structured_content["result"]["snapshot_token"] == (
        second.structured_content["result"]["snapshot_token"]
    )
    assert first.structured_content["result"]["entries"][0]["command_id"] != (
        second.structured_content["result"]["entries"][0]["command_id"]
    )


def test_real_mcpserver_call_writes_paired_tool_diagnostics(
    tmp_path,
    monkeypatch,
):
    vault = _vault(tmp_path)
    logger = _operational_log.OperationalLogger(vault, "server")
    monkeypatch.setattr(_operational_log, "_INSTALLED", logger)
    mcp, _catalogue, _resolver, _names = _registered(
        tmp_path,
        ("command.list",),
    )

    result = asyncio.run(
        mcp.call_tool("command_list", {"dependency_tier": "managed", "page_size": 1})
    )
    logger.close(exit_code=0)

    assert result.is_error is False
    records = [
        json.loads(line)
        for line in (
            _operational_log.diagnostics_directory(vault) / "server.log"
        ).read_text(encoding="utf-8").splitlines()
    ]
    tool_records = [record for record in records if record["event"].startswith("tool.")]
    assert [record["event"] for record in tool_records] == [
        "tool.started",
        "tool.handled",
    ]
    assert {record["command_id"] for record in tool_records} == {"command.list"}
    assert {record.get("invocation_id") for record in tool_records} == {"inv-1"}


def test_invocation_guard_runs_before_context_composition(tmp_path):
    catalogue = current_application_catalogue()
    mcp = MCPServer("guard-order-test")
    context_calls = []

    def reject_stale_process():
        raise SystemExit(10)

    register_application_tools(
        mcp,
        catalogue=catalogue,
        resolver=current_request_resolver(),
        context_factory=lambda **metadata: context_calls.append(metadata),
        invocation_guard=reject_stale_process,
    )

    with pytest.raises(SystemExit) as exc:
        asyncio.run(mcp.call_tool("command_list", {}))

    assert exc.value.code == 10
    assert context_calls == []


@pytest.mark.parametrize(
    "payload",
    (
        {"request": {}},
        {"page_size": "not-an-integer"},
        {"unknown": True},
    ),
)
def test_real_mcpserver_maps_envelopes_wrong_types_and_unknown_fields(
    tmp_path,
    payload,
):
    mcp, _catalogue, _resolver, _names = _registered(
        tmp_path,
        ("command.list",),
    )

    result = asyncio.run(mcp.call_tool("command_list", payload))

    assert result.structured_content["command"] == "command.list"
    assert result.structured_content["error"]["code"] == "invalid_request"
    assert result.is_error is True


def test_every_minimal_request_survives_real_mcpserver_projection(tmp_path):
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
        assert result.structured_content["command"] == entry.command_id
        assert (
            result.structured_content["error"]["code"] == "authority_denied"
        ), (entry.command_id, payload, result.structured_content)
        assert result.is_error is True


def test_dotted_canonical_id_is_not_a_callable_mcp_alias(tmp_path):
    from mcp.server.mcpserver.exceptions import ToolError

    mcp, _, _, _ = _registered(tmp_path, ("command.list",))
    with pytest.raises(ToolError, match="Unknown tool"):
        asyncio.run(mcp.call_tool("command.list", {}))


def test_operation_selector_is_transport_metadata_and_never_business_input(tmp_path):
    calls = []
    resolved_payloads = []
    resolver = current_request_resolver()

    class ObservedResolver:
        entries = resolver.entries

        def resolve(self, command_id, arguments):
            resolved_payloads.append(dict(arguments))
            return resolver.resolve(command_id, arguments)

    root = _vault(tmp_path)
    (root / "README.md").write_text("Readable content")
    context = context_for(root, allowed_commands=("vault.read-file",))
    def compose(**metadata):
        calls.append(metadata)
        return context

    mcp = MCPServer("operation-selector")
    register_application_tools(mcp, catalogue=current_application_catalogue(), resolver=ObservedResolver(),
                               context_factory=compose, invocation_guard=lambda: None)
    result = asyncio.run(mcp.call_tool("vault_read-file", {"path": "README.md", "brain_operation": "owned-operation"}))
    assert calls[0]["operation_id"] == "owned-operation"
    assert resolved_payloads == [{"path": "README.md"}]
    assert result.is_error is False


@pytest.mark.parametrize("tool", ["vault_read-file", "command_list"])
@pytest.mark.parametrize("selector", ["", "  ", "x" * 129, 12])
def test_invalid_operation_selector_is_rejected_before_context(tmp_path, selector, tool):
    calls = []
    mcp = MCPServer("invalid-selector")
    register_application_tools(mcp, catalogue=current_application_catalogue(), resolver=current_request_resolver(),
                               context_factory=lambda **metadata: calls.append(metadata), invocation_guard=lambda: None)
    result = asyncio.run(mcp.call_tool(tool, {"brain_operation": selector}))
    assert result.structured_content["error"]["code"] == "invalid_request"
    assert calls == []


def test_control_rejects_even_null_unadvertised_operation_selector(tmp_path):
    mcp, _, _, _ = _registered(tmp_path, ("command.list",))
    result = asyncio.run(mcp.call_tool("command_list", {"brain_operation": None}))
    assert result.structured_content["error"]["code"] == "invalid_request"
