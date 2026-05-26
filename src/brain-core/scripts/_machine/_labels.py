"""Shared human labels for machine-level Brain records."""

from __future__ import annotations


def brain_label(brain: dict) -> str:
    """Return the stable human label for a discovered Brain row."""
    alias = brain.get("alias")
    if alias:
        return f"{alias} ({brain['path']})"
    return brain["path"]
