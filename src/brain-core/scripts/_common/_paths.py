"""Shared XDG and user-home path helpers for launcher-safe code."""

from __future__ import annotations

import os
from pathlib import Path


def config_home() -> Path:
    """Return the effective XDG config-home directory as a Path."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg and os.path.isabs(xdg):
        return Path(xdg)
    home = os.environ.get("HOME") or str(Path.home())
    return Path(home) / ".config"
