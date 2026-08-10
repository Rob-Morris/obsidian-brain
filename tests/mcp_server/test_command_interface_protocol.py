"""Proxy/server command-interface header contracts."""

from __future__ import annotations

from copy import deepcopy

import pytest

from brain_mcp._command_adapter import application_interface_header
from brain_mcp._interface_protocol import (
    INTERFACE_HEADER_EXTENSION,
    PROXY_PROTOCOL,
    command_interface_wire,
    interface_header_from_initialize,
    parse_command_interface_header,
    proxy_protocol_supported,
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
    value["tools"]["brain_artefact_create"]["command_version"] = 999


def _invert_protocol_range(value):
    value["proxy_protocol"].update(minimum=3, maximum=2)


def _make_tool_name_irregular(value):
    value["tools"]["irregular"] = value["tools"].pop("brain_artefact_create")


def _contradict_tool_mapping(value):
    value["tools"]["brain_artefact_delete"] = value["tools"].pop(
        "brain_artefact_create"
    )


def _make_command_identifier_non_ascii(value):
    value["tools"]["brain_artefact_create"]["command_id"] = "artefact.créate"


def test_application_header_is_exact_catalogue_derived_mcp_mapping():
    catalogue = current_application_catalogue()
    header = application_interface_header(catalogue)
    eligible = tuple(
        entry
        for entry in catalogue.entries
        if Projection.MCP in entry.eligible_projections
    )

    assert len(header.tools) == len(eligible) == 109
    assert header.interface_epoch == catalogue.interface_epoch
    assert header.catalogue_schema == catalogue.schema
    assert header.result_schema == catalogue.result_schema
    assert header.catalogue_fingerprint == catalogue.fingerprint
    assert header.tool("brain_artefact_create").command_id == "artefact.create"
    assert header.tool("brain_artefact_create").mutation_class == (
        "selected_brain_mutation"
    )


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
        (_make_tool_name_irregular, "brain_<noun>_<verb>"),
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
