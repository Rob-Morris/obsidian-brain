"""One-time granular profile migration before the breaking cutover."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _application.projection import project_identity
from _application.registry import current_application_catalogue
from _command_interface.profile_migration import (
    _LEGACY_BUILTIN_ALLOW,
    _LEGACY_COMMANDS,
    ProfileMigrationError,
    migrate_profile_allow_lists,
)


DISPOSITIONS = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "command_interface_dispositions_v1.json"
)


def _legacy_builtins():
    return {
        name: {"allow": list(tools), "label": f"{name} profile"}
        for name, tools in _LEGACY_BUILTIN_ALLOW.items()
    }


def _leaf_command_ids(value):
    if isinstance(value, str):
        return {value}
    if not isinstance(value, dict):
        return set()
    return {
        command_id
        for nested in value.values()
        for command_id in _leaf_command_ids(nested)
    }


def _fixture_replacements(tool, fixture):
    disposition = fixture["mcp_tool_dispositions"][tool]
    if "target" in disposition:
        return {disposition["target"]}
    if "mappings" in disposition:
        return _leaf_command_ids(disposition["mappings"])
    if "mapping_ref" in disposition:
        value = fixture
        for part in disposition["mapping_ref"].split("."):
            value = value[part]
        if isinstance(value, dict) and "mappings" in value:
            value = value["mappings"]
        return _leaf_command_ids(value)
    return set()


def test_legacy_mapping_matches_the_closed_operation_disposition_evidence():
    fixture = json.loads(DISPOSITIONS.read_text(encoding="utf-8"))

    assert set(_LEGACY_COMMANDS) == set(fixture["mcp_tool_dispositions"])
    for tool, command_ids in _LEGACY_COMMANDS.items():
        expected = _fixture_replacements(tool, fixture)
        if tool == "brain_init":
            expected = {"command.describe", "command.list", "session.start"}
        assert set(command_ids) == expected, tool


def test_exact_legacy_builtins_become_catalogue_derived_granular_profiles():
    result = migrate_profile_allow_lists(
        _legacy_builtins(),
        current_application_catalogue(),
    )

    assert {name: len(value["allow"]) for name, value in result.profiles.items()} == {
        "reader": 41,
        "contributor": 82,
        "operator": 109,
    }
    assert [change.strategy for change in result.changes] == [
        "builtin",
        "builtin",
        "builtin",
    ]
    assert result.profiles["reader"]["label"] == "reader profile"
    assert "brain_action" not in result.profiles["operator"]["allow"]


def test_custom_profile_expands_only_its_legacy_capabilities():
    result = migrate_profile_allow_lists(
        {
            "auditor": {
                "allow": ["brain_read", "brain_search"],
                "description": "Read and search only.",
            }
        },
        current_application_catalogue(),
    )
    expected_commands = set(_LEGACY_COMMANDS["brain_read"]) | set(
        _LEGACY_COMMANDS["brain_search"]
    )

    assert result.profiles["auditor"] == {
        "allow": sorted(
            project_identity(command_id).mcp_tool
            for command_id in expected_commands
        ),
        "description": "Read and search only.",
    }
    assert result.changes[0].strategy == "custom"
    assert "brain_invocation_read" not in result.profiles["auditor"]["allow"]


def test_custom_mutator_gains_only_the_required_outcome_query_closure():
    result = migrate_profile_allow_lists(
        {"author": {"allow": ["brain_create"]}},
        current_application_catalogue(),
    )
    expected = {
        project_identity(command_id).mcp_tool
        for command_id in _LEGACY_COMMANDS["brain_create"]
    }
    expected.add("brain_invocation_read")

    assert set(result.profiles["author"]["allow"]) == expected


def test_mixed_and_already_granular_profiles_are_idempotent():
    catalogue = current_application_catalogue()
    first = migrate_profile_allow_lists(
        {
            "mixed": {
                "allow": [
                    "brain_read",
                    "brain_vault_read_file",
                    "brain_command_list",
                ]
            }
        },
        catalogue,
    )
    second = migrate_profile_allow_lists(first.profiles, catalogue)

    assert second.profiles == first.profiles
    assert second.changes == ()


@pytest.mark.parametrize(
    "profiles, message",
    (
        ({"bad": {"allow": ["brain_unknown"]}}, "unknown tool"),
        ({"bad": {"allow": "brain_read"}}, "non-empty strings"),
        ({"bad": []}, "must be a mapping"),
    ),
)
def test_profile_migration_fails_closed_before_returning_partial_output(
    profiles,
    message,
):
    with pytest.raises(ProfileMigrationError, match=message):
        migrate_profile_allow_lists(profiles, current_application_catalogue())
