"""One-time granular profile migration before the breaking cutover."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _common._yaml import dump_yaml_text, load_mapping_file
from _application.registry import current_application_catalogue
from _command_interface.profile_migration import (
    _LEGACY_BUILTIN_ALLOW,
    _LEGACY_COMMANDS,
    _REMOVED_GRANULAR_COMMANDS,
    ProfileMigrationError,
    migrate_profile_allow_lists,
)
from _command_interface.profiles import builtin_profile_allow_lists
import migrate_to_0_55_0
import migrate_to_0_56_0
import migrate_to_0_57_0
import migrate_to_0_59_0


DISPOSITIONS = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "command_interface_dispositions_v1.json"
)
CORE_ROOT = Path(__file__).resolve().parents[2] / "src" / "brain-core"


def _legacy_builtins():
    return {
        name: {"allow": list(tools), "label": f"{name} profile"}
        for name, tools in _LEGACY_BUILTIN_ALLOW.items()
    }


def _previous_granular_builtins():
    """Reconstruct the exact v0.55 built-ins from the current vocabulary."""

    reverse_consolidations = {}
    for old_tool, current_tool in _REMOVED_GRANULAR_COMMANDS.items():
        reverse_consolidations.setdefault(current_tool, set()).add(old_tool)
    profiles = {}
    for profile, tools in builtin_profile_allow_lists(
        current_application_catalogue()
    ).items():
        previous = set(tools) - {
            "access.reduce",
            "access.request",
            "access.status",
            "runtime.status",
            "runtime.warmup",
        }
        for current_tool, old_tools in reverse_consolidations.items():
            if current_tool in previous:
                previous.remove(current_tool)
                previous.update(old_tools)
        profiles[profile] = {
            "allow": sorted(previous),
            "label": f"{profile} profile",
        }
    return profiles


def test_shipped_authority_asset_and_profile_defaults_match_the_catalogue():
    catalogue = current_application_catalogue()
    expected = builtin_profile_allow_lists(catalogue)
    authority = json.loads(
        (CORE_ROOT / "defaults" / "command-authority.json").read_text(
            encoding="utf-8"
        )
    )
    defaults = load_mapping_file(CORE_ROOT / "defaults" / "config.yaml")

    assert authority == {
        "schema": "brain.command-authority/1",
        "commands": list(expected["administrator"]),
    }
    assert {
        name: tuple(definition["allow"])
        for name, definition in defaults["vault"]["profiles"].items()
    } == expected


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


def _final_replacements(command_ids):
    replacements = {
        _REMOVED_GRANULAR_COMMANDS.get(command_id, command_id)
        for command_id in command_ids
    }
    if "document.structured-edit" in replacements:
        replacements.update(
            {
                "document.replace-text",
                "document.update-frontmatter",
                "document.write-body",
            }
        )
    return replacements


def test_legacy_mapping_matches_the_closed_operation_disposition_evidence():
    fixture = json.loads(DISPOSITIONS.read_text(encoding="utf-8"))

    assert set(_LEGACY_COMMANDS) == set(fixture["mcp_tool_dispositions"])
    for tool, command_ids in _LEGACY_COMMANDS.items():
        expected = _final_replacements(_fixture_replacements(tool, fixture))
        if tool == "brain_init":
            expected = {
                "command.describe",
                "command.list",
                "runtime.status",
                "runtime.warmup",
                "session.start",
            }
        assert set(command_ids) == expected, tool


def test_exact_legacy_builtins_become_catalogue_derived_granular_profiles():
    result = migrate_profile_allow_lists(
        _legacy_builtins(),
        current_application_catalogue(),
    )

    assert {name: len(value["allow"]) for name, value in result.profiles.items()} == {
        "reader": 29,
        "contributor": 60,
        "maintainer": 73,
        "operator": 82,
        "administrator": 83,
    }
    assert [change.strategy for change in result.changes] == [
        "builtin",
        "builtin",
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
        "allow": sorted(command_id for command_id in expected_commands),
        "description": "Read and search only.",
    }
    assert result.changes[0].strategy == "custom"
    assert "invocation.read" not in result.profiles["auditor"]["allow"]


def test_custom_mutator_gains_only_the_required_outcome_query_closure():
    result = migrate_profile_allow_lists(
        {"author": {"allow": ["brain_create"]}},
        current_application_catalogue(),
    )
    expected = {command_id for command_id in _LEGACY_COMMANDS["brain_create"]}
    expected.add("invocation.read")

    assert set(result.profiles["author"]["allow"]) == expected


def test_legacy_builtins_with_custom_profile_add_the_missing_builtins():
    profiles = _legacy_builtins()
    profiles["author"] = {
        "allow": ["brain_read", "brain_create"],
        "description": "User-owned authority.",
    }

    result = migrate_profile_allow_lists(
        profiles,
        current_application_catalogue(),
    )

    expected_builtins = builtin_profile_allow_lists(current_application_catalogue())
    assert set(result.profiles) == set(expected_builtins) | {"author"}
    for profile, allow in expected_builtins.items():
        assert tuple(result.profiles[profile]["allow"]) == allow
    assert set(result.profiles["author"]["allow"]) == {
        "artefact.create",
        "artefact.read",
        "invocation.read",
        "resource.create",
        "resource.read",
        "runtime.read-environment",
        "vault.read-file",
        "vault.read-router",
        "workspace.read",
    }
    assert result.profiles["author"]["description"] == "User-owned authority."


def test_previous_granular_builtins_with_custom_profile_remain_recognisable():
    profiles = _previous_granular_builtins()
    profiles["custom"] = {
        "allow": ["memory.read", "session.start"],
        "description": "Custom profile.",
    }

    result = migrate_profile_allow_lists(
        profiles,
        current_application_catalogue(),
    )

    expected_builtins = builtin_profile_allow_lists(current_application_catalogue())
    for profile, allow in expected_builtins.items():
        assert tuple(result.profiles[profile]["allow"]) == allow
    assert result.profiles["custom"] == {
        "allow": ["resource.read", "session.start"],
        "description": "Custom profile.",
    }


def test_mixed_and_already_granular_profiles_are_idempotent():
    catalogue = current_application_catalogue()
    first = migrate_profile_allow_lists(
        {
            "mixed": {
                "allow": [
                    "brain_read",
                    "vault.read-file",
                    "command.list",
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


def test_v055_upgrade_migration_writes_all_five_builtin_profiles(tmp_path):
    config_path = tmp_path / ".brain" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        dump_yaml_text(
            {
                "vault": {
                    "brain_name": "Test Brain",
                    "profiles": _legacy_builtins(),
                },
                "defaults": {"default_profile": "operator"},
            }
        ),
        encoding="utf-8",
    )

    result = migrate_to_0_55_0.migrate(str(tmp_path))
    migrated = load_mapping_file(config_path)

    assert result["status"] == "ok"
    assert result["profiles"] == [
        "reader",
        "contributor",
        "maintainer",
        "operator",
        "administrator",
    ]
    assert {
        name: len(definition["allow"])
        for name, definition in migrated["vault"]["profiles"].items()
    } == {
        "reader": 29,
        "contributor": 60,
        "maintainer": 73,
        "operator": 82,
        "administrator": 83,
    }
    assert migrated["vault"]["brain_name"] == "Test Brain"
    assert migrated["defaults"] == {"default_profile": "operator"}


def test_v055_upgrade_migration_leaves_config_untouched_on_unknown_grant(tmp_path):
    config_path = tmp_path / ".brain" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    original = dump_yaml_text(
        {"vault": {"profiles": {"custom": {"allow": ["unknown.tool"]}}}}
    )
    config_path.write_text(original, encoding="utf-8")

    with pytest.raises(ProfileMigrationError, match="unknown tool"):
        migrate_to_0_55_0.migrate(str(tmp_path))

    assert config_path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("filename", ("AGENTS.md", "Agents.md", "CLAUDE.md"))
def test_v055_upgrade_migrates_known_bootstraps_without_shared_config(
    tmp_path,
    filename,
):
    bootstrap = tmp_path / filename
    bootstrap.write_text(
        "# Brain\n\nALWAYS DO FIRST: Call MCP `brain_session`.\n",
        encoding="utf-8",
    )

    result = migrate_to_0_55_0.migrate(str(tmp_path))
    reported_name = (
        "AGENTS.md"
        if filename == "Agents.md" and (tmp_path / "AGENTS.md").is_file()
        else filename
    )

    assert result == {
        "status": "ok",
        "profiles": [],
        "strategies": {},
        "bootstraps": [reported_name],
    }
    assert "Call MCP `session.start`" in bootstrap.read_text(encoding="utf-8")
    assert "brain_session" not in bootstrap.read_text(encoding="utf-8")


def test_v055_upgrade_migrates_profiles_and_deduplicates_symlinked_bootstrap(
    tmp_path,
):
    config_path = tmp_path / ".brain" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        dump_yaml_text({"vault": {"profiles": _legacy_builtins()}}),
        encoding="utf-8",
    )
    agents = tmp_path / "AGENTS.md"
    agents.write_text(
        "ALWAYS DO FIRST: Call MCP `brain_session`, else read "
        "`.brain-core/index.md` if it exists.\n",
        encoding="utf-8",
    )
    (tmp_path / "CLAUDE.md").symlink_to(agents)

    result = migrate_to_0_55_0.migrate(str(tmp_path))

    assert len(result["profiles"]) == 5
    assert result["bootstraps"] == ["AGENTS.md"]
    assert agents.read_text(encoding="utf-8").count("session.start") == 1


def test_v055_upgrade_migrates_shipped_project_workspace_bootstrap(tmp_path):
    bootstrap = tmp_path / "CLAUDE.md"
    bootstrap.write_text(
        "ALWAYS DO FIRST: Call MCP `brain_session`; if MCP is unavailable, run "
        "`brain session --json` from this workspace.\n",
        encoding="utf-8",
    )

    result = migrate_to_0_55_0.migrate(str(tmp_path))

    assert result["bootstraps"] == ["CLAUDE.md"]
    assert bootstrap.read_text(encoding="utf-8") == (
        "ALWAYS DO FIRST: Call MCP `session.start`; if MCP is unavailable, run "
        "`brain session start --json` from this workspace.\n"
    )


def test_v055_upgrade_validates_profiles_before_changing_bootstrap(tmp_path):
    config_path = tmp_path / ".brain" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        dump_yaml_text(
            {"vault": {"profiles": {"custom": {"allow": ["unknown.tool"]}}}}
        ),
        encoding="utf-8",
    )
    bootstrap = tmp_path / "AGENTS.md"
    original = "ALWAYS DO FIRST: Call MCP `brain_session`.\n"
    bootstrap.write_text(original, encoding="utf-8")

    with pytest.raises(ProfileMigrationError, match="unknown tool"):
        migrate_to_0_55_0.migrate(str(tmp_path))

    assert bootstrap.read_text(encoding="utf-8") == original


def test_v055_upgrade_preserves_unrecognised_bootstrap_prose(tmp_path):
    bootstrap = tmp_path / "AGENTS.md"
    original = "Our historical notes mention brain_session but are user-authored.\n"
    bootstrap.write_text(original, encoding="utf-8")

    result = migrate_to_0_55_0.migrate(str(tmp_path))

    assert result == {"status": "skipped", "profiles": [], "bootstraps": []}
    assert bootstrap.read_text(encoding="utf-8") == original


def test_v056_upgrade_migrates_exact_shipped_granular_profiles(tmp_path):
    config_path = tmp_path / ".brain" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        dump_yaml_text({"vault": {"profiles": _previous_granular_builtins()}}),
        encoding="utf-8",
    )

    result = migrate_to_0_56_0.migrate(str(tmp_path))
    migrated = load_mapping_file(config_path)

    assert result["status"] == "ok"
    assert result["profiles"] == [
        "reader",
        "contributor",
        "maintainer",
        "operator",
        "administrator",
    ]
    assert {
        name: tuple(definition["allow"])
        for name, definition in migrated["vault"]["profiles"].items()
    } == builtin_profile_allow_lists(current_application_catalogue())
    assert all(strategy == "builtin" for strategy in result["strategies"].values())


def test_v056_upgrade_preserves_custom_authority_while_consolidating(tmp_path):
    config_path = tmp_path / ".brain" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        dump_yaml_text(
            {
                "vault": {
                    "profiles": {
                        "custom": {
                            "allow": ["memory.read", "session.start"],
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = migrate_to_0_56_0.migrate(str(tmp_path))
    migrated = load_mapping_file(config_path)

    assert result == {
        "status": "ok",
        "profiles": ["custom"],
        "strategies": {"custom": "custom"},
    }
    assert migrated["vault"]["profiles"]["custom"]["allow"] == [
        "resource.read",
        "session.start",
    ]
    assert "runtime.status" not in migrated["vault"]["profiles"]["custom"]["allow"]


def test_v056_upgrade_fails_before_writing_an_unknown_grant(tmp_path):
    config_path = tmp_path / ".brain" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    original = dump_yaml_text(
        {"vault": {"profiles": {"custom": {"allow": ["unknown.tool"]}}}}
    )
    config_path.write_text(original, encoding="utf-8")

    with pytest.raises(ProfileMigrationError, match="unknown tool"):
        migrate_to_0_56_0.migrate(str(tmp_path))

    assert config_path.read_text(encoding="utf-8") == original


def test_v057_upgrade_adds_access_controls_only_to_exact_shipped_profiles(
    tmp_path,
):
    current = builtin_profile_allow_lists(current_application_catalogue())
    previous = {
        name: {
            "allow": sorted(
                set(commands)
                - {"access.reduce", "access.request", "access.status"}
            ),
            "label": f"{name} profile",
        }
        for name, commands in current.items()
    }
    config_path = tmp_path / ".brain" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        dump_yaml_text({"vault": {"profiles": previous}}),
        encoding="utf-8",
    )

    result = migrate_to_0_57_0.migrate(str(tmp_path))
    migrated = load_mapping_file(config_path)

    assert result["status"] == "ok"
    assert result["profiles"] == [
        "reader",
        "contributor",
        "maintainer",
        "operator",
        "administrator",
    ]
    assert {
        name: tuple(definition["allow"])
        for name, definition in migrated["vault"]["profiles"].items()
    } == current


def test_v057_upgrade_does_not_widen_custom_profiles(tmp_path):
    config_path = tmp_path / ".brain" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        dump_yaml_text(
            {
                "vault": {
                    "profiles": {
                        "custom": {
                            "allow": ["artefact.read", "session.start"],
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = migrate_to_0_57_0.migrate(str(tmp_path))

    assert result == {"status": "skipped", "profiles": []}
    assert load_mapping_file(config_path)["vault"]["profiles"]["custom"][
        "allow"
    ] == ["artefact.read", "session.start"]


def test_v059_upgrade_expands_exact_v058_builtins(tmp_path):
    current = builtin_profile_allow_lists(current_application_catalogue())
    previous = {
        name: {
            "allow": sorted(
                set(commands)
                - {
                    "document.replace-text",
                    "document.update-frontmatter",
                    "document.write-body",
                }
            ),
            "label": f"{name} profile",
        }
        for name, commands in current.items()
    }
    config_path = tmp_path / ".brain" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        dump_yaml_text({"vault": {"profiles": previous}}),
        encoding="utf-8",
    )

    result = migrate_to_0_59_0.migrate(str(tmp_path))
    migrated = load_mapping_file(config_path)

    assert result["status"] == "ok"
    assert result["profiles"] == [
        name for name, commands in current.items()
        if "document.structured-edit" in commands
    ]
    assert {
        name: tuple(definition["allow"])
        for name, definition in migrated["vault"]["profiles"].items()
    } == current


def test_v059_upgrade_expands_an_explicit_custom_document_edit_grant(tmp_path):
    config_path = tmp_path / ".brain" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        dump_yaml_text(
            {
                "vault": {
                    "profiles": {
                        "author": {"allow": ["document.edit"]},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = migrate_to_0_59_0.migrate(str(tmp_path))

    assert result == {
        "status": "ok",
        "profiles": ["author"],
        "strategies": {"author": "custom"},
    }
    assert load_mapping_file(config_path)["vault"]["profiles"]["author"][
        "allow"
    ] == [
        "document.replace-text",
        "document.structured-edit",
        "document.update-frontmatter",
        "document.write-body",
        "invocation.read",
    ]
