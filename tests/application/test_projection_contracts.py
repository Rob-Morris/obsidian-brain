"""Mechanical selected-Brain adapter projection contract tests."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import re
import pytest
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
    command_ids = tuple(entry.command_id for entry in catalogue.entries)
    for item in projections:
        assert re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", item.mcp_tool)
        assert command_id_from_mcp_tool(item.mcp_tool, command_ids) == item.command_id
        with pytest.raises(ValueError):
            command_id_from_mcp_tool(item.command_id, command_ids)
    assert (
        project_identity("document.structured-edit").mcp_tool
        == "document_structured-edit"
    )
    assert len({item.cli_argv for item in projections}) == len(projections)
    assert project_identity("vault.check").mcp_tool == "vault_check"
    assert project_identity("document.structured-edit").cli_argv == (
        "document",
        "structured-edit",
    )
    assert project_identity("document.structured-edit").module_path == (
        "_application/document/structured_edit.py"
    )
    assert all(
        1 <= len(entry.summary.removesuffix(".").split()) <= 12
        for entry in catalogue.entries
    )


def test_name_resolvers_reject_aliases_stutter_and_unowned_mcp_names():
    command_ids = tuple(
        entry.command_id for entry in current_application_catalogue().entries
    )
    assert command_id_from_argv(
        "document", "structured-edit", command_ids
    ) == "document.structured-edit"
    assert command_id_from_mcp_tool("vault_check", command_ids) == "vault.check"

    for noun, verb in (("brain", "vault-check"), ("artefact", "replace_text")):
        try:
            command_id_from_argv(noun, verb, command_ids)
        except ValueError:
            pass
        else:
            raise AssertionError("non-canonical CLI alias unexpectedly resolved")

    try:
        command_id_from_mcp_tool("one_two", command_ids)
    except ValueError as exc:
        assert "not owned" in str(exc)
    else:
        raise AssertionError("unowned MCP name unexpectedly resolved")


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


def test_document_mutation_schemas_preserve_typed_intents_and_revisions():
    from _application.document.structured_edit import DocumentStructuredEditRequest
    from _application.document.replace_text import DocumentReplaceTextRequest
    from _application.document.write_body import DocumentWriteBodyRequest

    schema = request_schema(DocumentStructuredEditRequest)

    assert schema["required"] == ["document", "expected_revision", "change"]
    assert schema["properties"]["fix_links"]["default"] is False
    assert schema["properties"]["document"]["properties"]["resource"]["enum"] == [
        "artefact",
        "memory",
        "skill",
        "style",
        "template",
    ]
    operations = {
        branch["properties"]["operation"]["enum"][0]
        for branch in schema["properties"]["change"]["anyOf"]
    }
    assert operations == {
        "replace",
        "insert",
        "delete",
    }
    encoded = json.dumps(schema)
    assert '"target"' not in encoded
    assert '"scope"' not in encoded

    write_schema = request_schema(DocumentWriteBodyRequest)
    assert write_schema["properties"]["operation"]["enum"] == [
        "replace",
        "append",
        "prepend",
    ]

    patch_schema = request_schema(DocumentReplaceTextRequest)
    assert {
        branch["properties"]["mode"]["enum"][0]
        for branch in patch_schema["properties"]["match"]["anyOf"]
    } == {"unique", "occurrence", "all"}


def test_request_schema_preserves_required_defaults_enums_and_nested_shapes():
    from _application.requests import CommandListRequest

    command_list_schema = request_schema(CommandListRequest)
    assert command_list_schema["properties"]["dependency_tier"] == {
        "type": ["string", "null"],
        "enum": ["bootstrap", "portable", "managed", None],
        "description": "Dependency tier",
        "default": None,
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


def test_catalogue_examples_are_valid_against_their_own_wire_schemas():
    from jsonschema import Draft202012Validator

    resolver = current_request_resolver()
    for entry in current_application_catalogue().entries:
        payload = minimal_request_payload(entry.request_type)
        Draft202012Validator(request_schema(entry.request_type)).validate(payload)
        assert type(resolver.resolve(entry.command_id, payload)) is entry.request_type


def test_frontmatter_wire_objects_round_trip_through_all_request_variants():
    from jsonschema import Draft202012Validator
    from _application.projection import canonical_wire_value
    import pytest

    fields = {
        "enabled": True,
        "empty": [],
        "missing": None,
        "rank": 2,
        "tags": ["one", None, 3.5],
    }
    catalogue = current_application_catalogue()
    resolver = current_request_resolver()
    cases = [
        (
            "artefact.create",
            {"type": "temporal/plan", "title": "Example", "frontmatter": fields},
            ("frontmatter",),
        )
    ]
    cases.extend(
        (
            "resource.create",
            {
                "target": {
                    "resource": resource,
                    "name": "example",
                    "frontmatter": fields,
                },
                "content": {"source": "inline", "content": "Example"},
            },
            ("target", "frontmatter"),
        )
        for resource in ("memory", "skill", "style")
    )
    cases.append(
        (
            "document.update-frontmatter",
            {
                "document": {"resource": "memory", "reference": "example"},
                "expected_revision": "sha256:" + "0" * 64,
                "updates": fields,
            },
            ("updates",),
        )
    )
    for command_id, payload, path in cases:
        import copy

        entry = next(
            entry for entry in catalogue.entries if entry.command_id == command_id
        )
        validator = Draft202012Validator(request_schema(entry.request_type))
        validator.validate(payload)
        encoded = canonical_wire_value(resolver.resolve(command_id, payload))
        for part in path:
            encoded = encoded[part]
        assert encoded == fields
        for invalid in (
            None,
            [],
            [{"name": "tags", "value": ["one"]}],
            {"nested": {"x": 1}},
            {"tags": [["nested"]]},
            {" ": "value"},
        ):
            bad = copy.deepcopy(payload)
            target = bad
            for part in path[:-1]:
                target = target[part]
            target[path[-1]] = invalid
            assert not validator.is_valid(bad), (command_id, invalid)
            with pytest.raises(ValueError):
                resolver.resolve(command_id, bad)
