#!/usr/bin/env python3
"""Launcher-safe Brain vault discovery helpers."""

from __future__ import annotations

import os
from pathlib import Path

from _common import find_vault_root as _find_common_vault_root
from _common import is_vault_root


def find_vault_root(vault_arg: str | None = None) -> Path:
    """Resolve a Brain vault root from arg, env, cwd, or script location."""
    if vault_arg:
        return _find_common_vault_root(vault_arg)

    env_root = os.environ.get("BRAIN_VAULT_ROOT")
    if env_root:
        candidate = Path(env_root).resolve()
        if is_vault_root(candidate):
            return candidate

    return _find_common_vault_root()
