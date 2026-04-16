"""Type coercion utilities for script parameters."""

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def coerce_bool(value, default):
    """Coerce common truthy/falsey strings to bool.

    Handles None, bool, and string inputs.  Returns *default* for
    unrecognised values.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_VALUES:
            return True
        if lowered in _FALSE_VALUES:
            return False
    return default
