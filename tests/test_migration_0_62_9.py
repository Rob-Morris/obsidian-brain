from __future__ import annotations

import copy
from pathlib import Path

import pytest

import migrate_to_0_62_9
from _command_interface.profile_migration import ProfileMigrationError
from _common._yaml import dump_yaml_text, load_mapping_file


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = Path(".brain/config.yaml")
REVERSE_DOCUMENT_COMMANDS = {
    "document.structured-edit": "document.edit",
    "document.replace-text": "document.patch",
    "document.write-body": "document.write",
}


def _write_config(vault: Path, profiles: dict[str, object]) -> Path:
    path = vault / CONFIG_PATH
    path.parent.mkdir(parents=True)
    path.write_text(
        dump_yaml_text({"vault": {"profiles": profiles}}),
        encoding="utf-8",
    )
    return path


def test_migrates_v0_62_8_builtins_to_current_defaults(tmp_path: Path) -> None:
    defaults = load_mapping_file(REPO_ROOT / "src/brain-core/defaults/config.yaml")
    current_profiles = defaults["vault"]["profiles"]
    previous_profiles = copy.deepcopy(current_profiles)
    for definition in previous_profiles.values():
        definition["allow"] = [
            REVERSE_DOCUMENT_COMMANDS.get(tool, tool) for tool in definition["allow"]
        ]
    config_path = _write_config(tmp_path, previous_profiles)

    result = migrate_to_0_62_9.migrate(str(tmp_path))

    changed_profiles = [
        profile
        for profile, definition in current_profiles.items()
        if any(tool in REVERSE_DOCUMENT_COMMANDS for tool in definition["allow"])
    ]
    assert result == {
        "status": "ok",
        "profiles": changed_profiles,
        "strategies": {profile: "rename" for profile in changed_profiles},
    }
    assert load_mapping_file(config_path)["vault"]["profiles"] == current_profiles
    assert migrate_to_0_62_9.migrate(str(tmp_path)) == {
        "status": "skipped",
        "profiles": [],
    }


def test_custom_profile_renames_each_grant_without_widening(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path,
        {
            "author": {
                "label": "Author",
                "allow": [
                    "artefact.read",
                    "document.edit",
                    "document.patch",
                    "document.write",
                    "invocation.read",
                ],
            }
        },
    )

    result = migrate_to_0_62_9.migrate(str(tmp_path))

    assert result["profiles"] == ["author"]
    assert load_mapping_file(config_path)["vault"]["profiles"]["author"]["allow"] == [
        "artefact.read",
        "document.structured-edit",
        "document.replace-text",
        "document.write-body",
        "invocation.read",
    ]


def test_unknown_grant_fails_before_writing(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path,
        {"author": {"allow": ["document.edit", "unknown.command"]}},
    )
    before = config_path.read_bytes()

    with pytest.raises(ProfileMigrationError, match="unknown tool 'unknown.command'"):
        migrate_to_0_62_9.migrate(str(tmp_path))

    assert config_path.read_bytes() == before
