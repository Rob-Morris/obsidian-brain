"""Platform-aware shell rendering for recovery guidance."""

from __future__ import annotations

import shlex
import subprocess
import sys


def join_argv(argv: list[str]) -> str:
    """Render argv as a guidance command for the current platform.

    Brain does not execute these strings; it shows them to users and agents.
    On Windows, ``list2cmdline`` targets cmd.exe-style quoting, which
    PowerShell also accepts for simple path arguments.
    """
    if sys.platform == "win32":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def quote_arg(value: str) -> str:
    """Render one command argument using the guidance quoting rule."""
    if sys.platform == "win32":
        return subprocess.list2cmdline([value])
    return shlex.quote(value)
