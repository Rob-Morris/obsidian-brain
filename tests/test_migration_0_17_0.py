"""Tests for migrations/migrate_to_0_17_0.py — preferences.json → config.yaml."""

import json
import os
import sys

import pytest
import yaml

# Add scripts and migrations dirs to path
SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "src", "brain-core", "scripts")
MIGRATIONS_DIR = os.path.join(SCRIPTS_DIR, "migrations")
sys.path.insert(0, os.path.abspath(SCRIPTS_DIR))
sys.path.insert(0, os.path.abspath(MIGRATIONS_DIR))

from migrate_to_0_17_0 import migrate


@pytest.fixture
def vault(tmp_path):
    """Minimal vault with .brain/ directory."""
    brain = tmp_path / ".brain"
    brain.mkdir()
    (brain / "local").mkdir()
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.17.0\n")
    (bc / "session-core.md").write_text("# Session Core\n")
    return tmp_path


def test_migrate_no_preferences(vault):
    """No preferences.json → skip."""
    result = migrate(str(vault))
    assert result["status"] == "skipped"
    assert result["actions"] == []


def test_migrate_empty_preferences(vault):
    """Empty preferences.json → delete, no config.yaml created."""
    (vault / ".brain" / "preferences.json").write_text("{}")

    result = migrate(str(vault))
    assert result["status"] == "ok"
    assert not (vault / ".brain" / "preferences.json").exists()
    assert not (vault / ".brain" / "config.yaml").exists()


def test_migrate_nonempty_preferences(vault):
    """Non-empty preferences → create config.yaml, delete preferences.json."""
    prefs = {
        "artefact_sync_exclude": ["temporal/cookies", "living/wiki"],
        "artefact_sync": "skip",
    }
    (vault / ".brain" / "preferences.json").write_text(json.dumps(prefs))

    result = migrate(str(vault))
    assert result["status"] == "ok"

    # preferences.json deleted
    assert not (vault / ".brain" / "preferences.json").exists()

    # config.yaml created with migrated values
    config_path = vault / ".brain" / "config.yaml"
    assert config_path.exists()
    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    assert cfg["defaults"]["exclude"]["artefact_sync"] == ["temporal/cookies", "living/wiki"]
    assert cfg["defaults"]["artefact_sync"] == "skip"


def test_migrate_preferences_exclude_only(vault):
    """Preferences with only exclude list → config.yaml has exclude only."""
    prefs = {"artefact_sync_exclude": ["temporal/cookies"]}
    (vault / ".brain" / "preferences.json").write_text(json.dumps(prefs))

    result = migrate(str(vault))
    assert result["status"] == "ok"

    config_path = vault / ".brain" / "config.yaml"
    assert config_path.exists()
    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    assert cfg["defaults"]["exclude"]["artefact_sync"] == ["temporal/cookies"]
    # No artefact_sync key (it was default "auto")
    assert "artefact_sync" not in cfg["defaults"]


def test_migrate_idempotent(vault):
    """config.yaml already exists → just delete preferences.json."""
    (vault / ".brain" / "preferences.json").write_text(json.dumps({"artefact_sync": "skip"}))
    (vault / ".brain" / "config.yaml").write_text("vault:\n  brain_name: rob\n")

    result = migrate(str(vault))
    assert result["status"] == "ok"

    # preferences.json deleted
    assert not (vault / ".brain" / "preferences.json").exists()

    # config.yaml unchanged (not overwritten)
    with open(vault / ".brain" / "config.yaml") as f:
        content = f.read()
    assert "brain_name: rob" in content
