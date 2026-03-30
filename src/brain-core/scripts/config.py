#!/usr/bin/env python3
"""
config.py — Brain vault configuration loader.

Reads .brain/config.yaml (shared, committed) and .brain/local/config.yaml
(machine-local, gitignored), merges them with shipped template defaults,
and returns a typed dict.

Two-zone merge model:
  vault:    shared authority — local config cannot override
  defaults: local can customise — merge follows from data type
    Scalars:  local wins if present
    Booleans: either-true wins
    Lists:    additive (union)

Requires PyYAML (server dependency — see mcp/requirements.txt).

Usage:
    from config import load_config
    cfg = load_config("/path/to/vault")
"""

import hashlib
import os
import sys
import warnings

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

CONFIG_YAML = os.path.join(".brain", "config.yaml")
LOCAL_CONFIG_YAML = os.path.join(".brain", "local", "config.yaml")

# All valid MCP tool names (for profile validation)
_VALID_TOOLS = frozenset([
    "brain_session", "brain_read", "brain_search",
    "brain_create", "brain_edit", "brain_process", "brain_action",
])


# ---------------------------------------------------------------------------
# Template discovery
# ---------------------------------------------------------------------------

def _find_template() -> str:
    """Locate defaults/config.yaml relative to this script's location.

    Works both from the dev repo (src/brain-core/scripts/ → src/brain-core/defaults/)
    and from an installed vault (.brain-core/scripts/ → .brain-core/defaults/).
    """
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(scripts_dir)
    template = os.path.join(parent, "defaults", "config.yaml")
    if os.path.isfile(template):
        return template
    raise FileNotFoundError(
        f"Config template not found at {template}. "
        "Ensure defaults/config.yaml is shipped with brain-core."
    )


def _read_yaml(path: str) -> dict:
    """Read a YAML file and return parsed dict. Returns {} if file missing."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except yaml.YAMLError as e:
        warnings.warn(f"config: failed to parse {path}: {e}")
        return {}


# ---------------------------------------------------------------------------
# Merge logic
# ---------------------------------------------------------------------------

def _merge_defaults(base: dict, overlay: dict) -> dict:
    """Merge overlay into base using type-based rules for the defaults zone.

    Scalars:  overlay wins if present
    Booleans: either-true wins
    Lists:    additive (union, preserving order, deduplicating)
    Dicts:    recursive merge
    """
    result = dict(base)
    for key, overlay_val in overlay.items():
        if key not in result:
            result[key] = overlay_val
            continue

        base_val = result[key]

        # Dict: recurse
        if isinstance(base_val, dict) and isinstance(overlay_val, dict):
            result[key] = _merge_defaults(base_val, overlay_val)
        # List: additive (union)
        elif isinstance(base_val, list) and isinstance(overlay_val, list):
            seen = set()
            merged = []
            for item in base_val + overlay_val:
                if item not in seen:
                    seen.add(item)
                    merged.append(item)
            result[key] = merged
        # Bool: either-true
        elif isinstance(base_val, bool) and isinstance(overlay_val, bool):
            result[key] = base_val or overlay_val
        # Scalar: overlay wins
        else:
            result[key] = overlay_val

    return result


def _merge_config(template: dict, vault_cfg: dict, local_cfg: dict) -> dict:
    """Apply two-zone merge rules.

    vault zone:    template, then vault overrides. Local vault keys ignored.
    defaults zone: template, then vault, then local with type-based merge.
    """
    result = {}

    # --- vault zone: shared authoritative ---
    base_vault = template.get("vault", {})
    vault_overlay = vault_cfg.get("vault", {})
    # Simple deep merge for vault (vault config overrides template)
    result["vault"] = _deep_merge(base_vault, vault_overlay)

    # Warn if local config tries to override vault zone
    local_vault = local_cfg.get("vault")
    if local_vault:
        warnings.warn(
            "config: local config contains 'vault' keys which are ignored. "
            "The vault zone is shared-authoritative."
        )

    # --- defaults zone: type-based merge ---
    base_defaults = template.get("defaults", {})
    vault_defaults = vault_cfg.get("defaults", {})
    local_defaults = local_cfg.get("defaults", {})

    # Layer 1: template + vault defaults
    merged_defaults = _merge_defaults(base_defaults, vault_defaults)
    # Layer 2: + local defaults
    merged_defaults = _merge_defaults(merged_defaults, local_defaults)
    result["defaults"] = merged_defaults

    return result


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Simple recursive dict merge (overlay wins for scalars)."""
    result = dict(base)
    for key, val in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_config(config: dict) -> list[str]:
    """Validate merged config. Returns list of warning strings."""
    warns = []
    vault = config.get("vault", {})
    defaults = config.get("defaults", {})

    # Validate profile tool names
    profiles = vault.get("profiles", {})
    for profile_name, profile_def in profiles.items():
        if not isinstance(profile_def, dict):
            warns.append(f"profile '{profile_name}' is not a dict")
            continue
        allow = profile_def.get("allow", [])
        for tool in allow:
            if tool not in _VALID_TOOLS:
                warns.append(
                    f"profile '{profile_name}' references unknown tool '{tool}'"
                )

    # Validate operator profile references
    operators = vault.get("operators", [])
    for op in operators:
        if not isinstance(op, dict):
            continue
        op_profile = op.get("profile", "")
        if op_profile and op_profile not in profiles:
            warns.append(
                f"operator '{op.get('id', '?')}' references "
                f"unknown profile '{op_profile}'"
            )

    # Validate default_profile exists
    default_profile = defaults.get("default_profile", "")
    if default_profile and default_profile not in profiles:
        warns.append(
            f"default_profile '{default_profile}' does not exist in profiles"
        )

    return warns


