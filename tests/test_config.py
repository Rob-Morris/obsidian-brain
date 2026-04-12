"""Tests for scripts/config.py — vault configuration loader."""

import json
import os
import sys
import warnings

import pytest
import yaml

# Add scripts dir to path
SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "src", "brain-core", "scripts")
sys.path.insert(0, os.path.abspath(SCRIPTS_DIR))

import config as config_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def vault(tmp_path):
    """Minimal vault with .brain-core/VERSION and defaults template."""
    bc = tmp_path / ".brain-core"
    bc.mkdir()
    (bc / "VERSION").write_text("0.17.0\n")
    (bc / "session-core.md").write_text("# Session Core\n")

    # Copy the real template into the vault's brain-core/defaults/
    defaults_dir = bc / "defaults"
    defaults_dir.mkdir()
    real_template = os.path.join(
        os.path.dirname(__file__), "..", "src", "brain-core", "defaults", "config.yaml"
    )
    with open(real_template, "r") as f:
        template_content = f.read()
    (defaults_dir / "config.yaml").write_text(template_content)

    # Ensure .brain/ and .brain/local/ exist
    (tmp_path / ".brain").mkdir()
    (tmp_path / ".brain" / "local").mkdir()

    return tmp_path


def _write_vault_config(vault, data):
    """Write a vault-level config.yaml."""
    path = vault / ".brain" / "config.yaml"
    with open(path, "w") as f:
        yaml.safe_dump(data, f, default_flow_style=False)


def _write_local_config(vault, data):
    """Write a machine-local config.yaml."""
    path = vault / ".brain" / "local" / "config.yaml"
    with open(path, "w") as f:
        yaml.safe_dump(data, f, default_flow_style=False)


# ---------------------------------------------------------------------------
# Template / load tests
# ---------------------------------------------------------------------------

def test_load_config_template_only(vault, monkeypatch):
    """No vault or local config — template defaults apply."""
    # Point template discovery at the vault's copy
    monkeypatch.setattr(config_mod, "_find_template",
                        lambda: str(vault / ".brain-core" / "defaults" / "config.yaml"))

    cfg = config_mod.load_config(str(vault))

    assert cfg["vault"]["brain_name"] == ""
    assert "reader" in cfg["vault"]["profiles"]
    assert "contributor" in cfg["vault"]["profiles"]
    assert "operator" in cfg["vault"]["profiles"]
    assert cfg["vault"]["operators"] == []
    assert cfg["defaults"]["default_profile"] == "operator"
    assert cfg["defaults"]["flags"] == {}
    assert cfg["defaults"]["exclude"]["artefact_sync"] == []


def test_load_config_vault_override(vault, monkeypatch):
    """Vault config overrides template values."""
    monkeypatch.setattr(config_mod, "_find_template",
                        lambda: str(vault / ".brain-core" / "defaults" / "config.yaml"))

    _write_vault_config(vault, {
        "vault": {"brain_name": "rob"},
        "defaults": {"default_profile": "reader"},
    })

    cfg = config_mod.load_config(str(vault))
    assert cfg["vault"]["brain_name"] == "rob"
    assert cfg["defaults"]["default_profile"] == "reader"
    # Profiles still present from template
    assert "operator" in cfg["vault"]["profiles"]


def test_load_config_full_three_layer(vault, monkeypatch):
    """All three layers merge correctly."""
    monkeypatch.setattr(config_mod, "_find_template",
                        lambda: str(vault / ".brain-core" / "defaults" / "config.yaml"))

    _write_vault_config(vault, {
        "vault": {"brain_name": "rob"},
        "defaults": {"default_profile": "reader"},
    })
    _write_local_config(vault, {
        "defaults": {"default_profile": "contributor"},
    })

    cfg = config_mod.load_config(str(vault))
    assert cfg["vault"]["brain_name"] == "rob"
    # Local overrides vault for defaults scalar
    assert cfg["defaults"]["default_profile"] == "contributor"


# ---------------------------------------------------------------------------
# Merge rule tests
# ---------------------------------------------------------------------------

def test_merge_defaults_scalar_override():
    """Local scalar wins over base."""
    base = {"default_profile": "reader"}
    overlay = {"default_profile": "contributor"}
    result = config_mod._merge_defaults(base, overlay)
    assert result["default_profile"] == "contributor"


def test_merge_defaults_boolean_either_true():
    """Either-true: if either side says True, result is True."""
    # base false, overlay true → true
    result = config_mod._merge_defaults(
        {"flags": {"suppress_cli_prompt": False}},
        {"flags": {"suppress_cli_prompt": True}},
    )
    assert result["flags"]["suppress_cli_prompt"] is True

    # base true, overlay false → true (either-true)
    result = config_mod._merge_defaults(
        {"flags": {"suppress_cli_prompt": True}},
        {"flags": {"suppress_cli_prompt": False}},
    )
    assert result["flags"]["suppress_cli_prompt"] is True


