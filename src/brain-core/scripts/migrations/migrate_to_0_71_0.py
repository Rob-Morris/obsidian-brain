#!/usr/bin/env python3
"""Remove the retired workspace.bind and workspace.register grants (DD-083).

Both commands are gone from the catalogue, and the authorisation loader rejects
an unknown grant in the profile it selects. They are dropped, never mapped:
workspace.setup replaces neither, and mapping would widen a profile that held
one without it. The retired set is the one the historical profile projections
already drop, so a vault upgraded from before 0.55 reaches the same result.
"""

from __future__ import annotations

import os

from _command_interface.profile_migration import RETIRED_COMMANDS
from _common import safe_write
from _common._yaml import dump_yaml_text, load_mapping_file


VERSION = "0.71.0"
SHARED_CONFIG = os.path.join(".brain", "config.yaml")
LOCAL_CONFIG = os.path.join(".brain", "local", "config.yaml")


def prospective_effects(vault_root: str) -> list[str]:
    """Declare the two configuration files the migration may rewrite."""
    return [os.path.join(vault_root, relative) for relative in (SHARED_CONFIG, LOCAL_CONFIG)]


def _without_retired(values):
    """Drop retired string grants; anything else is left for the loader to diagnose."""
    return [value for value in values if not (isinstance(value, str) and value in RETIRED_COMMANDS)]


def _strip(config: dict, label: str, *, vault_zone: bool) -> tuple[dict, list[str], list[str]]:
    """Return the config without the retired grants, the settings and the profiles that held one.

    Only the shared file's ``vault`` zone is merged, so only it is touched.
    """
    touched, profiles_touched = [], []
    migrated = dict(config)
    vault = config.get("vault")
    if vault_zone and isinstance(vault, dict) and isinstance(vault.get("profiles"), dict):
        profiles = {}
        for name, profile in vault["profiles"].items():
            if isinstance(profile, dict) and isinstance(profile.get("allow"), list):
                allow = _without_retired(profile["allow"])
                if allow != profile["allow"]:
                    profile = {**profile, "allow": allow}
                    touched.append(f"{label}: vault.profiles.{name}.allow")
                    profiles_touched.append(name)
            profiles[name] = profile
        migrated["vault"] = {**vault, "profiles": profiles}
    defaults = config.get("defaults")
    access = defaults.get("access") if isinstance(defaults, dict) else None
    if isinstance(access, dict):
        access = dict(access)
        initial = access.get("initial")
        if isinstance(initial, dict) and isinstance(initial.get("commands"), list):
            commands = _without_retired(initial["commands"])
            if commands != initial["commands"]:
                access["initial"] = {**initial, "commands": commands}
                touched.append(f"{label}: defaults.access.initial.commands")
        overrides = access.get("overrides")
        if isinstance(overrides, dict) and RETIRED_COMMANDS & set(overrides):
            # An emptied local mapping stays: removing it would let shared overrides apply.
            access["overrides"] = {command: value for command, value in overrides.items()
                                   if command not in RETIRED_COMMANDS}
            touched.append(f"{label}: defaults.access.overrides")
        migrated["defaults"] = {**defaults, "access": access}
    return migrated, touched, profiles_touched


def migrate(vault_root: str) -> dict[str, object]:
    """Drop the retired grants from the shared and local configuration files.

    ``profiles`` names the profiles whose allow-list changed, as earlier profile
    migrations report; ``settings`` names every changed setting by file.
    """
    settings, profiles = [], set()
    for relative in (SHARED_CONFIG, LOCAL_CONFIG):
        path = os.path.join(vault_root, relative)
        if not os.path.isfile(path):
            continue
        config = load_mapping_file(path)
        migrated, touched, profiles_touched = _strip(
            config, relative.replace(os.sep, "/"), vault_zone=relative == SHARED_CONFIG)
        if touched:
            safe_write(path, dump_yaml_text(migrated), bounds=vault_root)
            settings.extend(touched)
            profiles.update(profiles_touched)
    return {"status": "ok" if settings else "skipped", "profiles": sorted(profiles), "settings": settings}
