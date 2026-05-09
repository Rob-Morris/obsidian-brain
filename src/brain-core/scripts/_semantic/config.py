"""Semantic config and local-runtime flag helpers."""

from __future__ import annotations

import os

from _common import safe_write_via


LOCAL_CONFIG_REL = os.path.join(".brain", "local", "config.yaml")
SEMANTIC_PROCESSING_FLAG = "semantic_processing"
SEMANTIC_RETRIEVAL_FLAG = "semantic_retrieval"
SEMANTIC_ENGINE_INSTALLED_FLAG = "semantic_engine_installed"


class SemanticConfigLoadError(RuntimeError):
    """Raised when semantic config probing hits a real config load failure."""


def load_config_best_effort(vault_root, config=None):
    """Best-effort config loader used by semantic feature-policy helpers."""
    if config is not None:
        return config
    try:
        import config as config_mod
    except ImportError:
        return None
    try:
        return config_mod.load_config(str(vault_root))
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise SemanticConfigLoadError(f"failed to load config: {exc}") from exc


def _read_nested_flag(vault_root, section, flag_name, *, config=None):
    """Return a boolean from defaults.<section>.<flag_name>."""
    config = load_config_best_effort(vault_root, config=config)
    if not isinstance(config, dict):
        return False
    flags = config.get("defaults", {}).get(section, {})
    if not isinstance(flags, dict):
        return False
    return bool(flags.get(flag_name, False))


def semantic_processing_enabled(vault_root, *, config=None):
    """Return True when embedding-backed processing is enabled."""
    return _read_nested_flag(vault_root, "flags", SEMANTIC_PROCESSING_FLAG, config=config)


def semantic_retrieval_enabled(vault_root, *, config=None):
    """Return True when semantic retrieval is enabled for search."""
    return _read_nested_flag(vault_root, "flags", SEMANTIC_RETRIEVAL_FLAG, config=config)


def embeddings_enabled(vault_root, *, config=None):
    """Return True when any embedding-backed feature is enabled."""
    return (
        semantic_processing_enabled(vault_root, config=config)
        or semantic_retrieval_enabled(vault_root, config=config)
    )


def semantic_engine_installed(vault_root, *, config=None):
    """Return True when the local environment was provisioned for semantic work."""
    return _read_nested_flag(
        vault_root,
        "local_runtime",
        SEMANTIC_ENGINE_INSTALLED_FLAG,
        config=config,
    )


def set_semantic_engine_installed(vault_root, installed=True):
    """Write the local semantic-engine provisioning marker if this is a vault."""
    return _update_local_semantic_config(
        vault_root,
        local_runtime_updates={SEMANTIC_ENGINE_INSTALLED_FLAG: bool(installed)},
    )


def set_semantic_retrieval_enabled(vault_root, enabled=True):
    """Write the local semantic retrieval flag for this vault."""
    return _update_local_semantic_config(
        vault_root,
        flag_updates={SEMANTIC_RETRIEVAL_FLAG: bool(enabled)},
    )


def set_semantic_flags(vault_root, *, retrieval=None, processing=None):
    """Write local semantic feature flags, leaving any None-valued flag unchanged."""
    flag_updates = {}
    if retrieval is not None:
        flag_updates[SEMANTIC_RETRIEVAL_FLAG] = bool(retrieval)
    if processing is not None:
        flag_updates[SEMANTIC_PROCESSING_FLAG] = bool(processing)
    if not flag_updates:
        return False
    return _update_local_semantic_config(vault_root, flag_updates=flag_updates)


def _update_local_semantic_config(
    vault_root,
    *,
    flag_updates=None,
    local_runtime_updates=None,
):
    """Persist semantic local-config updates if this is a vault."""
    import yaml

    vault_root = str(vault_root)
    if not os.path.isdir(os.path.join(vault_root, ".brain")):
        return False

    config_path = os.path.join(vault_root, LOCAL_CONFIG_REL)
    os.makedirs(os.path.dirname(config_path), exist_ok=True)

    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except FileNotFoundError:
        data = {}
    if not isinstance(data, dict):
        data = {}

    defaults = data.get("defaults")
    if not isinstance(defaults, dict):
        defaults = {}
        data["defaults"] = defaults

    changed = False

    if flag_updates:
        flags = defaults.get("flags")
        if not isinstance(flags, dict):
            flags = {}
            defaults["flags"] = flags
            changed = True
        for key, value in flag_updates.items():
            if flags.get(key) != value:
                flags[key] = value
                changed = True

    if local_runtime_updates:
        local_runtime = defaults.get("local_runtime")
        if not isinstance(local_runtime, dict):
            local_runtime = {}
            defaults["local_runtime"] = local_runtime
            changed = True
        for key, value in local_runtime_updates.items():
            if local_runtime.get(key) != value:
                local_runtime[key] = value
                changed = True

    if not changed:
        return False

    safe_write_via(
        config_path,
        lambda handle: yaml.safe_dump(data, handle, sort_keys=False),
        mode="w",
    )
    return True
