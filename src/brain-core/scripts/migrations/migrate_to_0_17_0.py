#!/usr/bin/env python3
"""
migrate_to_0_17_0.py — Migrate preferences.json to config.yaml.

If .brain/preferences.json has non-default values, writes them into
.brain/config.yaml under the defaults zone and deletes the old file.
Empty or default-only preferences files are simply deleted.

See migrate_to_0_17_0.md for the canary-format companion.
"""

import json
import os
from _common import safe_write

VERSION = "0.17.0"

PREFERENCES_PATH = os.path.join(".brain", "preferences.json")
CONFIG_PATH = os.path.join(".brain", "config.yaml")


def _load_preferences(vault_root):
    """Read .brain/preferences.json, returning {} if missing or empty."""
    path = os.path.join(vault_root, PREFERENCES_PATH)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _has_non_default_values(prefs):
    """Check if preferences contain any non-default values."""
    return bool(prefs)


def _serialize_config(config_data):
    """Hand-write YAML for the migrated config (no PyYAML dependency).

    Only needs to handle the known migration output shape:
    defaults.exclude.artefact_sync (list) and defaults.artefact_sync (string).
    """
    lines = []
    defaults = config_data.get("defaults", {})
    if not defaults:
        return ""
    lines.append("defaults:")
    if "artefact_sync" in defaults:
        lines.append(f"  artefact_sync: {defaults['artefact_sync']}")
    exclude = defaults.get("exclude", {})
    if exclude:
        lines.append("  exclude:")
        sync_list = exclude.get("artefact_sync", [])
        if sync_list:
            lines.append("    artefact_sync:")
            for item in sync_list:
                lines.append(f"      - {item}")
    lines.append("")
    return "\n".join(lines)


def migrate(vault_root):
    """Migrate preferences.json to config.yaml.

    Returns dict with status and list of actions taken.
    """
    vault_root = str(vault_root)
    actions = []
    prefs_path = os.path.join(vault_root, PREFERENCES_PATH)
    config_path = os.path.join(vault_root, CONFIG_PATH)

    # Nothing to do if preferences.json doesn't exist
    if not os.path.exists(prefs_path):
        return {"status": "skipped", "actions": []}

    # Don't overwrite existing config.yaml
    if os.path.exists(config_path):
        # Clean up old preferences.json if config already exists
        os.remove(prefs_path)
        actions.append(f"deleted {PREFERENCES_PATH} (config.yaml already exists)")
        return {"status": "ok", "actions": actions}

    prefs = _load_preferences(vault_root)

    if _has_non_default_values(prefs):
        # Build config structure with preferences values under defaults
        config_data = {
            "defaults": {
                "exclude": {},
            },
        }

        # Migrate artefact_sync_exclude → defaults.exclude.artefact_sync
        exclude_list = prefs.get("artefact_sync_exclude", [])
        if exclude_list:
            config_data["defaults"]["exclude"]["artefact_sync"] = exclude_list

        # Migrate artefact_sync mode if non-default
        sync_mode = prefs.get("artefact_sync")
        if sync_mode and sync_mode != "auto":
            config_data["defaults"]["artefact_sync"] = sync_mode

        # Clean up empty nested dicts
        if not config_data["defaults"]["exclude"]:
            del config_data["defaults"]["exclude"]
        if not config_data["defaults"]:
            del config_data["defaults"]

        # Only write config if there's something to write
        if config_data:
            safe_write(config_path, _serialize_config(config_data), bounds=vault_root)
            actions.append(f"created {CONFIG_PATH} from {PREFERENCES_PATH}")

    # Delete old preferences file
    os.remove(prefs_path)
    actions.append(f"deleted {PREFERENCES_PATH}")

    if not actions:
        return {"status": "skipped", "actions": []}

    return {"status": "ok", "actions": actions}
