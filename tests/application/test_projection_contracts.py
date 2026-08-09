"""Mechanical selected-Brain adapter projection contract tests."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path

from _application.projection import (
    canonical_result_envelope,
    canonical_result_json,
    command_id_from_argv,
    command_id_from_mcp_tool,
    minimal_request_payload,
    project_identity,
    request_schema,
)
from _application.receipts import CommittedEffect, OutcomeReference
from _application.registry import current_application_catalogue
from _application.registry import current_request_resolver
from _application.results import (
    CommandError,
    Error,
    ErrorCode,
    Ok,
    OutcomeUnknownDetails,
    Partial,
)


class _Mode(str, Enum):
    EXACT = "exact"
    BROAD = "broad"


@dataclass(frozen=True, slots=True)
class _Nested:
    value: str


@dataclass(frozen=True, slots=True)
class _Payload:
    path: Path
    mode: _Mode
    nested: _Nested | None


def test_every_application_command_has_one_collision_free_mechanical_projection():
    catalogue = current_application_catalogue()
    projections = tuple(project_identity(entry.command_id) for entry in catalogue.entries)

    assert len({item.mcp_tool for item in projections}) == len(projections)
    assert len({item.cli_argv for item in projections}) == len(projections)
    assert project_identity("vault.check").mcp_tool == "brain_vault_check"
    assert project_identity("artefact.replace-text").cli_argv == (
        "artefact",
        "replace-text",
    )
    assert project_identity("artefact.replace-text").module_path == (
        "_application/artefact/replace_text.py"
    )
    assert all(
        1 <= len(entry.summary.removesuffix(".").split()) <= 12
        for entry in catalogue.entries
    )


def test_name_resolvers_reject_aliases_stutter_and_ambiguous_unowned_mcp_names():
    command_ids = tuple(
        entry.command_id for entry in current_application_catalogue().entries
    )
    assert command_id_from_argv(
        "artefact", "replace-text", command_ids
    ) == "artefact.replace-text"
    assert command_id_from_mcp_tool("brain_vault_check", command_ids) == "vault.check"

    for noun, verb in (("brain", "vault-check"), ("artefact", "replace_text")):
        try:
            command_id_from_argv(noun, verb, command_ids)
        except ValueError:
            pass
        else:
            raise AssertionError("non-canonical CLI alias unexpectedly resolved")

    try:
        command_id_from_mcp_tool("brain_one_two_three", command_ids)
    except ValueError as exc:
        assert "ambiguous" in str(exc)
    else:
        raise AssertionError("ambiguous MCP name unexpectedly resolved without a catalogue")


def test_every_application_request_projects_to_a_strict_described_object_schema():
    catalogue = current_application_catalogue()

    for entry in catalogue.entries:
        schema = request_schema(entry.request_type)
        assert schema["type"] == "object", entry.command_id
        assert schema["additionalProperties"] is False, entry.command_id
        assert all(
            property_schema.get("description")
            for property_schema in schema["properties"].values()
        ), entry.command_id
        assert "command_id" not in schema["properties"], entry.command_id
        assert "command_version" not in schema["properties"], entry.command_id

        pending = [schema]
        while pending:
            node = pending.pop()
            for property_schema in node.get("properties", {}).values():
                assert property_schema.get("description"), entry.command_id
                pending.append(property_schema)
            for branch_name in ("anyOf", "oneOf", "allOf", "prefixItems"):
                pending.extend(node.get(branch_name, ()))
            items = node.get("items")
            if isinstance(items, dict):
                pending.append(items)


def test_every_discovery_example_resolves_through_the_real_dynamic_boundary():
    catalogue = current_application_catalogue()
    resolver = current_request_resolver()

    assert {entry.command_id for entry in resolver.entries} == {
        entry.command_id for entry in catalogue.entries
    }
    for entry in catalogue.entries:
        payload = minimal_request_payload(entry.request_type)
        request = resolver.resolve(entry.command_id, payload)
        assert type(request) is entry.request_type, entry.command_id


def test_request_schema_preserves_required_defaults_enums_and_nested_shapes():
    from _application.artefact.replace_text import ArtefactReplaceTextRequest

    schema = request_schema(ArtefactReplaceTextRequest)

    assert schema["required"] == ["path", "old_text", "new_text"]
    assert schema["properties"]["replace_all"]["default"] is False
    assert schema["properties"]["scope"]["anyOf"][0]["enum"] == [
        "section",
        "intro",
        "body",
        "heading",
        "header",
    ]
    assert (
        schema["properties"]["selector"]["anyOf"][0]["properties"]["within"]["type"]
        == "array"
    )

    from _application.requests import CommandListRequest

    command_list_schema = request_schema(CommandListRequest)
    assert command_list_schema["properties"]["dependency_tier"]["anyOf"][0] == {
        "type": "string",
        "enum": ["bootstrap", "portable", "managed"],
    }


def test_canonical_result_projection_is_structural_and_deterministic():
    ok = Ok("vault.read-file", 1, _Payload(Path("note.md"), _Mode.EXACT, _Nested("x")))
    partial = Partial(
        "artefact.delete",
        1,
        CommandError(ErrorCode.CONFLICT, "Only part of the delete committed."),
        (CommittedEffect("artefact.delete", "note.md"),),
    )
    reference = OutcomeReference("inv-unknown")
    unknown = Error(
        "artefact.delete",
        1,
        CommandError(
            ErrorCode.COMMAND_OUTCOME_UNKNOWN,
            "Outcome unknown.",
            OutcomeUnknownDetails(reference),
        ),
        effects="unknown",
        outcome_reference=reference,
    )

    assert canonical_result_envelope(ok) == {
        "schema": "brain.command-result/1",
        "command": "vault.read-file",
        "command_version": 1,
        "status": "ok",
        "warnings": [],
        "result": {
            "path": "note.md",
            "mode": "exact",
            "nested": {"value": "x"},
        },
        "committed_effects": [],
    }
    partial_wire = canonical_result_envelope(partial)
    assert partial_wire["status"] == "partial"
    assert partial_wire["error"]["effects"] == "known"
    assert partial_wire["result"]["committed_effects"][0]["subject"] == "note.md"
    unknown_wire = canonical_result_envelope(unknown)
    assert unknown_wire["error"]["effects"] == "unknown"
    assert unknown_wire["error"]["retryable"] is False
    assert unknown_wire["error"]["outcome_reference"] == {
        "invocation_id": "inv-unknown"
    }
    assert json.loads(canonical_result_json(unknown)) == unknown_wire
