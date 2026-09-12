"""Proxy/server command-interface header contracts."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from dataclasses import replace

import pytest

from brain_mcp._command_adapter import application_interface_header
from brain_mcp._interface_protocol import (
    INTERFACE_HEADER_EXTENSION,
    PROXY_PROTOCOL,
    accept_call,
    command_interface_wire,
    interface_header_from_initialize,
    parse_command_interface_header,
    proxy_protocol_supported,
    replay_decision,
)
from _application.registry import current_application_catalogue
from _application.types import Projection


def _wire_header():
    return command_interface_wire(
        application_interface_header(current_application_catalogue())
    )


def _remove_fingerprint(value):
    value.pop("fingerprint")


def _add_unknown_field(value):
    value["extra"] = True


def _change_command_version(value):
    value["tools"]["artefact_create"]["command_version"] = 999


def _invert_protocol_range(value):
    value["proxy_protocol"].update(minimum=3, maximum=2)


def _make_tool_name_irregular(value):
    value["tools"]["irregular"] = value["tools"].pop("artefact_create")


def _contradict_tool_mapping(value):
    value["tools"]["artefact_delete"] = value["tools"].pop("artefact_create")


def _make_command_identifier_non_ascii(value):
    value["tools"]["artefact_create"]["command_id"] = "artefact.créate"


def test_application_header_is_exact_catalogue_derived_mcp_mapping():
    catalogue = current_application_catalogue()
    header = application_interface_header(catalogue)
    eligible = tuple(
        entry
        for entry in catalogue.entries
        if Projection.MCP in entry.eligible_projections
    )

    assert len(header.tools) == len(eligible) == 68
    assert header.interface_epoch == catalogue.interface_epoch
    assert header.catalogue_schema == catalogue.schema
    assert header.result_schema == catalogue.result_schema
    assert header.catalogue_fingerprint == catalogue.fingerprint
    assert header.tool("artefact_create").command_id == "artefact.create"
    assert header.tool("artefact_create").mutation_class == ("selected_brain_mutation")


def test_initialize_header_round_trips_and_supports_current_proxy_protocol():
    wire = _wire_header()
    response = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "capabilities": {
                "experimental": {INTERFACE_HEADER_EXTENSION: wire},
            },
        },
    }

    parsed = interface_header_from_initialize(response)
    assert command_interface_wire(parsed) == wire
    assert parsed.fingerprint == wire["fingerprint"]
    assert proxy_protocol_supported(parsed, PROXY_PROTOCOL)


@pytest.mark.parametrize(
    "mutate, message",
    (
        (_remove_fingerprint, "missing"),
        (_add_unknown_field, "unknown"),
        (_change_command_version, "fingerprint"),
        (_invert_protocol_range, "inverted"),
        (_make_tool_name_irregular, "noun_verb"),
        (_contradict_tool_mapping, "contradicts"),
        (_make_command_identifier_non_ascii, "canonical noun.verb"),
    ),
)
def test_header_parser_rejects_missing_unknown_contradictory_or_irregular_facts(
    mutate,
    message,
):
    wire = deepcopy(_wire_header())
    mutate(wire)

    with pytest.raises(ValueError, match=message):
        parse_command_interface_header(wire)


def test_initialize_header_is_required_at_the_declared_extension_location():
    with pytest.raises(ValueError, match="brainCommandInterface"):
        interface_header_from_initialize(
            {
                "result": {
                    "capabilities": {"experimental": {}},
                },
            }
        )


def _accepted_call(header=None):
    header = header or application_interface_header(current_application_catalogue())
    request = {
        "jsonrpc": "2.0",
        "id": "call-1",
        "method": "tools/call",
        "params": {
            "name": "artefact_create",
            "arguments": {"type": "living/wiki", "title": "Example"},
            "_meta": {"clientTrace": "trace-1"},
        },
    }
    record, forwarded = accept_call(
        request,
        header,
        invocation_id="mcp-invocation-1",
        accepted_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )
    return header, request, record, forwarded


def test_accepted_call_preserves_raw_request_and_injects_proxy_owned_identity():
    header, request, record, forwarded = _accepted_call()

    assert record.raw_request == request
    assert record.request_id == "call-1"
    assert record.projected_tool == "artefact_create"
    assert record.command_id == "artefact.create"
    assert record.command_version == header.tool(record.projected_tool).command_version
    assert record.interface_epoch == header.interface_epoch
    assert record.header_fingerprint == header.fingerprint
    assert record.mutation_class == "selected_brain_mutation"
    assert record.invocation_id == "mcp-invocation-1"
    assert "brainInvocation" not in request["params"]["_meta"]
    assert forwarded["params"]["_meta"] == {
        "clientTrace": "trace-1",
        "brainInvocation": {"invocationId": "mcp-invocation-1"},
    }


def test_accepted_call_rejects_unknown_tool_and_caller_owned_invocation_identity():
    header = application_interface_header(current_application_catalogue())
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "brain_unknown_command", "arguments": {}},
    }
    with pytest.raises(ValueError, match="absent"):
        accept_call(
            request,
            header,
            invocation_id="mcp-1",
            accepted_at=datetime.now(timezone.utc),
        )
    request["params"] = {
        "name": "artefact_create",
        "arguments": {},
        "_meta": {"brainInvocation": {"invocationId": "caller"}},
    }
    with pytest.raises(ValueError, match="owned by the proxy"):
        accept_call(
            request,
            header,
            invocation_id="mcp-1",
            accepted_at=datetime.now(timezone.utc),
        )


@pytest.mark.parametrize(
    "replacement, reason",
    (
        (
            lambda header: replace(header, interface_epoch=header.interface_epoch + 1),
            "interface_epoch_changed",
        ),
        (
            lambda header: replace(
                header,
                tools=tuple(
                    item for item in header.tools if item[0] != "artefact_create"
                ),
            ),
            "projected_tool_removed",
        ),
        (
            lambda header: replace(
                header,
                tools=tuple(
                    (
                        (
                            name,
                            replace(
                                mapping, command_version=mapping.command_version + 1
                            ),
                        )
                        if name == "artefact_create"
                        else (name, mapping)
                    )
                    for name, mapping in header.tools
                ),
            ),
            "command_version_changed",
        ),
        (
            lambda header: replace(
                header,
                tools=tuple(
                    (
                        (name, replace(mapping, mutation_class="none"))
                        if name == "artefact_create"
                        else (name, mapping)
                    )
                    for name, mapping in header.tools
                ),
            ),
            "mutation_class_changed",
        ),
    ),
)
def test_replay_refuses_every_command_level_incompatibility(replacement, reason):
    header, _request, record, _forwarded = _accepted_call()

    assert replay_decision(record, replacement(header)).reason == reason


def test_replay_allows_unchanged_command_across_unrelated_additive_fingerprint_change():
    header, _request, record, _forwarded = _accepted_call()
    replacement_header = replace(
        header,
        catalogue_fingerprint="sha256:" + "1" * 64,
    )

    decision = replay_decision(record, replacement_header)
    assert decision.compatible is True
    assert decision.reason is None
