"""Shared XDG and user-home path helpers for launcher-safe code."""

from __future__ import annotations

import os
import ntpath
import sys
from pathlib import Path


def config_home() -> Path:
    """Return Brain's effective user config-home directory as a Path."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg and os.path.isabs(xdg):
        return Path(xdg)
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata and ntpath.isabs(appdata):
            return Path(appdata)
    home = os.environ.get("HOME") or str(Path.home())
    return Path(home) / ".config"
