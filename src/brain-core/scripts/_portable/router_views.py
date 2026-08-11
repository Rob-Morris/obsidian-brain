"""Portable compiled-router inspection views without adapter dependencies."""

from __future__ import annotations

import platform
from pathlib import Path
import shutil
import sys

from _common import load_compiled_router


def read_environment(router, _vault_root=None, _name=None):
    return dict(router["environment"])


def read_router_meta(router, _vault_root=None, _name=None):
    return {
        "always_rules": router["always_rules"],
        "meta": router["meta"],
    }


def detect_environment(vault_root):
    """Return live bounded runtime facts without requiring derived state."""

    return {
        "vault_root": str(Path(vault_root).resolve()),
        "platform": sys.platform,
        "python_version": platform.python_version(),
        "cli_available": shutil.which("brain") is not None,
    }


def environment_from_vault(vault_root):
    return detect_environment(vault_root)


def router_meta_from_vault(vault_root):
    router = load_compiled_router(vault_root)
    if "error" in router:
        raise FileNotFoundError(router["error"])
    return read_router_meta(router)
