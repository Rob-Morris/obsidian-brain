#!/usr/bin/env python3
"""Migrate shared MCP profiles to the v0.55 command catalogue once."""

from __future__ import annotations

import os

from _application.registry import current_application_catalogue
from _command_interface.profile_migration import migrate_profile_allow_lists
from _common import BOOTSTRAP_VARIANTS, find_root_bootstrap_file, safe_write
from _common._yaml import dump_yaml_text, load_mapping_file


VERSION = "0.55.0"
CONFIG_PATH = os.path.join(".brain", "config.yaml")
BOOTSTRAP_TEXT = (
    "ALWAYS DO FIRST: Call MCP `session.start`, else read "
    "`.brain-core/index.md` if it exists."
)
PROJECT_BOOTSTRAP_TEXT = (
    "ALWAYS DO FIRST: Call MCP `session.start`; if MCP is unavailable, run "
    "`brain session start --json` from this workspace."
)
BOOTSTRAP_REPLACEMENTS = (
    (
        "ALWAYS DO FIRST: Call brain_session. Read [[.brain-core/index]]",
        BOOTSTRAP_TEXT,
    ),
    (
        "ALWAYS DO FIRST: Call MCP `brain_session`, else read "
        "`.brain-core/index.md` if it exists",
        BOOTSTRAP_TEXT,
    ),
    (
        "ALWAYS DO FIRST: Call MCP `brain_session`, else read "
        "`.brain-core/index.md` if it exists.",
        BOOTSTRAP_TEXT,
    ),
    ("ALWAYS DO FIRST: Call MCP `brain_session`.", BOOTSTRAP_TEXT),
    (
        "ALWAYS DO FIRST: Call MCP `brain_session`; if MCP is unavailable, run "
        "`brain session --json` from this workspace.",
        PROJECT_BOOTSTRAP_TEXT,
    ),
)


def prospective_effects(vault_root: str) -> list[str]:
    """Declare exact profile/bootstrap files before the migration mutates them."""
    effects = [os.path.join(os.path.realpath(vault_root), CONFIG_PATH)]
    for canonical_name in BOOTSTRAP_VARIANTS:
        path = find_root_bootstrap_file(vault_root, canonical_name)
        if path is not None:
            effects.append(os.path.realpath(path))
    return effects


def migrate(vault_root: str) -> dict[str, object]:
    """Replace known v0.54 profile and bootstrap contracts fail-closed."""

    config_path = os.path.join(vault_root, CONFIG_PATH)
    config_update = None
    profile_changes = ()
    if os.path.isfile(config_path):
        config = load_mapping_file(config_path)
        vault = config.get("vault")
        if vault is not None:
            if not isinstance(vault, dict):
                raise ValueError(".brain/config.yaml vault must be a mapping")
            profiles = vault.get("profiles")
            if profiles is not None:
                if not isinstance(profiles, dict):
                    raise ValueError(
                        ".brain/config.yaml vault.profiles must be a mapping"
                    )

                # Validate and project the complete profile input before any
                # config or bootstrap file is changed.
                projected = migrate_profile_allow_lists(
                    profiles,
                    current_application_catalogue(),
                )
                profile_changes = projected.changes
                if profile_changes:
                    migrated = dict(config)
                    migrated_vault = dict(vault)
                    migrated_vault["profiles"] = projected.profiles
                    migrated["vault"] = migrated_vault
                    config_update = dump_yaml_text(migrated)

    bootstrap_updates = _bootstrap_updates(vault_root)
    if config_update is not None:
        safe_write(config_path, config_update, bounds=vault_root)
    for path, content in bootstrap_updates:
        safe_write(path, content, bounds=vault_root)

    if not profile_changes and not bootstrap_updates:
        return {"status": "skipped", "profiles": [], "bootstraps": []}
    return {
        "status": "ok",
        "profiles": [change.profile for change in profile_changes],
        "strategies": {
            change.profile: change.strategy for change in profile_changes
        },
        "bootstraps": [
            os.path.relpath(path, vault_root) for path, _content in bootstrap_updates
        ],
    }


def _bootstrap_updates(vault_root: str) -> list[tuple[str, str]]:
    """Project updates only for exact bootstrap text shipped by Brain."""

    updates = []
    root = os.path.realpath(vault_root)
    seen_paths = set()
    for canonical_name in BOOTSTRAP_VARIANTS:
        bootstrap = find_root_bootstrap_file(vault_root, canonical_name)
        if bootstrap is None:
            continue
        path = os.path.realpath(bootstrap)
        if path in seen_paths:
            continue
        seen_paths.add(path)
        if os.path.commonpath((root, path)) != root:
            raise ValueError("bootstrap file resolves outside the Brain vault")
        try:
            with open(path, "r", encoding="utf-8") as stream:
                content = stream.read()
        except OSError:
            continue
        updated = content
        for legacy, replacement in BOOTSTRAP_REPLACEMENTS:
            updated = updated.replace(legacy, replacement)
        if updated != content:
            updates.append((path, updated))
    return updates
