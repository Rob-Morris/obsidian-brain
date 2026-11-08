"""Semantic config and local-runtime flag helpers."""

from __future__ import annotations

import importlib
import os

from _bootstrap import branch_guard
from _bootstrap.local_config import LocalConfigError, patch_local_config
from _common._yaml import YamlError


LOCAL_CONFIG_REL = os.path.join(".brain", "local", "config.yaml")
SEMANTIC_PROCESSING_FLAG = "semantic_processing"
SEMANTIC_RETRIEVAL_FLAG = "semantic_retrieval"
SEMANTIC_ENGINE_INSTALLED_FLAG = "semantic_engine_installed"


class SemanticConfigLoadError(RuntimeError):
    """Raised when semantic config probing hits a real config load failure."""


def load_config_checked(vault_root, config=None):
    """Load vault config or raise when the config layer is broken."""
    if config is not None:
        return config
    try:
        config_mod = importlib.import_module("config")
    except ImportError as exc:
        raise SemanticConfigLoadError(f"failed to import config module: {exc}") from exc
    try:
        return config_mod.load_config(str(vault_root))
    except (FileNotFoundError, OSError, ValueError, YamlError) as exc:
        raise SemanticConfigLoadError(f"failed to load config: {exc}") from exc


def _read_nested_flag(vault_root, section, flag_name, *, config=None):
    """Return a boolean from defaults.<section>.<flag_name>."""
    config = load_config_checked(vault_root, config=config)
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


def is_semantic_intent_active(vault_root, *, config=None):
    """Return True when the vault expects a working semantic stack."""
    return (
        semantic_retrieval_enabled(vault_root, config=config)
        or semantic_processing_enabled(vault_root, config=config)
        or semantic_engine_installed(vault_root, config=config)
    )


def semantic_engine_installed(vault_root, *, config=None):
    """Return True when the local environment was provisioned for semantic work."""
    return _read_nested_flag(
        vault_root,
        "local_runtime",
        SEMANTIC_ENGINE_INSTALLED_FLAG,
        config=config,
    )


def set_semantic_engine_installed(vault_root, installed=True, *, lock_held=False):
    """Write the local semantic-engine provisioning marker if this is a vault."""
    return _update_local_semantic_config(
        vault_root,
        local_runtime_updates={SEMANTIC_ENGINE_INSTALLED_FLAG: bool(installed)},
        lock_held=lock_held,
    )


def set_semantic_retrieval_enabled(vault_root, enabled=True, *, lock_held=False):
    """Write the local semantic retrieval flag for this vault."""
    return _update_local_semantic_config(
        vault_root,
        flag_updates={SEMANTIC_RETRIEVAL_FLAG: bool(enabled)},
        lock_held=lock_held,
    )


def set_semantic_flags(vault_root, *, retrieval=None, processing=None, lock_held=False):
    """Write local semantic feature flags, leaving any None-valued flag unchanged."""
    flag_updates = {}
    if retrieval is not None:
        flag_updates[SEMANTIC_RETRIEVAL_FLAG] = bool(retrieval)
    if processing is not None:
        flag_updates[SEMANTIC_PROCESSING_FLAG] = bool(processing)
    if not flag_updates:
        return False
    return _update_local_semantic_config(vault_root, flag_updates=flag_updates, lock_held=lock_held)


def _update_local_semantic_config(
    vault_root,
    *,
    flag_updates=None,
    local_runtime_updates=None,
    lock_held=False,
):
    """Patch fresh local settings, guarding authored flags but not support markers.

    Lifecycle callers holding the shared mutation lock (including in a parent
    interpreter) pass ``lock_held=True`` rather than acquiring it a second time.
    """
    if not os.path.isdir(os.path.join(str(vault_root), ".brain")):
        return False
    changed = False

    def update(data):
        nonlocal changed
        defaults = data.get("defaults")
        if not isinstance(defaults, dict):
            defaults = {}
            data["defaults"] = defaults
        for section, updates in (("flags", flag_updates), ("local_runtime", local_runtime_updates)):
            if not updates:
                continue
            values = defaults.get(section)
            if not isinstance(values, dict):
                values = {}
                defaults[section] = values
            for key, value in updates.items():
                if values.get(key) != value:
                    values[key] = value
                    changed = True

    try:
        # Capture before reading or planning settings; the callback runs while
        # the central patch owner holds the shared vault lock.
        with branch_guard.operation(vault_root):
            patch_local_config(
                vault_root, update, lock_held=lock_held,
                before_write=(lambda: branch_guard.checkpoint(vault_root)) if flag_updates else None,
            )
    except LocalConfigError as exc:
        raise SemanticConfigLoadError(f"failed to load local semantic config: {exc}") from exc
    return changed
