"""Portable compiled-router inspection views without adapter dependencies."""

from __future__ import annotations

from _common import load_compiled_router


def read_environment(router, _vault_root=None, _name=None):
    return dict(router["environment"])


def read_router_meta(router, _vault_root=None, _name=None):
    return {
        "always_rules": router["always_rules"],
        "meta": router["meta"],
    }


def environment_from_vault(vault_root):
    router = load_compiled_router(vault_root)
    if "error" in router:
        raise FileNotFoundError(router["error"])
    return read_environment(router)


def router_meta_from_vault(vault_root):
    router = load_compiled_router(vault_root)
    if "error" in router:
        raise FileNotFoundError(router["error"])
    return read_router_meta(router)