def test_merge_defaults_list_additive():
    """Lists are unioned, preserving order, deduplicating."""
    base = {"exclude": {"artefact_sync": ["type-a", "type-b"]}}
    overlay = {"exclude": {"artefact_sync": ["type-b", "type-c"]}}
    result = config_mod._merge_defaults(base, overlay)
    assert result["exclude"]["artefact_sync"] == ["type-a", "type-b", "type-c"]


def test_merge_vault_zone_ignored(vault, monkeypatch):
    """Local config vault keys are ignored with a warning."""
    monkeypatch.setattr(config_mod, "_find_template",
                        lambda: str(vault / ".brain-core" / "defaults" / "config.yaml"))

    _write_local_config(vault, {
        "vault": {"brain_name": "hacked"},
        "defaults": {"default_profile": "contributor"},
    })

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        cfg = config_mod.load_config(str(vault))

    # brain_name unchanged (from template, not local)
    assert cfg["vault"]["brain_name"] == ""
    # defaults still applied
    assert cfg["defaults"]["default_profile"] == "contributor"
    # Warning emitted
    vault_warns = [x for x in w if "vault" in str(x.message).lower()]
    assert len(vault_warns) >= 1


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------

def test_validate_config_bad_profile_tool():
    """Unknown tool name in profile flagged."""
    cfg = {
        "vault": {
            "profiles": {
                "bad": {"allow": ["brain_read", "nonexistent_tool"]},
            },
            "operators": [],
        },
        "defaults": {"default_profile": "bad"},
    }
    issues = config_mod._validate_config(cfg)
    assert any("nonexistent_tool" in i for i in issues)


def test_validate_config_bad_operator_profile():
    """Operator referencing nonexistent profile flagged."""
    cfg = {
        "vault": {
            "profiles": {"reader": {"allow": ["brain_read"]}},
            "operators": [{"id": "test", "profile": "nonexistent"}],
        },
        "defaults": {"default_profile": "reader"},
    }
    issues = config_mod._validate_config(cfg)
    assert any("nonexistent" in i for i in issues)


def test_validate_config_bad_default_profile():
    """default_profile referencing nonexistent profile flagged."""
    cfg = {
        "vault": {
            "profiles": {"reader": {"allow": ["brain_read"]}},
            "operators": [],
        },
        "defaults": {"default_profile": "nonexistent"},
    }
    issues = config_mod._validate_config(cfg)
    assert any("nonexistent" in i for i in issues)


def test_validate_config_clean():
    """Valid config produces no warnings."""
    cfg = {
        "vault": {
            "profiles": {
                "reader": {"allow": ["brain_session", "brain_read", "brain_search"]},
            },
            "operators": [
                {"id": "test", "profile": "reader", "auth": {"type": "key", "hash": "sha256:abc"}},
            ],
        },
        "defaults": {"default_profile": "reader"},
    }
    issues = config_mod._validate_config(cfg)
    assert issues == []


# ---------------------------------------------------------------------------
# Authentication tests
# ---------------------------------------------------------------------------

def test_hash_key():
    """SHA-256 format is correct."""
    result = config_mod.hash_key("timber-compass-violet")
    assert result.startswith("sha256:")
    assert len(result) == len("sha256:") + 64  # hex digest is 64 chars


def test_hash_key_deterministic():
    """Same input produces same hash."""
    assert config_mod.hash_key("test") == config_mod.hash_key("test")


def test_authenticate_operator_match():
    """Key matches registered operator, returns profile and id."""
    key = "timber-compass-violet"
    cfg = {
        "vault": {
            "operators": [
                {
                    "id": "robs-claude",
                    "profile": "operator",
                    "auth": {"type": "key", "hash": config_mod.hash_key(key)},
                },
            ],
        },
        "defaults": {"default_profile": "reader"},
    }
    profile, op_id = config_mod.authenticate_operator(key, cfg)
    assert profile == "operator"
    assert op_id == "robs-claude"


def test_authenticate_operator_no_match():
    """Wrong key raises ValueError."""
    cfg = {
        "vault": {
            "operators": [
                {
                    "id": "robs-claude",
                    "profile": "operator",
                    "auth": {"type": "key", "hash": "sha256:wrong"},
                },
            ],
        },
        "defaults": {"default_profile": "reader"},
    }
    with pytest.raises(ValueError, match="does not match"):
        config_mod.authenticate_operator("bad-key", cfg)


def test_authenticate_operator_no_key():
    """No key returns default profile with no operator id."""
    cfg = {
        "vault": {"operators": []},
        "defaults": {"default_profile": "reader"},
    }
    profile, op_id = config_mod.authenticate_operator(None, cfg)
    assert profile == "reader"
    assert op_id is None


def test_authenticate_operator_no_operators():
    """Key provided but no operators configured — raises ValueError."""
    cfg = {
        "vault": {"operators": []},
        "defaults": {"default_profile": "operator"},
    }
    with pytest.raises(ValueError, match="does not match"):
        config_mod.authenticate_operator("some-key", cfg)