# ---------------------------------------------------------------------------
# Authentication helpers
# ---------------------------------------------------------------------------

def hash_key(key: str) -> str:
    """SHA-256 hash of an operator key, formatted as 'sha256:<hexdigest>'."""
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def authenticate_operator(key: str | None, config: dict) -> tuple[str, str | None]:
    """Match an operator key to a profile.

    Returns (profile_name, operator_id).
    If key is None, returns the default profile with no operator id.
    Raises ValueError if key is provided but doesn't match any operator.
    """
    defaults = config.get("defaults", {})
    default_profile = defaults.get("default_profile", "operator")

    if key is None:
        return (default_profile, None)

    key_hash = hash_key(key)
    operators = config.get("vault", {}).get("operators", [])

    for op in operators:
        if not isinstance(op, dict):
            continue
        auth = op.get("auth", {})
        if not isinstance(auth, dict):
            continue
        if auth.get("type") == "key" and auth.get("hash") == key_hash:
            return (op.get("profile", default_profile), op.get("id"))

    raise ValueError("operator key does not match any registered operator")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_config(vault_root: str) -> dict:
    """Load and merge vault configuration.

    Three-layer merge: shipped template → .brain/config.yaml → .brain/local/config.yaml.
    Validates the result and emits warnings for issues.
    Returns the merged config dict.
    """
    # Layer 0: shipped template defaults
    template_path = _find_template()
    template = _read_yaml(template_path)

    # Layer 1: vault config (shared, committed)
    vault_path = os.path.join(vault_root, CONFIG_YAML)
    vault_cfg = _read_yaml(vault_path)

    # Layer 2: local config (machine-specific, gitignored)
    local_path = os.path.join(vault_root, LOCAL_CONFIG_YAML)
    local_cfg = _read_yaml(local_path)

    # Merge
    merged = _merge_config(template, vault_cfg, local_cfg)

    # Validate
    issues = _validate_config(merged)
    for issue in issues:
        warnings.warn(f"config: {issue}")

    return merged


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    from _common import find_vault_root

    vault_root = str(find_vault_root())
    cfg = load_config(vault_root)
    print(json.dumps(cfg, indent=2, ensure_ascii=False))
