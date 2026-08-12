#!/usr/bin/env python3
"""Migrate exact shipped profiles to the v0.57 access-control surface."""

from __future__ import annotations

import os

from _application.registry import current_application_catalogue
from _command_interface.profile_migration import migrate_profile_allow_lists
from _common import safe_write
from _common._yaml import dump_yaml_text, load_mapping_file


VERSION = "0.57.0"
CONFIG_PATH = os.path.join(".brain", "config.yaml")


def migrate(vault_root: str) -> dict[str, object]:
    """Add access controls to exact built-ins without widening custom profiles."""

    config_path = os.path.join(vault_root, CONFIG_PATH)
    if not os.path.isfile(config_path):
        return {"status": "skipped", "profiles": []}

    config = load_mapping_file(config_path)
    vault = config.get("vault")
    if vault is None:
        return {"status": "skipped", "profiles": []}
    if not isinstance(vault, dict):
        raise ValueError(".brain/config.yaml vault must be a mapping")
    profiles = vault.get("profiles")
    if profiles is None:
        return {"status": "skipped", "profiles": []}
    if not isinstance(profiles, dict):
        raise ValueError(".brain/config.yaml vault.profiles must be a mapping")

    projected = migrate_profile_allow_lists(
        profiles,
        current_application_catalogue(),
    )
    if not projected.changes:
        return {"status": "skipped", "profiles": []}

    migrated = dict(config)
    migrated_vault = dict(vault)
    migrated_vault["profiles"] = projected.profiles
    migrated["vault"] = migrated_vault
    safe_write(config_path, dump_yaml_text(migrated), bounds=vault_root)
    return {
        "status": "ok",
        "profiles": [change.profile for change in projected.changes],
        "strategies": {
            change.profile: change.strategy for change in projected.changes
        },
    }
