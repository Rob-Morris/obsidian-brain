"""Small lazy-import helpers for optional search dependencies."""

from __future__ import annotations

import importlib


class LazyModuleProxy:
    """Expose a module-like object without importing the target eagerly."""

    def __init__(self, module_name: str):
        # Bypass __setattr__ so _overrides exists before any override write.
        object.__setattr__(self, "_module_name", module_name)
        object.__setattr__(self, "_overrides", {})

    def __getattr__(self, name: str):
        overrides = object.__getattribute__(self, "_overrides")
        if name in overrides:
            return overrides[name]
        module = importlib.import_module(object.__getattribute__(self, "_module_name"))
        return getattr(module, name)

    def __setattr__(self, name: str, value):
        # Test monkeypatching is intentionally sticky: restore writes the original
        # object back through __setattr__, so subsequent reads come from overrides
        # rather than forcing another importlib lookup.
        object.__getattribute__(self, "_overrides")[name] = value

    def __delattr__(self, name: str):
        overrides = object.__getattribute__(self, "_overrides")
        if name in overrides:
            del overrides[name]
            return
        raise AttributeError(name)
